import logging
import socket
from core.database import get_db_connection
from services.gsm import get_gsm_info

logger = logging.getLogger(__name__)

def label_current_location(site_name, is_provisional=False, external_id=None):
    """
    Associe l'antenne actuelle au nom de chantier donné.
    """
    gsm = get_gsm_info()
    if not gsm.get("mcc") or not gsm.get("enodeb"):
        logger.error("Impossible de labelliser : aucune information GSM disponible.")
        return False

    hostname = socket.gethostname()
    
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
                    "UPDATE sites SET external_id = COALESCE(?, external_id), is_provisional = ?, is_dirty = 1 WHERE id = ?",
                    (external_id, 1 if is_provisional else 0, site_id)
                )
        
        if not site_id:
            # Création
            cursor.execute(
                "INSERT INTO sites (name, external_id, is_provisional, is_dirty) VALUES (?, ?, ?, 1)",
                (site_name, external_id, 1 if is_provisional else 0)
            )
            site_id = cursor.lastrowid
        
        # 2. S'assurer que l'antenne existe
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
        return True

def get_current_site_id():
    """Retourne l'ID du chantier actuel pour ce rpinode."""
    hostname = socket.gethostname()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.id 
            FROM sites s
            JOIN node_presence p ON s.id = p.site_id
            JOIN nodes n ON p.node_id = n.id
            WHERE n.hostname = ? AND p.is_current = 1
            LIMIT 1
            """,
            (hostname,)
        )
        row = cursor.fetchone()
        return row["id"] if row else None

def get_current_site_name():
    """Retourne le nom du chantier actuel pour ce rpinode."""
    hostname = socket.gethostname()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.name 
            FROM sites s
            JOIN node_presence p ON s.id = p.site_id
            JOIN nodes n ON p.node_id = n.id
            WHERE n.hostname = ? AND p.is_current = 1
            LIMIT 1
            """,
            (hostname,)
        )
        row = cursor.fetchone()
        return row["name"] if row else "Inconnu"

def is_current_site_provisional():
    """Vérifie si le chantier actuel est provisoire."""
    hostname = socket.gethostname()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.is_provisional 
            FROM sites s
            JOIN node_presence p ON s.id = p.site_id
            JOIN nodes n ON p.node_id = n.id
            WHERE n.hostname = ? AND p.is_current = 1
            LIMIT 1
            """,
            (hostname,)
        )
        row = cursor.fetchone()
        return bool(row["is_provisional"]) if row else True

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        logging.basicConfig(level=logging.INFO)
        label_current_location(sys.argv[1])
    else:
        print("Usage: python3 presence.py 'NOM_DU_CHANTIER'")
