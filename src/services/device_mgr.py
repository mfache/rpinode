import html
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from core.database import get_db_connection

logger = logging.getLogger(__name__)

SERIAL_BY_ID_DIR = "/dev/serial/by-id"

MODEM_DRIVERS = {"option", "qmi_wwan", "cdc_mbim", "cdc_wdm", "cdc_ncm", "qcserial"}
SERIAL_DRIVERS = {
    "ti_usb_3410_5052": "Moxa UPort (RS-232/422/485)",
    "ftdi_sio": "FTDI USB-Série",
    "cp210x": "Silicon Labs CP210x USB-Série",
    "ch341": "CH340/CH341 USB-Série",
    "pl2303": "Prolific PL2303 USB-Série",
    "cdc_acm": "USB CDC-ACM Port Série",
    "cypress_m8": "Cypress M8 USB-Série",
}

PHYSICAL_TYPES = {
    "rs485": "Bus RS-485 (Différentiel 2 fils)",
    "mbus": "Maître M-Bus (Comptage Énergie)",
    "rs232": "Port Série RS-232",
    "modem_4g_gps": "Modem 4G / GPS",
    "generic_serial": "Port Série Générique",
}

CAPABILITY_LABELS = {
    "bacnet_mstp": "BACnet MS/TP",
    "modbus_rtu": "Modbus RTU",
    "mbus": "M-Bus",
    "gsm_modem": "Modem 4G",
    "gps_nmea": "GPS NMEA",
}

_LSUSB_RE = re.compile(r"^Bus (\d+) Device (\d+): ID ([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s*(.*)$")
_TREE_BUS_RE = re.compile(r"^/:\s+Bus (\d+)")
_TREE_DEV_RE = re.compile(r"Dev (\d+),.*?Driver=([^,]*)")

# -----------------------------------------------------------------------------
# Base de données : Qualifications des Périphériques
# -----------------------------------------------------------------------------

def get_device_qualification(hardware_key: str) -> dict | None:
    """Récupère la qualification enregistrée pour une clé matérielle donnée."""
    if not hardware_key:
        return None
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, hardware_key, vendor_id, product_id, serial_number, by_id_name,
                       user_label, physical_type, capabilities_json, notes, is_dirty, synced_at,
                       is_shared_model, created_at, updated_at
                FROM device_qualifications
                WHERE hardware_key = ? OR by_id_name = ?
            """, (hardware_key, hardware_key))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                try:
                    d["capabilities"] = json.loads(d["capabilities_json"])
                except Exception:
                    d["capabilities"] = []
                return d
    except Exception as e:
        logger.warning(f"Erreur get_device_qualification({hardware_key}): {e}")
    return None

def get_all_qualifications() -> dict[str, dict]:
    """Retourne toutes les qualifications indexées par hardware_key et by_id_name."""
    qualifs = {}
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, hardware_key, vendor_id, product_id, serial_number, by_id_name,
                       user_label, physical_type, capabilities_json, notes, is_dirty, synced_at,
                       is_shared_model, created_at, updated_at
                FROM device_qualifications
            """)
            for row in cursor.fetchall():
                d = dict(row)
                try:
                    d["capabilities"] = json.loads(d["capabilities_json"])
                except Exception:
                    d["capabilities"] = []
                if d.get("hardware_key"):
                    qualifs[str(d["hardware_key"])] = d
                if d.get("by_id_name"):
                    qualifs[str(d["by_id_name"])] = d
                if d.get("vendor_id") and d.get("product_id"):
                    qualifs[f"{d['vendor_id']}:{d['product_id']}"] = d
    except Exception as e:
        logger.warning(f"Erreur get_all_qualifications: {e}")
    return qualifs

