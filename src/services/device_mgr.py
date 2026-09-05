import os
import re
import json
import logging
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
                    capabilities.extend(["bacnet_mstp", "modbus_rtu"])
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
                    "capabilities": ["bacnet_mstp", "modbus_rtu"] if (is_moxa or driver in SERIAL_DRIVERS) else [],
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

def list_usb_devices() -> list:
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

            # Ignore root hubs if desired, or keep them with flag
            is_root_hub = (vid_lower == "1d6b")
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

    return {
        "moxa_connected": moxa_device is not None,
        "moxa_device": moxa_device,
        "moxa_driver": moxa_driver_info,
        "rs485_ports": rs485_ports,
        "modem_ports": modem_ports,
        "usb_devices": usb_devs,
        "total_serial": len(rs485_ports),
        "total_usb": len([u for u in usb_devs if not u["is_hub"]]),
    }
