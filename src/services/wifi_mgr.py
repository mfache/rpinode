import logging
import shlex
import socket
import subprocess
import time

from core.database import get_db_connection
from core.sys import ping_check
from services.network import get_interface_status

logger = logging.getLogger(__name__)

# Constantes
RESCUE_SSID = "RPIRESCUE"
RESCUE_CON_NAME = "rpirescue"  # Correspond à ce qui existe déjà dans nmcli
AP_CON_NAME = "rpinode-ap"
WLAN_IFACE = "wlan0"

def get_ap_config():
    """Retourne l'identifiant et le mot de passe du point d'accès."""
    hostname = socket.gethostname()
    return {
        "ssid": f"RPINODE-{hostname.upper()}",
        "password": "deltathermic"
    }

def run_wifi_manager():
    """
    Boucle principale de gestion du WiFi.
    Suit les règles de README_wifi.md
    """
    logger.info("Démarrage du gestionnaire WiFi robuste.")
    
    while True:
        try:
            manage_wifi(force=False)
        except Exception as e:
            logger.error(f"Erreur dans manage_wifi: {e}")
        time.sleep(60) # Vérification toutes les minutes

def manage_wifi(force=False):
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
            _ensure_client_mode(RESCUE_CON_NAME, force=force)
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
        _ensure_client_mode(
            con_name,
            wifi_config['ssid'],
            wifi_config['psk'],
            method=wifi_config['method'],
            addresses=wifi_config['addresses'],
            gateway=wifi_config['gateway'],
            dhcp_range=wifi_config.get('dhcp_range'),
            force=force
        )
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
            "SELECT ssid, psk, method, addresses, gateway, dhcp_range FROM site_network_profiles WHERE site_id = ? AND interface = 'wlan0'",
            (site_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def get_visible_ssids():
    """Retourne une liste des SSIDs visibles sans doublons."""
    try:
        # On force un rescan léger
        subprocess.run(["sudo", "nmcli", "device", "wifi", "rescan"], timeout=5, capture_output=True)
        
        # On récupère la liste (SSID, BSSID, SIGNAL, BARS, SECURITY)
        # Mais on ne garde que le SSID pour la liste de choix
        cmd = ["nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi", "list"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        
        ssids = {}
        for line in res.stdout.splitlines():
            parts = line.split(':')
            if len(parts) >= 2:
                ssid = parts[0].strip()
                try:
                    signal = int(parts[1])
                except:
                    signal = 0
                
                if ssid and (ssid not in ssids or signal > ssids[ssid]):
                    ssids[ssid] = signal
        
        # Tri par signal décroissant
        sorted_ssids = sorted(ssids.keys(), key=lambda x: ssids[x], reverse=True)
        return sorted_ssids
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des SSIDs: {e}")
        return []

def _is_ssid_visible(ssid):
    try:
        subprocess.run(["sudo", "nmcli", "device", "wifi", "rescan"], timeout=5, capture_output=True)
        cmd = ["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return ssid in [line.strip() for line in res.stdout.splitlines()]
    except:
        return False

def _ensure_client_mode(con_name, ssid=None, psk=None, method="auto", addresses=None, gateway=None, dhcp_range=None, force=False):
    """S'assure que wlan0 est connecté à l'AP spécifié."""
    # 1. Vérifier si déjà connecté à cette connexion
    status = subprocess.run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev", "status"], capture_output=True, text=True)
    if f"wlan0:{con_name}" in status.stdout and not force:
        return # Déjà OK et pas de forçage

    logger.info(f"Configuration/Activation de la connexion client: {con_name} (SSID: {ssid})")
    
    # 2. Créer/Mettre à jour la connexion si c'est un site (on ne touche pas au rescue existant)
    if ssid:
        opts = f"802-11-wireless.ssid {shlex.quote(ssid)} connection.autoconnect yes connection.autoconnect-priority 50 "
        if psk:
            opts += f"wifi-sec.key-mgmt wpa-psk wifi-sec.psk {shlex.quote(psk)} "
        else:
            opts += "wifi-sec.key-mgmt none "
        
        # Gestion de l'IP
        if method == "manual" and addresses:
            import json
            try:
                addrs_list = json.loads(addresses) if addresses else []
                if not isinstance(addrs_list, list):
                    addrs_list = [addresses]
            except Exception:
                addrs_list = [a.strip() for a in (addresses or "").split(",") if a.strip()]

            addrs_nm = ",".join(addrs_list)
            opts += f"ipv4.method manual ipv4.addresses {shlex.quote(addrs_nm)} "
            if gateway:
                opts += f"ipv4.gateway {shlex.quote(gateway)} "
            else:
                opts += "ipv4.gateway '' "
        elif method == "shared":
            import json
            try:
                addrs_list = json.loads(addresses) if addresses else []
                if not isinstance(addrs_list, list):
                    addrs_list = [addresses]
            except Exception:
                addrs_list = [a.strip() for a in (addresses or "").split(",") if a.strip()]

            addrs_nm = ",".join(addrs_list)
            opts += f"ipv4.method shared ipv4.addresses {shlex.quote(addrs_nm)} "
            if gateway:
                opts += f"ipv4.gateway {shlex.quote(gateway)} "
            else:
                opts += "ipv4.gateway '' "
            if dhcp_range:
                opts += f"ipv4.dhcp-range {shlex.quote(dhcp_range)} "
            else:
                opts += "ipv4.dhcp-range '' "
        else:
            opts += "ipv4.method auto "
            
        # On évite que le WiFi devienne la route par défaut si on a la 4G (wwan0)
        # Sauf si on veut explicitement que le WiFi chantier soit prioritaire sur la 4G ?
        # Dans le doute, on garde ipv4.never-default yes pour ne pas casser l'accès cloud via 4G
        opts += "ipv4.never-default yes "

        check = subprocess.run(["nmcli", "con", "show", con_name], capture_output=True)
        if check.returncode == 0:
            subprocess.run(f"sudo nmcli con mod '{con_name}' {opts}", shell=True)
        else:
            subprocess.run(f"sudo nmcli con add type wifi ifname wlan0 con-name '{con_name}' {opts}", shell=True)

    # 3. Basculer
    subprocess.run(["sudo", "nmcli", "con", "up", con_name], timeout=30)

def _ensure_ap_mode():
    """S'assure que wlan0 est en mode Access Point."""
    status = subprocess.run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev", "status"], capture_output=True, text=True, timeout=5)
    if f"wlan0:{AP_CON_NAME}" in status.stdout:
        return # Déjà OK

    logger.info("Activation du mode Access Point.")
    hostname = socket.gethostname()
    ssid = f"RPINODE-{hostname.upper()}"
    password = "deltathermic"
    
    # Construction de la config
    opts = (
        f"mode ap ssid {ssid} "
        "ipv4.method shared "
        "802-11-wireless-security.key-mgmt wpa-psk "
        f"802-11-wireless-security.psk {password} "
        "connection.autoconnect no "
        "connection.autoconnect-priority 10 "
    )
    
    # 1. Libérer wlan0
    subprocess.run(["sudo", "nmcli", "device", "disconnect", "wlan0"], capture_output=True, timeout=10)
    
    # 2. Configurer NetworkManager pour l'AP
    check = subprocess.run(["nmcli", "con", "show", AP_CON_NAME], capture_output=True, timeout=5)
    if check.returncode == 0:
        subprocess.run(f"sudo nmcli con mod '{AP_CON_NAME}' {opts}", shell=True, timeout=10)
    else:
        subprocess.run(f"sudo nmcli con add type wifi ifname wlan0 con-name '{AP_CON_NAME}' {opts}", shell=True, timeout=10)
    
    # 3. Lancer l'AP
    subprocess.run(["sudo", "nmcli", "con", "up", AP_CON_NAME], timeout=30, capture_output=True)

    # 4. Activer le routage et le NAT de manière robuste
    _setup_routing_and_nat()

def _setup_routing_and_nat():
    """Configure le routage IP au niveau du noyau.
    Le NAT (masquerade) est géré de manière robuste par /etc/nftables.conf.
    """
    try:
        # Activation du routage IPv4 dans le noyau (persistant via /etc/sysctl.d/99-forwarding.conf normalement)
        # On le force ici par sécurité à chaque bascule de mode.
        subprocess.run(["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"], capture_output=True, timeout=5)

    except Exception as e:
        logger.error(f"Erreur lors de l'activation du routage noyau : {e}")
