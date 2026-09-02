import json
import logging
import queue
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import paho.mqtt.client as mqtt

from core.database import get_db_connection
from services.mqtt_service import mqtt_client
from services.presence import get_current_site_id

logger = logging.getLogger(__name__)

CATALOG_BUILD_WORKERS = 5
CATALOG_DEVICE_TIMEOUT = 20.0
POINT_PREFIXES = ("analog-", "binary-", "multi-state-", "loop")
FLEET_PUSH_BATCH_SIZE = 8000  # Compressé en gzip côté fleet.py, reste bien sous la limite de 100 Ko du serveur


def _probe_device_catalog(ip, device_instance, timeout=CATALOG_DEVICE_TIMEOUT):
    """Demande au démon BACnet MQTT les points (nom seul) d'un appareil. Appel bloquant,
    à utiliser uniquement depuis un thread dédié (jamais depuis la boucle asyncio)."""
    job_id = str(uuid.uuid4())
    res_queue = queue.Queue()

    def on_msg(client, userdata, msg):
        try:
            res_queue.put(json.loads(msg.payload.decode("utf-8")))
        except Exception:
            pass

    try:
        temp_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        temp_client = mqtt.Client()
    temp_client.on_message = on_msg
    temp_client.connect("127.0.0.1", 1883, 60)
    temp_client.loop_start()
    temp_client.subscribe(f"rpinode/bacnet/res/catalog/{job_id}")

    try:
        mqtt_client.publish("rpinode/bacnet/cmd/catalog", {
            "job_id": job_id, "ip": ip, "device_instance": device_instance
        })
        try:
            return res_queue.get(timeout=timeout)
        except queue.Empty:
            return {"status": "error", "message": "Délai d'attente dépassé"}
    finally:
        temp_client.loop_stop()
        temp_client.disconnect()


def upsert_device_points(site_id, network_address, device_instance, objects):
    """
    Met à jour le dictionnaire local pour un appareil d'un chantier donné, à partir
    d'une liste d'objets {object_id, name, ...}. Filtre automatiquement aux types de
    points utiles (analog/binary/multi-state/loop). Utilisée à la fois par la
    construction complète et par l'outil de découverte manuelle (qui rafraîchit
    l'appareil qu'il vient de lire).
    """
    if not site_id:
        return 0

    rows = [
        (site_id, network_address, int(device_instance), o["object_id"], o.get("name"))
        for o in objects
        if o.get("object_id") and o["object_id"].split(":")[0].startswith(POINT_PREFIXES)
    ]
    if not rows:
        return 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO bacnet_points_catalog (site_id, network_address, device_instance, object_id, object_name, is_dirty, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(site_id, device_instance, object_id) DO UPDATE SET
                network_address = excluded.network_address,
                object_name = excluded.object_name,
                is_dirty = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            rows
        )
        conn.commit()
    return len(rows)


def get_status():
    """Retourne l'état courant de la construction du dictionnaire, plus quelques stats
    limitées au chantier actuel (pour ne jamais laisser transparaître les données d'un
    précédent chantier)."""
    site_id = get_current_site_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bacnet_catalog_status WHERE id = 1")
        row = cursor.fetchone()
        status = dict(row) if row else {
            "status": "idle", "scheduled_at": None, "started_at": None, "finished_at": None,
            "total_devices": 0, "done_devices": 0, "failed_devices": 0, "last_error": None
        }
        if site_id:
            cursor.execute("SELECT COUNT(*) as c FROM bacnet_points_catalog WHERE site_id = ?", (site_id,))
            status["catalog_count"] = cursor.fetchone()["c"]
            cursor.execute("SELECT MAX(updated_at) as m FROM bacnet_points_catalog WHERE site_id = ?", (site_id,))
            status["last_built_at"] = cursor.fetchone()["m"]
        else:
            status["catalog_count"] = 0
            status["last_built_at"] = None
        return status


def schedule_build(when=None):
    """Planifie une construction. `when` : chaîne ISO datetime, ou None pour immédiat.
    S'applique toujours au chantier courant au moment où la construction démarre
    réellement (et non celui d'aujourd'hui, au cas où le boîtier serait déplacé)."""
    scheduled_at = when or datetime.now().isoformat(timespec="seconds")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bacnet_catalog_status
            SET status = 'scheduled', scheduled_at = ?, started_at = NULL, finished_at = NULL,
                total_devices = 0, done_devices = 0, failed_devices = 0, last_error = NULL
            WHERE id = 1
            """,
            (scheduled_at,)
        )
        conn.commit()
    return scheduled_at


def cancel_build():
    """Annule une construction planifiée qui n'a pas encore démarré."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE bacnet_catalog_status SET status = 'idle', scheduled_at = NULL WHERE id = 1 AND status = 'scheduled'")
        conn.commit()


def _update_status(**fields):
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE bacnet_catalog_status SET {set_clause} WHERE id = 1", list(fields.values()))
        conn.commit()


