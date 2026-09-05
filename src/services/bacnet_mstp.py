import html as html_lib
import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

try:
    import serial
except ImportError:
    serial = None

from core import paths
from services.device_mgr import list_serial_ports

logger = logging.getLogger(__name__)

MSTP_WHOIS_BIN = "/opt/boitier-bacnet/mstp/bin/bacwi"
MSTP_STATE_FILE = paths.DATA_DIR / "bacnet_mstp_state.json"

MSTP_BAUDS = [9600, 19200, 38400, 57600, 76800, 115200]
DEFAULT_BAUD = 38400
DEFAULT_MAC = 127
DEFAULT_MAX_MASTER = 127
DEFAULT_NPOLL = 2
DEFAULT_CYCLE_MS = 20000
MIN_CYCLE_MS = 5000
MAX_CYCLE_MS = 120000
DEFAULT_SNIFF_MS = 5000
MIN_SNIFF_MS = 1000
MAX_SNIFF_MS = 30000

FRESH_CYCLES = 1.5
STALE_CYCLES = 3.0

_IAM_RE = re.compile(r"^IAM;(\d+);([0-9A-Fa-f]*);(\d+)\s*$")

FRAME_TOKEN = 0
FRAME_POLL_FOR_MASTER = 1
FRAME_REPLY_TO_POLL = 2
FRAME_TEST_REQUEST = 3
FRAME_TEST_RESPONSE = 4
FRAME_DATA_EXPECTING_REPLY = 5
FRAME_DATA_NOT_EXPECTING_REPLY = 6
FRAME_REPLY_POSTPONED = 7

FRAME_TYPE_NAMES = {
    FRAME_TOKEN: "Token",
    FRAME_POLL_FOR_MASTER: "Poll For Master",
    FRAME_REPLY_TO_POLL: "Reply To Poll For Master",
    FRAME_TEST_REQUEST: "Test_Request",
    FRAME_TEST_RESPONSE: "Test_Response",
    FRAME_DATA_EXPECTING_REPLY: "Data Expecting Reply",
    FRAME_DATA_NOT_EXPECTING_REPLY: "Data NOT Expecting Reply",
    FRAME_REPLY_POSTPONED: "Reply Postponed",
}

MSTP_PREAMBLE = b"\x55\xff"
MSTP_BROADCAST = 255


def _crc8_header(data: bytes) -> int:
    """CRC de l'en-tête MS/TP (BACnet 135, Clause 9.2.3)."""
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x01:
                crc = (crc >> 1) ^ 0x81
            else:
                crc >>= 1
    return (~crc) & 0xFF


def parse_mstp_stream(data: bytes):
    """
    Décode un flux d'octets brut et renvoie (trames, statistiques).
    """
    frames = []
    stats = {
        "crc_ok": 0,
        "crc_bad": 0,
        "stray_bytes": 0,
        "frames_with_stray": 0,
        "bytes": len(data),
    }
    i = 0
    prev_end = None
    while True:
        idx = data.find(MSTP_PREAMBLE, i)
        if idx == -1 or idx + 8 > len(data):
            break
        hdr = data[idx : idx + 8]
        ftype, dest, src = hdr[2], hdr[3], hdr[4]
        length = (hdr[5] << 8) | hdr[6]
        if _crc8_header(hdr[2:7]) == hdr[7]:
            stats["crc_ok"] += 1
            frames.append({"type": ftype, "dest": dest, "src": src, "length": length})
            if prev_end is not None and idx > prev_end:
                stats["stray_bytes"] += idx - prev_end
                stats["frames_with_stray"] += 1
            prev_end = idx + 8 + length + (2 if length else 0)
            i = prev_end if prev_end > idx else idx + 1
        else:
            stats["crc_bad"] += 1
            i = idx + 1
    return frames, stats


def mstp_available() -> bool:
    """Vérifie si le binaire de découverte MS/TP bacwi est installé et exécutable."""
    return os.access(MSTP_WHOIS_BIN, os.X_OK)


def _mac_sort_key(mac: str) -> int:
    try:
        return int(mac, 16)
    except (TypeError, ValueError):
        return 1 << 32


