import asyncio
import datetime
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys

from core.config import load_config, save_config
from core.database import get_db_connection
from core.paths import DATABASE_FILE, IPSCAN_RUNNING_FILE
from services.presence import get_current_site_id

logger = logging.getLogger(__name__)

def get_oui_vendor(mac):
    if not mac:
        return "Inconnu"
    mac_clean = mac.lower().replace(":", "")
    
    try:
        with get_db_connection() as conn:
            # 1. Recherche par préfixe OUI (longueurs décroissantes: 8 chars ex 00:a0:03, 6 chars ex 00a003)
            # On teste d'abord les 8 premiers caractères (format avec colonnes)
            # puis les 6 premiers (format brut)
            prefixes = [mac.lower()[:8], mac_clean[:6]]
            for p in prefixes:
                row = conn.execute(
                    "SELECT vendor FROM mac_vendors WHERE prefix = ?", 
                    (p,)
                ).fetchone()
                if row:
                    return row["vendor"]
            
            # 2. Repli sur discovered_devices pour une annotation spécifique à CETTE MAC
            row = conn.execute(
                "SELECT vendor FROM discovered_devices WHERE mac = ?", 
                (mac.lower(),)
            ).fetchone()
            if row and row["vendor"] and row["vendor"] != "Inconnu":
                return row["vendor"]
    except sqlite3.Error:
        pass

    return "Inconnu"

def candidate_ifaces():
    """Interfaces LAN à examiner."""
    return ["eth0", "wlan0"]

def get_local_macs():
    """Retourne la liste des adresses MAC locales du boîtier."""
    macs = set()
    for iface in candidate_ifaces() + ["wwan0"]:
        try:
            with open(f"/sys/class/net/{iface}/address") as f:
                mac = f.read().strip().lower()
                if mac and mac != "00:00:00:00:00:00":
                    macs.add(mac)
        except OSError:
            pass
    return macs

def get_local_ips():
    """Retourne la liste des adresses IPv4 locales du boîtier."""
    ips = set()
    for iface in candidate_ifaces() + ["wwan0"]:
        net = iface_ipv4(iface)
        if net:
            ips.add(net[0])
    return ips

def iface_ipv4(iface):
    """(ip, prefix) de l'interface, ou None si elle n'a pas d'adresse IPv4."""
    try:
        out = subprocess.check_output(
            ["ip", "-o", "-4", "addr", "show", "dev", iface],
            stderr=subprocess.DEVNULL
        ).decode()
    except subprocess.CalledProcessError:
        return None
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", out)
    if not m:
        return None
    return m.group(1), m.group(2)

async def check_port(ip, port):
    """Tente de se connecter au port TCP ou vérifie le port UDP pour BACnet."""
    if port == 47808:
        return await check_bacnet_udp(ip)
    
    if port == 502:
        # On vérifie d'abord si le port est ouvert
        res = await check_tcp_port(ip, port)
        if res:
            # On pourrait tenter un probe Modbus immédiat, mais on garde ça pour l'enrichissement
            return 502
        return None
        
    return await check_tcp_port(ip, port)

async def check_tcp_port(ip, port):
    """Vérifie si un port TCP est ouvert."""
    try:
        fut = asyncio.open_connection(ip, port)
        _, writer = await asyncio.wait_for(fut, timeout=0.4)
        writer.close()
        await writer.wait_closed()
        return port
    except Exception:
        return None

async def check_bacnet_udp(ip):
    """Envoie un WhoIs minimal en UDP pour vérifier si c'est un automate BACnet."""
    # Packet WhoIs (BVLC + NPDU + APDU)
    whois_pkt = bytes.fromhex("810a000c0120ffff00ff1008")
    
    def _probe():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.3)
        try:
            sock.sendto(whois_pkt, (ip, 47808))
            data, addr = sock.recvfrom(1024)
            return 47808 if data else None
        except:
            return None
        finally:
            sock.close()
            
    # On utilise to_thread pour ne pas bloquer l'event loop
    return await asyncio.to_thread(_probe)

