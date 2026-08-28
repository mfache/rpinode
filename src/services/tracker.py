import time
import logging
import socket
from services.gsm import get_gsm_info
from services.presence import label_current_location, get_current_site_name
from services.network_config import apply_site_network_profiles
from services.fleet import fleet
from core.database import get_db_connection

logger = logging.getLogger(__name__)

def check_and_update_site():
    """
    Vérifie l'antenne actuelle et met à jour le chantier si nécessaire.
    Synchronise également les données globales (annotations, templates) avec le serveur.
    """
    gsm = get_gsm_info()
    
    # 1. Synchronisation systématique avec le serveur maître si enregistré
    # Même sans GSM, cela permet de tirer les annotations et de pousser les découvertes IP.
    if fleet.is_registered():
        logger.debug("Interrogation périodique du serveur maître (Fleet)...")
        sync_data = fleet.sync_location(gsm)
        
        # Si on a des infos GSM et que le serveur reconnaît un chantier, on traite
        if sync_data and sync_data.get("chantier") and gsm.get("enodeb"):
            chantier_distant = sync_data["chantier"]
            dist_name = chantier_distant.get("ref")
            dist_id = str(chantier_distant.get("id"))
            
            if label_current_location(dist_name, is_provisional=False, external_id=dist_id):
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM sites WHERE external_id = ?", (dist_id,))
                    row = cursor.fetchone()
                    if row:
                        local_site_id = row["id"]
                        if "net_profiles" in sync_data:
                            from services.network_config import save_site_network_profiles
                            save_site_network_profiles(local_site_id, sync_data["net_profiles"])
                        apply_site_network_profiles(local_site_id)
            return

    if not gsm.get("mcc") or not gsm.get("enodeb"):
        return

    mcc, mnc, enodeb = gsm["mcc"], gsm["mnc"], gsm["enodeb"]
    
    # 2. Résolution locale

    # 3. Toujours inconnu : création d'un site temporaire
    temp_name = f"TEMP-{mcc}-{mnc}-{enodeb}"
    current_site = get_current_site_name()
    if temp_name != current_site:
        logger.info(f"Nouvelle antenne détectée ({enodeb}). Création du chantier temporaire : {temp_name}")
        label_current_location(temp_name, is_provisional=True)

def start_tracker(interval=30):
    """
    Boucle de surveillance en arrière-plan.
    """
    logger.info(f"Démarrage du tracker de localisation (intervalle: {interval}s)")
    while True:
        try:
            check_and_update_site()
        except Exception as e:
            logger.error(f"Erreur dans le tracker de localisation : {e}")
        time.sleep(interval)

if __name__ == "__main__":
    # Test manuel
    logging.basicConfig(level=logging.INFO)
    check_and_update_site()
