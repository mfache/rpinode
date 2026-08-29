import logging
from logging.handlers import RotatingFileHandler
import threading

from core.database import init_db
from core.paths import DATA_DIR, LOG_FILE
from services.logger import start_data_logger
from services.reporter import reporter
from services.tracker import start_tracker
from services.wifi_mgr import run_wifi_manager
from web.server import start_server

def main():
    # Handler vers le serveur maître (Filtré sur WARNING par défaut pour ne pas spammer, ou INFO selon le besoin)
    from services.remote_log import FleetLogHandler
    fleet_handler = FleetLogHandler(batch_size=20, flush_interval=30)
    fleet_handler.setLevel(logging.INFO)
    # On simplifie le format pour l'API (le timestamp et le module sont déjà séparés)
    fleet_handler.setFormatter(logging.Formatter("%(message)s"))

    # Configuration du logging (Fichier rotatif + Console + Serveur distant)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            RotatingFileHandler(LOG_FILE, maxBytes=2*1024*1024, backupCount=2, encoding="utf-8"),
            logging.StreamHandler(),
            fleet_handler
        ]
    )

    logging.info(f"Démarrage de rpinode. Dossier de données : {DATA_DIR}")
    logging.info(f"Logs enregistrés dans : {LOG_FILE}")
    
    # Initialisation de la DB
    init_db()
    
    # Publication initiale des routes sur Tailscale
    try:
        from services.network_config import (apply_site_network_profiles,
                                             publish_tailscale_routes)
        from services.presence import get_current_site_id
        
        logging.info("--- DEMARRAGE RPINODE ---")
        
        # 1. On applique le profil réseau du chantier actuel
        current_site_id = get_current_site_id()
        if current_site_id:
            logging.info(f"Application du profil réseau pour le chantier actuel (ID: {current_site_id})")
            apply_site_network_profiles(current_site_id)
            
        # 2. On publie les routes
        publish_tailscale_routes()
    except Exception as e:
        logging.error(f"Erreur initialisation réseau : {e}")
    
    # Démarrage du tracker de localisation en arrière-plan
    tracker_thread = threading.Thread(target=start_tracker, kwargs={'interval': 60}, daemon=True)
    tracker_thread.start()
    
    # Démarrage du thread de gestion WiFi robuste
    wifi_thread = threading.Thread(target=run_wifi_manager, daemon=True)
    wifi_thread.start()
    
    # Démarrage du service d'enregistrement des données (Trends)
    logger_thread = threading.Thread(target=start_data_logger, kwargs={'interval': 60}, daemon=True)
    logger_thread.start()
    
    # Démarrage du reporter MQTT (Bridge données internes -> MQTT)
    reporter.start()
    
    start_server()

if __name__ == "__main__":
    main()
