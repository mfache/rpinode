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
import os
import socket
import subprocess
import tempfile

from core.config import load_config, save_config

logger = logging.getLogger(__name__)

LOGIN_SERVER = "https://docs.deltathermic.be"
CPUINFO_PATH = "/proc/cpuinfo"

# Compte technique utilise par `docs` pour administrer ce boitier via
# Tailscale SSH (voir docs/operations/HEADSCALE_SSH_ACL.md). Verrouille (pas
# de mot de passe utilisable, pas de cle SSH classique) : seule l'identite
# reseau Headscale, autorisee par l'ACL cote serveur, y donne acces.
DOCSADMIN_USER = "docsadmin"
DOCSADMIN_SUDOERS_PATH = "/etc/sudoers.d/docsadmin"
DOCSADMIN_SUDOERS_CONTENT = """\
# Sudo complet pour le compte technique docsadmin (acces via Headscale SSH
# depuis docs.deltathermic.be uniquement, cf. acl.hujson cote Headscale).
# Genere automatiquement par services/headscale_enroll.py, ne pas editer a
# la main (sera ecrase au prochain demarrage si le contenu differe).
docsadmin ALL=(ALL) NOPASSWD: ALL
"""


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


def _docsadmin_account_exists():
    try:
        result = subprocess.run(["id", DOCSADMIN_USER], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _ensure_docsadmin_account():
    """Cree le compte technique `docsadmin` s'il n'existe pas encore, sans mot
    de passe utilisable ni cle SSH classique (seule l'identite Headscale, via
    l'ACL cote serveur, permet de s'y connecter). Idempotent."""
    if _docsadmin_account_exists():
        return True
    try:
        result = subprocess.run(
            ["sudo", "useradd", "-m", "-s", "/bin/bash", DOCSADMIN_USER],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logger.error(f"Échec de la création du compte {DOCSADMIN_USER} : {result.stderr.strip()}")
            return False
        subprocess.run(["sudo", "passwd", "-l", DOCSADMIN_USER], capture_output=True, timeout=10)
        logger.info(f"Compte technique {DOCSADMIN_USER} créé (verrouillé, sans clé SSH).")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de la création du compte {DOCSADMIN_USER} : {e}")
        return False


def _ensure_docsadmin_sudoers():
    """Depose /etc/sudoers.d/docsadmin avec le sudo accorde a ce compte,
    valide par `visudo -c` avant toute installation. Sans effet si le
    contenu en place est deja a jour. Idempotent."""
    try:
        try:
            with open(DOCSADMIN_SUDOERS_PATH, "r") as f:
                if f.read() == DOCSADMIN_SUDOERS_CONTENT:
                    return True
        except OSError:
            pass

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".sudoers") as tmp:
            tmp.write(DOCSADMIN_SUDOERS_CONTENT)
            tmp_path = tmp.name

        try:
            check = subprocess.run(
                ["sudo", "visudo", "-c", "-f", tmp_path],
                capture_output=True, text=True, timeout=10,
            )
            if check.returncode != 0:
                logger.error(f"Policy sudoers docsadmin invalide, non installée : {check.stderr.strip()}")
                return False

            install = subprocess.run(
                ["sudo", "install", "-o", "root", "-g", "root", "-m", "440",
                 tmp_path, DOCSADMIN_SUDOERS_PATH],
                capture_output=True, text=True, timeout=10,
            )
            if install.returncode != 0:
                logger.error(f"Échec de l'installation du sudoers docsadmin : {install.stderr.strip()}")
                return False
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        logger.info("Sudo pour docsadmin déployé/mis à jour.")
        return True
    except Exception as e:
        logger.error(f"Erreur lors du déploiement du sudoers docsadmin : {e}")
        return False


def _ensure_tailscale_ssh_enabled():
    """Active le serveur SSH integre a Tailscale si ce n'est pas deja fait.
    Necessite qu'une regle ACL cote Headscale couvre deja un acces admin
    humain vers ce noeud (voir docs/operations/HEADSCALE_SSH_ACL.md) : sans
    quoi cette activation coupe la session SSH en cours, comme observe lors
    de l'incident du 9 septembre 2026."""
    prefs = _tailscale_json("debug", "prefs")
    if prefs and prefs.get("RunSSH"):
        return True
    try:
        result = subprocess.run(
            ["sudo", "tailscale", "set", "--ssh", "--accept-risk=lose-ssh"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logger.error(f"Échec de l'activation de Tailscale SSH : {result.stderr.strip()}")
            return False
        logger.info("Tailscale SSH activé.")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de l'activation de Tailscale SSH : {e}")
        return False


def ensure_docs_admin_access():
    """S'assure que `docs` peut administrer ce boitier via Tailscale SSH :
    compte `docsadmin` (verrouille, sudo complet NOPASSWD) + serveur SSH
    Tailscale actif. Chaque etape est independante et best-effort (une
    etape en echec n'empeche pas les autres) : le taggage `tag:fleet`
    correspondant est gere cote serveur (voir POST /headscale/routes dans
    docs/integrations/HEADSCALE_AUTO_ENROLL.md)."""
    ok = _ensure_docsadmin_account()
    ok = _ensure_docsadmin_sudoers() and ok
    ok = _ensure_tailscale_ssh_enabled() and ok
    return ok


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
        ensure_docs_admin_access()
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

    # Le tag `tag:fleet` (pose cote serveur par /headscale/routes ci-dessus)
    # doit deja etre en place avant d'activer Tailscale SSH ici, pour que
    # l'ACL admin (group:fleet-admins -> tag:fleet) s'applique des l'activation.
    ensure_docs_admin_access()

    return True