def _bus_health(stats, types, now):
    total = stats["crc_ok"]
    tokens = types.get(FRAME_TOKEN, 0)
    polls = types.get(FRAME_POLL_FOR_MASTER, 0)
    replies = types.get(FRAME_REPLY_TO_POLL, 0)
    data_frames = types.get(FRAME_DATA_EXPECTING_REPLY, 0) + types.get(FRAME_DATA_NOT_EXPECTING_REPLY, 0)
    anomalies = []

    if total == 0:
        if stats["bytes"] == 0:
            anomalies.append({
                "level": "error",
                "text": "Aucun octet reçu : vérifiez le câblage RS-485 (D+/D−, masse commune) et que la passerelle est connectée."
            })
        else:
            anomalies.append({
                "level": "error",
                "text": f"{stats['bytes']} octets reçus mais aucune trame MS/TP valide : vitesse (bauds) probablement incorrecte."
            })
    else:
        if tokens == 0:
            anomalies.append({
                "level": "error",
                "text": "Aucun jeton ne circule : l'anneau à jeton n'est pas formé. Les équipements sont visibles mais aucune donnée applicative (donc aucun Who-Is) n'est possible."
            })
            if replies:
                anomalies.append({
                    "level": "warn",
                    "text": f"{replies} réponse(s) « Reply To Poll For Master » ignorée(s) : vérifier la paire RX, inversion A/B ou répéteur."
                })
        elif data_frames == 0:
            anomalies.append({
                "level": "warn",
                "text": "Le jeton circule mais aucune donnée applicative n'a été vue pendant l'écoute."
            })

    if stats["crc_bad"]:
        ratio = 100.0 * stats["crc_bad"] / max(1, stats["crc_bad"] + total)
        anomalies.append({
            "level": "warn" if ratio < 20 else "error",
            "text": f"{stats['crc_bad']} trame(s) corrompue(s) ({ratio:.0f} %) : vérifier terminaisons 120 Ω et blindage."
        })

    if total and stats["frames_with_stray"] >= total * 0.5:
        anomalies.append({
            "level": "warn",
            "text": f"{stats['stray_bytes']} octet(s) parasite(s) entre les trames : polarisations de ligne (bias) à vérifier."
        })

    return {
        "at": now,
        "frames": total,
        "crc_bad": stats["crc_bad"],
        "stray_bytes": stats["stray_bytes"],
        "bytes": stats["bytes"],
        "tokens": tokens,
        "polls": polls,
        "replies": replies,
        "data_frames": data_frames,
        "ring_ok": tokens > 0,
        "anomalies": anomalies,
    }


def format_health_stats(health: dict) -> str:
    if not health:
        return "aucune écoute"
    parts = []
    if health.get("ring_ok"):
        parts.append(f"jeton OK ({health.get('tokens', 0)} jetons)")
    else:
        parts.append("aucun jeton")
    parts.append(f"{health.get('frames', 0)} trames")
    if health.get("crc_bad"):
        parts.append(f"{health['crc_bad']} erreurs CRC")
    if health.get("stray_bytes"):
        parts.append(f"{health['stray_bytes']} parasites")
    return ", ".join(parts)


