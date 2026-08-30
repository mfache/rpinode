import re
import socket
import struct
import threading
import time

# Constantes Modbus
READ_TIMEOUT = 1.5
SCAN_TIMEOUT = 0.5
MIN_TIMEOUT = 0.2
MAX_TIMEOUT = 30.0
MAX_SCAN_TIMEOUT = 15.0

# Synchronisation et gestion de concurrence pour bus RS485 / passerelles
_GATEWAY_LOCKS = {}
_GATEWAY_LOCKS_MUTEX = threading.Lock()
_LAST_TXN_TIME = {}

def _get_gateway_lock(address, port):
    key = f"{address}:{port}"
    with _GATEWAY_LOCKS_MUTEX:
        if key not in _GATEWAY_LOCKS:
            _GATEWAY_LOCKS[key] = threading.Lock()
        return _GATEWAY_LOCKS[key], key

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

def crc16(data: bytes) -> int:
    """Calcule le CRC16 Modbus (polynôme 0xA001)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc

def _rtu_over_tcp_transaction(ip, port, unit, pdu, timeout):
    raw_req = struct.pack(">B", unit & 0xFF) + pdu
    crc = crc16(raw_req)
    req = raw_req + struct.pack("<H", crc)
    
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(req)
            
            hdr = _recv_exact(s, 2)
            resp_unit, resp_fc = hdr[0], hdr[1]
            
            if resp_fc & 0x80:
                rest = _recv_exact(s, 3)
                full_resp = hdr + rest
            elif resp_fc in (1, 2, 3, 4):
                bc_byte = _recv_exact(s, 1)
                byte_count = bc_byte[0]
                rest = _recv_exact(s, byte_count + 2)
                full_resp = hdr + bc_byte + rest
            elif resp_fc in (5, 6, 15, 16):
                rest = _recv_exact(s, 6)
                full_resp = hdr + rest
            else:
                rest = s.recv(1024)
                full_resp = hdr + rest
    except ModbusError:
        raise
    except socket.timeout:
        raise ModbusError(f"Délai dépassé ({timeout:g} s) : pas de réponse.")
    except OSError as exc:
        raise ModbusError(f"Connexion impossible : {exc}")
        
    if len(full_resp) < 4:
        raise ModbusError("Réponse RTU trop courte.")
        
    resp_crc = struct.unpack("<H", full_resp[-2:])[0]
    calc_crc = crc16(full_resp[:-2])
    if resp_crc != calc_crc:
        raise ModbusError(f"Erreur de contrôle CRC RTU (reçu 0x{resp_crc:04x}, attendu 0x{calc_crc:04x}).")
        
    body = full_resp[1:-2]
    if not body:
        raise ModbusError("Réponse vide.")
    func = body[0]
    if func & 0x80:
        code = body[1] if len(body) > 1 else 0
        name = EXCEPTION_NAMES.get(code, f"Code exception {code}")
        raise ModbusError(f"Exception Modbus : {name}")
    return body

def transaction(protocol, address, port, unit, pdu, timeout=READ_TIMEOUT, tid=1, retries=2):
    """Joue une transaction Modbus (TCP ou RTU) avec gestion de concurrence (Mutex), espacement inter-trame et réessai."""
    lock, key = _get_gateway_lock(address, port or 502)
    with lock:
        # Espacement minimal de 60ms pour laisser le bus RS485 et les transceivers se réinitialiser
        now = time.time()
        elapsed = now - _LAST_TXN_TIME.get(key, 0)
        if elapsed < 0.060:
            time.sleep(0.060 - elapsed)
            
        last_error = None
        for attempt in range(retries + 1):
            try:
                if protocol in ("rtu_over_tcp", "rtu", "mstp"):
                    if re.match(r"^(\d{1,3}\.){3}\d{1,3}$", str(address)):
                        res = _rtu_over_tcp_transaction(address, port or 502, unit, pdu, timeout)
                    else:
                        raise ModbusError("Liaison série directe (port COM) non encore configurée.")
                else:
                    res = _tcp_transaction(address, port, unit, pdu, timeout, tid)
                _LAST_TXN_TIME[key] = time.time()
                return res
            except ModbusError as e:
                _LAST_TXN_TIME[key] = time.time()
                # Si c'est une exception applicative Modbus (ex: 02 Adresse illégale), on ne retente pas inutilement
                if "Exception Modbus" in str(e):
                    raise
                last_error = e
                if attempt < retries:
                    time.sleep(0.15)  # 150ms de purge du buffer série avant réessai
            except Exception as e:
                _LAST_TXN_TIME[key] = time.time()
                last_error = ModbusError(str(e))
                if attempt < retries:
                    time.sleep(0.15)
                    
        _LAST_TXN_TIME[key] = time.time()
        raise last_error or ModbusError("Échec de la transaction Modbus.")

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
