import socket
import struct
import re

# Constantes Modbus
READ_TIMEOUT = 1.0
SCAN_TIMEOUT = 0.5
MIN_TIMEOUT = 0.2
MAX_TIMEOUT = 30.0
MAX_SCAN_TIMEOUT = 15.0

MAX_REGISTERS = 125
MAX_BITS = 2000
MAX_SCAN = 64
MAX_SCAN_SECONDS = 120

PROBE_FUNC_LABELS = {
    1: "FC01 Coils",
    2: "FC02 Discrete",
    3: "FC03 Holding",
    4: "FC04 Input",
}
PROBE_MAX_SPAN = 2000
PROBE_MAX_SECONDS = 180

EXCEPTION_NAMES = {
    1: "Fonction illégale (01)",
    2: "Adresse de donnée illégale (02)",
    3: "Valeur de donnée illégale (03)",
    4: "Défaillance de l'esclave (04)",
    5: "Acquittement / traitement long (05)",
    6: "Esclave occupé (06)",
    8: "Erreur de parité mémoire (08)",
    10: "Passerelle : chemin indisponible (0A)",
    11: "Passerelle : pas de réponse de la cible (0B)",
}

class ModbusError(Exception):
    """Erreur applicative Modbus (exception protocolaire, timeout ou réseau)."""

# ---------------------------------------------------------------------------
# Couche transport : MBAP + PDU sur TCP.
# ---------------------------------------------------------------------------
def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ModbusError("Connexion fermée par la cible (réponse tronquée).")
        buf += chunk
    return buf

def _txn_on_socket(sock, unit, pdu, tid=1):
    """Une transaction Modbus sur une socket déjà ouverte ; renvoie la PDU réponse."""
    mbap = struct.pack(">HHHB", tid & 0xFFFF, 0, len(pdu) + 1, unit & 0xFF)
    sock.sendall(mbap + pdu)
    header = _recv_exact(sock, 7)
    _, _, length, _ = struct.unpack(">HHHB", header)
    if length < 1:
        raise ModbusError("En-tête MBAP invalide.")
    body = _recv_exact(sock, length - 1)
    if not body:
        raise ModbusError("Réponse vide.")
    func = body[0]
    if func & 0x80:  # bit d'erreur : c'est une réponse d'exception
        code = body[1] if len(body) > 1 else 0
        name = EXCEPTION_NAMES.get(code, f"Code exception {code}")
        raise ModbusError(f"Exception Modbus : {name}")
    return body

def _tcp_transaction(ip, port, unit, pdu, timeout, tid):
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            return _txn_on_socket(s, unit, pdu, tid)
    except ModbusError:
        raise
    except socket.timeout:
        raise ModbusError(f"Délai dépassé ({timeout:g} s) : pas de réponse.")
    except OSError as exc:
        raise ModbusError(f"Connexion impossible : {exc}")

def transaction(protocol, address, port, unit, pdu, timeout=READ_TIMEOUT, tid=1):
    """Joue une transaction Modbus (TCP ou RTU) et referme la connexion."""
    if protocol == "mstp" or protocol == "rtu":
        raise ModbusError("Support RTU non encore complètement importé dans modbus_tools.")
        # return _rtu_transaction(conn, unit, pdu, timeout)
    return _tcp_transaction(address, port, unit, pdu, timeout, tid)

# ---------------------------------------------------------------------------
# Fonctions Modbus applicatives.
# ---------------------------------------------------------------------------
def read_registers(protocol, address, port, unit, function, reg_address, count, timeout=READ_TIMEOUT):
    """FC03 (Holding) ou FC04 (Input). Renvoie une liste d'entiers 16 bits."""
    if function not in (3, 4):
        raise ModbusError("Fonction de lecture registre invalide.")
    if not 1 <= count <= MAX_REGISTERS:
        raise ModbusError(f"Quantité hors limites (1 à {MAX_REGISTERS}).")
    pdu = struct.pack(">BHH", function, reg_address & 0xFFFF, count)
    body = transaction(protocol, address, port, int(unit), pdu, timeout)
    byte_count = body[1]
    data = body[2 : 2 + byte_count]
    if len(data) != count * 2:
        raise ModbusError("Réponse incohérente (taille des données inattendue).")
    return list(struct.unpack(">" + "H" * count, data))

def read_bits(protocol, address, port, unit, function, reg_address, count, timeout=READ_TIMEOUT):
    """FC01 (Coils) ou FC02 (Discrete Inputs). Renvoie une liste de booléens."""
    if function not in (1, 2):
        raise ModbusError("Fonction de lecture bit invalide.")
    if not 1 <= count <= MAX_BITS:
        raise ModbusError(f"Quantité hors limites (1 à {MAX_BITS}).")
    pdu = struct.pack(">BHH", function, reg_address & 0xFFFF, count)
    body = transaction(protocol, address, port, int(unit), pdu, timeout)
    byte_count = body[1]
    data = body[2 : 2 + byte_count]
    bits = []
    for i in range(count):
        byte = data[i // 8] if (i // 8) < len(data) else 0
        bits.append(bool((byte >> (i % 8)) & 1))
    return bits

def write_single_register(protocol, address, port, unit, reg_address, value, timeout=READ_TIMEOUT):
    """FC06 : écrit un registre 16 bits. Renvoie (adresse, valeur) écho de l'esclave."""
    pdu = struct.pack(">BHH", 6, reg_address & 0xFFFF, int(value) & 0xFFFF)
    body = transaction(protocol, address, port, int(unit), pdu, timeout)
    _, r_addr, r_val = struct.unpack(">BHH", body[:5])
    return r_addr, r_val

def write_single_coil(protocol, address, port, unit, reg_address, on, timeout=READ_TIMEOUT):
    """FC05 : écrit un coil (ON/OFF). Renvoie (adresse, valeur) écho de l'esclave."""
    pdu = struct.pack(">BHH", 5, reg_address & 0xFFFF, 0xFF00 if on else 0x0000)
    body = transaction(protocol, address, port, int(unit), pdu, timeout)
    _, r_addr, r_val = struct.unpack(">BHH", body[:5])
    return r_addr, r_val

def _classify_modbus_error(msg):
    if "02" in msg:
        return "illegal"
    elif "Délai dépassé" in msg:
        return "timeout"
    return "other"

def probe_range(protocol, address, port, unit, funcs, start, end, block, timeout):
    """Sonde [start..end] par blocs sur les fonctions demandées (FC01/02/03/04),
    sans rien savoir de la table de registres. Renvoie, par fonction :
    {"found": [(adresse, [valeurs])...], "illegal": n, "other": n, "timeout": n}.
    """
    results = {}
    for func in funcs:
        is_bits = func in (1, 2)
        maxc = MAX_BITS if is_bits else MAX_REGISTERS
        count = max(1, min(block, maxc))
        found = []
        illegal = other = timeouts = 0
        addr = start
        while addr <= end:
            c = min(count, end - addr + 1)
            try:
                if is_bits:
                    vals = read_bits(protocol, address, port, unit, func, addr, c, timeout)
                else:
                    vals = read_registers(protocol, address, port, unit, func, addr, c, timeout)
                found.append((addr, vals))
            except ModbusError as exc:
                kind = _classify_modbus_error(str(exc))
                if kind == "illegal":
                    illegal += 1
                elif kind == "timeout":
                    timeouts += 1
                else:
                    other += 1
            except Exception as e:
                other += 1
            addr += c
        results[func] = {
            "found": found,
            "illegal": illegal,
            "other": other,
            "timeout": timeouts,
        }
    return results
