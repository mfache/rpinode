import json
import logging
import subprocess

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
        
        cable = "NO-CARRIER" not in flags
        
        # Check DHCP & Profile IP
        is_dhcp = False
        is_dhcp_server = False
        con_name = None
        try:
            # Récupérer la connexion active
            cmd_con = ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", iface]
            res_con = subprocess.run(cmd_con, capture_output=True, text=True)
            if res_con.returncode == 0:
                con_line = res_con.stdout.strip()
                if con_line.startswith("GENERAL.CONNECTION:"):
                    val = con_line.split(":", 1)[1]
                    if val and val != "--":
                        con_name = val

            # Si le câble est débranché, NM peut cacher la connexion active.
            # On tente de fallback sur le nom standard utilisé par l'app.
            if not con_name and iface == "eth0":
                con_name = "eth0-manual"

            if con_name:
                # Check method
                cmd_meth = ["nmcli", "-t", "-f", "ipv4.method", "con", "show", con_name]
                res_meth = subprocess.run(cmd_meth, capture_output=True, text=True)
                if res_meth.returncode == 0:
                    meth_line = res_meth.stdout.strip()
                    if meth_line == "ipv4.method:auto":
                        is_dhcp = True
                    elif meth_line == "ipv4.method:shared":
                        is_dhcp_server = True
                    elif meth_line == "ipv4.method:manual":
                        # Si pas d'IP détectée physiquement (ex: débranché), on lit le profil
                        if not has_ip:
                            cmd_ip = ["nmcli", "-t", "-f", "ipv4.addresses", "con", "show", con_name]
                            res_ip = subprocess.run(cmd_ip, capture_output=True, text=True)
                            if res_ip.returncode == 0:
                                ip_line = res_ip.stdout.strip()
                                if ip_line.startswith("ipv4.addresses:"):
                                    static_ips = ip_line.split(":", 1)[1]
                                    if static_ips:
                                        ip = static_ips.split(",")[0]
                                        # On ne met pas has_ip = True car il n'est pas "up" réseau,
                                        # mais on a récupéré l'affichage.
        except Exception:
            pass

        if cable and not has_ip and is_dhcp:
            ip = "En attente d'attribution DHCP"
        elif not cable and is_dhcp and not has_ip:
            ip = "DHCP (Auto)"

        if not cable:
            is_up = False
        else:
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
            "cable": cable,
            "dhcp": is_dhcp,
            "is_dhcp_server": is_dhcp_server,
            "has_ip": has_ip,
            "ip": ip,
            "mac": mac,
            "routes": routes
        }
    except Exception as e:
        logger.error(f"Erreur status {iface}: {e}")
        return {"active": False, "cable": False, "dhcp": False, "is_dhcp_server": False, "has_ip": False, "ip": "Erreur", "mac": "-", "routes": []}

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

def get_active_wifi_macs(iface="wlan0"):
    """Récupère la liste des adresses MAC réellement connectées au WiFi."""
    macs = set()
    try:
        cmd = ["sudo", "iw", "dev", iface, "station", "dump"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Station"):
                    parts = line.split()
                    if len(parts) >= 2:
                        macs.add(parts[1].lower())
    except Exception as e:
        logger.debug(f"Erreur iw station dump {iface}: {e}")
    return macs

def get_dhcp_clients(iface="wlan0"):
    """Récupère la liste des clients DHCP connectés (via dnsmasq de NM)."""
    clients = []
    lease_file = f"/var/lib/NetworkManager/dnsmasq-{iface}.leases"
    
    active_macs = None
    if iface == "wlan0":
        active_macs = get_active_wifi_macs(iface)

    try:
        # Lecture avec sudo car le dossier est restreint
        cmd = ["sudo", "cat", lease_file]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    mac = parts[1].lower()
                    
                    # Si c'est du WiFi, on filtre par présence réelle (iw station dump)
                    if active_macs is not None and mac not in active_macs:
                        continue

                    # Format: timestamp mac ip hostname client_id
                    clients.append({
                        "ip": parts[2],
                        "mac": mac,
                        "hostname": parts[3] if parts[3] != "*" else "Inconnu"
                    })
            logger.debug(f"DHCP Clients trouvés sur {iface}: {len(clients)}")
    except Exception as e:
        logger.debug(f"Erreur lecture baux DHCP {iface}: {e}")
        
    return clients

def get_network_overview():
    """Agrège toutes les infos pour le graphique et les détails."""
    wwan = get_interface_status("wwan0")
    eth = get_interface_status("eth0")
    wlan = get_interface_status("wlan0")
    
    # Ajout des clients DHCP uniquement si l'interface est serveur DHCP
    wlan["clients"] = get_dhcp_clients("wlan0") if wlan.get("is_dhcp_server") else []
    eth["clients"] = get_dhcp_clients("eth0") if eth.get("is_dhcp_server") else []
    
    ts = get_tailscale_status()
    ts_exit = get_tailscale_exit_node()
    
    return {
        "wwan0": wwan,
        "eth0": eth,
        "wlan0": wlan,
        "tailscale": ts,
        "ts_exit": ts_exit
    }
