import json
import logging
import os
import random
import subprocess
import sys
import threading
import time

from core.config import load_config
from core.database import get_db_connection
from core.paths import DATA_DIR
from services.fleet import fleet
from services.modbus_mgr import get_site_modbus_points, read_point_value
from services.mqtt_service import mqtt_client
from services.presence import (get_current_site_name,
                               is_current_site_provisional)

logger = logging.getLogger(__name__)

# Fichier optionnel pour forcer des points BACnet spécifiques (compatibilité ancien système)
BACNET_POINTS_FILE = DATA_DIR / "bacnet_points.json"

CADENCE_MAP = {
    "max": 2,
    "5s": 5,
    "10s": 10,
    "30s": 30,
    "1m": 60,
    "5m": 300,
}
HEARTBEAT_SECONDS = 900  # 15 min

def start_data_logger(interval=1):
    """
    Démarre la boucle d'enregistrement des données.
    Tourne avec un tick rapide (1s) pour respecter les cadences (5s, 10s, 30s, 1m, 5m).
    """
    logger.info("Démarrage du service d'enregistrement des données (Trends & Suivi)")
    
    last_poll_times = {}       # {point_id: timestamp}
    last_recorded_values = {}  # {point_id: val_str}
    last_store_ts = {}         # {point_id: timestamp}
    consecutive_errors = {}    # {point_id: error_count}
    last_bacnet_time = 0
    last_fleet_sync = 0

    while True:
        try:
            config = load_config()
            retries = config.get("logger_retries", 3)
            modbus_timeout = config.get("modbus_timeout", 1.2)
            bacnet_timeout = config.get("bacnet_timeout", 45)

            if not is_current_site_provisional():
                now = time.time()
                
                # 1. Cycle Modbus (enregistrements selon cadence individuelle)
                run_modbus_logging_cycle(now, last_poll_times, last_recorded_values, last_store_ts, consecutive_errors, 
                                         retries=retries, timeout=modbus_timeout)
                
                # 2. Cycle BACnet (toutes les 60s)
                if now - last_bacnet_time >= 60:
                    run_bacnet_logging_cycle(int(now), timeout=bacnet_timeout)
                    last_bacnet_time = now
                    
                # 3. Synchronisation flotte (toutes les 30s)
                if now - last_fleet_sync >= 30:
                    if fleet.is_registered():
                        sync_trends_to_fleet()
                    last_fleet_sync = now
            else:
                logger.debug("Logging ignoré : le chantier est en mode provisoire.")
        except Exception as e:
            logger.error(f"Erreur dans le cycle d'enregistrement : {e}")
        
        time.sleep(1)

def run_modbus_logging_cycle(now, last_poll_times, last_recorded_values, last_store_ts, consecutive_errors=None, retries=3, timeout=1.2):
    """Effectue un tick de lecture/enregistrement pour les points Modbus avec tolérance sur n cycles en cas d'erreur."""
    if consecutive_errors is None:
        consecutive_errors = {}
    site_name = get_current_site_name()
    if not site_name or site_name == "Inconnu":
        return

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
        site_row = cursor.fetchone()
        if not site_row:
            return
        site_id = site_row["id"]
        
        # Récupérer tous les points suivis (Live) et/ou enregistrés (Historique)
        cursor.execute(
            """
            SELECT p.*, d.name as device_name, d.protocol, d.address, d.port,
                   COALESCE(p.slave_unit, d.slave_unit, 1) as slave_unit
            FROM modbus_points p
            JOIN modbus_devices d ON p.device_id = d.id
            WHERE p.site_id = ? AND (p.is_monitored = 1 OR p.is_recorded = 1)
            """,
            (site_id,)
        )
        points = [dict(r) for r in cursor.fetchall()]
        
        for p in points:
            pid = p["id"]
            # Si le point est enregistré, respecter sa cadence, sinon cadence par défaut 5s pour le live
            if p.get("is_recorded"):
                interval = CADENCE_MAP.get(p.get("cadence", "1m"), 60)
            else:
                interval = 5
            
            if now - last_poll_times.get(pid, 0) < interval:
                continue
            last_poll_times[pid] = now
            
            protocol = p["protocol"]
            address = p["address"]
            port = p["port"] or 502
            unit = p.get("slave_unit") or (int(address) if protocol == "mstp" else 1)
            func = p["function"]
            reg = p["reg"]
            base = p.get("base", 0)
            type_str = p["type"]
            scale = p["scale"]
            unit_str = f" {p['unit']}" if p.get("unit") else ""
            
            try:
                val_str, disp_val = read_point_value(protocol, address, port, unit, func, reg, type_str, scale, base=base, timeout=timeout)
                if val_str is None:
                    continue
                
                # Réinitialiser le compteur d'erreurs en cas de succès
                consecutive_errors[pid] = 0
                
                # Mise à jour last_value en base
                cursor.execute(
                    "UPDATE modbus_points SET last_value = ?, last_read_ts = ? WHERE id = ?",
                    (val_str, int(now), pid)
                )

                # Publication temps réel vers le broker MQTT local pour le flux SSE
                full_display = f"{disp_val}{unit_str}"
                mqtt_payload = {
                    "point_id": pid,
                    "value": val_str,
                    "display": full_display,
                    "name": p.get("name"),
                    "device_name": p.get("device_name"),
                    "ts": int(now),
                    "error": None
                }
                mqtt_client.publish(f"rpinode/modbus/point/{pid}", mqtt_payload)
                
                # Enregistrement dans les tendances (si le point est coché pour enregistrement)
                if p.get("is_recorded"):
                    changed = (val_str != last_recorded_values.get(pid))
                    heartbeat_due = (now - last_store_ts.get(pid, 0)) >= HEARTBEAT_SECONDS
                    
                    if changed or heartbeat_due:
                        ts = int(now)
                        obj_id = f"FC{func:02d}_{reg}"
                        record_trend(cursor, site_id, "modbus", ts, p["device_name"], obj_id, val_str)
                        last_recorded_values[pid] = val_str
                        last_store_ts[pid] = ts
            except Exception as e:
                err_count = consecutive_errors.get(pid, 0) + 1
                consecutive_errors[pid] = err_count
                
                # Tolérance : on conserve la dernière valeur connue pendant les n-1 premiers cycles d'échec
                if err_count < retries and p.get("last_value") is not None:
                    logger.debug(f"Modbus logger: Échec cycle {err_count}/{retries} pour {pid} ({p.get('name')}), dernière valeur conservée.")
                    full_display = f"{p['last_value']}{unit_str}"
                    mqtt_payload = {
                        "point_id": pid,
                        "value": p["last_value"],
                        "display": full_display,
                        "name": p.get("name"),
                        "device_name": p.get("device_name"),
                        "ts": int(now),
                        "error": None,
                        "retained": True
                    }
                    mqtt_client.publish(f"rpinode/modbus/point/{pid}", mqtt_payload)
                elif err_count >= retries:
                    logger.warning(f"Modbus logger: Échec confirmé ({err_count}/{retries}) pour {pid} ({p.get('name')}) : {e}")
                    mqtt_payload = {
                        "point_id": pid,
                        "value": None,
                        "display": "—",
                        "name": p.get("name"),
                        "device_name": p.get("device_name"),
                        "ts": int(now),
                        "error": str(e)
                    }
                    mqtt_client.publish(f"rpinode/modbus/point/{pid}", mqtt_payload)

        conn.commit()

