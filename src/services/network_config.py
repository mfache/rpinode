import subprocess
import logging
import shlex
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
        logger.info(f"Aucun profil réseau spécifique pour le site {site_id}. Utilisation des réglages par défaut (DHCP).")
        # Par défaut, on s'assure d'être en DHCP sur eth0 si rien n'est spécifié ?
        # Pour l'instant, on ne fait rien pour ne pas casser une config manuelle existante.
        return

    for p in profiles:
        iface = p["interface"]
        method = p["method"]
        
        if iface == "eth0":
            _apply_eth0_profile(method, p["addresses"], p["gateway"])
        elif iface == "wlan0":
            _apply_wlan0_profile(method, p["ssid"], p["psk"], p["addresses"], p["gateway"])

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
        nm_cmd = f"sudo nmcli con mod '{con_name}' ipv4.method manual ipv4.addresses '{addresses}'"
        if gateway:
            nm_cmd += f" ipv4.gateway '{gateway}'"
    
    try:
        subprocess.run(nm_cmd, shell=True, check=True)
        subprocess.run(f"sudo nmcli con up '{con_name}'", shell=True, check=True)
        logger.info(f"Profil eth0 appliqué avec succès.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Erreur lors de l'application du profil eth0: {e}")

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
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for p in profiles_list:
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
                (site_id, p['interface'], p.get('method', 'auto'), p.get('addresses'), 
                 p.get('gateway'), p.get('ssid'), p.get('psk'))
            )
        conn.commit()