async def scan_host(ip, mac, iface, scan_timestamp, delay_ms=0):
    """Scanne les ports d'un hôte trouvé."""
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)
        
    ports_to_check = [80, 443, 502, 4196, 47808, 22, 23, 445]
    tasks = [check_port(ip, p) for p in ports_to_check]
    results = await asyncio.gather(*tasks)
    open_ports = [p for p in results if p is not None]

    res = {
        "ip": ip,
        "mac": mac,
        "iface": iface,
        "vendor": get_oui_vendor(mac),
        "ports": open_ports,
        "last_seen": scan_timestamp
    }
    
    # Enregistrer en base et diffuser
    update_db_results([res])
    from services.mqtt_service import mqtt_client
    mqtt_client.publish("rpinode/ipscan/host_ready", json.dumps(res))
    
    return res

async def sweep_iface(iface, ip_str, prefix):
    """Ping sweep + lecture ARP sur une interface. Renvoie [(ip, mac), ...]."""
    logger.info(f"Balayage de {iface} ({ip_str}/{prefix})...")

    # On augmente le timeout à 400ms pour les appareils lents (ex: ESP32 en veille WiFi)
    cmd = f"fping -a -I {iface} -r 0 -t 400 -g {ip_str}/{prefix} 2>/dev/null"
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    alive_ips = set(stdout.decode().splitlines())

    await asyncio.sleep(0.5)  # Temps de mise à jour de la table ARP

    local_mac = ""
    try:
        with open(f"/sys/class/net/{iface}/address") as f:
            local_mac = f.read().strip().lower()
    except OSError:
        pass

    alive = []
    try:
        arp_out = subprocess.check_output(
            ["ip", "-4", "neigh", "show", "dev", iface]
        ).decode()
    except subprocess.CalledProcessError:
        arp_out = ""

    for line in arp_out.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            ip_h = parts[0]
            # Ignorer l'adresse de notre propre interface
            if ip_h == ip_str:
                continue
            state = parts[-1] if parts else ""
            if ip_h in alive_ips or state in ["REACHABLE", "DELAY"]:
                try:
                    ipaddress.IPv4Address(ip_h)
                    if "lladdr" in parts:
                        mac_h = parts[parts.index("lladdr") + 1].lower()
                        if mac_h and mac_h != "<incomplete>" and mac_h != local_mac:
                            alive.append((ip_h, mac_h))
                except (ValueError, IndexError):
                    pass
        
    return alive

def load_ipscan_results():
    """Charge les derniers résultats du scan depuis la base de données pour le site actuel."""
    site_id = get_current_site_id()
    
    devices = []
    if not site_id:
        return {"scanned_at": "Chantier inconnu", "devices": []}

    try:
        with get_db_connection() as conn:
            # On récupère les MACs locales du boîtier pour les exclure
            local_macs = list(get_local_macs())
            mac_filter = ""
            params = [site_id]
            if local_macs:
                mac_filter = f" AND mac NOT IN ({','.join(['?']*len(local_macs))})"
                params.extend(local_macs)

            # On récupère le timestamp du dernier scan pour ce chantier
            row_last = conn.execute(
                f"SELECT MAX(last_seen) as last_scan FROM discovered_devices WHERE site_id = ?{mac_filter}",
                params
            ).fetchone()
            last_at = row_last["last_scan"] if row_last and row_last["last_scan"] else "Jamais"

            # On récupère les équipements vus pour le site actuel
            rows = conn.execute(f"""
                SELECT * FROM discovered_devices 
                WHERE site_id = ?{mac_filter}
                ORDER BY last_seen DESC, last_ip ASC
            """, params).fetchall()
            for row in rows:
                d = dict(row)
                d["ip"] = d.get("last_ip") or "Inconnu"
                d["iface"] = d.get("last_iface") or "Inconnu"
                d["ports"] = json.loads(d["last_ports"]) if d.get("last_ports") else []
                devices.append(d)
    except Exception as e:
        logger.error(f"Erreur load_ipscan_results : {e}")
        last_at = "Erreur"
        
    return {
        "scanned_at": last_at,
        "devices": devices
    }

def is_ipscan_running():
    if not os.path.exists(IPSCAN_RUNNING_FILE):
        return False
    try:
        # Si le fichier a plus de 2 minutes, on considère que c'est un reste d'un crash
        age = datetime.datetime.now().timestamp() - os.path.getmtime(IPSCAN_RUNNING_FILE)
        return age < 120
    except OSError:
        return False