class _MstpSession:
    def __init__(self, params, user="local"):
        self.params = params
        self.user = user
        self.started_at = time.time()
        self.started_by = user
        self.cycles = 0
        self.last_cycle_at = None
        self.error = None
        self.phase = "demarrage"
        self.health = None
        self.devices = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.terminate()
                except OSError:
                    pass
        self._thread.join(timeout=5)

    def is_running(self):
        return self._thread.is_alive() and not self._stop.is_set()

    def _env(self):
        env = dict(os.environ)
        env.update({
            "BACNET_IFACE": self.params["device"],
            "BACNET_MSTP_BAUD": str(self.params["baud"]),
            "BACNET_MSTP_MAC": str(self.params["mac"]),
            "BACNET_MAX_MASTER": str(self.params["max_master"]),
            "BACNET_MSTP_NPOLL": str(self.params["npoll"]),
            "BACNET_MAX_INFO_FRAMES": "1",
        })
        return env

    def _run(self):
        logger.info(
            f"Session BACnet MS/TP démarrée par {self.user} sur {self.params['device']} "
            f"@{self.params['baud']} bauds (mac={self.params['mac']}, npoll={self.params['npoll']})"
        )
        while not self._stop.is_set():
            # Phase 1 : écoute passive
            self._sniff()
            if self._stop.is_set():
                break
            # Phase 2 : Who-Is actif
            self._whois()
            with self._lock:
                self.cycles += 1
                self.last_cycle_at = time.time()
            _save_state(self.snapshot())
            if self._stop.wait(0.5):
                break
        with self._lock:
            self.phase = "arretee"

    def _sniff(self):
        if serial is None:
            self._set_error("Module pyserial absent : écoute passive indisponible.")
            return
        with self._lock:
            self.phase = "ecoute"
        duration = self.params["sniff_ms"] / 1000.0
        data = b""
        try:
            ser = serial.Serial(
                self.params["device"],
                baudrate=self.params["baud"],
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=0.5,
            )
        except Exception as exc:
            self._set_error(f"Port série inaccessible ({self.params['device']}) : {exc}")
            logger.warning(f"Session MS/TP écoute impossible: {exc}")
            self._stop.wait(2)
            return

        try:
            ser.reset_input_buffer()
            end = time.time() + duration
            while time.time() < end and not self._stop.is_set():
                chunk = ser.read(4096)
                if chunk:
                    data += chunk
        except Exception as exc:
            self._set_error(f"Lecture série interrompue : {exc}")
        finally:
            try:
                ser.close()
            except Exception:
                pass

        frames, stats = parse_mstp_stream(data)
        now = time.time()
        types = {}
        with self._lock:
            for fr in frames:
                types[fr["type"]] = types.get(fr["type"], 0) + 1
                src = fr["src"]
                if src == MSTP_BROADCAST:
                    continue
                mac = f"{src:02X}"
                entry = self.devices.get(mac)
                if entry is None:
                    self.devices[mac] = {
                        "mac": mac,
                        "device": None,
                        "apdu": None,
                        "first_seen": now,
                        "last_seen": now,
                        "last_iam": None,
                        "seen": 0,
                        "frames": 1,
                    }
                    logger.info(f"Session MS/TP : nœud MAC {mac} détecté à l'écoute")
                else:
                    entry["last_seen"] = now
                    entry["frames"] = entry.get("frames", 0) + 1

            self.health = _bus_health(stats, types, now)
            if self.error and "série" not in self.error:
                self.error = None

    def _whois(self):
        if not mstp_available():
            self._set_error(f"Binaire Who-Is MS/TP introuvable ({MSTP_WHOIS_BIN}).")
            self._stop.wait(2)
            return

        with self._lock:
            self.phase = "who-is"
        cycle_ms = self.params["cycle_ms"]
        try:
            proc = subprocess.Popen(
                [MSTP_WHOIS_BIN, "--timeout", str(cycle_ms)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=self._env(),
                start_new_session=True,
            )
        except Exception as exc:
            self._set_error(f"Lancement du Who-Is impossible : {exc}")
            logger.warning(f"Session MS/TP lancement impossible : {exc}")
            self._stop.wait(2)
            return

        with self._lock:
            self._proc = proc

        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                self._parse_line(line)
        except Exception as exc:
            logger.warning(f"Session MS/TP lecture interrompue : {exc}")
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    pass
            with self._lock:
                self._proc = None

    def _parse_line(self, line):
        m = _IAM_RE.match(line.strip())
        if not m:
            return
        device_id = int(m.group(1))
        mac = m.group(2).upper()
        apdu = int(m.group(3))
        now = time.time()
        with self._lock:
            entry = self.devices.get(mac)
            if entry is None:
                self.devices[mac] = {
                    "mac": mac,
                    "device": device_id,
                    "apdu": apdu,
                    "first_seen": now,
                    "last_seen": now,
                    "last_iam": now,
                    "seen": 1,
                    "frames": 0,
                }
                logger.info(f"Session MS/TP : nouvel équipement BACnet MAC {mac} (Device ID {device_id})")
            else:
                if entry.get("device") is None:
                    logger.info(f"Session MS/TP : MAC {mac} répond au Who-Is (Device ID {device_id})")
                entry["device"] = device_id
                entry["apdu"] = apdu
                entry["last_seen"] = now
                entry["last_iam"] = now
                entry["seen"] = entry.get("seen", 0) + 1

    def _set_error(self, msg):
        with self._lock:
            self.error = msg

    def snapshot(self):
        with self._lock:
            devices = [dict(d) for d in self.devices.values()]
            error = self.error
            cycles = self.cycles
            last_cycle_at = self.last_cycle_at
            health = dict(self.health) if self.health else None
            phase = self.phase
        devices.sort(key=lambda d: _mac_sort_key(d["mac"]))
        return {
            "running": self.is_running(),
            "phase": phase,
            "params": dict(self.params),
            "started_at": self.started_at,
            "started_by": self.started_by,
            "cycles": cycles,
            "last_cycle_at": last_cycle_at,
            "error": error,
            "health": health,
            "devices": devices,
            "now": time.time(),
        }


_SESSION = None
_SESSION_LOCK = threading.Lock()


def _save_state(snap):
    try:
        os.makedirs(os.path.dirname(MSTP_STATE_FILE), exist_ok=True)
        tmp = str(MSTP_STATE_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f, indent=2, ensure_ascii=False)
        os.replace(tmp, MSTP_STATE_FILE)
    except OSError as exc:
        logger.warning(f"Session MS/TP état non enregistré : {exc}")


def _load_state():
    try:
        with open(MSTP_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


def get_mstp_snapshot():
    with _SESSION_LOCK:
        session = _SESSION
    if session is not None:
        return session.snapshot()
    saved = _load_state()
    if saved:
        saved["running"] = False
        saved["now"] = time.time()
        return saved
    return {
        "running": False,
        "phase": "inactif",
        "params": {
            "device": "",
            "baud": DEFAULT_BAUD,
            "mac": DEFAULT_MAC,
            "max_master": DEFAULT_MAX_MASTER,
            "npoll": DEFAULT_NPOLL,
            "cycle_ms": DEFAULT_CYCLE_MS,
            "sniff_ms": DEFAULT_SNIFF_MS,
        },
        "devices": [],
        "health": None,
        "error": None,
        "cycles": 0,
        "now": time.time(),
    }


def get_mstp_stream_payload():
    snap = get_mstp_snapshot()
    params = snap.get("params") or {}
    cycle_s = (params.get("cycle_ms", DEFAULT_CYCLE_MS) + params.get("sniff_ms", DEFAULT_SNIFF_MS)) / 1000.0
    health = snap.get("health")
    return {
        "running": snap.get("running", False),
        "phase": snap.get("phase", "inactif"),
        "cycles": snap.get("cycles", 0),
        "cycle_s": round(cycle_s, 1),
        "now": snap.get("now", time.time()),
        "devices": [
            {
                "mac": d["mac"],
                "device": d.get("device"),
                "apdu": d.get("apdu"),
                "last_seen": round(d.get("last_seen", 0), 1),
                "frames": d.get("frames", 0),
                "seen": d.get("seen", 0),
            }
            for d in snap.get("devices", [])
        ],
        "health": {
            "stats": format_health_stats(health),
            "anomalies": health.get("anomalies", []),
        } if health else None,
        "error": snap.get("error"),
    }


def get_mstp_signature():
    snap = get_mstp_snapshot()
    health = snap.get("health") or {}
    return {
        "running": snap.get("running", False),
        "phase": snap.get("phase"),
        "cycles": snap.get("cycles", 0),
        "error": snap.get("error"),
        "ring_ok": health.get("ring_ok"),
        "anomalies": [a["text"] for a in health.get("anomalies", [])],
        "devices": [
            (d["mac"], d.get("device"), round(d.get("last_seen", 0), 1))
            for d in snap.get("devices", [])
        ],
    }


def start_mstp_session(params: dict, user: str = "local") -> tuple[bool, str]:
    global _SESSION
    device = params.get("device", "").strip()
    if not device:
        return False, "Choisissez un périphérique série (passerelle RS-485)."
    if not os.path.exists(device):
        return False, f"Périphérique introuvable ({device}). Vérifiez le branchement USB."

    try:
        baud = int(params.get("baud", DEFAULT_BAUD))
        if baud not in MSTP_BAUDS:
            baud = DEFAULT_BAUD
    except ValueError:
        baud = DEFAULT_BAUD

    try:
        mac = max(0, min(127, int(params.get("mac", DEFAULT_MAC))))
    except ValueError:
        mac = DEFAULT_MAC

    try:
        max_master = max(0, min(127, int(params.get("max_master", DEFAULT_MAX_MASTER))))
    except ValueError:
        max_master = DEFAULT_MAX_MASTER

    try:
        npoll = max(1, min(50, int(params.get("npoll", DEFAULT_NPOLL))))
    except ValueError:
        npoll = DEFAULT_NPOLL

    try:
        cycle_ms = max(MIN_CYCLE_MS, min(MAX_CYCLE_MS, int(params.get("cycle_ms", DEFAULT_CYCLE_MS))))
    except ValueError:
        cycle_ms = DEFAULT_CYCLE_MS

    try:
        sniff_ms = max(MIN_SNIFF_MS, min(MAX_SNIFF_MS, int(params.get("sniff_ms", DEFAULT_SNIFF_MS))))
    except ValueError:
        sniff_ms = DEFAULT_SNIFF_MS

    clean_params = {
        "device": device,
        "baud": baud,
        "mac": mac,
        "max_master": max_master,
        "npoll": npoll,
        "cycle_ms": cycle_ms,
        "sniff_ms": sniff_ms,
    }

    with _SESSION_LOCK:
        if _SESSION is not None and _SESSION.is_running():
            return False, "Une session MS/TP est déjà en cours d'exécution."
        _SESSION = _MstpSession(clean_params, user=user)
        _SESSION.start()

    return True, f"Surveillance BACnet MS/TP démarrée sur {device} @ {baud} bauds."


def stop_mstp_session() -> tuple[bool, str]:
    global _SESSION
    with _SESSION_LOCK:
        session = _SESSION
        _SESSION = None
    if session is not None:
        session.stop()
        return True, "Session BACnet MS/TP arrêtée."
    return True, "Aucune session MS/TP en cours."