def save_device_qualification(
    hardware_key: str,
    user_label: str,
    physical_type: str,
    capabilities: list[str],
    notes: str = "",
    vendor_id: str = "",
    product_id: str = "",
    serial_number: str = "",
    by_id_name: str = "",
    is_shared_model: bool = False
) -> dict:
    """
    Enregistre ou met à jour la qualification d'un périphérique.
    Positionne automatiquement is_dirty = 1 et updated_at = CURRENT_TIMESTAMP.
    """
    if not hardware_key:
        raise ValueError("hardware_key obligatoire")
    if not user_label:
        user_label = "Périphérique Série"
    if physical_type not in PHYSICAL_TYPES:
        physical_type = "generic_serial"

    caps_json = json.dumps(list(set(capabilities)))

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO device_qualifications (
                hardware_key, vendor_id, product_id, serial_number, by_id_name,
                user_label, physical_type, capabilities_json, notes,
                is_dirty, is_shared_model, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(hardware_key) DO UPDATE SET
                user_label = EXCLUDED.user_label,
                physical_type = EXCLUDED.physical_type,
                capabilities_json = EXCLUDED.capabilities_json,
                notes = EXCLUDED.notes,
                vendor_id = CASE WHEN EXCLUDED.vendor_id != '' THEN EXCLUDED.vendor_id ELSE device_qualifications.vendor_id END,
                product_id = CASE WHEN EXCLUDED.product_id != '' THEN EXCLUDED.product_id ELSE device_qualifications.product_id END,
                serial_number = CASE WHEN EXCLUDED.serial_number != '' THEN EXCLUDED.serial_number ELSE device_qualifications.serial_number END,
                by_id_name = CASE WHEN EXCLUDED.by_id_name != '' THEN EXCLUDED.by_id_name ELSE device_qualifications.by_id_name END,
                is_dirty = 1,
                is_shared_model = EXCLUDED.is_shared_model,
                updated_at = CURRENT_TIMESTAMP
        """, (
            hardware_key,
            vendor_id.lower() if vendor_id else "",
            product_id.lower() if product_id else "",
            serial_number,
            by_id_name,
            user_label.strip(),
            physical_type,
            caps_json,
            notes.strip(),
            1 if is_shared_model else 0
        ))
        conn.commit()

    return get_device_qualification(hardware_key) or {}

def delete_device_qualification(hardware_key: str) -> bool:
    """Supprime une qualification manuelle pour revenir au profil auto-détecté."""
    try:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM device_qualifications WHERE hardware_key = ?", (hardware_key,))
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"Erreur delete_device_qualification({hardware_key}): {e}")
        return False

def get_dirty_qualifications() -> list[dict]:
    """Retourne la liste des qualifications modifiées localement en attente de synchronisation."""
    dirty = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT hardware_key, vendor_id, product_id, serial_number, by_id_name,
                       user_label, physical_type, capabilities_json, notes, is_dirty,
                       synced_at, is_shared_model, created_at, updated_at
                FROM device_qualifications
                WHERE is_dirty = 1
            """)
            for row in cursor.fetchall():
                d = dict(row)
                try:
                    d["capabilities"] = json.loads(d["capabilities_json"])
                except Exception:
                    d["capabilities"] = []
                dirty.append(d)
    except Exception as e:
        logger.warning(f"Erreur get_dirty_qualifications: {e}")
    return dirty