async def run_ip_scan():
    """Lance le scan complet et enregistre les résultats."""
    if is_ipscan_running():
        logger.warning("Un scan IP est déjà en cours.")
        return
        
    with open(IPSCAN_RUNNING_FILE, "w") as f:
        f.write(datetime.datetime.now().isoformat())
        
    try:
        found = {}  # ip -> (mac, iface)
        scanned_ifaces = []
        local_ips = get_local_ips()
        local_macs = get_local_macs()
        
        for iface in candidate_ifaces():
            net = iface_ipv4(iface)
            if not net:
                continue
            ip_str, prefix = net
            scanned_ifaces.append(iface)
            for ip_h, mac_h in await sweep_iface(iface, ip_str, prefix):
                if ip_h not in local_ips and mac_h not in local_macs:
                    if ip_h not in found:
                        found[ip_h] = (mac_h, iface)

        if not scanned_ifaces:
            logger.warning("Aucune interface LAN n'a d'adresse IP.")
            results = []
        else:
            scan_timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"{len(found)} hôtes trouvés, scan des ports...")
            
            tasks = []
            for i, (ip_h, (mac_h, iface)) in enumerate(found.items()):
                # On ajoute un délai incrémental (50ms) pour "étaler" visuellement l'apparition dans l'UI
                # sans ralentir significativement le processus global.
                tasks.append(scan_host(ip_h, mac_h, iface, scan_timestamp, delay_ms=i * 50))
                
            results = await asyncio.gather(*tasks)
            results.sort(key=lambda x: ipaddress.IPv4Address(x["ip"]))

            # On retire le lock du scan pour que l'UI réagisse plus vite
            if os.path.exists(IPSCAN_RUNNING_FILE):
                os.remove(IPSCAN_RUNNING_FILE)

            # Phase 2 : Enrichissement BACnet & Modbus (Différé)
            await enrich_results(results)

        logger.info("Scan IP terminé.")

    finally:
        if os.path.exists(IPSCAN_RUNNING_FILE):
            os.remove(IPSCAN_RUNNING_FILE)

