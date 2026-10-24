#!/usr/bin/env python3
"""Exporte tous les points BACnet IP des appareils découverts sur le chantier actuel.

Pour chaque appareil connu (table discovered_devices, chantier courant) :
  1. demande au démon BACnet (via MQTT, commande `discover`) la liste des objets
     avec nom et valeur ;
  2. lit la propriété `description` de chaque objet avec une pile BACpypes3
     temporaire (port 47809, pour ne pas entrer en conflit avec le démon sur 47808).

Écrit un CSV : IP, Device, ObjectId, Name, Description, Value.
La description est vide lorsque l'objet n'en fournit pas.

À lancer avec le Python du venv BACnet (qui fournit bacpypes3) :
    /opt/rpinode-bacnet/venv/bin/python tools/export_bacnet_points.py [sortie.csv]
Par défaut : data/bacnet_points_export.csv
"""
import asyncio
import csv
import ipaddress
import json
import queue
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.append("/usr/lib/python3/dist-packages")

import paho.mqtt.client as mqtt  # noqa: E402

from core import paths  # noqa: E402
from core.database import get_db_connection  # noqa: E402
from services.presence import get_current_site_id, get_current_site_name  # noqa: E402

try:
    from bacpypes3.app import Application
    from bacpypes3.argparse import SimpleArgumentParser
    from bacpypes3.pdu import Address
    from bacpypes3.apdu import ErrorRejectAbortNack
    HAVE_BACPYPES = True
except ImportError:
    HAVE_BACPYPES = False

DISCOVER_TIMEOUT = 600.0
PROBE_PORT = 47809
DESC_TIMEOUT = 3.0
DESC_CONCURRENCY = 8


def list_devices(site_id):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT last_ip, bacnet_instance, bacnet_name FROM discovered_devices "
            "WHERE site_id = ? AND bacnet_instance IS NOT NULL AND last_ip IS NOT NULL "
            "ORDER BY last_ip",
            (site_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def local_interfaces():
    """Retourne la liste des réseaux IPv4 locaux (ip/prefixe) via `ip -o -4 addr`."""
    nets = []
    try:
        out = subprocess.check_output(["ip", "-o", "-4", "addr", "show"], text=True)
    except Exception:
        return nets
    for line in out.splitlines():
        parts = line.split()
        if "inet" in parts:
            cidr = parts[parts.index("inet") + 1]
            try:
                nets.append(ipaddress.ip_interface(cidr))
            except ValueError:
                pass
    return nets


def cidr_for_device(ip, interfaces):
    addr = ipaddress.ip_address(ip)
    for itf in interfaces:
        if addr in itf.network and not itf.ip.is_loopback:
            return f"{itf.ip}/{itf.network.prefixlen}"
    return None


def discover(client, res_queue, ip, instance):
    job_id = str(uuid.uuid4())
    client.subscribe(f"rpinode/bacnet/res/discover/{job_id}")
    client.publish(
        "rpinode/bacnet/cmd/discover",
        json.dumps({"job_id": job_id, "ip": ip, "device_instance": instance}),
    )
    try:
        return res_queue.get(timeout=DISCOVER_TIMEOUT)
    except queue.Empty:
        return {"status": "error", "message": "Délai d'attente dépassé"}


async def read_descriptions(app, ip, object_ids):
    """Retourne {object_id: description} ; description absente si illisible/vide."""
    sem = asyncio.Semaphore(DESC_CONCURRENCY)
    result = {}

    async def one(obj):
        async with sem:
            try:
                v = await asyncio.wait_for(
                    app.read_property(Address(ip), obj, "description"), timeout=DESC_TIMEOUT
                )
                text = str(v).strip() if v is not None else ""
                if text:
                    result[obj] = text
            except (Exception, ErrorRejectAbortNack):
                pass

    await asyncio.gather(*(one(o) for o in object_ids))
    return result


async def run(out, devices):
    res_queue = queue.Queue()

    def on_msg(c, u, msg):
        try:
            res_queue.put(json.loads(msg.payload.decode("utf-8")))
        except Exception:
            pass

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()
    client.on_message = on_msg
    client.connect("127.0.0.1", 1883, 60)
    client.loop_start()

    interfaces = local_interfaces()
    apps = {}  # une pile BACpypes3 par réseau local (cidr)
    loop = asyncio.get_running_loop()

    def get_app(cidr):
        if cidr not in apps:
            host = cidr.split("/")[0]
            prefix = cidr.split("/")[1]
            args = SimpleArgumentParser().parse_args(
                ["--name", "RPINode-Export", "--instance", "4194300",
                 "--address", f"{host}/{prefix}:{PROBE_PORT}"]
            )
            apps[cidr] = Application.from_args(args)
        return apps[cidr]

    total = with_desc = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["IP", "Device", "ObjectId", "Name", "Description", "Value"])
        for dev in devices:
            ip, inst = dev["last_ip"], dev["bacnet_instance"]
            res = await loop.run_in_executor(None, discover, client, res_queue, ip, inst)
            if res.get("status") != "ok":
                print(f"  {ip} (device {inst}) : ÉCHEC - {res.get('message')}", flush=True)
                writer.writerow([ip, inst, "", f"ERREUR: {res.get('message')}", "", ""])
                continue
            objs = res.get("objects", [])

            descs = {}
            cidr = cidr_for_device(ip, interfaces) if HAVE_BACPYPES else None
            if cidr:
                try:
                    descs = await read_descriptions(get_app(cidr), ip, [o["object_id"] for o in objs])
                except Exception as e:
                    print(f"  {ip} : lecture des descriptions impossible ({e})", flush=True)

            for o in objs:
                writer.writerow([ip, inst, o.get("object_id"), o.get("name"),
                                 descs.get(o.get("object_id"), ""), o.get("value")])
            f.flush()
            total += len(objs)
            with_desc += len(descs)
            print(f"  {ip} (device {inst}) : {len(objs)} point(s), {len(descs)} avec description", flush=True)

    for app in apps.values():
        app.close()
    client.loop_stop()
    client.disconnect()
    print(f"Terminé : {total} point(s), {with_desc} avec description -> {out}", flush=True)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else paths.DATA_DIR / "bacnet_points_export.csv"

    if not HAVE_BACPYPES:
        print("Attention : bacpypes3 introuvable, descriptions ignorées. "
              "Lancez avec /opt/rpinode-bacnet/venv/bin/python.", flush=True)

    site_id = get_current_site_id()
    if not site_id:
        sys.exit("Aucun chantier actif.")
    devices = list_devices(site_id)
    print(f"Chantier {get_current_site_name()} : {len(devices)} appareil(s) BACnet", flush=True)

    asyncio.run(run(out, devices))


if __name__ == "__main__":
    main()
