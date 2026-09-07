import logging
import time

from core.database import get_db_connection
from services.fleet import fleet
from services.gsm import get_gsm_info
from services.network_config import apply_site_network_profiles
from services.presence import (get_current_site_name,
                               is_current_site_provisional,
                               label_current_location)

logger = logging.getLogger(__name__)


def _is_provisional_site(site_name, is_provisional=False):
    """Retourne True si le chantier doit être considéré comme provisoire."""
    name = site_name or ""
    return bool(is_provisional or name == "Inconnu" or name.startswith("AUTO-") or name.startswith("TEMP-"))


def _find_local_site_for_gsm(gsm):
    """Retourne le chantier local associé à l'antenne GSM courante, si connu."""
    if not gsm.get("mcc") or not gsm.get("enodeb"):
        return None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.id, s.name, s.external_id, s.is_provisional
            FROM sites s
            JOIN site_antennas sa ON s.id = sa.site_id
            JOIN antennas a ON sa.antenna_id = a.id
            WHERE a.mcc = ? AND a.mnc = ? AND a.enodeb = ?
            LIMIT 1
            """,
            (gsm["mcc"], gsm.get("mnc", ""), gsm["enodeb"])
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def _build_site_hint_for_sync(gsm, current_site, current_is_prov, local_site):
    """Construit un hint sûr pour la synchro flotte.

    Lors d'un déplacement, le chantier courant persistant peut encore être celui de la
    veille. On n'envoie donc un hint que s'il correspond déjà à l'antenne courante,
    ou si aucune information GSM exploitable n'est disponible.
    """
    if local_site:
        local_name = local_site.get("name")
        if not _is_provisional_site(local_name, bool(local_site.get("is_provisional"))):
            return local_name
        return None

    if not gsm.get("mcc") or not gsm.get("enodeb"):
        if current_site and not _is_provisional_site(current_site, current_is_prov):
            return current_site

    return None


def check_and_update_site():
    """
    Vérifie l'antenne actuelle et met à jour le chantier si nécessaire.
    Synchronise également les données globales (annotations, templates) avec le serveur.
    """
    gsm = get_gsm_info()
    current_site = get_current_site_name()
    current_is_prov = is_current_site_provisional()
    local_site = _find_local_site_for_gsm(gsm)

    # 1. Synchronisation systématique avec le serveur maître si enregistré
    # Même sans GSM, cela permet de tirer les annotations et de pousser les découvertes IP.
    if fleet.is_registered():
        logger.debug("Interrogation périodique du serveur maître (Fleet)...")
        hint = _build_site_hint_for_sync(gsm, current_site, current_is_prov, local_site)
        sync_data = fleet.sync_location(gsm, site_hint_name=hint)
        
        # Si on a des infos GSM et que le serveur reconnaît un chantier, on traite
        if sync_data and sync_data.get("chantier") and gsm.get("enodeb"):
            chantier_distant = sync_data["chantier"]
            dist_name = chantier_distant.get("ref")
            dist_id = str(chantier_distant.get("id"))
            is_dist_prov = bool(dist_name.startswith("AUTO-") or dist_name.startswith("TEMP-"))

            trusted_local_name = None
            if local_site:
                local_name = local_site.get("name")
                if not _is_provisional_site(local_name, bool(local_site.get("is_provisional"))):
                    trusted_local_name = local_name
            
            # Si le serveur renvoie un nom automatique, on ne conserve un nom local
            # que s'il est déjà rattaché à l'antenne courante. Cela évite de recycler
            # le chantier de la veille après un déplacement du boîtier.
            if is_dist_prov and trusted_local_name:
                logger.info(f"Le serveur a renvoyé un nom automatique ({dist_name}), conservation du nom local fiable ({trusted_local_name}) avec external_id={dist_id}")
                label_current_location(trusted_local_name, is_provisional=False, external_id=dist_id)
            elif dist_name != current_site:
                logger.info(f"Nouveau chantier détecté via le serveur : {dist_name} (ID: {dist_id})")
                if label_current_location(dist_name, is_provisional=is_dist_prov, external_id=dist_id):
                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT id FROM sites WHERE external_id = ?", (dist_id,))
                        row = cursor.fetchone()
                        if row:
                            local_site_id = row["id"]
                            if "net_profiles" in sync_data:
                                from services.network_config import \
                                    save_site_network_profiles
                                save_site_network_profiles(local_site_id, sync_data["net_profiles"], is_dirty=False)
                            apply_site_network_profiles(local_site_id)
            return

    if not gsm.get("mcc") or not gsm.get("enodeb"):
        return

    mcc, mnc, enodeb = gsm["mcc"], gsm["mnc"], gsm["enodeb"]
    
    # 2. Résolution locale
    if local_site:
        local_site_id = local_site["id"]
        local_site_name = local_site["name"]
        is_prov = _is_provisional_site(local_site_name, bool(local_site.get("is_provisional")))

        if local_site_name != current_site:
            logger.info(f"Antenne reconnue localement : {local_site_name}")
            label_current_location(local_site_name, is_provisional=is_prov)
            apply_site_network_profiles(local_site_id)
        return

    # 3. Toujours inconnu : création d'un site temporaire
    temp_name = f"TEMP-{mcc}-{mnc}-{enodeb}"
    current_site = get_current_site_name()
    if temp_name != current_site:
        logger.info(f"Nouvelle antenne détectée ({enodeb}). Création du chantier temporaire : {temp_name}")
        if label_current_location(temp_name, is_provisional=True):
            # Reset network to DHCP for temporary sites
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM sites WHERE name = ?", (temp_name,))
                row = cursor.fetchone()
                if row:
                    apply_site_network_profiles(row["id"])

def start_tracker(interval=30):
    """
    Boucle de surveillance en arrière-plan.
    """
    logger.info(f"Démarrage du tracker de localisation (intervalle: {interval}s)")
    while True:
        try:
            check_and_update_site()
        except Exception as e:
            logger.exception(f"Erreur dans le tracker de localisation : {e}")
        time.sleep(interval)

if __name__ == "__main__":
    # Test manuel
    logging.basicConfig(level=logging.INFO)
    check_and_update_site()