def mark_qualifications_synced(hardware_keys: list[str] | None = None) -> int:
    """Marque les qualifications spécifiées (ou toutes les dirty) comme synchronisées."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if hardware_keys is not None and len(hardware_keys) > 0:
                placeholders = ",".join(["?"] * len(hardware_keys))
                cursor.execute(f"""
                    UPDATE device_qualifications
                    SET is_dirty = 0, synced_at = CURRENT_TIMESTAMP
                    WHERE hardware_key IN ({placeholders})
                """, tuple(hardware_keys))
            else:
                cursor.execute("""
                    UPDATE device_qualifications
                    SET is_dirty = 0, synced_at = CURRENT_TIMESTAMP
                    WHERE is_dirty = 1
                """)
            conn.commit()
            return cursor.rowcount
    except Exception as e:
        logger.warning(f"Erreur mark_qualifications_synced: {e}")
        return 0

# -----------------------------------------------------------------------------
# Détection Système & Pilotes
# -----------------------------------------------------------------------------

def is_moxa_driver_installed() -> bool:
    """Vérifie si le module noyau ti_usb_3410_5052 est chargé ou présent."""
    try:
        with open("/proc/modules", "r") as f:
            content = f.read()
            if "ti_usb_3410_5052" in content:
                return True
    except Exception:
        pass
    return False

def get_moxa_driver_info() -> dict:
    """Retourne les informations sur le pilote Moxa personnalisé."""
    installed = is_moxa_driver_installed()
    ko_path = ""
    try:
        kver = os.uname().release
        potential_ko = Path(f"/lib/modules/{kver}/kernel/drivers/usb/serial/ti_usb_3410_5052.ko")
        if potential_ko.exists():
            ko_path = str(potential_ko)
    except Exception:
        pass

    return {
        "installed": installed,
        "module_name": "ti_usb_3410_5052",
        "custom_rs485": True,
        "ko_path": ko_path,
        "description": "Pilote noyau personnalisé pour forcer le Moxa UPort 1150 en mode RS-485 2 fils."
    }

def _get_tty_driver(tty_name: str) -> str:
    """Trouve le pilote noyau lié à un tty (ex: ttyUSB5)."""
    try:
        dev_path = Path(f"/sys/class/tty/{tty_name}/device/driver")
        if dev_path.exists():
            drv = dev_path.resolve().name
            for base in list(SERIAL_DRIVERS.keys()) + list(MODEM_DRIVERS):
                if drv.startswith(base):
                    return base
            return drv
    except Exception:
        pass
    return ""

def list_serial_ports(include_modems: bool = False, filter_capability: str | None = None) -> list:
    """
    Liste les ports série disponibles sur le système en intégrant les qualifications enregistrées.
    Résolution stable par /dev/serial/by-id.
    """
    ports = []
    seen_paths = set()
    qualifs = get_all_qualifications()

    # 1. Parcourir /dev/serial/by-id
    if os.path.isdir(SERIAL_BY_ID_DIR):
        try:
            for name in sorted(os.listdir(SERIAL_BY_ID_DIR)):
                full_by_id = os.path.join(SERIAL_BY_ID_DIR, name)
                real_path = os.path.realpath(full_by_id)
                tty_name = os.path.basename(real_path)
                driver = _get_tty_driver(tty_name)

                is_modem_driver = driver in MODEM_DRIVERS or "SimTech" in name or "Quectel" in name
                is_moxa_driver = "Moxa" in name or driver == "ti_usb_3410_5052" or "110a:1150" in name or "UPort" in name

                hw_key = name
                q = qualifs.get(hw_key) or qualifs.get(name)

                if q:
                    physical_type = str(q["physical_type"])
                    user_label = str(q["user_label"])
                    capabilities = list(q.get("capabilities", []))
                    notes = str(q.get("notes", "") or "")
                    is_dirty = bool(q.get("is_dirty", 0))
                    synced_at = q.get("synced_at")
                    is_moxa = is_moxa_driver or (physical_type == "rs485" and "moxa" in user_label.lower())
                    is_modem = physical_type == "modem_4g_gps" or "gsm_modem" in capabilities
                    is_rs485 = physical_type == "rs485" or "bacnet_mstp" in capabilities or "modbus_rtu" in capabilities
                    is_qualified = True
                else:
                    physical_type = "modem_4g_gps" if is_modem_driver else ("rs485" if is_moxa_driver else "generic_serial")
                    if is_moxa_driver:
                        user_label = "Moxa UPort 1150 (RS-485)"
                    elif driver in SERIAL_DRIVERS:
                        user_label = f"{SERIAL_DRIVERS[driver]} ({name})"
                    elif is_modem_driver:
                        user_label = f"Modem 4G / GPS ({name})"
                    else:
                        user_label = name

                    capabilities = []
                    if is_moxa_driver or driver in SERIAL_DRIVERS:
                        capabilities.extend(["bacnet_mstp", "modbus_rtu", "mbus"])
                    if is_modem_driver:
                        capabilities.extend(["gsm_modem", "gps_nmea"])

                    notes = ""
                    is_dirty = False
                    synced_at = None
                    is_moxa = is_moxa_driver
                    is_modem = is_modem_driver
                    is_rs485 = is_moxa_driver or (driver in SERIAL_DRIVERS and not is_modem)
                    is_qualified = False

                if is_modem and not include_modems:
                    continue

                if filter_capability and filter_capability not in capabilities:
                    continue

                ports.append({
                    "path": real_path,
                    "by_id": full_by_id,
                    "by_id_name": name,
                    "hardware_key": hw_key,
                    "tty_name": tty_name,
                    "driver": driver,
                    "driver_label": SERIAL_DRIVERS.get(driver, driver or "Inconnu"),
                    "description": user_label,
                    "user_label": user_label,
                    "physical_type": physical_type,
                    "physical_type_label": PHYSICAL_TYPES.get(physical_type, physical_type),
                    "is_qualified": is_qualified,
                    "is_dirty": is_dirty,
                    "synced_at": synced_at,
                    "notes": notes,
                    "is_moxa": is_moxa,
                    "is_modem": is_modem,
                    "is_rs485": is_rs485,
                    "capabilities": capabilities,
                })
                seen_paths.add(real_path)
        except Exception as e:
            logger.warning(f"Erreur lecture {SERIAL_BY_ID_DIR}: {e}")

    # 2. Chercher les ttyUSB/ttyACM non listés dans by-id
    try:
        for tty_dir in Path("/sys/class/tty").glob("ttyUSB*"):
            tty_name = tty_dir.name
            real_path = f"/dev/{tty_name}"
            if real_path not in seen_paths and os.path.exists(real_path):
                driver = _get_tty_driver(tty_name)
                is_modem_driver = driver in MODEM_DRIVERS
                is_moxa_driver = driver == "ti_usb_3410_5052"

                hw_key = tty_name
                q = qualifs.get(hw_key) or qualifs.get(real_path)

                if q:
                    physical_type = str(q["physical_type"])
                    user_label = str(q["user_label"])
                    capabilities = list(q.get("capabilities", []))
                    notes = str(q.get("notes", "") or "")
                    is_dirty = bool(q.get("is_dirty", 0))
                    synced_at = q.get("synced_at")
                    is_moxa = is_moxa_driver or (physical_type == "rs485" and "moxa" in user_label.lower())
                    is_modem = physical_type == "modem_4g_gps" or "gsm_modem" in capabilities
                    is_rs485 = physical_type == "rs485" or "bacnet_mstp" in capabilities or "modbus_rtu" in capabilities
                    is_qualified = True
                else:
                    physical_type = "modem_4g_gps" if is_modem_driver else ("rs485" if is_moxa_driver else "generic_serial")
                    user_label = "Moxa UPort (RS-485)" if is_moxa_driver else f"Port série {tty_name}"
                    capabilities = ["bacnet_mstp", "modbus_rtu", "mbus"] if (is_moxa_driver or driver in SERIAL_DRIVERS) else []
                    if is_modem_driver:
                        capabilities = ["gsm_modem", "gps_nmea"]
                    notes = ""
                    is_dirty = False
                    synced_at = None
                    is_moxa = is_moxa_driver
                    is_modem = is_modem_driver
                    is_rs485 = is_moxa_driver or (driver in SERIAL_DRIVERS and not is_modem)
                    is_qualified = False

                if is_modem and not include_modems:
                    continue

                if filter_capability and filter_capability not in capabilities:
                    continue

                ports.append({
                    "path": real_path,
                    "by_id": real_path,
                    "by_id_name": tty_name,
                    "hardware_key": hw_key,
                    "tty_name": tty_name,
                    "driver": driver,
                    "driver_label": SERIAL_DRIVERS.get(driver, driver or "Inconnu"),
                    "description": user_label,
                    "user_label": user_label,
                    "physical_type": physical_type,
                    "physical_type_label": PHYSICAL_TYPES.get(physical_type, physical_type),
                    "is_qualified": is_qualified,
                    "is_dirty": is_dirty,
                    "synced_at": synced_at,
                    "notes": notes,
                    "is_moxa": is_moxa,
                    "is_modem": is_modem,
                    "is_rs485": is_rs485,
                    "capabilities": capabilities,
                })
                seen_paths.add(real_path)
    except Exception as e:
        logger.warning(f"Erreur parcours sys/class/tty: {e}")

    # Trier : Passerelles qualifiées/Moxa en premier
    ports.sort(key=lambda p: (0 if p["is_moxa"] else (1 if p["is_qualified"] else (3 if p["is_modem"] else 2)), p["path"]))
    return ports

def _lsusb_tree_drivers() -> dict:
    """{(bus, dev): {pilotes...}} via `lsusb -t`."""
    drivers = {}
    try:
        res = subprocess.run(["lsusb", "-t"], capture_output=True, text=True, timeout=3, check=False)
        if res.returncode != 0:
            return drivers
        bus = None
        for line in res.stdout.splitlines():
            bm = _TREE_BUS_RE.match(line)
            if bm:
                bus = str(int(bm.group(1)))
                continue
            dm = _TREE_DEV_RE.search(line)
            if dm and bus is not None:
                dev = str(int(dm.group(1)))
                driver = dm.group(2).strip()
                if driver:
                    drivers.setdefault((bus, dev), set()).add(driver)
    except Exception as e:
        logger.debug(f"Erreur lsusb -t: {e}")
    return drivers

def list_usb_devices(include_root_hubs: bool = False) -> list:
    """Liste tous les périphériques USB connectés avec détails et pilotes."""
    devices = []
    tree_drivers = _lsusb_tree_drivers()

    try:
        res = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=3, check=False)
        if res.returncode != 0:
            return devices

        for line in res.stdout.splitlines():
            m = _LSUSB_RE.match(line.strip())
            if not m:
                continue
            bus_str, dev_str, vid, pid, desc = m.groups()
            bus_int = str(int(bus_str))
            dev_int = str(int(dev_str))
            vid_lower = vid.lower()
            pid_lower = pid.lower()

            is_root_hub = (vid_lower == "1d6b" or "root hub" in desc.lower())
            if not include_root_hubs and is_root_hub:
                continue

            drivers = list(tree_drivers.get((bus_int, dev_int), []))
            driver_str = ", ".join(drivers) if drivers else "Aucun"

            is_moxa = (vid_lower == "110a" and pid_lower in ("1150", "1151", "1130", "1131")) or "ti_usb_3410_5052" in drivers
            is_modem = any(d in MODEM_DRIVERS for d in drivers) or (vid_lower == "1e0e") or "SimTech" in desc
            is_hub = "hub" in desc.lower() or is_root_hub

            category = "Autre"
            if is_moxa:
                category = "Passerelle RS-485 / Série"
            elif is_modem:
                category = "Modem 4G / GPS"
            elif is_hub:
                category = "Hub USB"
            elif any(d in SERIAL_DRIVERS for d in drivers):
                category = "Adaptateur Série"

            devices.append({
                "bus": bus_int,
                "device": dev_int,
                "vid": vid_lower,
                "pid": pid_lower,
                "vendor_id": f"0x{vid_lower}",
                "product_id": f"0x{pid_lower}",
                "description": desc.strip(),
                "drivers": drivers,
                "driver_str": driver_str,
                "category": category,
                "is_moxa": is_moxa,
                "is_modem": is_modem,
                "is_hub": is_hub,
                "is_root_hub": is_root_hub,
            })
    except Exception as e:
        logger.warning(f"Erreur lsusb: {e}")

    return devices

def list_system_devices() -> dict:
    """Rassemble la vue d'ensemble des périphériques matériels pour la page /devices."""
    serial_ports = list_serial_ports(include_modems=True)
    rs485_ports = [p for p in serial_ports if not p["is_modem"]]
    modem_ports = [p for p in serial_ports if p["is_modem"]]
    usb_devs = list_usb_devices()
    moxa_driver_info = get_moxa_driver_info()

    gateways = [p for p in rs485_ports if p.get("is_rs485") or p.get("is_qualified")]
    moxa_device = next((p for p in rs485_ports if p.get("is_moxa")), None)

    return {
        "moxa_connected": moxa_device is not None,
        "moxa_device": moxa_device,
        "gateways": gateways,
        "moxa_driver": moxa_driver_info,
        "rs485_ports": rs485_ports,
        "modem_ports": modem_ports,
        "usb_devices": usb_devs,
        "total_serial": len(rs485_ports),
        "total_usb": len([u for u in usb_devs if not u["is_hub"]]),
        "dirty_count": len(get_dirty_qualifications()),
    }

