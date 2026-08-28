import asyncio
import datetime
import ipaddress
import json
import os
import re
import subprocess
import logging
import sqlite3
import sys
import socket
from core.database import get_db_connection
from core.paths import IPSCAN_RUNNING_FILE
from core.config import load_config, save_config

logger = logging.getLogger(__name__)

def get_oui_vendor(mac):
    if not mac:
        return "Inconnu"
    mac_lower = mac.lower()
    
    # Recherche dans la base de données (annotations synchronisées ou locales)
    # On cherche d'abord la MAC complète, puis le préfixe OUI (6 chars + 2 séparateurs = 8)
    try:
        with get_db_connection() as conn:
            # 1. MAC exacte
            row = conn.execute(
                "SELECT vendor FROM discovered_devices WHERE mac = ?", 
                (mac_lower,)
            ).fetchone()
            if row and row["vendor"]:
                return row["vendor"]
            
            # 2. Préfixe OUI (ex: 00:90:e8)
            oui_prefix = mac_lower[:8]
            row = conn.execute(
                "SELECT vendor FROM discovered_devices WHERE mac = ?", 
                (oui_prefix,)
            ).fetchone()
            if row and row["vendor"]:
                return row["vendor"]
    except sqlite3.Error:
        pass

    return "Inconnu"

def candidate_ifaces():
    """Interfaces LAN à examiner."""
    return ["eth0", "wlan0"]

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
        sock.settimeout(1.0)
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

async def scan_host(ip, mac, iface):
    """Scanne les ports d'un hôte trouvé."""
    ports_to_check = [80, 443, 502, 47808, 22, 23, 445]
    tasks = [check_port(ip, p) for p in ports_to_check]
    results = await asyncio.gather(*tasks)
    open_ports = [p for p in results if p is not None]

    return {
        "ip": ip,
        "mac": mac,
        "iface": iface,
        "vendor": get_oui_vendor(mac),
        "ports": open_ports
    }

async def sweep_iface(iface, ip_str, prefix):
    """Ping sweep + lecture ARP sur une interface. Renvoie [(ip, mac), ...]."""
    logger.info(f"Balayage de {iface} ({ip_str}/{prefix})...")
    
    # Utilisation de fping si disponible, sinon on peut pas faire grand chose de rapide
    cmd = f"fping -I {iface} -c 1 -t 50 -q -g {ip_str}/{prefix} 2>/dev/null"
    try:
        subprocess.run(cmd, shell=True)
    except Exception as e:
        logger.error(f"Erreur fping : {e}")
        
    await asyncio.sleep(0.5)  # Temps de mise à jour de la table ARP

    alive = []
    try:
        arp_out = subprocess.check_output(
            ["ip", "-4", "neigh", "show", "dev", iface]
        ).decode()
    except subprocess.CalledProcessError:
        arp_out = ""
        
    for line in arp_out.splitlines():
        if "REACHABLE" in line or "STALE" in line or "DELAY" in line:
            parts = line.split()
            if len(parts) >= 4:
                ip_h = parts[0]
                try:
                    ipaddress.IPv4Address(ip_h)
                except ValueError:
                    continue
                mac_h = ""
                try:
                    if "lladdr" in parts:
                        mac_h = parts[parts.index("lladdr") + 1]
                except (ValueError, IndexError):
                    pass
                if mac_h and mac_h != "<incomplete>":
                    alive.append((ip_h, mac_h))

    # Ajouter l'IP du boîtier lui-même
    try:
        with open(f"/sys/class/net/{iface}/address") as f:
            alive.append((ip_str, f.read().strip()))
    except OSError:
        pass
        
    return alive

