import subprocess

def get_sys(name=''):
    try:
        if name == "cpu_temp":
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = float(f.read()) / 1000.0
            return f"{temp:.1f}"
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