def run_bacnet_logging_cycle(timestamp, timeout=45):
    """Gère la lecture et l'enregistrement des points BACnet."""
    site_name = get_current_site_name()
    if not site_name or site_name == "Inconnu":
        return

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
        site_row = cursor.fetchone()
        if not site_row:
            return
        site_id = site_row["id"]
        
        run_bacnet_cycle(cursor, site_id, timestamp, timeout=timeout)
        conn.commit()

def run_bacnet_cycle(cursor, site_id, timestamp, timeout=45):
    """Gère la lecture des points BACnet via le script services/bacnet_reader.py"""
    requests = []
    
    # Priorité 1 : Fichier JSON (si présent, pour compatibilité ou forçage)
    if BACNET_POINTS_FILE.exists():
        try:
            with open(BACNET_POINTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # On s'attend à un format {"points": [{"addr": "...", "obj": "...", "instance": 123}, ...]}
                requests = data.get("points", [])
        except Exception as e:
            logger.warning(f"Fichier {BACNET_POINTS_FILE} illisible : {e}")
    
    # Priorité 2 : Base de données (si aucune requête via fichier)
    if not requests:
        try:
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
                    requests.append({
                        "addr": dev["network_address"],
                        "instance": dev["device_instance"],
                        "obj": obj["obj"],
                        "device_id": str(dev["device_instance"]) # Pour le stockage trend
                    })
        except Exception as e:
            logger.error(f"Erreur lecture BACnet DB : {e}")

    if not requests:
        return

    # Appel du script externe
    try:
        # Chemin absolu vers le reader
        reader_path = os.path.join(os.path.dirname(__file__), "bacnet_reader.py")
        
        # Utilisation du venv BACnet s'il existe
        bacnet_python = "/opt/boitier-bacnet/venv/bin/python"
        if not os.path.exists(bacnet_python):
            bacnet_python = sys.executable
            
        process = subprocess.Popen(
            [bacnet_python, reader_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=json.dumps(requests), timeout=timeout)
        
        if process.returncode == 0:
            data = json.loads(stdout)
            if "error" in data:
                logger.warning(f"Erreur retournée par le reader BACnet : {data['error']}")
            
            for res in data.get("results", []):
                if res.get("status") == "ok":
                    # On retrouve le device_id d'origine pour l'enregistrement
                    # (Dans un système réel, on passerait un ID de contexte)
                    dev_id = next((r["device_id"] for r in requests if r["addr"] == res["addr"] and r["obj"] == res["obj"]), res["instance"])
                    record_trend(cursor, site_id, "bacnet", timestamp, str(dev_id), res["obj"], res["value"])
        else:
            logger.error(f"Le reader BACnet a échoué (code {process.returncode}) : {stderr}")

    except subprocess.TimeoutExpired:
        logger.warning("Le reader BACnet a expiré (timeout).")
    except Exception as e:
        logger.error(f"Erreur lors de l'appel au reader BACnet : {e}")

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

def sync_trends_to_fleet(site_id=None, external_site_id=None):
    """Envoie les relevés non synchronisés au serveur maître."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        if not site_id:
            site_name = get_current_site_name()
            cursor.execute("SELECT id, external_id FROM sites WHERE name = ?", (site_name,))
            row = cursor.fetchone()
            if not row:
                return
            site_id = row["id"]
            external_site_id = row["external_id"] or str(site_id)
        elif not external_site_id:
            cursor.execute("SELECT external_id FROM sites WHERE id = ?", (site_id,))
            row = cursor.fetchone()
            external_site_id = row["external_id"] if row and row["external_id"] else str(site_id)
            
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
