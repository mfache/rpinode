import atexit
import logging
import os
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler

from core.database import init_db
from core.paths import DATA_DIR, LOG_FILE
from services.logger import start_data_logger
from services.reporter import reporter
from services.tracker import check_and_update_site, start_tracker
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

    # Publication initiale des routes sur Headscale
    try:
        from services.network_config import (apply_site_network_profiles,
                                             publish_tailscale_routes)
        from services.presence import get_current_site_id, clear_current_location

        logging.info("--- DEMARRAGE RPINODE ---")

        # 0. Effacement systématique du chantier en cours (fichier /tmp volatile)
        # Assure qu'après un redémarrage physique, la machine ne devine jamais sa
        # position depuis la base de données. Elle doit attendre une position 4G 
        # stable ou une confirmation de l'utilisateur pour reprendre le flux de données.
        clear_current_location()

        # 1. Résoudre immédiatement la localisation réelle avant de réappliquer un
        # profil réseau persistant. Sans cela, un boîtier déplacé au repos peut
        # redémarrer en croyant encore être sur le chantier précédent.
        check_and_update_site()

        # 2. On applique le profil réseau du chantier désormais courant
        current_site_id = get_current_site_id()
        if current_site_id:
            logging.info(f"Application du profil réseau pour le chantier actuel (ID: {current_site_id})")
            apply_site_network_profiles(current_site_id)

        # 3. On publie les routes
        publish_tailscale_routes()
    except Exception as e:
        logging.error(f"Erreur initialisation réseau : {e}")

    # Rattachement automatique a la flotte et au reseau Headscale (identite +
    # cle de pre-authentification obtenues aupres de docs, sans intervention
    # manuelle). Sans effet si deja enregistre/rattache.
    def run_headscale_enroll():
        import time
        from services.headscale_enroll import ensure_headscale_enrolled
        headscale_logger = logging.getLogger("HeadscaleEnroll")
        for attempt in range(10):
            try:
                if ensure_headscale_enrolled():
                    return
            except Exception as e:
                headscale_logger.error(f"Erreur lors du rattachement Headscale : {e}")
            time.sleep(30)

    headscale_thread = threading.Thread(target=run_headscale_enroll, daemon=True)
    headscale_thread.start()

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

    # Planificateur du dictionnaire de points BACnet (construction à la demande + synchro flotte)
    from services.bacnet_catalog import run_scheduler_loop as run_bacnet_catalog_scheduler
    bacnet_catalog_thread = threading.Thread(target=run_bacnet_catalog_scheduler, daemon=True)
    bacnet_catalog_thread.start()

    # Démarrage du démon BACnet unifié (MQTT), utilisé par les outils BACnet (discover, who-has, ...)
    def run_bacnet_daemon():
        import time
        bacnet_logger = logging.getLogger("BACnetDaemonRunner")
        daemon_path = os.path.join(os.path.dirname(__file__), "services", "bacnet_daemon.py")
        bacnet_python = "/opt/boitier-bacnet/venv/bin/python"

        if not os.path.exists(bacnet_python):
            bacnet_python = sys.executable

        # Certains chemins de redémarrage (ex: /api/restart qui utilise os.execve) ne
        # déclenchent jamais les hooks atexit, ce qui peut laisser un ancien démon orphelin
        # tourner en parallèle et se battre avec le nouveau sur le port UDP 47808. On s'assure
        # donc de tuer toute instance résiduelle avant d'en lancer une nouvelle.
        try:
            subprocess.run(["pkill", "-9", "-f", "bacnet_daemon.py"], check=False)
            time.sleep(0.2)
        except Exception as e:
            bacnet_logger.warning(f"Impossible de nettoyer les anciens démons BACnet: {e}")

        bacnet_logger.info(f"Démarrage de bacnet_daemon via {bacnet_python}")
        try:
            # Lancer le démon comme un sous-processus continu
            proc = subprocess.Popen([bacnet_python, daemon_path])

            def cleanup():
                bacnet_logger.info("Arrêt du bacnet_daemon enfant...")
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

            atexit.register(cleanup)
            proc.wait()
        except Exception as e:
            bacnet_logger.error(f"Impossible de démarrer le démon BACnet: {e}")

    bacnet_thread = threading.Thread(target=run_bacnet_daemon, daemon=True)
    bacnet_thread.start()

    start_server()

if __name__ == "__main__":
    main()
