import json
import logging
import struct
import time

from core.database import get_db_connection
from services.modbus_tools import read_registers, read_bits, ModbusError

logger = logging.getLogger(__name__)

def get_all_templates(include_hidden=False):
    """Récupère tous les templates Modbus disponibles."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        where = "" if include_hidden else "WHERE is_local_hidden = 0"
        cursor.execute(f"SELECT * FROM modbus_templates {where} ORDER BY name")
        return [dict(row) for row in cursor.fetchall()]

def get_template(template_id):
    """Récupère un template spécifique."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM modbus_templates WHERE id = ?", (template_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def save_template(name, manufacturer, registers, template_id=None, is_shared=None, external_id=None):
    """Crée ou met à jour un template Modbus, gère le versioning immuable et synchronise les points des appareils liés."""
    import uuid
    import socket
    registers_json = json.dumps(registers, ensure_ascii=False)
    hostname = socket.gethostname()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if template_id:
            cursor.execute("SELECT * FROM modbus_templates WHERE id = ?", (template_id,))
            existing = cursor.fetchone()
            if existing:
                existing = dict(existing)
                t_uuid = existing.get("template_uuid") or str(uuid.uuid5(uuid.NAMESPACE_DNS, f"template-{name}"))
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
                    UPDATE modbus_templates 
                    SET name = ?, manufacturer = ?, registers_json = ?,
                        template_uuid = ?, revision_uuid = ?, parent_revision_uuid = ?,
                        version = ?, is_shared = ?, is_local_hidden = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (name, manufacturer, registers_json, t_uuid, new_rev_uuid, parent_rev, new_version, shared_val, template_id)
                )
            else:
                template_id = None

        if not template_id:
            t_uuid = str(uuid.uuid4())
            rev_uuid = str(uuid.uuid4())
            shared_val = 1 if is_shared else 0
            cursor.execute(
                """
                INSERT INTO modbus_templates 
                (template_uuid, revision_uuid, parent_revision_uuid, name, manufacturer, version, is_shared, is_local_hidden, created_by_node, registers_json)
                VALUES (?, ?, NULL, ?, ?, 1, ?, 0, ?, ?)
                """,
                (t_uuid, rev_uuid, name, manufacturer, shared_val, hostname, registers_json)
            )
            template_id = cursor.lastrowid
            
        # Synchroniser les points pour tous les appareils utilisant ce template
        cursor.execute("SELECT id, site_id FROM modbus_devices WHERE template_id = ?", (template_id,))
        devices = cursor.fetchall()
        
        tpl_map = {f"{r.get('function', 3)}:{int(r['reg'])}": r for r in registers}
        
        for dev in devices:
            dev_id = dev["id"]
            cursor.execute("SELECT * FROM modbus_points WHERE device_id = ?", (dev_id,))
            current_points = [dict(p) for p in cursor.fetchall()]
            
            for p in current_points:
                pkey = f"{p['function']}:{p['reg']}"
                if pkey in tpl_map:
                    tr = tpl_map[pkey]
                    cursor.execute(
                        """
                        UPDATE modbus_points
                        SET name = ?, type = ?, scale = ?, unit = ?, base = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            tr.get("name", p["name"]),
                            tr.get("type", p["type"]),
                            float(tr.get("scale", 1.0) or 1.0),
                            tr.get("unit", ""),
                            int(tr.get("base", 0)),
                            p["id"]
                        )
                    )
                else:
                    if not p.get("is_recorded"):
                        cursor.execute("DELETE FROM modbus_points WHERE id = ?", (p["id"],))
        conn.commit()

    # Si le template est partagé, on notifie la flotte immédiatement si possible
    tpl = get_template(template_id)
    if tpl and tpl.get("is_shared") == 1:
        try:
            from services.fleet import fleet
            if fleet.is_registered():
                payload_def = format_local_template_for_fleet(tpl)
                fleet.sync_modbus_templates({tpl["template_uuid"] or tpl["name"]: payload_def})
        except Exception as e:
            logger.debug(f"Notification flotte ignorée : {e}")

    return template_id

def delete_template(template_id):
    """
    Supprime ou masque un template Modbus localement s'il n'est utilisé par aucun appareil.
    Si partagé avec la flotte, marque is_local_hidden = 1 pour éviter sa réinjection automatique.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) as cnt FROM modbus_devices WHERE template_id = ?", (template_id,))
        row = cursor.fetchone()
        if row and row["cnt"] > 0:
            return False, f"Impossible de supprimer ce template : il est utilisé par {row['cnt']} appareil(s)."
        
        cursor.execute("SELECT is_shared FROM modbus_templates WHERE id = ?", (template_id,))
        tpl = cursor.fetchone()
        if tpl and tpl["is_shared"] == 1:
            cursor.execute("UPDATE modbus_templates SET is_local_hidden = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (template_id,))
        else:
            cursor.execute("DELETE FROM modbus_templates WHERE id = ?", (template_id,))
        conn.commit()
        return True, None

def normalize_fleet_definition_to_local(name, definition):
    """Convertit la définition JSON reçue de docs vers le format de template local rpinode."""
    manufacturer = definition.get("notes") or definition.get("manufacturer") or ""
    reads = definition.get("reads", [])
    registers = []
    
    type_map = {
        "u16": "uint16", "uint16": "uint16",
        "i16": "int16", "int16": "int16",
        "f32": "float32", "float32": "float32",
        "u32": "uint32", "uint32": "uint32",
        "i32": "int32", "int32": "int32"
    }
    
    base = definition.get("base", 0)
    for r in reads:
        reg_addr = r.get("address") if r.get("address") is not None else r.get("reg", 0)
        func = r.get("function", 3)
        lbl = r.get("label") or r.get("name") or f"Reg {reg_addr}"
        raw_type = str(r.get("type", "uint16")).lower()
        t = type_map.get(raw_type, "uint16")
        scale = r.get("scale")
        scale_val = float(scale) if scale is not None else 1.0
        unit = r.get("unit") or ""
        
        registers.append({
            "reg": int(reg_addr),
            "function": int(func),
            "base": int(base),
            "name": lbl,
            "type": t,
            "scale": scale_val,
            "unit": unit
        })
        
    return {
        "name": name,
        "manufacturer": manufacturer,
        "registers": registers
    }

def format_local_template_for_fleet(tpl):
    """Convertit un template local rpinode vers le format versionné attendu par docs."""
    try:
        registers = json.loads(tpl.get("registers_json", "[]"))
    except Exception:
        registers = []
        
    reads = []
    for r in registers:
        t = r.get("type", "uint16")
        type_str = "u16" if t == "uint16" else ("i16" if t == "int16" else ("f32" if t == "float32" else ("i32" if t == "int32" else ("u32" if t == "uint32" else "u16"))))
        reads.append({
            "label": r.get("name", ""),
            "function": int(r.get("function", 3)),
            "address": int(r.get("reg", 0)),
            "type": type_str,
            "scale": float(r.get("scale", 1.0)),
            "unit": r.get("unit", "")
        })
        
    base = registers[0].get("base", 0) if registers else 0
    definition = {
        "name": tpl["name"],
        "notes": tpl.get("manufacturer", "") or "",
        "port": 502,
        "unit": 1,
        "base": base,
        "reads": reads,
        "commands": []
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
        "port": 502,
        "unit": 1,
        "base": base,
        "reads": reads,
        "commands": []
    }

def import_template_from_fleet(template_name_or_uuid):
    """Télécharge un template depuis la flotte docs et l'installe/met à jour en local."""
    from services.fleet import fleet
    remote_templates = fleet.get_remote_templates()
    
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
        cursor.execute("SELECT id FROM modbus_templates WHERE template_uuid = ? OR name = ?", (tpl_uuid, norm["name"]))
        existing = cursor.fetchone()
        local_id = existing["id"] if existing else None
        
        registers_json = json.dumps(norm["registers"], ensure_ascii=False)
        
        if local_id:
            cursor.execute("""
                UPDATE modbus_templates
                SET name = ?, manufacturer = ?, registers_json = ?,
                    template_uuid = ?, revision_uuid = ?, parent_revision_uuid = ?,
                    version = ?, is_shared = 1, is_local_hidden = 0,
                    created_by_node = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (norm["name"], norm["manufacturer"], registers_json, tpl_uuid, rev_uuid, parent_rev_uuid, version, created_by_node, local_id))
        else:
            cursor.execute("""
                INSERT INTO modbus_templates
                (template_uuid, revision_uuid, parent_revision_uuid, name, manufacturer, version, is_shared, is_local_hidden, created_by_node, registers_json)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
            """, (tpl_uuid, rev_uuid, parent_rev_uuid, norm["name"], norm["manufacturer"], version, created_by_node, registers_json))
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
        conn.execute("UPDATE modbus_templates SET is_shared = 1 WHERE id = ?", (template_id,))
        conn.commit()

    tpl = get_template(template_id)
    payload_def = format_local_template_for_fleet(tpl)
    success = fleet.sync_modbus_templates({tpl["template_uuid"] or tpl["name"]: payload_def})
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
    remote_templates = fleet.get_remote_templates() if fleet.is_registered() else {}
    
    fleet_list = []
    for name, definition in remote_templates.items():
        t_uuid = definition.get("template_uuid")
        local_match = local_uuid_map.get(t_uuid) or local_name_map.get(name)
        is_installed = local_match is not None
        local_id = local_match["id"] if local_match else None
        local_version = local_match.get("version") if local_match else None
        fleet_version = definition.get("version", 1)

        reads = definition.get("reads", [])
        fleet_list.append({
            "name": name,
            "template_uuid": t_uuid,
            "revision_uuid": definition.get("revision_uuid"),
            "notes": definition.get("notes") or definition.get("manufacturer") or "—",
            "version": fleet_version,
            "local_version": local_version,
            "needs_update": is_installed and local_version is not None and local_version < fleet_version,
            "reads_count": len(reads),
            "created_by": definition.get("created_by_node") or definition.get("created_by") or "Flotte",
            "is_installed": is_installed,
            "local_id": local_id
        })
        
    return local_templates, fleet_list

def get_site_devices(site_id):
    """Récupère les appareils Modbus configurés pour un chantier."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.*, t.name as template_name, t.manufacturer as template_manufacturer
            FROM modbus_devices d
            JOIN modbus_templates t ON d.template_id = t.id
            WHERE d.site_id = ?
            """,
            (site_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def add_device_to_site(site_id, template_id, name, protocol, address, port=502, slave_unit=1):
    """Ajoute un appareil Modbus à un chantier."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO modbus_devices (site_id, template_id, name, protocol, address, port, slave_unit)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (site_id, template_id, name, protocol, address, port, int(slave_unit or 1))
        )
        conn.commit()
        return cursor.lastrowid

def delete_device_from_site(device_id):
    """Supprime un appareil Modbus et ses points associés."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM modbus_points WHERE device_id = ?", (device_id,))
        cursor.execute("DELETE FROM modbus_devices WHERE id = ?", (device_id,))
        conn.commit()
        return True

# ---------------------------------------------------------------------------
# Gestion des Points Suivis / Enregistrés
# ---------------------------------------------------------------------------

def get_device_points(device_id):
    """Récupère les points configurés dans modbus_points pour un appareil donné."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM modbus_points WHERE device_id = ?",
            (device_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def save_device_points_selection(device_id, site_id, selected_points):
    """
    Met à jour la sélection des points à suivre pour un appareil.
    selected_points: list of dict {reg, function, name, type, scale, unit, base, is_monitored, slave_unit}
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Récupérer les infos de l'appareil
        cursor.execute("SELECT slave_unit FROM modbus_devices WHERE id = ?", (device_id,))
        dev_row = cursor.fetchone()
        dev_slave_unit = dev_row["slave_unit"] if dev_row and dev_row["slave_unit"] is not None else 1
        
        # Récupérer les points existants pour ne pas écraser is_recorded et cadence
        cursor.execute("SELECT * FROM modbus_points WHERE device_id = ?", (device_id,))
        existing = {f"{r['function']}:{r['reg']}": dict(r) for r in cursor.fetchall()}
        
        for p in selected_points:
            reg = int(p["reg"])
            func = int(p.get("function", 3))
            base = int(p.get("base", 0))
            key = f"{func}:{reg}"
            name = p.get("name", f"Reg {reg}")
            type_str = p.get("type", "int16")
            scale = float(p.get("scale", 1.0) or 1.0)
            unit = p.get("unit", "")
            slave_unit = int(p.get("slave_unit") or dev_slave_unit or 1)
            is_monitored = 1 if p.get("is_monitored") else 0
            
            if key in existing:
                # Si le point n'est plus suivi, on le supprime (règle : pas d'enregistrement sans suivi)
                if not is_monitored:
                    cursor.execute("DELETE FROM modbus_points WHERE id = ?", (existing[key]["id"],))
                else:
                    cursor.execute(
                        """
                        UPDATE modbus_points 
                        SET is_monitored = 1, name = ?, type = ?, scale = ?, unit = ?, base = ?, slave_unit = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (name, type_str, scale, unit, base, slave_unit, existing[key]["id"])
                    )
            else:
                if is_monitored:
                    cursor.execute(
                        """
                        INSERT INTO modbus_points (site_id, device_id, reg, function, base, slave_unit, name, type, scale, unit, is_monitored, is_recorded, cadence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, '1m')
                        """,
                        (site_id, device_id, reg, func, base, slave_unit, name, type_str, scale, unit)
                    )
        conn.commit()

def get_site_modbus_points(site_id, only_monitored=False):
    """Récupère les points Modbus d'un chantier avec les infos de l'appareil."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        where_clause = "WHERE p.site_id = ?"
        if only_monitored:
            where_clause += " AND p.is_monitored = 1"
            
        cursor.execute(
            f"""
            SELECT p.*, d.name as device_name, d.protocol, d.address, d.port,
                   COALESCE(p.slave_unit, d.slave_unit, 1) as slave_unit,
                   t.name as template_name
            FROM modbus_points p
            JOIN modbus_devices d ON p.device_id = d.id
            JOIN modbus_templates t ON d.template_id = t.id
            {where_clause}
            ORDER BY d.name, p.function, p.reg
            """,
            (site_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def update_point_settings(point_id, is_monitored=None, is_recorded=None, cadence=None):
    """Met à jour le statut de suivi/enregistrement et la cadence d'un point."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM modbus_points WHERE id = ?", (point_id,))
        row = cursor.fetchone()
        if not row:
            return False
            
        cur_mon = row["is_monitored"] if is_monitored is None else (1 if is_monitored else 0)
        cur_rec = row["is_recorded"] if is_recorded is None else (1 if is_recorded else 0)
        cur_cad = row["cadence"] if cadence is None else cadence
        
        # Règle stricte : si un point n'est pas suivi, il ne peut pas être enregistré
        if not cur_mon:
            cursor.execute("DELETE FROM modbus_points WHERE id = ?", (point_id,))
        else:
            cursor.execute(
                """
                UPDATE modbus_points
                SET is_monitored = 1, is_recorded = ?, cadence = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (cur_rec, cur_cad, point_id)
            )
        conn.commit()
        return True

def delete_modbus_point(point_id):
    """Supprime un point de modbus_points."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM modbus_points WHERE id = ?", (point_id,))
        conn.commit()
        return True

def read_point_value(protocol, address, port, unit, function, reg, type_str="int16", scale=1.0, timeout=1.5, base=0):
    """Lit et décode la valeur d'un registre Modbus en tenant compte de la base (0 ou 1)."""
    func = int(function)
    scale = float(scale or 1.0)
    type_str = str(type_str or "int16").lower()
    wire_reg = int(reg) - (1 if int(base or 0) == 1 else 0)
    
    if func in (1, 2):
        bits = read_bits(protocol, address, port, unit, func, wire_reg, 1, timeout=timeout)
        val = 1 if bits[0] else 0
        return "1" if val else "0", ("ON" if val else "OFF")
        
    count = 2 if type_str in ("float32", "int32", "uint32", "f32", "i32", "u32", "u32s", "f32s", "i32s") else 1
    vals = read_registers(protocol, address, port, unit, func, wire_reg, count, timeout=timeout)
    
    if count == 1:
        v = vals[0]
        if type_str in ("int16", "i16") and v > 32767:
            v -= 65536
        num = v * scale
    else:
        packed = struct.pack(">HH", vals[0], vals[1])
        if type_str in ("float32", "f32"):
            num = struct.unpack(">f", packed)[0] * scale
        elif type_str in ("int32", "i32"):
            num = struct.unpack(">i", packed)[0] * scale
        else:
            num = struct.unpack(">I", packed)[0] * scale
            
    num = round(num, 3)
    if isinstance(num, float) and num.is_integer():
        num = int(num)
    val_str = str(num)
    return val_str, val_str

def read_site_monitored_points_live(site_id):
    """
    Lit tous les points suivis (is_monitored=1) pour un chantier et renvoie un dict:
    {point_id: {"value": val, "display": display_val, "error": err, "updated_at": ts}}
    """
    points = get_site_modbus_points(site_id, only_monitored=True)
    results = {}
    now = int(time.time())
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for p in points:
            pid = p["id"]
            if not p.get("is_monitored"):
                continue
                
            protocol = p["protocol"]
            address = p["address"]
            port = p["port"] or 502
            unit = p.get("slave_unit") or (int(address) if protocol == "mstp" else 1)
            func = p["function"]
            reg = p["reg"]
            base = p.get("base", 0)
            type_str = p["type"]
            scale = p["scale"]
            unit_str = f" {p['unit']}" if p["unit"] else ""
            
            try:
                raw_val, disp_val = read_point_value(protocol, address, port, unit, func, reg, type_str, scale, base=base, timeout=0.4)
                full_display = f"{disp_val}{unit_str}"
                results[str(pid)] = {
                    "value": raw_val,
                    "display": full_display,
                    "error": None,
                    "ts": now
                }
                cursor.execute(
                    "UPDATE modbus_points SET last_value = ?, last_read_ts = ? WHERE id = ?",
                    (raw_val, now, pid)
                )
            except ModbusError as e:
                # Si une valeur précédente existe, on la conserve pour éviter les micro-coupures d'affichage
                if p.get("last_value") is not None:
                    results[str(pid)] = {
                        "value": p["last_value"],
                        "display": f"{p['last_value']}{unit_str}",
                        "error": None,
                        "retained": True,
                        "ts": p.get("last_read_ts") or now
                    }
                else:
                    results[str(pid)] = {
                        "value": None,
                        "display": "—",
                        "error": str(e),
                        "ts": now
                    }
            except Exception as e:
                if p.get("last_value") is not None:
                    results[str(pid)] = {
                        "value": p["last_value"],
                        "display": f"{p['last_value']}{unit_str}",
                        "error": None,
                        "retained": True,
                        "ts": p.get("last_read_ts") or now
                    }
                else:
                    results[str(pid)] = {
                        "value": None,
                        "display": "—",
                        "error": str(e),
                        "ts": now
                    }
        conn.commit()
        
    return results

def get_site_monitored_cached_values(site_id):
    """
    Renvoie les dernières valeurs connues des points suivis depuis la base locale (instantané sans solliciter le bus).
    """
    points = get_site_modbus_points(site_id, only_monitored=True)
    results = {}
    now = int(time.time())
    for p in points:
        pid = p["id"]
        val_str = p.get("last_value")
        unit_str = f" {p['unit']}" if p.get("unit") else ""
        if val_str is not None:
            results[str(pid)] = {
                "value": val_str,
                "display": f"{val_str}{unit_str}",
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
