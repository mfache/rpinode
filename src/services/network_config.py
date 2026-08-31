import logging
import shlex
import subprocess

from core.database import get_db_connection

logger = logging.getLogger(__name__)

def apply_site_network_profiles(site_id):
    """
    Récupère et applique les profils réseau associés à un chantier.
    """
    logger.info(f"Vérification des profils réseau pour le site {site_id}")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT interface, method, addresses, gateway, dhcp_range, ssid, psk FROM site_network_profiles WHERE site_id = ?",
            (site_id,)
        )
        profiles = cursor.fetchall()

    if not profiles:
        logger.info(f"Aucun profil réseau spécifique pour le site {site_id}. Reset eth0 en DHCP.")
        _apply_eth0_profile("auto", None, None, None)
        return

    # On suit les interfaces traitées
    applied_ifaces = []

    for p in profiles:
        iface = p["interface"]
        method = p["method"]
        applied_ifaces.append(iface)
        
        if iface == "eth0":
            _apply_eth0_profile(method, p["addresses"], p["gateway"], p.get("dhcp_range"))
        elif iface == "wlan0":
            # Pour wlan0, on délègue au wifi_mgr qui gère les priorités (RPIRESCUE, AP, etc.)
            try:
                from services.wifi_mgr import manage_wifi
                manage_wifi(force=True)
            except Exception as e:
                logger.error(f"Erreur lors de l'appel à manage_wifi: {e}")

    # Si eth0 n'était pas dans les profils, on s'assure qu'il est en DHCP (reset)
    if "eth0" not in applied_ifaces:
        logger.info(f"Pas de profil spécifique pour eth0 sur le site {site_id}. Reset en DHCP.")
        _apply_eth0_profile("auto", None, None, None)

def publish_tailscale_routes():
    """Publie les réseaux locaux sur Tailscale pour l'accès distant."""
    import ipaddress

    from services.network import get_interface_status
    
    routes = []
    for iface in ["eth0", "wlan0"]:
        status = get_interface_status(iface)
        if status["active"] and "/" in status["ip"]:
            try:
                # On extrait le réseau correspondant à l'IP
                net = ipaddress.IPv4Interface(status["ip"]).network
                routes.append(str(net))
            except Exception:
                continue
    
    if routes:
        routes_str = ",".join(set(routes))
        logger.info(f"Publication des routes sur Tailscale : {routes_str}")
        subprocess.run(f"sudo tailscale set --advertise-routes={routes_str}", shell=True)

