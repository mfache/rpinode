import html
import json
import logging
import os
import re
import subprocess
from pathlib import Path

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

CAPABILITY_LABELS = {
    "bacnet_mstp": "BACnet MS/TP",
    "modbus_rtu": "Modbus RTU",
    "mbus": "M-Bus",
    "gsm_modem": "Modem 4G / GPS",
}

_LSUSB_RE = re.compile(r"^Bus (\d+) Device (\d+): ID ([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s*(.*)$")
_TREE_BUS_RE = re.compile(r"^/:\s+Bus (\d+)")
_TREE_DEV_RE = re.compile(r"Dev (\d+),.*?Driver=([^,]*)")

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
            # Nettoyer les suffixes comme _1 (ex: ti_usb_3410_5052_1 -> ti_usb_3410_5052, option1 -> option)
            for base in list(SERIAL_DRIVERS.keys()) + list(MODEM_DRIVERS):
                if drv.startswith(base):
                    return base
            return drv
    except Exception:
        pass
    return ""

def list_serial_ports(include_modems: bool = False) -> list:
    """
    Liste les ports série disponibles sur le système avec résolution par identifiant stable (/dev/serial/by-id).
    """
    ports = []
    seen_paths = set()

    # 1. Parcourir /dev/serial/by-id
    if os.path.isdir(SERIAL_BY_ID_DIR):
        try:
            for name in sorted(os.listdir(SERIAL_BY_ID_DIR)):
                full_by_id = os.path.join(SERIAL_BY_ID_DIR, name)
                real_path = os.path.realpath(full_by_id)
                tty_name = os.path.basename(real_path)
                driver = _get_tty_driver(tty_name)

                is_modem = driver in MODEM_DRIVERS or "SimTech" in name or "Quectel" in name
                if is_modem and not include_modems:
                    continue

                is_moxa = "Moxa" in name or driver == "ti_usb_3410_5052" or "110a:1150" in name or "UPort" in name
                desc = name
                if is_moxa:
                    desc = "Moxa UPort 1150 (RS-485)"
                elif driver in SERIAL_DRIVERS:
                    desc = f"{SERIAL_DRIVERS[driver]} ({name})"

                capabilities = []
                if is_moxa or driver in SERIAL_DRIVERS:
                    capabilities.extend(["bacnet_mstp", "modbus_rtu", "mbus"])
                if is_modem:
                    capabilities.append("gsm_modem")

                ports.append({
                    "path": real_path,
                    "by_id": full_by_id,
                    "by_id_name": name,
                    "tty_name": tty_name,
                    "driver": driver,
                    "driver_label": SERIAL_DRIVERS.get(driver, driver or "Inconnu"),
                    "description": desc,
                    "is_moxa": is_moxa,
                    "is_modem": is_modem,
                    "is_rs485": is_moxa or (driver in SERIAL_DRIVERS and not is_modem),
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
                is_modem = driver in MODEM_DRIVERS
                if is_modem and not include_modems:
                    continue
                is_moxa = driver == "ti_usb_3410_5052"
                ports.append({
                    "path": real_path,
                    "by_id": real_path,
                    "by_id_name": tty_name,
                    "tty_name": tty_name,
                    "driver": driver,
                    "driver_label": SERIAL_DRIVERS.get(driver, driver or "Inconnu"),
                    "description": "Moxa UPort (RS-485)" if is_moxa else f"Port série {tty_name}",
                    "is_moxa": is_moxa,
                    "is_modem": is_modem,
                    "is_rs485": is_moxa or (driver in SERIAL_DRIVERS and not is_modem),
                    "capabilities": ["bacnet_mstp", "modbus_rtu", "mbus"] if (is_moxa or driver in SERIAL_DRIVERS) else [],
                })
                seen_paths.add(real_path)
    except Exception as e:
        logger.warning(f"Erreur parcours sys/class/tty: {e}")

    # Trier pour mettre le Moxa en premier
    ports.sort(key=lambda p: (0 if p["is_moxa"] else (2 if p["is_modem"] else 1), p["path"]))
    return ports

def _lsusb_tree_drivers() -> dict:
    """{(bus, dev): {pilotes...}} via `lsusb -t`."""
    drivers = {}
    try:
        res = subprocess.run(["lsusb", "-t"], capture_output=True, text=True, timeout=3)
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
        res = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=3)
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

            # Ignore root hubs / bus internes si non demandés
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
    """
    Rassemble la vue d'ensemble des périphériques matériels pour la page /devices.
    """
    serial_ports = list_serial_ports(include_modems=True)
    rs485_ports = [p for p in serial_ports if not p["is_modem"]]
    modem_ports = [p for p in serial_ports if p["is_modem"]]
    usb_devs = list_usb_devices()
    moxa_driver_info = get_moxa_driver_info()

    moxa_device = None
    for p in rs485_ports:
        if p["is_moxa"]:
            moxa_device = p
            break

    if not moxa_device:
        for u in usb_devs:
            if u["is_moxa"]:
                moxa_device = {
                    "path": "Détecté (USB)",
                    "description": u["description"],
                    "is_moxa": True,
                    "is_rs485": True,
                    "driver": u["driver_str"],
                }
                break

    # Passerelles de communication connectées (Moxa, CP210x, FTDI, CH341, etc.)
    gateways = [p for p in rs485_ports if p.get("is_rs485")]

    moxa_device = None
    for p in rs485_ports:
        if p["is_moxa"]:
            moxa_device = p
            break

    if not moxa_device:
        for u in usb_devs:
            if u["is_moxa"]:
                moxa_device = {
                    "path": "Détecté (USB)",
                    "description": u["description"],
                    "is_moxa": True,
                    "is_rs485": True,
                    "driver": u["driver_str"],
                }
                break

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
    }

