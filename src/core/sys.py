import subprocess


def get_sys(name=''):
    try:
        if name == "cpu_temp":
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = float(f.read()) / 1000.0
            return f"{temp:.1f}"
        elif name == "uptime":
            with open("/proc/uptime", "r") as f:
                total_seconds = int(float(f.readline().split()[0]))
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            if days > 0:
                return f"{days}j {hours:02d}h {minutes:02d}m"
            elif hours > 0:
                return f"{hours}h {minutes:02d}m"
            else:
                return f"{minutes} min"
        return "N/A"
    except Exception:
        return "N/A"

def ping_check(interface=None, target="8.8.8.8", timeout=3):
    """Vérifie la connectivité via un ping."""
    cmd = ["ping", "-c", "1", "-W", str(timeout), target]
    if interface:
        cmd.extend(["-I", interface])
    
    try:
        res = subprocess.run(cmd, capture_output=True)
        return res.returncode == 0
    except Exception:
        return False

def is_process_running(pattern):
    """Vérifie si un processus correspondant au motif est actif (via pgrep)."""
    try:
        res = subprocess.run(["pgrep", "-f", pattern], capture_output=True)
        return res.returncode == 0
    except Exception:
        return False

def get_undervoltage_status():
    """Interroge vcgencmd pour détecter une sous-alimentation (actuelle ou passée).
    Retourne None si vcgencmd est indisponible (ex: hors Raspberry Pi)."""
    try:
        res = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3)
        if res.returncode != 0 or "=" not in res.stdout:
            return None
        value = int(res.stdout.strip().split("=")[1], 16)
        return {
            "now": bool(value & 0x1),
            "past": bool(value & 0x10000)
        }
    except Exception:
        return None
