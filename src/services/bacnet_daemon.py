import asyncio
import json
import logging
import os
import socket
import queue
import sys
from datetime import datetime

# Permettre à ce script qui tourne sous un virtualenv isolé (pour bacpypes3) 
# de quand même importer les paquets installés sur le système global (comme paho-mqtt)
sys.path.append("/usr/lib/python3/dist-packages")
try:
    import paho.mqtt.client as mqtt
except ImportError:
    import paho.mqtt.client as mqtt # Should fail if really missing

from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.pdu import Address
from bacpypes3.apdu import ErrorRejectAbortNack

logger = logging.getLogger(__name__)

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883

class BacnetMqttDaemon:
    def __init__(self, iface="eth0", port=47808, bbmd_ttl=0):
        self.iface = iface
        self.port = port
        self.app = None
        try:
            self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bacnet_daemon_{os.getpid()}")
        except AttributeError:
            self.mqtt_client = mqtt.Client(client_id=f"bacnet_daemon_{os.getpid()}")
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.msg_queue = queue.Queue()

    def get_iface_cidr(self):
        try:
            import subprocess, re
            out = subprocess.check_output(["ip", "-o", "-4", "addr", "show", "dev", self.iface]).decode()
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", out)
            if m:
                return f"{m.group(1)}/{m.group(2)}"
        except Exception:
            pass
        return "0.0.0.0/0"

    async def start(self):
        self.loop = asyncio.get_running_loop()
        
        # 1. Connexion MQTT
        self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.mqtt_client.loop_start()

        # 2. Initialisation BACnet (BACpypes3)
        cidr = self.get_iface_cidr()
        parser = SimpleArgumentParser()
        # Instance unique pour le boitier (evite les conflits)
        args = parser.parse_args(["--name", "RPINode-Daemon", "--instance", "4194301", "--address", cidr])
        
        self.app = Application.from_args(args)
        logger.info(f"Démon BACnet démarré sur {cidr} (instance {args.instance})")

        # Hook pour écouter les I-Am passifs
        self.app.i_am = self.on_i_am_received

        # Boucle infinie pour maintenir le démon en vie et traiter les messages MQTT
        while True:
            while not self.msg_queue.empty():
                topic, payload = self.msg_queue.get()
                if topic == "rpinode/bacnet/cmd/whois":
                    low = payload.get("low")
                    high = payload.get("high")
                    asyncio.create_task(self.send_whois(low, high))
                elif topic == "rpinode/bacnet/cmd/read":
                    job_id = payload.get("job_id", "default")
                    points = payload.get("points", [])
                    asyncio.create_task(self.process_reads(job_id, points))
                elif topic == "rpinode/bacnet/cmd/probe":
                    job_id = payload.get("job_id", "default")
                    ip = payload.get("ip")
                    if ip:
                        asyncio.create_task(self.process_probe(job_id, ip))
                elif topic == "rpinode/bacnet/cmd/discover":
                    job_id = payload.get("job_id", "default")
                    ip = payload.get("ip")
                    device_instance = payload.get("device_instance")
                    if ip and device_instance:
                        asyncio.create_task(self.process_discover(job_id, ip, device_instance))
                elif topic == "rpinode/bacnet/cmd/whohas":
                    job_id = payload.get("job_id", "default")
                    object_name = payload.get("object_name")
                    if object_name:
                        asyncio.create_task(self.process_whohas(job_id, object_name))
                elif topic == "rpinode/bacnet/cmd/catalog":
                    job_id = payload.get("job_id", "default")
                    ip = payload.get("ip")
                    device_instance = payload.get("device_instance")
                    if ip and device_instance:
                        asyncio.create_task(self.process_catalog(job_id, ip, device_instance))
            await asyncio.sleep(0.1)

    def on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        logger.info("Connecté au broker MQTT")
        client.subscribe("rpinode/bacnet/cmd/whois")
        client.subscribe("rpinode/bacnet/cmd/read")
        client.subscribe("rpinode/bacnet/cmd/probe")
        client.subscribe("rpinode/bacnet/cmd/discover")
        client.subscribe("rpinode/bacnet/cmd/whohas")
        client.subscribe("rpinode/bacnet/cmd/catalog")

    def on_mqtt_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
        except json.JSONDecodeError:
            return
        # Paho tourne dans un thread séparé. On transmet à la boucle asyncio via une Queue thread-safe
        self.msg_queue.put((topic, payload))

    def on_i_am_received(self, app, i_am):
        """Callback déclenché à chaque I-Am vu sur le réseau (passif ou en réponse à Who-Is)."""
        dev_id = i_am.iAmDeviceIdentifier[1]
        addr = str(i_am.pduSource)
        vendor_id = i_am.vendorID
        
        # addr contiendra "192.168.1.10" (IP) ou "2001:14" (MS/TP) automatiquement !
        evt = {
            "device_instance": dev_id,
            "address": addr,
            "vendor_id": vendor_id,
            "timestamp": datetime.now().isoformat()
        }
        self.mqtt_client.publish("rpinode/bacnet/evt/iam", json.dumps(evt))

    async def process_probe(self, job_id, ip):
        """Sonde un équipement IP pour lire son object-name, vendor-name et object-identifier."""
        info = {}
        try:
            # 1. Lire device object identifier (pour avoir l'instance)
            # En BACnet, on peut lire object-identifier de l'objet device sans connaitre son instance exacte
            # Mais souvent, on lit plutôt vendor-identifier, vendor-name, object-name.
            # En BACpypes3, faire un whois ciblé à l'IP est souvent plus sûr pour récupérer l'instance.
            # On va émettre un who-is unicast
            iam_list = await self.app.who_is(address=Address(ip), timeout=2.0)
            if iam_list:
                iam = iam_list[0]
                info["instance"] = iam.iAmDeviceIdentifier[1]
                info["vendor_id"] = iam.vendorID
                
                # Lire l'object-name
                objid = f"device:{info['instance']}"
                try:
                    name = await asyncio.wait_for(
                        self.app.read_property(Address(ip), objid, "object-name"),
                        timeout=2.0
                    )
                    info["name"] = str(name)
                except (Exception, ErrorRejectAbortNack):
                    pass
                    
        except (Exception, ErrorRejectAbortNack) as e:
            info["error"] = str(e)
            
        self.mqtt_client.publish(f"rpinode/bacnet/res/probe/{job_id}", json.dumps(info))

    async def _read_object_list(self, ip, device_instance, max_objs=400):
        """
        Lit la liste complète des identifiants d'objets d'un device. Tente d'abord une
        lecture globale de object-list, puis se replie sur une lecture index par index
        si l'équipement ne supporte pas la lecture groupée (fréquent sur les petits automates).
        """
        objid = f"device:{device_instance}"
        try:
            obj_list_raw = await asyncio.wait_for(
                self.app.read_property(Address(ip), objid, "object-list"),
                timeout=5.0
            )
            return obj_list_raw
        except (Exception, ErrorRejectAbortNack) as e:
            logger.warning(f"Echec lecture globale object-list (timeout/invalid), tentative index par index: {e}")
            try:
                length_raw = await asyncio.wait_for(
                    self.app.read_property(Address(ip), objid, "object-list", array_index=0),
                    timeout=5.0
                )
                length = int(length_raw)
                obj_list = []
                # Limiter pour ne pas bloquer trop longtemps si l'équipement est énorme
                for i in range(1, min(length, max_objs) + 1):
                    try:
                        obj = await asyncio.wait_for(
                            self.app.read_property(Address(ip), objid, "object-list", array_index=i),
                            timeout=2.0
                        )
                        obj_list.append(obj)
                    except (Exception, ErrorRejectAbortNack):
                        pass
                return obj_list
            except (Exception, ErrorRejectAbortNack) as ex2:
                raise Exception(f"Echec lecture index par index: {ex2}")

    async def process_discover(self, job_id, ip, device_instance):
        """Lit la propriété object-list du device et récupère le nom de chaque objet."""
        results = []
        try:
            obj_list = await self._read_object_list(ip, device_instance)

            if not obj_list:
                raise Exception("object-list vide ou illisible")

            # 2. Lire object-name et present-value pour chaque objet
            for obj in obj_list:
                obj_type_str = obj[0]
                obj_inst = obj[1]
                obj_str = f"{obj_type_str}:{obj_inst}"
                
                # Nom
                try:
                    name_raw = await asyncio.wait_for(
                        self.app.read_property(Address(ip), obj_str, "object-name"),
                        timeout=2.0
                    )
                    name = str(name_raw)
                except (Exception, ErrorRejectAbortNack):
                    name = "Inconnu"
                    
                # Valeur
                try:
                    val_raw = await asyncio.wait_for(
                        self.app.read_property(Address(ip), obj_str, "present-value"),
                        timeout=2.0
                    )
                    val = str(val_raw)
                except (Exception, ErrorRejectAbortNack):
                    val = None
                    
                results.append({
                    "object_id": obj_str,
                    "name": name,
                    "value": val
                })
        except (Exception, ErrorRejectAbortNack) as e:
            results = [{"error": str(e)}]
            
        self.mqtt_client.publish(f"rpinode/bacnet/res/discover/{job_id}", json.dumps({"status": "ok" if "error" not in (results[0] if results else {}) else "error", "objects": results, "message": results[0].get("error") if results and "error" in results[0] else ""}))

    # Types d'objets considérés comme des "points" exploitables pour le dictionnaire
    # (on exclut les fichiers, journaux, classes de notification, vues structurées, etc.)
    CATALOG_POINT_PREFIXES = ("analog-", "binary-", "multi-state-", "loop")

    async def process_catalog(self, job_id, ip, device_instance):
        """
        Version légère de process_discover pour la construction du dictionnaire de points :
        ne lit que le nom (pas la valeur) et uniquement pour les types d'objets utiles,
        afin de rester rapide même appliqué à des centaines d'appareils.
        """
        results = []
        try:
            obj_list = await self._read_object_list(ip, device_instance)

            if not obj_list:
                raise Exception("object-list vide ou illisible")

            for obj in obj_list:
                obj_type_str = str(obj[0])
                obj_inst = obj[1]

                if not obj_type_str.startswith(self.CATALOG_POINT_PREFIXES):
                    continue

                obj_str = f"{obj_type_str}:{obj_inst}"
                try:
                    name_raw = await asyncio.wait_for(
                        self.app.read_property(Address(ip), obj_str, "object-name"),
                        timeout=2.0
                    )
                    results.append({"object_id": obj_str, "name": str(name_raw)})
                except (Exception, ErrorRejectAbortNack):
                    pass
        except (Exception, ErrorRejectAbortNack) as e:
            self.mqtt_client.publish(f"rpinode/bacnet/res/catalog/{job_id}", json.dumps({"status": "error", "message": str(e)}))
            return

        self.mqtt_client.publish(f"rpinode/bacnet/res/catalog/{job_id}", json.dumps({"status": "ok", "objects": results}))

    async def process_whohas(self, job_id, object_name):
        results = []
        try:
            logger.info(f"Envoi Who-Has pour: {object_name}")
            ihave_list = await self.app.who_has(object_name=object_name, timeout=5.0)
            for res in ihave_list:
                dev_id = res.deviceIdentifier[1]
                obj_id_type = res.objectIdentifier[0]
                obj_id_inst = res.objectIdentifier[1]
                obj_name = str(res.objectName)
                addr = str(res.pduSource)
                
                # Lire la valeur
                val = None
                try:
                    val_raw = await asyncio.wait_for(
                        self.app.read_property(res.pduSource, f"{obj_id_type}:{obj_id_inst}", "present-value"),
                        timeout=2.0
                    )
                    val = val_raw
                except (Exception, ErrorRejectAbortNack) as e:
                    val = f"Erreur: {e}"
                
                results.append({
                    "device_id": dev_id,
                    "address": addr,
                    "object_id": f"{obj_id_type}:{obj_id_inst}",
                    "object_name": obj_name,
                    "value": str(val) if val is not None else None
                })
        except (Exception, ErrorRejectAbortNack) as e:
            logger.error(f"Who-Has error: {e}")
            results = [{"error": str(e)}]
            
        self.mqtt_client.publish(f"rpinode/bacnet/res/whohas/{job_id}", json.dumps(results))

    async def send_whois(self, low=None, high=None):
        logger.info(f"Envoi Who-Is ({low} - {high})")
        try:
            iam_list = await self.app.who_is(low, high, timeout=4)
            for iam in iam_list:
                self.on_i_am_received(self.app, iam)
        except (Exception, ErrorRejectAbortNack) as e:
            logger.error(f"Erreur Who-Is: {e}")

    async def process_reads(self, job_id, points):
        """Lit une liste de points en parallèle (chaque appareil étant indépendant, le temps
        total reste borné par le timeout d'un seul point plutôt que par leur somme) et
        publie le résultat."""
        async def _read_one(p):
            addr = p.get("address")
            obj_id = p.get("object_id") # ex: "analogInput:1"
            dev_id = p.get("device_id")

            res_val = None
            err = None
            try:
                # BACpypes3 gère le routage si l'adresse est "2001:14"
                res_val = await asyncio.wait_for(
                    self.app.read_property(Address(addr), obj_id, "present-value"),
                    timeout=3.0
                )
            except ErrorRejectAbortNack as e:
                err = f"BACnet Error: {e}"
            except asyncio.TimeoutError:
                err = "Timeout"
            except (Exception, ErrorRejectAbortNack) as e:
                err = str(e)

            return {
                "device_id": dev_id,
                "address": addr,
                "object_id": obj_id,
                "value": str(res_val) if res_val is not None else None,
                "error": err
            }

        results = list(await asyncio.gather(*(_read_one(p) for p in points))) if points else []
        self.mqtt_client.publish(f"rpinode/bacnet/res/read/{job_id}", json.dumps(results))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - BACnetDaemon - %(message)s")
    daemon = BacnetMqttDaemon()
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        logger.info("Arrêt du démon BACnet")
