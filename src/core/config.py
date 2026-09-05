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
    "logger_retries": 3,
    "modbus_timeout": 1.2,
    "bacnet_timeout": 45
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
