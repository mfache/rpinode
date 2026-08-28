
def get_sys(name=''):
    try:
        if name == "cpu_temp":
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = float(f.read()) / 1000.0
            return f"{temp:.1f}"
        return "N/A"
    except Exception:
        return "N/A"
