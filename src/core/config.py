import json
import logging

from core.paths import CONFIG_FILE

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "hostname": "rpinode-01",
    "port": 8082,
    "debug": True,
    "base_url": "",
    "fleet_url": "https://docs.deltathermic.be/reports/api",
    "fleet_secret": "",  # Peut être défini via la variable d'environnement FLEET_SECRET
    "fleet_token": "",    # Jeton obtenu après enregistrement
    "fleet_assigned_hostname": "",  # Nom d'hôte (ex: rpi02) attribué par docs lors de l'enregistrement automatique
    "mobile_session_secret": "",  # Généré automatiquement (services/mobile_auth.py) pour signer les cookies de session mobile
    "logger_retries": 3,
    "modbus_timeout": 1.2,
    "bacnet_timeout": 45,
    # Interface du LAN filaire du chantier : "eth0" (rpi01) ou "br0" quand les deux
    # prises RJ45 sont pontées en switch (boîtiers EDATEC). Les profils réseau du
    # chantier (clé "eth0" en base) s'appliquent à cette interface.
    "lan_interface": "eth0",
    # Profil NetworkManager à utiliser si l'interface n'a pas de connexion active
    # (vide = "<interface>-manual", ex. "eth0-manual" ; pont EDATEC : "lan-pont").
    "lan_connection": "",
    # Le LAN filaire du chantier peut-il fournir la route par défaut (Internet) ?
    # false (rpi01) : jamais, Internet passe par la 4G ou le Wi-Fi.
    # true : boîtier sans carte SIM qui sort par le réseau du chantier (rpi02).
    "lan_default_route": False
}

def load_config() -> dict:
    """Charge la configuration depuis le fichier, ou retourne les valeurs par défaut."""
    if not CONFIG_FILE.exists():
        logger.info(f"Fichier de configuration {CONFIG_FILE} introuvable, création avec les valeurs par défaut.")
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
        
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Fusion avec les clés manquantes
            config = DEFAULT_CONFIG.copy()
            config.update(data)
            return config
    except Exception as e:
        logger.error(f"Erreur de lecture de {CONFIG_FILE}: {e}")
        return DEFAULT_CONFIG.copy()

def lan_interface() -> str:
    """Interface du LAN filaire du chantier (eth0 par défaut, br0 sur un pont)."""
    return load_config().get("lan_interface") or "eth0"


def lan_connection_fallback() -> str:
    """Profil NetworkManager du LAN filaire quand aucune connexion n'est active."""
    cfg = load_config()
    return cfg.get("lan_connection") or f"{cfg.get('lan_interface') or 'eth0'}-manual"


def lan_port_labels() -> dict:
    """Noms affichés des prises du pont LAN, ex. {"eth0": "prise 1000M"} (facultatif)."""
    labels = load_config().get("lan_port_labels")
    return labels if isinstance(labels, dict) else {}


def lan_default_route() -> bool:
    """True si le LAN filaire du chantier peut fournir la route par défaut."""
    return bool(load_config().get("lan_default_route"))


def save_config(config: dict) -> None:
    """Enregistre la configuration dans le fichier de manière atomique."""
    try:
        tmp_file = CONFIG_FILE.with_suffix('.tmp')
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        tmp_file.replace(CONFIG_FILE)
        logger.info("Configuration sauvegardée avec succès.")
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde de {CONFIG_FILE}: {e}")
