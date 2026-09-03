import json
import logging
import uuid
import socket

from core.database import get_db_connection

logger = logging.getLogger(__name__)

def get_all_templates(include_hidden=False):
    """Récupère tous les templates BACnet disponibles."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        where = "" if include_hidden else "WHERE is_local_hidden = 0"
        cursor.execute(f"SELECT * FROM bacnet_templates {where} ORDER BY name")
        return [dict(row) for row in cursor.fetchall()]

def get_template(template_id):
    """Récupère un template spécifique."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bacnet_templates WHERE id = ?", (template_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def save_template(name, manufacturer, objects, template_id=None, is_shared=None, external_id=None):
    """Crée ou met à jour un template BACnet, gère le versioning immuable et synchronise les points des appareils liés."""
    objects_json = json.dumps(objects, ensure_ascii=False)
    hostname = socket.gethostname()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if template_id:
            cursor.execute("SELECT * FROM bacnet_templates WHERE id = ?", (template_id,))
            existing = cursor.fetchone()
            if existing:
                existing = dict(existing)
                t_uuid = existing.get("template_uuid") or str(uuid.uuid5(uuid.NAMESPACE_DNS, f"bacnet-template-{name}"))
                prev_rev_uuid = existing.get("revision_uuid")
                current_version = int(existing.get("version") or 1)
                shared_val = existing.get("is_shared", 0) if is_shared is None else (1 if is_shared else 0)

                # Si le template est partagé, toute modification incrémente la version et génère un nouveau revision_uuid
                if shared_val == 1:
                    new_version = current_version + 1
                    new_rev_uuid = str(uuid.uuid4())
                    parent_rev = prev_rev_uuid
                else:
                    new_version = current_version
                    new_rev_uuid = str(uuid.uuid4())
                    parent_rev = existing.get("parent_revision_uuid")

                cursor.execute(
                    """
                    UPDATE bacnet_templates 
                    SET name = ?, manufacturer = ?, objects_json = ?, external_id = COALESCE(?, external_id),
                        template_uuid = ?, revision_uuid = ?, parent_revision_uuid = ?,
                        version = ?, is_shared = ?, is_local_hidden = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (name, manufacturer, objects_json, external_id, t_uuid, new_rev_uuid, parent_rev, new_version, shared_val, template_id)
                )
            else:
                template_id = None

        if not template_id:
            t_uuid = str(uuid.uuid4())
            rev_uuid = str(uuid.uuid4())
            shared_val = 1 if is_shared else 0
            cursor.execute(
                """
                INSERT INTO bacnet_templates 
                (template_uuid, revision_uuid, parent_revision_uuid, name, manufacturer, version, is_shared, is_local_hidden, created_by_node, objects_json, external_id)
                VALUES (?, ?, NULL, ?, ?, 1, ?, 0, ?, ?, ?)
                """,
                (t_uuid, rev_uuid, name, manufacturer, shared_val, hostname, objects_json, external_id)
            )
            template_id = cursor.lastrowid

        # Synchroniser les points pour tous les appareils utilisant ce template
        cursor.execute("SELECT id, site_id FROM bacnet_devices WHERE template_id = ?", (template_id,))
        devices = cursor.fetchall()

        tpl_map = {r.get("obj"): r for r in objects}

        for dev in devices:
            dev_id = dev["id"]
            cursor.execute("SELECT * FROM bacnet_points WHERE device_id = ?", (dev_id,))
            current_points = [dict(p) for p in cursor.fetchall()]

            for p in current_points:
                pkey = p["object_id"]
                if pkey in tpl_map:
                    tr = tpl_map[pkey]
                    cursor.execute(
                        """
                        UPDATE bacnet_points
                        SET name = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (tr.get("name", p["name"]), p["id"])
                    )
                else:
                    if not p.get("is_recorded"):
                        cursor.execute("DELETE FROM bacnet_points WHERE id = ?", (p["id"],))
        conn.commit()

    # Si le template est partagé, on notifie la flotte immédiatement si possible
    tpl = get_template(template_id)
    if tpl and tpl.get("is_shared") == 1:
        try:
            from services.fleet import fleet
            if fleet.is_registered():
                payload_def = format_local_template_for_fleet(tpl)
                fleet.sync_bacnet_templates({tpl["template_uuid"] or tpl["name"]: payload_def})
        except Exception as e:
            logger.debug(f"Notification flotte ignorée : {e}")

    return template_id

def delete_template(template_id):
    """
    Supprime ou masque un template BACnet localement s'il n'est utilisé par aucun appareil.
    Si partagé avec la flotte, marque is_local_hidden = 1 pour éviter sa réinjection automatique.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) as cnt FROM bacnet_devices WHERE template_id = ?", (template_id,))
        row = cursor.fetchone()
        if row and row["cnt"] > 0:
            return False, f"Impossible de supprimer ce template : il est utilisé par {row['cnt']} appareil(s)."

        cursor.execute("SELECT is_shared FROM bacnet_templates WHERE id = ?", (template_id,))
        tpl = cursor.fetchone()
        if tpl and tpl["is_shared"] == 1:
            cursor.execute("UPDATE bacnet_templates SET is_local_hidden = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (template_id,))
        else:
            cursor.execute("DELETE FROM bacnet_templates WHERE id = ?", (template_id,))
        conn.commit()
        return True, None

def normalize_fleet_definition_to_local(name, definition):
    """Convertit la définition JSON reçue de docs vers le format de template local rpinode."""
    manufacturer = definition.get("notes") or definition.get("manufacturer") or ""
    reads = definition.get("reads", [])
    objects = []
    for r in reads:
        objects.append({
            "obj": r.get("obj"),
            "name": r.get("label") or r.get("name")
        })

    return {
        "name": name,
        "manufacturer": manufacturer,
        "objects": objects
    }

def format_local_template_for_fleet(tpl):
    """Convertit un template local rpinode vers le format versionné attendu par docs."""
    try:
        objects = json.loads(tpl.get("objects_json", "[]"))
    except Exception:
        objects = []

    reads = []
    for r in objects:
        reads.append({
            "obj": r.get("obj", ""),
            "name": r.get("name", "")
        })

    definition = {
        "name": tpl["name"],
        "notes": tpl.get("manufacturer", "") or "",
        "objects": reads
    }
    return {
        "template_uuid": tpl.get("template_uuid"),
        "revision_uuid": tpl.get("revision_uuid"),
        "parent_revision_uuid": tpl.get("parent_revision_uuid"),
        "name": tpl["name"],
        "manufacturer": tpl.get("manufacturer", "") or "",
        "notes": tpl.get("manufacturer", "") or "",
        "version": int(tpl.get("version") or 1),
        "created_by_node": tpl.get("created_by_node"),
        "definition": definition,
        "objects": reads
    }

def import_template_from_fleet(template_name_or_uuid):
    """Télécharge un template depuis la flotte docs et l'installe/met à jour en local."""
    from services.fleet import fleet
    remote_templates = fleet.get_remote_bacnet_templates()

    definition = None
    tpl_key = None
    for k, defn in remote_templates.items():
        if k == template_name_or_uuid or defn.get("template_uuid") == template_name_or_uuid or defn.get("name") == template_name_or_uuid:
            definition = defn
            tpl_key = k
            break

    if not definition:
        return False, f"Template '{template_name_or_uuid}' non trouvé sur le serveur distant."

    norm = normalize_fleet_definition_to_local(definition.get("name") or tpl_key, definition)
    tpl_uuid = definition.get("template_uuid")
    rev_uuid = definition.get("revision_uuid")
    parent_rev_uuid = definition.get("parent_revision_uuid")
    version = int(definition.get("version") or 1)
    created_by_node = definition.get("created_by_node") or "Flotte"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM bacnet_templates WHERE template_uuid = ? OR name = ?", (tpl_uuid, norm["name"]))
        existing = cursor.fetchone()
        local_id = existing["id"] if existing else None

        objects_json = json.dumps(norm["objects"], ensure_ascii=False)

        if local_id:
            cursor.execute("""
                UPDATE bacnet_templates
                SET name = ?, manufacturer = ?, objects_json = ?,
                    template_uuid = ?, revision_uuid = ?, parent_revision_uuid = ?,
                    version = ?, is_shared = 1, is_local_hidden = 0,
                    created_by_node = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (norm["name"], norm["manufacturer"], objects_json, tpl_uuid, rev_uuid, parent_rev_uuid, version, created_by_node, local_id))
        else:
            cursor.execute("""
                INSERT INTO bacnet_templates
                (template_uuid, revision_uuid, parent_revision_uuid, name, manufacturer, version, is_shared, is_local_hidden, created_by_node, objects_json)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
            """, (tpl_uuid, rev_uuid, parent_rev_uuid, norm["name"], norm["manufacturer"], version, created_by_node, objects_json))
            local_id = cursor.lastrowid
        conn.commit()

    return True, None

def share_template_to_fleet(template_id):
    """Pousse un template local vers la bibliothèque de la flotte docs."""
    from services.fleet import fleet
    tpl = get_template(template_id)
    if not tpl:
        return False, "Template local introuvable."

    with get_db_connection() as conn:
        conn.execute("UPDATE bacnet_templates SET is_shared = 1 WHERE id = ?", (template_id,))
        conn.commit()

    tpl = get_template(template_id)
    payload_def = format_local_template_for_fleet(tpl)
    success = fleet.sync_bacnet_templates({tpl["template_uuid"] or tpl["name"]: payload_def})
    if success:
        return True, None
    return False, "Échec lors de l'envoi vers le serveur distant."

def get_templates_overview():
    """
    Récupère la liste des templates locaux (non masqués) et des templates distants.
    """
    local_templates = get_all_templates(include_hidden=False)
    local_uuid_map = {t["template_uuid"]: t for t in local_templates if t.get("template_uuid")}
    local_name_map = {t["name"]: t for t in local_templates}

    from services.fleet import fleet
    remote_templates = fleet.get_remote_bacnet_templates() if fleet.is_registered() else {}

    fleet_list = []
    for name, definition in remote_templates.items():
        t_uuid = definition.get("template_uuid")
        local_match = local_uuid_map.get(t_uuid) or local_name_map.get(name)
        is_installed = local_match is not None
        local_id = local_match["id"] if local_match else None
        local_version = local_match.get("version") if local_match else None
        fleet_version = definition.get("version", 1)

        objects = definition.get("objects") or definition.get("reads", [])
        fleet_list.append({
            "name": name,
            "template_uuid": t_uuid,
            "revision_uuid": definition.get("revision_uuid"),
            "notes": definition.get("notes") or definition.get("manufacturer") or "—",
            "version": fleet_version,
            "local_version": local_version,
            "needs_update": is_installed and local_version is not None and local_version < fleet_version,
            "objects_count": len(objects),
            "created_by": definition.get("created_by_node") or definition.get("created_by") or "Flotte",
            "is_installed": is_installed,
            "local_id": local_id
        })

    return local_templates, fleet_list

def get_site_devices(site_id):
    """Récupère les appareils BACnet configurés pour un chantier."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.*, t.name as template_name, t.manufacturer as template_manufacturer
            FROM bacnet_devices d
            JOIN bacnet_templates t ON d.template_id = t.id
            WHERE d.site_id = ?
            """,
            (site_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def add_device_to_site(site_id, template_id, name, device_instance, network_address):
    """Ajoute un appareil BACnet à un chantier."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bacnet_devices (site_id, template_id, name, device_instance, network_address)
            VALUES (?, ?, ?, ?, ?)
            """,
            (site_id, template_id, name, device_instance, network_address)
        )
        conn.commit()
        return cursor.lastrowid

def delete_device_from_site(device_id):
    """Supprime un appareil BACnet et ses points associés."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bacnet_points WHERE device_id = ?", (device_id,))
        cursor.execute("DELETE FROM bacnet_devices WHERE id = ?", (device_id,))
        conn.commit()

# ---------------------------------------------------------------------------
# Gestion des Points BACnet Suivis / Enregistrés
# ---------------------------------------------------------------------------

def get_device_points(device_id):
    """Récupère les points configurés dans bacnet_points pour un appareil donné."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bacnet_points WHERE device_id = ?",
            (device_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def save_device_points_selection(device_id, site_id, selected_points):
    """
    Met à jour la sélection des points à suivre pour un appareil BACnet.
    selected_points: list of dict {object_id, name, is_monitored}
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT network_address, device_instance FROM bacnet_devices WHERE id = ?", (device_id,))
        dev_row = cursor.fetchone()
        if not dev_row:
            return
        net_addr = dev_row["network_address"]
        dev_inst = dev_row["device_instance"]
        
        cursor.execute("SELECT * FROM bacnet_points WHERE device_id = ?", (device_id,))
        existing = {r['object_id']: dict(r) for r in cursor.fetchall()}
        
        for p in selected_points:
            obj_id = p.get("object_id") or p.get("obj")
            if not obj_id:
                continue
            name = p.get("name", obj_id)
            is_monitored = 1 if p.get("is_monitored") else 0
            
            if obj_id in existing:
                if not is_monitored:
                    cursor.execute("DELETE FROM bacnet_points WHERE id = ?", (existing[obj_id]["id"],))
                else:
                    cursor.execute(
                        """
                        UPDATE bacnet_points 
                        SET is_monitored = 1, name = ?, network_address = ?, device_instance = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (name, net_addr, dev_inst, existing[obj_id]["id"])
                    )
            else:
                if is_monitored:
                    cursor.execute(
                        """
                        INSERT INTO bacnet_points (site_id, device_id, network_address, device_instance, object_id, name, is_monitored, is_recorded, cadence)
                        VALUES (?, ?, ?, ?, ?, ?, 1, 0, '1m')
                        """,
                        (site_id, device_id, net_addr, dev_inst, obj_id, name)
                    )
        conn.commit()

def get_site_bacnet_points(site_id, only_monitored=False):
    """Récupère les points BACnet d'un chantier avec les infos de l'appareil."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        where_clause = "WHERE p.site_id = ?"
        if only_monitored:
            where_clause += " AND p.is_monitored = 1"
            
        cursor.execute(
            f"""
            SELECT p.*, COALESCE(d.name, 'Appareil BACnet') as device_name,
                   COALESCE(p.network_address, d.network_address) as network_address,
                   COALESCE(p.device_instance, d.device_instance) as device_instance,
                   COALESCE(t.name, 'Générique') as template_name,
                   t.manufacturer as template_manufacturer
            FROM bacnet_points p
            LEFT JOIN bacnet_devices d ON p.device_id = d.id
            LEFT JOIN bacnet_templates t ON d.template_id = t.id
            {where_clause}
            ORDER BY device_name, p.object_id
            """,
            (site_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def update_point_settings(point_id, is_monitored=None, is_recorded=None, cadence=None):
    """Met à jour le statut de suivi/enregistrement et la cadence d'un point BACnet."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bacnet_points WHERE id = ?", (point_id,))
        row = cursor.fetchone()
        if not row:
            return False
            
        cur_mon = row["is_monitored"] if is_monitored is None else (1 if is_monitored else 0)
        cur_rec = row["is_recorded"] if is_recorded is None else (1 if is_recorded else 0)
        cur_cad = row["cadence"] if cadence is None else cadence
        
        if not cur_mon:
            cursor.execute("DELETE FROM bacnet_points WHERE id = ?", (point_id,))
        else:
            cursor.execute(
                """
                UPDATE bacnet_points
                SET is_monitored = 1, is_recorded = ?, cadence = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (cur_rec, cur_cad, point_id)
            )
        conn.commit()
        return True

def delete_bacnet_point(point_id):
    """Supprime un point de bacnet_points."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bacnet_points WHERE id = ?", (point_id,))
        conn.commit()
        return True

def read_site_monitored_points_live(site_id):
    """
    Lit tous les points BACnet suivis (is_monitored=1) pour un chantier et renvoie un dict:
    {point_id: {"value": val, "display": display_val, "error": err, "ts": ts}}
    """
    import time
    import queue
    import paho.mqtt.client as mqtt
    from services.mqtt_service import mqtt_client
    
    points = get_site_bacnet_points(site_id, only_monitored=True)
    if not points:
        return {}
        
    results = {}
    now = int(time.time())
    
    req_points = [
        {"device_id": p["device_instance"], "address": p["network_address"], "object_id": p["object_id"]}
        for p in points
    ]
    
    job_id = str(uuid.uuid4())
    res_queue = queue.Queue()

    def on_msg(client, userdata, msg):
        try:
            res_queue.put(json.loads(msg.payload.decode('utf-8')))
        except Exception:
            pass

    read_results = []
    try:
        try:
            temp_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            temp_client = mqtt.Client()
        temp_client.on_message = on_msg
        temp_client.connect("127.0.0.1", 1883, 60)
        temp_client.loop_start()
        temp_client.subscribe(f"rpinode/bacnet/res/read/{job_id}")

        mqtt_client.publish("rpinode/bacnet/cmd/read", {"job_id": job_id, "points": req_points})

        try:
            read_results = res_queue.get(timeout=4.0)
        except queue.Empty:
            read_results = []
        finally:
            temp_client.loop_stop()
            temp_client.disconnect()
    except Exception as e:
        logger.debug(f"Erreur communication MQTT pour live BACnet: {e}")
        read_results = []

    values_map = {}
    for r in (read_results or []):
        if isinstance(r, dict):
            key = (r.get("address"), r.get("object_id"))
            values_map[key] = r

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for p in points:
            pid = p["id"]
            key = (p["network_address"], p["object_id"])
            item = values_map.get(key)
            
            if item and item.get("value") is not None:
                val = item["value"]
                results[str(pid)] = {
                    "value": val,
                    "display": str(val),
                    "error": None,
                    "ts": now
                }
                cursor.execute(
                    "UPDATE bacnet_points SET last_value = ?, last_read_ts = ? WHERE id = ?",
                    (str(val), now, pid)
                )
            elif item and item.get("error"):
                if p.get("last_value") is not None:
                    results[str(pid)] = {
                        "value": p["last_value"],
                        "display": str(p["last_value"]),
                        "error": None,
                        "retained": True,
                        "ts": p.get("last_read_ts") or now
                    }
                else:
                    results[str(pid)] = {
                        "value": None,
                        "display": "—",
                        "error": item["error"],
                        "ts": now
                    }
            else:
                if p.get("last_value") is not None:
                    results[str(pid)] = {
                        "value": p["last_value"],
                        "display": str(p["last_value"]),
                        "error": None,
                        "retained": True,
                        "ts": p.get("last_read_ts") or now
                    }
                else:
                    results[str(pid)] = {
                        "value": None,
                        "display": "—",
                        "error": "Aucune réponse",
                        "ts": now
                    }
        conn.commit()
        
    return results

def get_site_monitored_cached_values(site_id):
    """
    Renvoie les dernières valeurs connues des points BACnet suivis depuis la base locale.
    """
    import time
    points = get_site_bacnet_points(site_id, only_monitored=True)
    results = {}
    now = int(time.time())
    for p in points:
        pid = p["id"]
        val_str = p.get("last_value")
        if val_str is not None:
            results[str(pid)] = {
                "value": val_str,
                "display": str(val_str),
                "error": None,
                "ts": p.get("last_read_ts") or now
            }
        else:
            results[str(pid)] = {
                "value": None,
                "display": "—",
                "error": None,
                "ts": now
            }
    return results