def update_db_results(devices, ifaces=None):
    """Met à jour la base de données avec les résultats du scan pour le chantier actuel."""
    site_id = get_current_site_id()
    if not site_id:
        logger.error("Impossible de mettre à jour les résultats : aucun chantier actif.")
        return

    # On fige le timestamp exact de ce scan pour tous les appareils trouvés
    # Cela permet de les identifier de manière unique lors de la lecture pour savoir qui est "en ligne"
    scan_timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        with get_db_connection() as conn:
            for d in devices:
                ports_json = json.dumps(d.get("ports", []))
                mac_clean = d["mac"].lower()
                ip_clean = d["ip"]

                # Libère l'IP sur les autres équipements éventuels du même chantier
                conn.execute("""
                    UPDATE discovered_devices
                    SET last_ip = NULL
                    WHERE site_id = ? AND mac != ? AND last_ip = ?
                """, (site_id, mac_clean, ip_clean))

                conn.execute("""
                    INSERT INTO discovered_devices (
                        site_id, mac, vendor, last_ip, last_ports, last_iface,
                        bacnet_instance, bacnet_name, modbus_info, last_seen, updated_at, is_dirty
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
                    ON CONFLICT(site_id, mac) DO UPDATE SET
                        vendor = CASE
                            WHEN discovered_devices.vendor IS NULL OR discovered_devices.vendor = 'Inconnu' THEN EXCLUDED.vendor
                            ELSE discovered_devices.vendor
                        END,
                        last_ip = EXCLUDED.last_ip,
                        last_ports = EXCLUDED.last_ports,
                        last_iface = EXCLUDED.last_iface,
                        bacnet_instance = COALESCE(EXCLUDED.bacnet_instance, discovered_devices.bacnet_instance),
                        bacnet_name = COALESCE(EXCLUDED.bacnet_name, discovered_devices.bacnet_name),
                        modbus_info = COALESCE(EXCLUDED.modbus_info, discovered_devices.modbus_info),
                        last_seen = EXCLUDED.last_seen
                """, (
                    site_id, mac_clean, d["vendor"], ip_clean, ports_json, d["iface"],
                    d.get("bacnet_instance"), d.get("bacnet_name"), d.get("modbus_info"),
                    d.get("last_seen", datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                ))
            conn.commit()
    except Exception as e:
        logger.error(f"Erreur update_db_results : {e}")

async def enrich_results(devices):
    """Tente d'enrichir les résultats (BACnet & Modbus)."""
    tasks = []
    for d in devices:
        ports = d.get("ports", [])
        if 47808 in ports:
            tasks.append(enrich_bacnet_device(d))
        if 502 in ports:
            tasks.append(enrich_modbus_device(d))
    
    if tasks:
        await asyncio.gather(*tasks)

async def enrich_bacnet_device(d):
    """Tente de trouver l'instance BACnet."""
    logger.info(f"Enrichissement BACnet pour {d['ip']}...")
    reader_path = os.path.join(os.path.dirname(__file__), "bacnet_reader.py")
    bacnet_python = "/opt/boitier-bacnet/venv/bin/python"
    if not os.path.exists(bacnet_python):
        bacnet_python = sys.executable
        
    try:
        proc = await asyncio.create_subprocess_exec(
            bacnet_python, reader_path, "probe", d["ip"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode == 0:
            info = json.loads(stdout.decode())
            if "instance" in info:
                d["bacnet_instance"] = info["instance"]
                        
                # Résolution du fabricant BACnet via la base de connaissance
                vendor_name = info.get("name", "Automate BACnet")
                if info.get("vendor_id"):
                    try:
                        with get_db_connection() as conn:
                            row = conn.execute(
                                "SELECT name FROM bacnet_vendors WHERE vendor_id = ?", 
                                (info["vendor_id"],)
                            ).fetchone()
                            if row:
                                vendor_name = row["name"]
                            else:
                                vendor_name = f"Fabricant #{info['vendor_id']}"
                    except:
                        pass

                d["bacnet_name"] = vendor_name
                update_db_results([d])
                from services.mqtt_service import mqtt_client
                mqtt_client.publish("rpinode/ipscan/host_ready", json.dumps(d))
    except Exception as e:
        logger.warning(f"Échec enrichissement BACnet pour {d['ip']}: {e}")

async def enrich_modbus_device(d):
    """Tente de trouver les Unit IDs Modbus."""
    logger.info(f"Enrichissement Modbus pour {d['ip']}...")
    
    def _probe_modbus():
        import socket
        import struct
        
        target_port = 502 if 502 in d.get("ports", []) else (4196 if 4196 in d.get("ports", []) else 502)
        found_units = []
        # On teste les Unit IDs les plus courants: 1, 2, 3, 255, 0
        for unit in [1, 2, 3, 255, 0]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            try:
                sock.connect((d["ip"], target_port))
                # Transaction ID: 1, Protocol: 0, Length: 6, Unit: <unit>, Func: 3, Addr: 0, Count: 1
                # Format: >HHHBBHH
                req = struct.pack(">HHHBBHH", 1, 0, 6, unit, 3, 0, 1)
                sock.send(req)
                resp = sock.recv(1024)
                # Réponse valide (min 9 octets pour FC03): Tid(2), Proto(2), Len(2), Unit(1), Func(1), ByteCount(1), Data...
                if len(resp) >= 9 and resp[7] == 3:
                    found_units.append(str(unit))
                elif len(resp) >= 9 and resp[7] == 0x83:
                    # Exception Modbus = Esclave présent mais erreur sur la requête (ex: registre 0 inexistant)
                    found_units.append(str(unit))
            except:
                pass
            finally:
                sock.close()
        
        if found_units:
            return f"Units: {', '.join(found_units)}"
        return None

    try:
        info = await asyncio.to_thread(_probe_modbus)
        if info:
            d["modbus_info"] = info
            update_db_results([d])
            from services.mqtt_service import mqtt_client
            mqtt_client.publish("rpinode/ipscan/host_ready", json.dumps(d))
    except Exception as e:
        logger.warning(f"Échec enrichissement Modbus pour {d['ip']}: {e}")

def _run_async_scan():
    """Exécute le scan dans une nouvelle boucle d'événements (pour thread séparé)."""
    asyncio.run(run_ip_scan())

def start_ip_scan_in_background():
    """Lance le scan dans un thread séparé car le serveur HTTP est bloquant."""
    import threading
    thread = threading.Thread(target=_run_async_scan, daemon=True)
    thread.start()
