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
    """
    gsm = get_gsm_info()
    if not gsm.get("mcc") or not gsm.get("enodeb"):
        logger.debug("Tracker: Pas d'infos GSM complètes.")
        return

    mcc, mnc, enodeb = gsm["mcc"], gsm["mnc"], gsm["enodeb"]
    
    # 1. Tenter une résolution locale d'abord (plus rapide)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.name, s.external_id, s.is_provisional
            FROM sites s
            JOIN site_antennas sa ON s.id = sa.site_id
            JOIN antennas a ON sa.antenna_id = a.id
            WHERE a.mcc = ? AND a.mnc = ? AND a.enodeb = ?
            ORDER BY sa.linked_at DESC
            LIMIT 1
            """,
            (mcc, mnc, enodeb)
        )
        row = cursor.fetchone()
        
        if row:
            site_name = row["name"]
            external_id = row["external_id"]
            is_prov = row["is_provisional"]
            current_site = get_current_site_name()
            
            if site_name != current_site:
                logger.info(f"Changement d'antenne détecté ({enodeb}). Passage automatique sur le chantier : {site_name}")
                if label_current_location(site_name, is_provisional=is_prov, external_id=external_id):
                    # Récupérer l'id local pour appliquer les profils réseau
                    cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
                    site_row = cursor.fetchone()
                    if site_row:
                        apply_site_network_profiles(site_row["id"])
            return

    # 2. Si inconnu localement, interroger le serveur maître (Fleet)
    if fleet.is_registered():
        logger.info(f"Antenne {enodeb} inconnue localement, interrogation du serveur maître...")
        sync_data = fleet.sync_location(gsm)
        if sync_data and sync_data.get("chantier"):
            chantier_distant = sync_data["chantier"]
            dist_name = chantier_distant.get("ref")
            dist_id = str(chantier_distant.get("id"))
            logger.info(f"Le serveur maître reconnaît l'antenne sur le chantier : {dist_name}")
            
            if label_current_location(dist_name, is_provisional=False, external_id=dist_id):
                # Récupérer l'id local créé ou mis à jour
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM sites WHERE external_id = ?", (dist_id,))
                    row = cursor.fetchone()
                    if row:
                        local_site_id = row["id"]
                        # Enregistrer les profils réseau reçus si présents
                        if "net_profiles" in sync_data:
                            from services.network_config import save_site_network_profiles
                            save_site_network_profiles(local_site_id, sync_data["net_profiles"])
                        
                        apply_site_network_profiles(local_site_id)
            return

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
