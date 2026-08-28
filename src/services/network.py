import subprocess
import json
import logging

logger = logging.getLogger(__name__)

def get_interface_status(iface):
    """Retourne l'état (up/down), l'IP, la MAC et les routes d'une interface."""
    try:
        # 1. Infos d'adresse et lien
        cmd = ["ip", "-j", "addr", "show", iface]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"active": False, "ip": "Non détectée", "mac": "-", "routes": []}
            
        data = json.loads(result.stdout)
        if not data:
            return {"active": False, "ip": "Absent", "mac": "-", "routes": []}
            
        iface_data = data[0]
        flags = iface_data.get("flags", [])
        operstate = iface_data.get("operstate", "")
        mac = iface_data.get("address", "-")
        
        ip = "Pas d'IP"
        has_ip = False
        for addr in iface_data.get("addr_info", []):
            if addr.get("family") == "inet":
                ip = f"{addr['local']}/{addr.get('prefixlen', 24)}"
                has_ip = True
                break
        
        is_up = (operstate == "UP") or ("UP" in flags and has_ip)
        
        # 2. Infos de routage
        routes = []
        cmd_route = ["ip", "-j", "route", "show", "dev", iface]
        result_route = subprocess.run(cmd_route, capture_output=True, text=True)
        if result_route.returncode == 0:
            route_data = json.loads(result_route.stdout)
            for r in route_data:
                dst = r.get("dst", "unknown")
                gw = r.get("gateway", "")
                metric = r.get("metric", "")
                route_str = f"{dst}"
                if gw: route_str += f" via {gw}"
                if metric: route_str += f" (m:{metric})"
                routes.append(route_str)

        return {
            "active": is_up, 
            "ip": ip, 
            "mac": mac,
            "routes": routes
        }
    except Exception as e:
        logger.error(f"Erreur status {iface}: {e}")
        return {"active": False, "ip": "Erreur", "mac": "-", "routes": []}

def get_tailscale_status():
    """Retourne les infos détaillées de Tailscale."""
    try:
        cmd = ["tailscale", "status", "--json"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"active": False, "ip": "-", "name": "-", "routes": []}
            
        data = json.loads(result.stdout)
        self_info = data.get("Self", {})
        backend = data.get("BackendState")
        online = self_info.get("Online", False)
        
        ips = self_info.get("TailscaleIPs", ["-"])
        name = (self_info.get("DNSName") or "-").rstrip(".")
        routes = self_info.get("PrimaryRoutes", [])
        
        return {
            "active": (backend == "Running" and online),
            "status": backend,
            "ip": ips[0] if ips else "-",
            "name": name,
            "routes": routes
        }
    except:
        return {"active": False, "ip": "-", "name": "-", "routes": []}

def get_tailscale_exit_node():
    """Détermine par quelle interface Tailscale sort vers Internet."""
    try:
        # Note: On regarde la route par défaut.
        cmd_route = ["ip", "route", "get", "8.8.8.8"]
        result_route = subprocess.run(cmd_route, capture_output=True, text=True)
        
        if "dev wwan0" in result_route.stdout: return "wwan0"
        if "dev eth0" in result_route.stdout: return "eth0"
        if "dev wlan0" in result_route.stdout: return "wlan0"
        
        return "wwan0" # Par défaut sur nos boitiers
    except:
        return "wwan0"

def get_network_overview():
    """Agrège toutes les infos pour le graphique et les détails."""
    wwan = get_interface_status("wwan0")
    eth = get_interface_status("eth0")
    wlan = get_interface_status("wlan0")
    ts = get_tailscale_status()
    ts_exit = get_tailscale_exit_node()
    
    return {
        "wwan0": wwan,
        "eth0": eth,
        "wlan0": wlan,
        "tailscale": ts,
        "ts_exit": ts_exit
    }