def render_devices_components(sys_devices: dict, base_url: str = "") -> dict:
    """Génère les fragments HTML pour les périphériques connectés et leur qualification."""
    gateways = sys_devices.get("gateways", [])
    if not gateways and sys_devices.get("rs485_ports"):
        gateways = [p for p in sys_devices.get("rs485_ports", []) if isinstance(p, dict) and (p.get("is_rs485") or p.get("is_qualified"))]
    if not gateways and sys_devices.get("moxa_connected") and isinstance(sys_devices.get("moxa_device"), dict):
        gateways = [sys_devices["moxa_device"]]

    # 1. Encarts Héro pour chaque passerelle / adaptateur bus connecté (Moxa, CP210x, FTDI...)
    cards_html = []
    for gw in gateways:
        if not isinstance(gw, dict):
            continue
        is_moxa = bool(gw.get("is_moxa", False))
        is_qualified = bool(gw.get("is_qualified", False))
        is_dirty = bool(gw.get("is_dirty", False))
        physical_type = str(gw.get("physical_type", "rs485" if is_moxa else "generic_serial") or "generic_serial")
        driver_name = str(gw.get("driver", "") or "")
        driver_label = str(gw.get("driver_label", driver_name) or driver_name)

        if is_moxa:
            driver_badge_text = "Pilote ti_usb_3410_5052 (RS-485 2 fils)"
        elif driver_name:
            driver_badge_text = f"Pilote {driver_name} ({driver_label})"
        else:
            driver_badge_text = "Adaptateur Série"

        path_val = str(gw.get("path", "") or "")
        by_id_val = str(gw.get("by_id_name", path_val) or path_val)
        desc_val = str(gw.get("user_label", gw.get("description", "Passerelle Bus / Série")) or "Passerelle Bus / Série")
        notes_val = str(gw.get("notes", "") or "")

        caps_raw = gw.get("capabilities", [])
        caps_labels: list[str] = [str(CAPABILITY_LABELS.get(str(c), str(c))) for c in caps_raw if c]
        caps_str = ", ".join(caps_labels) if caps_labels else "Non spécifié"

        # Badge Qualification
        if is_qualified:
            qualif_badge = f'<span class="moxa-badge moxa-badge-green">● Qualifié : {html.escape(PHYSICAL_TYPES.get(physical_type, physical_type))}</span>'
        else:
            qualif_badge = '<span class="moxa-badge" style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3);">⚠️ Auto-détecté (À qualifier)</span>'

        # Badge Sync
        if is_dirty:
            sync_badge = '<span class="moxa-badge" style="background: rgba(245, 158, 11, 0.2); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.4);" title="Modifié localement, en attente de synchro avec docs">🟡 Sync en attente</span>'
        elif is_qualified:
            sync_badge = '<span class="moxa-badge" style="background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3);" title="Synchronisé avec le serveur central docs">🟢 Sync docs</span>'
        else:
            sync_badge = ''

        cards_html.append(f"""
        <div class="moxa-hero-card" style="margin-bottom: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 10px;">
                <div>
                    <h3>⚡ {html.escape(desc_val)}</h3>
                    <div class="moxa-badges">
                        {qualif_badge}
                        {sync_badge}
                        <span class="moxa-badge moxa-badge-blue">{html.escape(driver_badge_text)}</span>
                        <span class="moxa-badge moxa-badge-purple">{html.escape(caps_str)}</span>
                    </div>
                </div>
                <button class="btn-secondary btn-sm" onclick="openQualifyModal({html.escape(json.dumps(gw))})" style="background: rgba(255,255,255,0.2); color: white; border: 1px solid rgba(255,255,255,0.3); font-weight: 600; padding: 6px 12px; border-radius: 6px; cursor: pointer;">
                    ✏️ Déclarer / Qualifier
                </button>
            </div>
            <div class="moxa-details-grid">
                <div class="moxa-detail-item">
                    <div class="label">Port TTY / Périphérique</div>
                    <div class="value">{html.escape(path_val)}</div>
                </div>
                <div class="moxa-detail-item">
                    <div class="label">Identifiant persistant (by-id)</div>
                    <div class="value" style="font-size: 0.8rem;">{html.escape(by_id_val)}</div>
                </div>
                <div class="moxa-detail-item">
                    <div class="label">Pilote Noyau</div>
                    <div class="value">{html.escape(driver_name or '—')}</div>
                </div>
                {f'<div class="moxa-detail-item"><div class="label">Notes / Repérage</div><div class="value" style="font-family: inherit; font-size: 0.85rem; font-weight: normal; color: #cbd5e1;">{html.escape(notes_val)}</div></div>' if notes_val else ''}
            </div>
            <div class="moxa-actions">
                {f'<a href="{base_url}/bacnet/tools?tab=mstp&amp;device={html.escape(path_val)}" class="btn-primary" style="text-decoration: none; background: #22c55e; border-color: #16a34a; display: inline-flex; align-items: center; gap: 6px;"><span>🔌</span> Recherche BACnet MS/TP</a>' if 'bacnet_mstp' in caps_raw else ''}
                {f'<a href="{base_url}/modbus/tools?port={html.escape(path_val)}" class="btn-secondary" style="text-decoration: none; background: rgba(255,255,255,0.15); color: white; border-color: rgba(255,255,255,0.3); display: inline-flex; align-items: center; gap: 6px;"><span>🔍</span> Outils Modbus RTU</a>' if 'modbus_rtu' in caps_raw else ''}
            </div>
        </div>
        """)

    moxa_card_html = "\n".join(cards_html)

    # 2. Table des ports série
    serial_ports = sys_devices.get("rs485_ports", []) + sys_devices.get("modem_ports", [])
    if serial_ports:
        serial_rows = ""
        for p in serial_ports:
            badge_color = "#22c55e" if p.get("is_qualified") else ("#3b82f6" if p.get("is_rs485") else "#64748b")
            raw_caps = p.get("capabilities", []) if isinstance(p, dict) else []
            cap_labels: list[str] = [str(CAPABILITY_LABELS.get(str(c), str(c))) for c in raw_caps if c]
            caps = ", ".join(cap_labels) if cap_labels else "Série générique"
            p_json = html.escape(json.dumps(p))
            is_dirty = p.get("is_dirty", False)
            sync_tag = '<span style="font-size: 0.75rem; color: #f59e0b; margin-left: 4px;" title="Modifié, en attente de sync">🟡</span>' if is_dirty else ''
            p_type_label = str(PHYSICAL_TYPES.get(str(p.get("physical_type", "")), str(p.get("physical_type", ""))))

            serial_rows += f"""
            <tr>
                <td><code>{html.escape(str(p.get('path', '')))}</code></td>
                <td><small style="color: #64748b; font-family: monospace;">{html.escape(str(p.get('by_id_name', '—')))}</small></td>
                <td>
                    <strong>{html.escape(str(p.get('user_label', p.get('description', ''))))}</strong>{sync_tag}
                    {f'<br><small style="color: #94a3b8;">{html.escape(p_type_label)}</small>' if p.get("is_qualified") else ''}
                </td>
                <td><code>{html.escape(str(p.get('driver', '—')))}</code></td>
                <td><span style="font-size: 0.85rem; color: {badge_color}; font-weight: bold;">{html.escape(caps)}</span></td>
                <td>
                    <div style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center;">
                        <button class="btn-secondary btn-sm" onclick="openQualifyModal({p_json})" style="padding: 4px 8px; font-size: 0.8rem;">
                            ✏️ Qualifier
                        </button>
                        {f'<a href="{base_url}/bacnet/tools?tab=mstp&amp;device={html.escape(str(p.get("path", "")))}" class="btn-secondary btn-sm" style="text-decoration: none;">BACnet</a>' if 'bacnet_mstp' in raw_caps else ''}
                        {f'<a href="{base_url}/modbus/tools?port={html.escape(str(p.get("path", "")))}" class="btn-secondary btn-sm" style="text-decoration: none;">Modbus</a>' if 'modbus_rtu' in raw_caps else ''}
                    </div>
                </td>
            </tr>
            """
        serial_ports_table_html = f"""
        <div style="overflow-x: auto;">
            <table class="data-table">
                <thead>
                    <tr>
                        <th style="width: 140px;">Port TTY</th>
                        <th>Identifiant (by-id)</th>
                        <th>Nom &amp; Type Déclaré</th>
                        <th style="width: 130px;">Pilote</th>
                        <th style="width: 180px;">Capacités</th>
                        <th style="width: 190px;">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {serial_rows}
                </tbody>
            </table>
        </div>
        """
    else:
        serial_ports_table_html = "<p style='color: #64748b; padding: 15px;'>Aucun port série détecté.</p>"

    # 3. Lignes périphériques USB
    usb_rows = ""
    for u in sys_devices.get("usb_devices", []):
        usb_rows += f"""
        <tr>
            <td><code>Bus {u.get('bus')} / Dev {u.get('device')}</code></td>
            <td><code>{html.escape(str(u.get('vendor_id', '')))}:{html.escape(str(u.get('product_id', '')))}</code></td>
            <td><strong>{html.escape(str(u.get('description', '')))}</strong></td>
            <td><span class="badge-gray">{html.escape(str(u.get('category', '')))}</span></td>
            <td><code>{html.escape(str(u.get('driver_str', 'Aucun')))}</code></td>
        </tr>
        """
    if not usb_rows:
        usb_rows = "<tr><td colspan='5' style='text-align: center; color: #64748b; padding: 15px;'>Aucun périphérique USB listé.</td></tr>"

    return {
        "moxa_card_html": moxa_card_html,
        "serial_ports_table_html": serial_ports_table_html,
        "usb_devices_rows_html": usb_rows,
        "total_serial": sys_devices.get("total_serial", 0),
        "total_usb": sys_devices.get("total_usb", 0),
        "dirty_count": sys_devices.get("dirty_count", 0),
    }
