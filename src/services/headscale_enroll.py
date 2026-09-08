"""
Rattachement automatique du boîtier au réseau Headscale auto-hébergé sur
`docs.deltathermic.be`, sans intervention manuelle (pas de génération de clé
en SSH).

Principe (voir docs/integrations/HEADSCALE_AUTO_ENROLL.md pour le détail
complet, y compris la partie côté serveur `docs`) :

1. Le boîtier a besoin d'une identité de flotte (`fleet_token`). S'il n'en a
   pas encore (première mise en service), il s'enregistre automatiquement
   auprès de docs via son numéro de série CPU (identifiant matériel stable,
   indépendant des interfaces réseau) ; le serveur lui attribue alors un nom
   d'hôte (`rpiNN`).
2. Une fois cette identité connue, le boîtier demande à docs une clé de
   pré-authentification Headscale à usage unique, lance `tailscale up`, puis
   confirme le succès pour faire approuver les routes locales annoncées.

Idempotent : si l'appareil est déjà connecté au bon serveur Headscale, aucune
action n'est effectuée.
"""

import ipaddress
import json
import logging
import socket
import subprocess

from core.config import load_config, save_config

logger = logging.getLogger(__name__)

LOGIN_SERVER = "https://docs.deltathermic.be"
CPUINFO_PATH = "/proc/cpuinfo"


def get_cpu_serial():
    """Retourne le numéro de série CPU du Raspberry Pi, ou None si
    indisponible (ex: environnement de développement hors Raspberry Pi)."""
    try:
        with open(CPUINFO_PATH, "r") as f:
            for line in f:
                if line.lower().startswith("serial"):
                    serial = line.split(":", 1)[1].strip()
                    if serial and serial.strip("0"):
                        return serial
    except OSError:
        pass
    return None


def _tailscale_json(*args):
    try:
        result = subprocess.run(
            ["sudo", "tailscale"] + list(args),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


def is_headscale_active():
    """Vrai si l'appareil est déjà connecté au bon serveur Headscale et en
    ligne (BackendState "Running")."""
    prefs = _tailscale_json("debug", "prefs")
    status = _tailscale_json("status", "--json")
    return bool(
        prefs and status
        and prefs.get("ControlURL") == LOGIN_SERVER
        and status.get("BackendState") == "Running"
    )


def ensure_fleet_identity():
    """
    S'assure que le boîtier possède une identité de flotte (fleet_token) et
    un nom d'hôte assigné. Si aucun jeton n'existe encore, s'enregistre
    automatiquement via le numéro de série CPU.

    Retourne le hostname assigné/existant, ou None en cas d'échec.
    """
    from services.fleet import fleet

    config = load_config()
    assigned = config.get("fleet_assigned_hostname")
    if assigned and fleet.is_registered():
        return assigned

    if fleet.is_registered():
        # Cas d'un boîtier déjà enregistré via l'ancien flux manuel
        # (/register avec hostname choisi, ex: rpi01) : on reprend le nom de
        # la machine tel quel, sans le modifier.
        assigned = socket.gethostname()
        config["fleet_assigned_hostname"] = assigned
        save_config(config)
        return assigned

    cpu_serial = get_cpu_serial()
    if not cpu_serial:
        logger.warning(
            "Numéro de série CPU introuvable : enregistrement automatique impossible."
        )
        return None

    hostname = fleet.register_auto(cpu_serial)
    if hostname:
        config = load_config()
        config["fleet_assigned_hostname"] = hostname
        save_config(config)
    return hostname


def _local_routes():
    """Sous-réseaux locaux (eth0/wlan0) à annoncer sur Headscale, au même
    format que services.network_config.publish_tailscale_routes()."""
    from services.network import get_interface_status

    routes = []
    for iface in ["eth0", "wlan0"]:
        status = get_interface_status(iface)
        if status["active"] and "/" in status["ip"]:
            try:
                net = ipaddress.IPv4Interface(status["ip"]).network
                routes.append(str(net))
            except Exception:
                continue
    return sorted(set(routes))


def ensure_headscale_enrolled():
    """
    S'assure que le boîtier est rattaché au réseau Headscale de docs. Ne fait
    rien si c'est déjà le cas. Sinon, obtient une clé de pré-authentification
    auprès de docs et lance `tailscale up`.

    Retourne True si l'appareil est (ou vient d'être rendu) actif sur
    Headscale, False sinon.
    """
    if is_headscale_active():
        return True

    hostname = ensure_fleet_identity()
    if not hostname:
        logger.warning(
            "Identité de flotte indisponible : impossible de rejoindre Headscale."
        )
        return False

    from services.fleet import fleet

    routes = _local_routes()
    enroll = fleet.headscale_enroll(routes)
    if not enroll:
        logger.warning("Échec de l'obtention d'une clé Headscale auprès de docs.")
        return False

    # Se détache proprement d'un éventuel réseau Tailscale précédent
    # (ex: SaaS) avant de rejoindre Headscale.
    subprocess.run(["sudo", "tailscale", "down"], capture_output=True, timeout=15)
    subprocess.run(["sudo", "tailscale", "logout"], capture_output=True, timeout=15)

    cmd = [
        "sudo", "tailscale", "up",
        f"--login-server={enroll['login_server']}",
        f"--authkey={enroll['authkey']}",
        f"--hostname={enroll['hostname']}",
    ]
    if routes:
        cmd.append(f"--advertise-routes={','.join(routes)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        logger.error(f"Erreur lors du 'tailscale up' vers Headscale : {e}")
        return False

    if result.returncode != 0:
        logger.error(f"Échec de 'tailscale up' vers Headscale : {result.stderr.strip()}")
        return False

    logger.info(f"Rattaché au réseau Headscale sous le nom {enroll['hostname']}.")

    fleet.headscale_sync_routes(routes)

    return True