def render_devices_components(sys_devices: dict, base_url: str = "") -> dict:
    """Génère les fragments HTML pour les périphériques connectés."""
    gateways = sys_devices.get("gateways", [])
    if not gateways and sys_devices.get("rs485_ports"):
        gateways = [p for p in sys_devices.get("rs485_ports", []) if isinstance(p, dict) and p.get("is_rs485")]
    if not gateways and sys_devices.get("moxa_connected") and isinstance(sys_devices.get("moxa_device"), dict):
        gateways = [sys_devices["moxa_device"]]

    # 1. Encarts Héro pour chaque passerelle / adaptateur bus connecté (Moxa, CP210x, FTDI...)
    cards_html = []
    for gw in gateways:
        if not isinstance(gw, dict):
            continue
        is_moxa = bool(gw.get("is_moxa", False))
        driver_name = str(gw.get("driver", "") or "")
        driver_label = str(gw.get("driver_label", driver_name) or driver_name)
        if is_moxa:
            driver_badge_text = "Pilote ti_usb_3410_5052 (RS-485 2 fils)"
        elif driver_name:
            driver_badge_text = f"Pilote {driver_name} ({driver_label})"
        else:
            driver_badge_text = "Adaptateur Série / Passerelle Bus"

        path_val = str(gw.get("path", "") or "")
        by_id_val = str(gw.get("by_id_name", path_val) or path_val)
        desc_val = str(gw.get("description", "Passerelle Bus / Série") or "Passerelle Bus / Série")

        cards_html.append(f"""
        <div class="moxa-hero-card" style="margin-bottom: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 10px;">
                <div>
                    <h3>⚡ {html.escape(desc_val)}</h3>
                    <div class="moxa-badges">
                        <span class="moxa-badge moxa-badge-green">● Connecté &amp; Opérationnel</span>
                        <span class="moxa-badge moxa-badge-blue">{html.escape(driver_badge_text)}</span>
                        <span class="moxa-badge moxa-badge-purple">BACnet MS/TP, Modbus RTU &amp; M-Bus</span>
                    </div>
                </div>
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
            </div>
            <div class="moxa-actions">
                <a href="{base_url}/bacnet/tools?tab=mstp&amp;device={html.escape(path_val)}" class="btn-primary" style="text-decoration: none; background: #22c55e; border-color: #16a34a; display: inline-flex; align-items: center; gap: 6px;">
                    <span>🔌</span> Lancer la recherche BACnet MS/TP
                </a>
                <a href="{base_url}/modbus/tools?port={html.escape(path_val)}" class="btn-secondary" style="text-decoration: none; background: rgba(255,255,255,0.15); color: white; border-color: rgba(255,255,255,0.3); display: inline-flex; align-items: center; gap: 6px;">
                    <span>🔍</span> Outils Modbus RTU
                </a>
            </div>
        </div>
        """)

    moxa_card_html = "\n".join(cards_html)

    # 2. Table des ports série
    serial_ports = sys_devices.get("rs485_ports", []) + sys_devices.get("modem_ports", [])
    if serial_ports:
        serial_rows = ""
        for p in serial_ports:
            badge_color = "#22c55e" if p.get("is_moxa") else ("#3b82f6" if p.get("is_rs485") else "#64748b")
            raw_caps = p.get("capabilities", []) if isinstance(p, dict) else []
            cap_labels: list[str] = [CAPABILITY_LABELS.get(str(c), str(c)) for c in raw_caps if c]
            caps = ", ".join(cap_labels) if cap_labels else "Série générique"
            serial_rows += f"""
            <tr>
                <td><code>{html.escape(p.get('path', ''))}</code></td>
                <td><small style="color: #64748b; font-family: monospace;">{html.escape(p.get('by_id_name', '—'))}</small></td>
                <td><strong>{html.escape(p.get('description', ''))}</strong></td>
                <td><code>{html.escape(p.get('driver', '—'))}</code></td>
                <td><span style="font-size: 0.85rem; color: {badge_color}; font-weight: bold;">{html.escape(caps)}</span></td>
                <td>
                    <div style="display: flex; gap: 6px;">
                        {f'<a href="{base_url}/bacnet/tools?tab=mstp&amp;device={html.escape(p.get("path", ""))}" class="btn-secondary btn-sm" style="text-decoration: none;">BACnet MS/TP</a>' if p.get("is_rs485") else ''}
                        {f'<a href="{base_url}/modbus/tools?port={html.escape(p.get("path", ""))}" class="btn-secondary btn-sm" style="text-decoration: none;">Modbus</a>' if p.get("is_rs485") else ''}
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
                        <th>Description</th>
                        <th style="width: 150px;">Pilote</th>
                        <th style="width: 180px;">Capacités</th>
                        <th style="width: 180px;">Actions</th>
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
            <td><code>{html.escape(u.get('vendor_id', ''))}:{html.escape(u.get('product_id', ''))}</code></td>
            <td><strong>{html.escape(u.get('description', ''))}</strong></td>
            <td><span class="badge-gray">{html.escape(u.get('category', ''))}</span></td>
            <td><code>{html.escape(u.get('driver_str', 'Aucun'))}</code></td>
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
    }
