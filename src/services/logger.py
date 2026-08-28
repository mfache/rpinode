import time
import logging
import random
import threading
from core.database import get_db_connection
from services.presence import get_current_site_name, is_current_site_provisional
from services.fleet import fleet

logger = logging.getLogger(__name__)

def start_data_logger(interval=60):
    """
    Démarre la boucle d'enregistrement des données.
    """
    logger.info(f"Démarrage du service d'enregistrement des données (intervalle: {interval}s)")
    
    while True:
        try:
            if not is_current_site_provisional():
                run_logging_cycle()
            else:
                logger.debug("Logging ignoré : le chantier est en mode provisoire.")
        except Exception as e:
            logger.error(f"Erreur dans le cycle d'enregistrement : {e}")
        
        time.sleep(interval)

def run_logging_cycle():
    """
    Effectue un cycle de lecture sur tous les appareils du chantier actuel.
    """
    site_name = get_current_site_name()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Récupérer l'ID du site
        cursor.execute("SELECT id, external_id FROM sites WHERE name = ?", (site_name,))
        site_row = cursor.fetchone()
        if not site_row:
            return
        
        site_id = site_row["id"]
        external_site_id = site_row["external_id"] or str(site_id)
        
        timestamp = int(time.time())
        
        # 1. Modbus
        cursor.execute(
            """
            SELECT d.*, t.registers_json 
            FROM modbus_devices d
            JOIN modbus_templates t ON d.template_id = t.id
            WHERE d.site_id = ?
            """,
            (site_id,)
        )
        for dev in cursor.fetchall():
            registers = json_loads(dev["registers_json"])
            for reg in registers:
                value = simulate_value(reg["name"])
                record_trend(cursor, site_id, "modbus", timestamp, dev["address"], str(reg["reg"]), value)

        # 2. BACnet
        cursor.execute(
            """
            SELECT d.*, t.objects_json 
            FROM bacnet_devices d
            JOIN bacnet_templates t ON d.template_id = t.id
            WHERE d.site_id = ?
            """,
            (site_id,)
        )
        for dev in cursor.fetchall():
            objects = json_loads(dev["objects_json"])
            for obj in objects:
                value = simulate_value(obj["name"])
                record_trend(cursor, site_id, "bacnet", timestamp, str(dev["device_instance"]), obj["obj"], value)
        
        conn.commit()
        
        # 3. Tentative de synchronisation avec la flotte
        if fleet.is_registered():
            sync_trends_to_fleet(site_id, external_site_id)

def simulate_value(name):
    """Simule une valeur réaliste basée sur le nom du point."""
    name = name.lower()
    if "temp" in name:
        return f"{random.uniform(18.0, 25.0):.1f}"
    if "consigne" in name:
        return "21.0"
    if "vitesse" in name:
        return str(random.randint(0, 100))
    if "puissance" in name:
        return f"{random.uniform(100.0, 5000.0):.1f}"
    if "alarme" in name:
        return "0" if random.random() > 0.1 else "1"
    return str(random.randint(0, 1000))

def record_trend(cursor, site_id, protocol, timestamp, device_id, object_id, value):
    """Enregistre un relevé en base locale."""
    cursor.execute(
        """
        INSERT INTO trends (site_id, protocol, timestamp, device_id, object_id, value)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (site_id, protocol, timestamp, device_id, object_id, value)
    )

def sync_trends_to_fleet(site_id, external_site_id):
    """Envoie les relevés non synchronisés au serveur maître."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM trends WHERE site_id = ? AND is_synced = 0 LIMIT 100",
            (site_id,)
        )
        rows = cursor.fetchall()
        
        if not rows:
            return
            
        trends_payload = []
        ids = []
        for r in rows:
            trends_payload.append({
                "s": external_site_id,
                "p": r["protocol"],
                "t": r["timestamp"],
                "d": r["device_id"],
                "o": r["object_id"],
                "v": r["value"]
            })
            ids.append(r["id"])
        
        if fleet.send_trends(trends_payload):
            cursor.execute(
                f"UPDATE trends SET is_synced = 1 WHERE id IN ({','.join(['?']*len(ids))})",
                ids
            )
            conn.commit()
            logger.info(f"{len(ids)} relevés synchronisés avec le serveur.")

def json_loads(data):
    import json
    try:
        return json.loads(data)
    except:
        return []
