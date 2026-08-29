import logging
import shlex
import subprocess

from core.database import get_db_connection

logger = logging.getLogger(__name__)

def apply_site_network_profiles(site_id):
    """
    Récupère et applique les profils réseau associés à un chantier.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT interface, method, addresses, gateway, ssid, psk FROM site_network_profiles WHERE site_id = ?",
            (site_id,)
        )
        profiles = cursor.fetchall()

    if not profiles:
        logger.info(f"Aucun profil réseau spécifique pour le site {site_id}. Reset eth0 en DHCP.")
        _apply_eth0_profile("auto", None, None)
        return

    for p in profiles:
        iface = p["interface"]
        method = p["method"]
        
        if iface == "eth0":
            _apply_eth0_profile(method, p["addresses"], p["gateway"])
        elif iface == "wlan0":
            _apply_wlan0_profile(method, p["ssid"], p["psk"], p["addresses"], p["gateway"])

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

def _apply_eth0_profile(method, addresses, gateway):
    """Applique la config sur eth0 via nmcli."""
    logger.info(f"Application profil eth0 ({method})")
    
    # Trouver le nom de la connexion pour eth0
    cmd_find = "nmcli -t -f NAME,DEVICE con show --active | grep ':eth0$' | cut -d: -f1"
    res = subprocess.run(cmd_find, shell=True, capture_output=True, text=True)
    con_name = res.stdout.strip() or "eth0-manual"

    if method == "auto":
        nm_cmd = f"sudo nmcli con mod '{con_name}' ipv4.method auto ipv4.addresses '' ipv4.gateway ''"
    else:
        # Nettoyage des adresses. Pour nmcli, plusieurs adresses doivent être séparées par des virgules
        # dans une seule chaîne de caractères si on utilise 'con mod'.
        addrs_nm = ",".join([a.strip() for a in addresses.split(",") if a.strip()])
        logger.info(f"Paramètres NM pour eth0: method={method}, addresses='{addrs_nm}', gateway='{gateway}'")
        
        nm_cmd = f"sudo nmcli con mod '{con_name}' ipv4.method manual ipv4.addresses '{addrs_nm}'"
        if gateway:
            nm_cmd += f" ipv4.gateway '{gateway}'"
    
    try:
        # 1. On vide radicalement les adresses pour éviter que l'ancienne IP (IEJN) ne reste
        subprocess.run(f"sudo ip addr flush dev eth0", shell=True, check=False)
        
        # 2. On modifie le profil NM
        subprocess.run(nm_cmd, shell=True, check=True)
        
        # 3. On tente de lever la connexion
        # On force la porteuse à être ignorée pour ne pas bloquer si pas de câble
        subprocess.run(f"sudo nmcli con mod '{con_name}' ipv4.never-default yes", shell=True, check=True)
        
        res_up = subprocess.run(f"sudo nmcli con up '{con_name}'", shell=True, capture_output=True, text=True)
        if res_up.returncode != 0:
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

def _apply_wlan0_profile(method, ssid, psk, addresses, gateway):
    """Applique la config WiFi sur wlan0 via nmcli."""
    if not ssid:
        return

    logger.info(f"Application profil wlan0 (SSID: {ssid}, Method: {method})")
    con_name = "wlan0-manual"
    
    # Construction de la commande de modification ou ajout
    opts = f"802-11-wireless.ssid {shlex.quote(ssid)} connection.autoconnect yes "
    if psk:
        opts += f"wifi-sec.key-mgmt wpa-psk wifi-sec.psk {shlex.quote(psk)} "
    else:
        opts += "wifi-sec.key-mgmt none "

    if method == "manual":
        opts += f"ipv4.method manual ipv4.addresses {shlex.quote(addresses)} "
        if gateway:
            opts += f"ipv4.gateway {shlex.quote(gateway)} "
    else:
        opts += "ipv4.method auto "

    # Vérifier si la connexion existe
    check_cmd = f"nmcli con show '{con_name}'"
    exists = subprocess.run(check_cmd, shell=True, capture_output=True).returncode == 0

    if exists:
        full_cmd = f"sudo nmcli con mod '{con_name}' {opts}"
    else:
        full_cmd = f"sudo nmcli con add type wifi ifname wlan0 con-name '{con_name}' {opts}"

    try:
        subprocess.run("sudo nmcli radio wifi on", shell=True, check=True)
        subprocess.run(full_cmd, shell=True, check=True)
        subprocess.run(f"sudo nmcli con up '{con_name}'", shell=True, check=True)
        logger.info(f"Profil wlan0 appliqué avec succès.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Erreur lors de l'application du profil wlan0: {e}")

def save_site_network_profiles(site_id, profiles_list):
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
                
            addresses = p.get('addresses')
            if isinstance(addresses, list):
                addresses = ",".join(addresses)
                
            cursor.execute(
                """
                INSERT INTO site_network_profiles 
                (site_id, interface, method, addresses, gateway, ssid, psk)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id, interface) DO UPDATE SET
                    method = excluded.method,
                    addresses = excluded.addresses,
                    gateway = excluded.gateway,
                    ssid = excluded.ssid,
                    psk = excluded.psk,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (site_id, iface, p.get('method', 'auto'), addresses, 
                 p.get('gateway'), p.get('ssid'), p.get('psk'))
            )
        conn.commit()