def _apply_eth0_profile(method, addresses, gateway, dhcp_range=None):
    """Applique la config sur eth0 via nmcli."""
    logger.info(f"Application profil eth0 ({method})")

    # Trouver le nom de la connexion pour eth0
    cmd_find = "nmcli -t -f NAME,DEVICE con show --active | grep ':eth0$' | cut -d: -f1"
    res = subprocess.run(cmd_find, shell=True, capture_output=True, text=True)
    con_name = res.stdout.strip() or "eth0-manual"

    if method == "auto":
        nm_cmd = f"sudo nmcli con mod '{con_name}' ipv4.method auto ipv4.addresses '' ipv4.gateway '' ipv4.dhcp-range ''"
    else:
        # Nettoyage des adresses. Pour nmcli, plusieurs adresses doivent être séparées par des virgules
        # dans une seule chaîne de caractères si on utilise 'con mod'.
        import json
        try:
            addrs_list = json.loads(addresses) if addresses else []
            if not isinstance(addrs_list, list):
                addrs_list = [addresses]
        except Exception:
            addrs_list = [a.strip() for a in (addresses or "").split(",") if a.strip()]

        addrs_nm = ",".join(addrs_list)
        logger.info(f"Paramètres NM pour eth0: method={method}, addresses='{addrs_nm}', gateway='{gateway}', dhcp_range='{dhcp_range}'")

        nm_cmd = f"sudo nmcli con mod '{con_name}' ipv4.method {method} ipv4.addresses '{addrs_nm}'"
        if gateway:
            nm_cmd += f" ipv4.gateway '{gateway}'"
        else:
            nm_cmd += f" ipv4.gateway ''"
            
        if method == "shared":
            if dhcp_range:
                nm_cmd += f" ipv4.dhcp-range '{dhcp_range}'"
            else:
                nm_cmd += f" ipv4.dhcp-range ''"
    
    try:
        # 0. On désactive temporairement pour que NM lâche prise
        subprocess.run(f"sudo nmcli con down '{con_name}'", shell=True, check=False, timeout=10)

        # 1. On vide radicalement les adresses pour éviter que l'ancienne IP (IEJN) ne reste
        # On le fait deux fois pour être sûr, car NM peut réagir lentement
        logger.info("Flush des adresses sur eth0...")
        subprocess.run(f"sudo ip addr flush dev eth0", shell=True, check=False)
        
        # 2. On modifie le profil NM
        subprocess.run(nm_cmd, shell=True, check=True)
        
        # 3. On tente de lever la connexion
        # On force la porteuse à être ignorée pour ne pas bloquer si pas de câble
        subprocess.run(f"sudo nmcli con mod '{con_name}' ipv4.never-default yes", shell=True, check=True)
        
        res_up = subprocess.run(f"sudo nmcli con up '{con_name}'", shell=True, capture_output=True, text=True, timeout=30)
        
        # 4. Flush de sécurité APRES le up si il a échoué (car il a pu remettre des IPs fantômes)
        if res_up.returncode != 0:
            subprocess.run(f"sudo ip addr flush dev eth0", shell=True, check=False)
            if "no suitable device" in res_up.stderr.lower() or "no carrier" in res_up.stderr.lower():
                logger.info("eth0 n'a pas de lien physique, le profil DHCP sera activé au branchement.")
            else:
                logger.error(f"Erreur activation eth0 : {res_up.stderr}")
        else:
            logger.info(f"Profil eth0 appliqué avec succès.")
        
        # Publication des routes sur Tailscale
        publish_tailscale_routes()
    except Exception as e:
        logger.error(f"Erreur lors de l'application du profil eth0: {e}")

def get_site_network_profile(site_id, interface="eth0"):
    """Récupère le profil réseau d'une interface pour un site donné."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM site_network_profiles WHERE site_id = ? AND interface = ?",
            (site_id, interface)
        )
        row = cursor.fetchone()
        return dict(row) if row else None



def save_site_network_profiles(site_id, profiles_list, is_dirty=True):
    """
    Enregistre une liste de profils réseau pour un chantier.
    profiles_list: liste de dict [{interface, method, addresses, gateway, ssid, psk}]
    """
    if not isinstance(profiles_list, list):
        profiles_list = [profiles_list]
        
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for p in profiles_list:
            # Gérer les deux noms de clés possibles ('iface' ou 'interface')
            iface = p.get('interface') or p.get('iface')
            if not iface:
                continue
                
            import json
            addresses = p.get('addresses')
            if isinstance(addresses, list):
                addresses = json.dumps(addresses)
            elif isinstance(addresses, str):
                try:
                    # check if it's already a valid json
                    json.loads(addresses)
                except Exception:
                    # convert comma separated string to JSON list
                    addresses = json.dumps([a.strip() for a in addresses.split(',') if a.strip()])
                
            cursor.execute(
                """
                INSERT INTO site_network_profiles
                (site_id, interface, method, addresses, gateway, dhcp_range, ssid, psk, is_dirty)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id, interface) DO UPDATE SET
                    method = excluded.method,
                    addresses = excluded.addresses,
                    gateway = excluded.gateway,
                    dhcp_range = excluded.dhcp_range,
                    ssid = excluded.ssid,
                    psk = excluded.psk,
                    is_dirty = excluded.is_dirty,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (site_id, iface, p.get('method', 'auto'), addresses,
                 p.get('gateway'), p.get('dhcp_range'), p.get('ssid'), p.get('psk'), 1 if is_dirty else 0)
            )
        conn.commit()
