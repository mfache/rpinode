import json
import logging
import socket

from core.database import get_db_connection
from core.paths import CURRENT_SITE_FILE
from services.gsm import get_gsm_info

logger = logging.getLogger(__name__)

def clear_current_location():
    """
    Supprime la localisation courante du boîtier.
    Utilisé au démarrage pour forcer une réévaluation de l'emplacement.
    """
    # Effacement de la RAM
    if CURRENT_SITE_FILE.exists():
        try:
            CURRENT_SITE_FILE.unlink()
        except Exception as e:
            logger.error(f"Erreur lors de la suppression de {CURRENT_SITE_FILE}: {e}")

    # Synchronisation BDD
    hostname = socket.gethostname()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE node_presence
            SET is_current = 0
            WHERE node_id = (SELECT id FROM nodes WHERE hostname = ?)
            """,
            (hostname,)
        )
        conn.commit()

def label_current_location(site_name, is_provisional=False, external_id=None):
    """
    Associe l'antenne actuelle au nom de chantier donné.
    """
    gsm = get_gsm_info()
    has_gsm = bool(gsm.get("mcc") and gsm.get("enodeb"))

    hostname = socket.gethostname()
    
    # Tout nom commençant par AUTO- ou TEMP- est par essence provisoire
    if site_name and (site_name.startswith("AUTO-") or site_name.startswith("TEMP-")):
        is_provisional = True
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Gestion du site (Source de vérité: external_id si présent, sinon name)
        site_id = None
        
        if external_id:
            # Chercher par external_id
            cursor.execute("SELECT id FROM sites WHERE external_id = ?", (external_id,))
            row = cursor.fetchone()
            if row:
                site_id = row["id"]
                cursor.execute(
                    "UPDATE sites SET name = ?, is_provisional = ?, is_dirty = 1 WHERE id = ?",
                    (site_name, 1 if is_provisional else 0, site_id)
                )
        
        if not site_id:
            # Chercher par nom
            cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
            row = cursor.fetchone()
            if row:
                site_id = row["id"]
                cursor.execute(
                    "UPDATE sites SET external_id = COALESCE(external_id, ?), is_provisional = ?, is_dirty = 1 WHERE id = ?",
                    (external_id, 1 if is_provisional else 0, site_id)
                )
        
        if not site_id:
            # Si le site actuel est provisoire (ou AUTO-... / TEMP-...) et qu'on définit un vrai nom
            cursor.execute(
                """
                SELECT s.id, s.name, s.external_id, s.is_provisional
                FROM sites s
                JOIN node_presence p ON s.id = p.site_id
                JOIN nodes n ON p.node_id = n.id
                WHERE n.hostname = ? AND p.is_current = 1
                LIMIT 1
                """,
                (hostname,)
            )
            curr_row = cursor.fetchone()
            if curr_row and (curr_row["is_provisional"] or (curr_row["name"] and (curr_row["name"].startswith("AUTO-") or curr_row["name"].startswith("TEMP-")))):
                # On renomme le site provisoire existant pour conserver les configurations associées
                site_id = curr_row["id"]
                cursor.execute(
                    "UPDATE sites SET name = ?, external_id = COALESCE(external_id, ?), is_provisional = ?, is_dirty = 1 WHERE id = ?",
                    (site_name, external_id, 1 if is_provisional else 0, site_id)
                )
            else:
                # Création
                cursor.execute(
                    "INSERT INTO sites (name, external_id, is_provisional, is_dirty) VALUES (?, ?, ?, 1)",
                    (site_name, external_id, 1 if is_provisional else 0)
                )
                site_id = cursor.lastrowid
        
        # 2. S'assurer que l'antenne existe, seulement si on a du GSM
        if has_gsm:
            # On met à jour les coordonnées GPS si on en a de nouvelles
            gps = gsm.get("gps")
            lat, lon = (gps["lat"], gps["lon"]) if gps else (None, None)

            # On s'assure que les valeurs ne sont pas des listes (sécurité SQL)
            mcc = str(gsm["mcc"]) if gsm.get("mcc") else None
            mnc = str(gsm["mnc"]) if gsm.get("mnc") else None
            enodeb = str(gsm["enodeb"]) if gsm.get("enodeb") else None
            lac_tac = str(gsm.get("tac") or gsm.get("lac")) if (gsm.get("tac") or gsm.get("lac")) else None
            cid = str(gsm.get("cid")) if gsm.get("cid") else None

            cursor.execute(
                """
                INSERT INTO antennas (mcc, mnc, enodeb, lac_tac, cid, lat, lon)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mcc, mnc, enodeb) DO UPDATE SET
                    lac_tac = excluded.lac_tac,
                    cid = excluded.cid,
                    lat = COALESCE(excluded.lat, lat),
                    lon = COALESCE(excluded.lon, lon),
                    last_seen = CURRENT_TIMESTAMP
                """,
                (mcc, mnc, enodeb, lac_tac, cid, lat, lon)
            )
            cursor.execute(
                "SELECT id FROM antennas WHERE mcc = ? AND mnc = ? AND enodeb = ?",
                (gsm["mcc"], gsm["mnc"], gsm["enodeb"])
            )
            antenna_id = cursor.fetchone()["id"]

            # 3. Lier l'antenne au chantier
            cursor.execute(
                "INSERT OR IGNORE INTO site_antennas (site_id, antenna_id) VALUES (?, ?)",
                (site_id, antenna_id)
            )

        # 4. Enregistrer la présence du node
        # On commence par marquer les anciennes présences comme non-actuelles
        cursor.execute(
            "UPDATE node_presence SET is_current = 0 WHERE is_current = 1"
        )
        
        # On récupère le node_id (ou on le crée)
        cursor.execute(
            "INSERT OR IGNORE INTO nodes (hostname) VALUES (?)",
            (hostname,)
        )
        cursor.execute("SELECT id FROM nodes WHERE hostname = ?", (hostname,))
        node_id = cursor.fetchone()["id"]
        
        cursor.execute(
            """
            INSERT INTO node_presence (node_id, site_id, is_current)
            VALUES (?, ?, 1)
            """,
            (node_id, site_id)
        )
        
        conn.commit()
        logger.info(f"Localisation réussie : {hostname} est maintenant sur le chantier '{site_name}'")
        
    # Validation du chantier actif en l'inscrivant dans le dossier /tmp (RAM)
    try:
        with open(CURRENT_SITE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "id": site_id,
                "name": site_name,
                "is_provisional": is_provisional
            }, f)
    except Exception as e:
        logger.error(f"Erreur lors de l'écriture dans {CURRENT_SITE_FILE}: {e}")

    return True

def get_current_site_id():
    """Retourne l'ID du chantier actuel pour ce rpinode depuis la RAM (/tmp)."""
    if CURRENT_SITE_FILE.exists():
        try:
            with open(CURRENT_SITE_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("id")
        except Exception:
            pass
    return None

def get_current_site_name():
    """Retourne le nom du chantier actuel pour ce rpinode depuis la RAM (/tmp)."""
    if CURRENT_SITE_FILE.exists():
        try:
            with open(CURRENT_SITE_FILE, "r", encoding="utf-8") as f:
                name = json.load(f).get("name")
                if name:
                    return name
        except Exception:
            pass
    return "Inconnu"

def is_current_site_provisional():
    """Vérifie si le chantier actuel est provisoire depuis la RAM (/tmp)."""
    if CURRENT_SITE_FILE.exists():
        try:
            with open(CURRENT_SITE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                name = data.get("name", "")
                is_prov = data.get("is_provisional", False)
                return bool(is_prov or name.startswith("AUTO-") or name.startswith("TEMP-") or name == "Inconnu")
        except Exception:
            pass
    return True

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        logging.basicConfig(level=logging.INFO)
        label_current_location(sys.argv[1])
    else:
        print("Usage: python3 presence.py 'NOM_DU_CHANTIER'")