def load_ipscan_results():
    """Charge les derniers résultats du scan depuis la base de données."""
    config = load_config()
    last_at = config.get("ipscan_last_at", "Jamais")
    
    devices = []
    try:
        with get_db_connection() as conn:
            # On récupère les équipements vus lors du dernier scan (ou les 100 derniers)
            # Pour la fluidité, on prend tout ce qui a été vu depuis 'last_at' (ou tout court)
            rows = conn.execute("""
                SELECT * FROM discovered_devices 
                ORDER BY last_seen DESC, last_ip ASC
            """).fetchall()
            for row in rows:
                d = dict(row)
                d["ip"] = d.get("last_ip") or "Inconnu"
                d["iface"] = d.get("last_iface") or "Inconnu"
                d["ports"] = json.loads(d["last_ports"]) if d.get("last_ports") else []
                devices.append(d)
    except Exception as e:
        logger.error(f"Erreur load_ipscan_results : {e}")
        
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
        
        for iface in candidate_ifaces():
            net = iface_ipv4(iface)
            if not net:
                continue
            ip_str, prefix = net
            scanned_ifaces.append(iface)
            for ip_h, mac_h in await sweep_iface(iface, ip_str, prefix):
                if ip_h not in found:
                    found[ip_h] = (mac_h, iface)

        if not scanned_ifaces:
            logger.warning("Aucune interface LAN n'a d'adresse IP.")
            results = []
        else:
            logger.info(f"{len(found)} hôtes trouvés, scan des ports...")
            tasks = [scan_host(ip_h, mac_h, iface) for ip_h, (mac_h, iface) in found.items()]
            results = await asyncio.gather(*tasks)
            results.sort(key=lambda x: ipaddress.IPv4Address(x["ip"]))
            
            # Persistance immédiate dans la base de données
            update_db_results(results, scanned_ifaces)
            
            # Phase 2 : Enrichissement BACnet (Différé)
            await enrich_bacnet_results(results)

        logger.info("Scan IP terminé.")
        
    finally:
        if os.path.exists(IPSCAN_RUNNING_FILE):
            os.remove(IPSCAN_RUNNING_FILE)

def update_db_results(devices, ifaces=None):
    """Met à jour la base de données avec les résultats du scan."""
    try:
        with get_db_connection() as conn:
            for d in devices:
                ports_json = json.dumps(d.get("ports", []))
                conn.execute("""
                    INSERT INTO discovered_devices (
                        mac, vendor, last_ip, last_ports, last_iface, 
                        bacnet_instance, bacnet_name, last_seen, updated_at, is_dirty
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
                    ON CONFLICT(mac) DO UPDATE SET
                        vendor = COALESCE(discovered_devices.vendor, EXCLUDED.vendor),
                        last_ip = EXCLUDED.last_ip,
                        last_ports = EXCLUDED.last_ports,
                        last_iface = EXCLUDED.last_iface,
                        bacnet_instance = COALESCE(EXCLUDED.bacnet_instance, discovered_devices.bacnet_instance),
                        bacnet_name = COALESCE(EXCLUDED.bacnet_name, discovered_devices.bacnet_name),
                        last_seen = CURRENT_TIMESTAMP
                """, (
                    d["mac"].lower(), d["vendor"], d["ip"], ports_json, d["iface"],
                    d.get("bacnet_instance"), d.get("bacnet_name")
                ))
            conn.commit()
            
            # On stocke la date du scan dans la config pour l'UI
            if ifaces:
                config = load_config()
                config["ipscan_last_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_config(config)
    except Exception as e:
        logger.error(f"Erreur update_db_results : {e}")

async def enrich_bacnet_results(devices):
    """Tente de trouver l'instance BACnet pour les équipements ayant le port 47808 ouvert."""
    reader_path = os.path.join(os.path.dirname(__file__), "bacnet_reader.py")
    bacnet_python = "/opt/boitier-bacnet/venv/bin/python"
    if not os.path.exists(bacnet_python):
        bacnet_python = sys.executable
    
    for d in devices:
        if 47808 in d.get("ports", []):
            logger.info(f"Enrichissement BACnet pour {d['ip']}...")
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
                        d["bacnet_name"] = info.get("name")
                        # Mise à jour DB progressive
                        update_db_results([d])
            except Exception as e:
                logger.warning(f"Échec enrichissement BACnet pour {d['ip']}: {e}")

def _run_async_scan():
    """Exécute le scan dans une nouvelle boucle d'événements (pour thread séparé)."""
    asyncio.run(run_ip_scan())

def start_ip_scan_in_background():
    """Lance le scan dans un thread séparé car le serveur HTTP est bloquant."""
    import threading
    thread = threading.Thread(target=_run_async_scan, daemon=True)
    thread.start()
