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
