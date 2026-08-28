import subprocess
import logging
import time
import socket
import shlex
from services.network import get_interface_status
from core.sys import ping_check
from core.database import get_db_connection

logger = logging.getLogger(__name__)

# Constantes
RESCUE_SSID = "RPIRESCUE"
RESCUE_CON_NAME = "rpirescue"  # Correspond à ce qui existe déjà dans nmcli
AP_CON_NAME = "rpinode-ap"
WLAN_IFACE = "wlan0"

def run_wifi_manager():
    """
    Boucle principale de gestion du WiFi.
    Suit les règles de README_wifi.md
    """
    logger.info("Démarrage du gestionnaire WiFi robuste.")
    
    while True:
        try:
            manage_wifi()
        except Exception as e:
            logger.error(f"Erreur dans manage_wifi: {e}")
        time.sleep(60) # Vérification toutes les minutes

def manage_wifi():
    """Applique la logique de décision WiFi."""
    # 1. État de la 4G
    wwan_alive = ping_check(interface="wwan0")
    
    # 2. Récupérer le chantier actuel
    site_info = _get_current_site_info()
    site_id = site_info['id'] if site_info else None
    
    # 3. Récupérer la config WiFi du chantier
    wifi_config = _get_site_wifi_config(site_id) if site_id else None
    
    # LOGIQUE DE DÉCISION
    
    # RÈGLE A: Si wwan0 est perdu, on tente le Rescue WiFi
    if not wwan_alive:
        logger.warning("wwan0 (4G) hors ligne. Tentative de secours...")
        if _is_ssid_visible(RESCUE_SSID):
            logger.info(f"SSID {RESCUE_SSID} détecté. Activation du mode secours.")
            _ensure_client_mode(RESCUE_CON_NAME)
            return
        else:
            logger.info(f"Secours {RESCUE_SSID} non détecté. Passage en mode AP pour accès local.")
            _ensure_ap_mode()
            return

    # RÈGLE B: wwan0 est OK. On regarde si on a une config chantier pour wlan0
    if wifi_config and wifi_config['ssid']:
        logger.info(f"wwan0 OK. Application de la config WiFi chantier: {wifi_config['ssid']}")
        # On utilise une connexion nommée d'après le chantier pour nmcli
        con_name = f"site-{site_id}-wifi"
        _ensure_client_mode(con_name, wifi_config['ssid'], wifi_config['psk'])
    else:
        # RÈGLE C: Pas de config chantier, on se comporte en AP
        logger.info("wwan0 OK mais pas de config WiFi chantier. Passage en mode AP.")
        _ensure_ap_mode()

def _get_current_site_info():
    hostname = socket.gethostname()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.id, s.name 
            FROM sites s
            JOIN node_presence p ON s.id = p.site_id
            JOIN nodes n ON p.node_id = n.id
            WHERE n.hostname = ? AND p.is_current = 1
            LIMIT 1
            """,
            (hostname,)
        )
        return cursor.fetchone()

def _get_site_wifi_config(site_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ssid, psk, method, addresses, gateway FROM site_network_profiles WHERE site_id = ? AND interface = 'wlan0'",
            (site_id,)
        )
        return cursor.fetchone()

def _is_ssid_visible(ssid):
    try:
        subprocess.run(["sudo", "nmcli", "device", "wifi", "rescan"], timeout=10, capture_output=True)
        cmd = ["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return ssid in [line.strip() for line in res.stdout.splitlines()]
    except:
        return False

def _ensure_client_mode(con_name, ssid=None, psk=None):
    """S'assure que wlan0 est connecté à l'AP spécifié."""
    # 1. Vérifier si déjà connecté à cette connexion
    status = subprocess.run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev", "status"], capture_output=True, text=True)
    if f"wlan0:{con_name}" in status.stdout:
        return # Déjà OK

    logger.info(f"Activation de la connexion client: {con_name}")
    
    # 2. Créer/Mettre à jour la connexion si c'est un site (on ne touche pas au rescue existant)
    if ssid:
        opts = f"802-11-wireless.ssid {shlex.quote(ssid)} connection.autoconnect yes connection.priority 50 "
        if psk:
            opts += f"wifi-sec.key-mgmt wpa-psk wifi-sec.psk {shlex.quote(psk)} "
        else:
            opts += "wifi-sec.key-mgmt none "
        
        # On force l'IP auto pour wlan0 client (pour ne pas interférer avec les routes 4G)
        opts += "ipv4.method auto ipv4.never-default yes "

        check = subprocess.run(["nmcli", "con", "show", con_name], capture_output=True)
        if check.returncode == 0:
            subprocess.run(f"sudo nmcli con mod '{con_name}' {opts}", shell=True)
        else:
            subprocess.run(f"sudo nmcli con add type wifi ifname wlan0 con-name '{con_name}' {opts}", shell=True)

    # 3. Basculer
    subprocess.run(["sudo", "nmcli", "con", "up", con_name], timeout=30)

def _ensure_ap_mode():
    """S'assure que wlan0 est en mode Access Point."""
    status = subprocess.run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev", "status"], capture_output=True, text=True)
    if f"wlan0:{AP_CON_NAME}" in status.stdout:
        return # Déjà OK

    logger.info("Activation du mode Access Point.")
    hostname = socket.gethostname()
    ssid = f"RPINODE-{hostname.upper()}"
    
    # Création/MàJ de la connexion AP
    opts = (
        f"mode ap ssid {ssid} "
        "ipv4.method shared " # Active le serveur DHCP et le partage (NAT non nécessaire ici mais active DHCP)
        "connection.autoconnect no "
        "connection.priority 10 "
    )
    
    check = subprocess.run(["nmcli", "con", "show", AP_CON_NAME], capture_output=True)
    if check.returncode == 0:
        subprocess.run(f"sudo nmcli con mod '{AP_CON_NAME}' {opts}", shell=True)
    else:
        subprocess.run(f"sudo nmcli con add type wifi ifname wlan0 con-name '{AP_CON_NAME}' {opts}", shell=True)
    
    subprocess.run(["sudo", "nmcli", "con", "up", AP_CON_NAME], timeout=30)