def build_catalog():
    """
    Construit (ou reconstruit) le dictionnaire des points BACnet du chantier courant,
    à partir des appareils déjà identifiés lors des scans réseau. Le chantier est résolu
    au moment de l'exécution (et non de la planification), pour toujours refléter la
    localisation réelle du boîtier.
    """
    site_id = get_current_site_id()
    if not site_id:
        logger.warning("Construction du dictionnaire BACnet annulée : aucun chantier actif.")
        _update_status(status="error", last_error="Aucun chantier actif", finished_at=datetime.now().isoformat(timespec="seconds"))
        return

    logger.info(f"Début de la construction du dictionnaire BACnet (chantier id={site_id})...")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_ip, bacnet_instance FROM discovered_devices WHERE site_id = ? AND bacnet_instance IS NOT NULL AND last_ip IS NOT NULL",
            (site_id,)
        )
        devices = [dict(r) for r in cursor.fetchall()]

    total = len(devices)
    _update_status(
        status="running", started_at=datetime.now().isoformat(timespec="seconds"),
        finished_at=None, total_devices=total, done_devices=0, failed_devices=0, last_error=None
    )

    if total == 0:
        _update_status(status="done", finished_at=datetime.now().isoformat(timespec="seconds"))
        logger.info("Aucun appareil BACnet connu sur ce chantier (lancez d'abord un scan IP).")
        return

    done = 0
    failed = 0

    def _process_one(dev):
        return dev, _probe_device_catalog(dev["last_ip"], dev["bacnet_instance"])

    with ThreadPoolExecutor(max_workers=CATALOG_BUILD_WORKERS) as executor:
        futures = [executor.submit(_process_one, dev) for dev in devices]
        for future in as_completed(futures):
            try:
                dev, result = future.result()
                if result and result.get("status") == "ok":
                    upsert_device_points(site_id, dev["last_ip"], dev["bacnet_instance"], result.get("objects", []))
                else:
                    failed += 1
                    logger.debug(f"Catalogage échoué pour {dev.get('last_ip')} (instance {dev.get('bacnet_instance')}): {result.get('message') if result else 'aucune réponse'}")
            except Exception as e:
                logger.warning(f"Erreur catalogage d'un appareil: {e}")
                failed += 1
            done += 1
            _update_status(done_devices=done, failed_devices=failed)

    logger.info(f"Dictionnaire BACnet reconstruit : {done}/{total} appareil(s) traité(s), {failed} échec(s).")
    _update_status(status="done", finished_at=datetime.now().isoformat(timespec="seconds"))

    try:
        push_dirty_to_fleet()
    except Exception as e:
        logger.debug(f"Synchro flotte du dictionnaire BACnet ignorée : {e}")


def search_points(pattern):
    """
    Recherche des points dans le dictionnaire local par nom, avec le support natif des
    jokers SQLite (`*` = n'importe quelle suite de caractères, `?` = un seul caractère).
    Toujours limitée au chantier courant.
    """
    site_id = get_current_site_id()
    if not site_id:
        return []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT network_address, device_instance, object_id, object_name
            FROM bacnet_points_catalog
            WHERE site_id = ? AND object_name GLOB ?
            ORDER BY device_instance, object_id
            LIMIT 500
            """,
            (site_id, pattern)
        )
        return [dict(r) for r in cursor.fetchall()]


def push_dirty_to_fleet(batch_size=FLEET_PUSH_BATCH_SIZE):
    """
    Pousse vers le serveur central (docs) les points du dictionnaire non encore
    synchronisés, par lots (le dictionnaire complet peut compter des dizaines de
    milliers de lignes). Chaque point est tagué avec l'identifiant externe (docs) de
    son chantier, puisque le serveur central agrège les données de plusieurs chantiers.
    """
    from services.fleet import fleet

    if not fleet.is_registered():
        return

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.id, s.external_id as chantier_id, c.network_address, c.device_instance, c.object_id, c.object_name
            FROM bacnet_points_catalog c
            JOIN sites s ON c.site_id = s.id
            WHERE c.is_dirty = 1 AND s.external_id IS NOT NULL
            """
        )
        dirty_rows = [dict(r) for r in cursor.fetchall()]

    if not dirty_rows:
        return

    for i in range(0, len(dirty_rows), batch_size):
        batch = dirty_rows[i:i + batch_size]
        payload = [
            {
                "chantier_id": r["chantier_id"],
                "network_address": r["network_address"],
                "device_instance": r["device_instance"],
                "object_id": r["object_id"],
                "object_name": r["object_name"]
            }
            for r in batch
        ]
        success = fleet.sync_bacnet_points_catalog(payload)
        if success:
            ids = [r["id"] for r in batch]
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.executemany(
                    "UPDATE bacnet_points_catalog SET is_dirty = 0, sync_updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [(i,) for i in ids]
                )
                conn.commit()
        else:
            logger.warning("Échec de l'envoi d'un lot du dictionnaire BACnet vers la flotte, nouvelle tentative au prochain cycle.")
            break


def run_scheduler_loop(poll_interval=15):
    """
    Boucle de fond à lancer dans un thread dédié au démarrage de rpinode : vérifie
    périodiquement si une construction planifiée est arrivée à échéance, et la lance.
    Profite aussi de chaque passage pour pousser les éventuels points en attente
    de synchronisation vers la flotte (ex: après une découverte manuelle).
    """
    while True:
        try:
            status = get_status()
            if status.get("status") == "scheduled" and status.get("scheduled_at"):
                try:
                    scheduled_dt = datetime.fromisoformat(status["scheduled_at"])
                except ValueError:
                    scheduled_dt = datetime.now()
                if datetime.now() >= scheduled_dt:
                    build_catalog()
            else:
                push_dirty_to_fleet()
        except Exception as e:
            logger.error(f"Erreur dans la boucle du planificateur de dictionnaire BACnet: {e}")
        time.sleep(poll_interval)
