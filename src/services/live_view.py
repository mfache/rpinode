import json
import logging
import threading
import socket
import time
from services.mqtt_service import mqtt_client

logger = logging.getLogger(__name__)

# Le Live View est un outil de debug ponctuel (visualisation temps reel depuis docs).
# Il ne doit jamais rester actif indefiniment : sur une liaison 4G facturee au volume,
# un "stop" perdu (coupure reseau, onglet ferme, crash cote docs, ...) transformerait
# une session de quelques minutes en pompe a data permanente. On coupe donc
# automatiquement apres MAX_SESSION_SECONDS, meme sans "stop" recu.
MAX_SESSION_SECONDS = 300  # 5 minutes

class LiveViewService(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.boitier_id = socket.gethostname()
        self.cmd_topic = f"dt/cmd/{self.boitier_id}/live_view"
        self.data_topic = "reports/sse/updates"
        self.active = False
        self.points_to_poll = []
        self.interval = 1
        self.started_at = None

        def on_cmd_message(client, userdata, msg):
            self._handle_cmd(msg)

        mqtt_client.client.message_callback_add(self.cmd_topic, on_cmd_message)

    def _handle_cmd(self, msg):
        try:
            payload = json.loads(msg.payload.decode())
            self.handle_action(payload)
        except Exception as e:
            logger.error(f"LiveView cmd error: {e}")

    def handle_action(self, payload):
        """Point d'entree partage entre la commande MQTT (docs) et la route HTTP
        locale de debug (/api/live_view), pour garantir le meme comportement
        (notamment l'auto-stop de securite) quelle que soit l'origine."""
        action = payload.get("action")
        if action == "start":
            self.points_to_poll = payload.get("points", [])
            self.interval = max(1, payload.get("interval", 1))
            self.active = True
            self.started_at = time.time()
            logger.info(f"Live view ACTIVE START: {len(self.points_to_poll)} points (auto-stop dans {MAX_SESSION_SECONDS}s).")
        elif action == "stop":
            self.active = False
            self.points_to_poll = []
            self.started_at = None
            logger.info("Live view ACTIVE STOP")
        return {
            "active": self.active,
            "points": len(self.points_to_poll),
            "remaining_seconds": max(0, int(MAX_SESSION_SECONDS - (time.time() - self.started_at))) if self.active and self.started_at else 0
        }

    def _poll_bacnet(self, bacnet_points, results):
        """Lit un lot de points BACnet en un seul aller-retour MQTT vers
        bacnet_daemon (via bacnet_mgr.read_bacnet_points_live_raw), plutot
        qu'un aller-retour par point : le polling live tourne toutes les
        self.interval secondes, un aller-retour par point saturerait vite
        le bus MQTT local et le daemon BACnet."""
        from services.bacnet_mgr import read_bacnet_points_live_raw
        from core.database import get_db_connection

        resolved = []
        pt_by_key = {}
        try:
            with get_db_connection() as conn:
                for pt in bacnet_points:
                    obj_id = pt.get("obj")
                    try:
                        device_instance = int(pt.get("device"))
                    except (TypeError, ValueError):
                        logger.warning(f"Point BACnet 'device' invalide (live view) : {pt}")
                        continue
                    row = conn.execute(
                        "SELECT network_address FROM bacnet_points WHERE device_instance = ? AND object_id = ? LIMIT 1",
                        (device_instance, obj_id)
                    ).fetchone()
                    if not row or not row["network_address"]:
                        logger.warning(f"Point BACnet introuvable en base locale (live view) : {pt}")
                        continue
                    key = f"{device_instance}|{obj_id}"
                    pt_by_key[key] = pt
                    resolved.append({
                        "key": key,
                        "address": row["network_address"],
                        "object_id": obj_id,
                        "device_id": device_instance,
                    })
        except Exception as e:
            logger.error(f"LiveView bacnet resolution err: {e}")
            return

        if not resolved:
            return

        # bacnet_daemon applique son propre timeout de 3.0s par point (voir
        # process_reads dans bacnet_daemon.py) : il faut laisser une marge
        # confortable au-dessus, sinon on rate systematiquement la reponse
        # meme quand la lecture reussit cote BACnet.
        raw = read_bacnet_points_live_raw(resolved, timeout=max(5.0, self.interval + 4.0))
        for key, item in raw.items():
            if not item or item.get("value") is None:
                continue
            pt = pt_by_key.get(key)
            if not pt:
                continue
            result_key = f"{self.boitier_id}|bacnet|{pt['device']}|{pt['obj']}"
            val_str = str(item.get("display")) if item.get("display") not in (None, "—") else str(item.get("value"))
            results[result_key] = {"v": val_str, "c": 0}

    def run(self):
        logger.info("LiveViewService active polling thread started.")
        subscribed = False
        while True:
            try:
                if mqtt_client.client.is_connected():
                    if not subscribed:
                        mqtt_client.client.subscribe(self.cmd_topic)
                        subscribed = True
                else:
                    subscribed = False
            except Exception:
                pass

            if self.active and self.started_at and (time.time() - self.started_at > MAX_SESSION_SECONDS):
                logger.warning(f"Live view : arret automatique apres {MAX_SESSION_SECONDS}s (securite conso donnees 4G).")
                self.active = False
                self.points_to_poll = []
                self.started_at = None

            if not self.active or not self.points_to_poll:
                time.sleep(1)
                continue

            try:
                from services.modbus_mgr import read_point_value
                from core.database import get_db_connection

                results = {}
                current_points = list(self.points_to_poll)

                bacnet_points = [p for p in current_points if p.get("protocol") == "bacnet"]
                if bacnet_points:
                    try:
                        self._poll_bacnet(bacnet_points, results)
                    except Exception as e:
                        logger.error(f"LiveView bacnet err: {e}")

                for pt in current_points:
                    if pt.get("protocol") == "modbus":
                        address = pt.get("address")
                        if address:
                            port = int(pt.get("port", 502))
                            unit = int(pt.get("unit", 1))
                            func = int(pt.get("function", 3))
                            reg = int(pt.get("reg", 0))
                            t_str = pt.get("type", "int16")
                            scale = float(pt.get("scale", 1.0))
                            base = int(pt.get("base", 0))
                            proto = pt.get("transport", "tcp")
                        else:
                            with get_db_connection() as conn:
                                r = conn.execute("""
                                    SELECT p.*, d.protocol as dproto, d.address as daddr, d.port as dport, COALESCE(p.slave_unit, d.slave_unit, 1) as sunit
                                    FROM modbus_points p
                                    JOIN modbus_devices d ON p.device_id = d.id
                                    WHERE d.name = ? AND printf('FC%02d_%d', p.function, p.reg) = ?
                                """, (pt.get("device"), pt.get("obj"))).fetchone()
                            if not r:
                                logger.warning(f"Point not found: {pt}")
                                continue
                            address = r["daddr"]
                            port = int(r["dport"] or 502)
                            unit = int(r["sunit"])
                            func = int(r["function"])
                            reg = int(r["reg"])
                            t_str = r["type"]
                            scale = float(r["scale"] or 1.0)
                            base = int(r["base"] or 0)
                            proto = r["dproto"]

                        try:
                            v, display = read_point_value(
                                proto, address, port, unit, func, reg, t_str, scale, base=base, timeout=0.5
                            )
                            val_str = str(display) if display else str(v)
                            key = f"{self.boitier_id}|modbus|{pt['device']}|{pt['obj']}"
                            results[key] = {"v": val_str, "c": 0}
                        except Exception as e:
                            logger.error(f"Modbus err {pt}: {e}")
                            pass

                if results:
                    mqtt_client.publish(self.data_topic, results)

            except Exception as e:
                logger.error(f"LiveView polling error: {e}")

            time.sleep(self.interval)

live_view_service = LiveViewService()
