import json
import logging
import queue
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from core.utils import get_changed_items

logger = logging.getLogger(__name__)


def _extract_client_ip(handler):
    """Extrait l'adresse IP du client si disponible."""
    try:
        if hasattr(handler, "client_address") and isinstance(handler.client_address, (tuple, list)) and len(handler.client_address) > 0:
            return str(handler.client_address[0])
    except Exception:
        pass
    return "127.0.0.1"


class SSEMonitorHub:
    """
    Gestionnaire central pour le suivi des connexions SSE actives
    et la diffusion du trafic SSE vers la page Moniteur SSE.
    """
    STREAM_METADATA = {
        "/api/stream": {"label": "Flux Général (Statut & Alertes)", "color": "#4ec9b0"},
        "/api/devices/stream": {"label": "Inventaire Périphériques", "color": "#ce9178"},
        "/api/bacnet/mstp/stream": {"label": "Découverte BACnet MS/TP", "color": "#569cd6"},
        "/api/mqtt/stream": {"label": "Moniteur MQTT", "color": "#dcdcaa"},
    }

    def __init__(self):
        self._lock = threading.Lock()
        self._active_connections = {}  # stream_path -> {client_id: {"connected_at": float, "ip": str}}
        self._monitor_listeners = set()  # set of queue.Queue

    def register_client(self, stream_path: str, client_id: str, client_ip: str = "127.0.0.1"):
        with self._lock:
            if stream_path not in self._active_connections:
                self._active_connections[stream_path] = {}
            self._active_connections[stream_path][client_id] = {
                "connected_at": time.time(),
                "ip": client_ip
            }
            active_info = self._get_active_streams_unlocked()

        self._notify_status_change(active_info, change_type="connect", stream=stream_path, client_id=client_id)

    def unregister_client(self, stream_path: str, client_id: str):
        with self._lock:
            if stream_path in self._active_connections:
                self._active_connections[stream_path].pop(client_id, None)
                if not self._active_connections[stream_path]:
                    del self._active_connections[stream_path]
            active_info = self._get_active_streams_unlocked()

        self._notify_status_change(active_info, change_type="disconnect", stream=stream_path, client_id=client_id)

    def is_stream_active(self, stream_path: str) -> bool:
        with self._lock:
            return bool(self._active_connections.get(stream_path))

    def _get_active_streams_unlocked(self):
        result = []
        for path, clients in self._active_connections.items():
            if clients:
                meta = self.STREAM_METADATA.get(path, {"label": path, "color": "#9cdcfe"})
                result.append({
                    "path": path,
                    "label": meta["label"],
                    "color": meta["color"],
                    "clients_count": len(clients),
                    "clients": list(clients.values())
                })
        return result

    def get_active_streams(self):
        with self._lock:
            return self._get_active_streams_unlocked()

    def _notify_status_change(self, active_streams, **extra):
        msg = {
            "type": "status",
            "active_streams": active_streams,
            "timestamp": time.time(),
            **extra
        }
        self._dispatch_to_listeners(msg)

    def record_event(self, stream_path: str, event_type: str, data, is_heartbeat: bool = False, client_ip: str = "127.0.0.1"):
        """
        Diffuse un événement de trafic SSE à tous les auditeurs du moniteur SSE.
        """
        with self._lock:
            if not self._monitor_listeners:
                return
            clients = self._active_connections.get(stream_path, {})
            clients_count = len(clients)

        meta = self.STREAM_METADATA.get(stream_path, {"label": stream_path, "color": "#9cdcfe"})
        event_obj = {
            "type": "traffic",
            "stream": stream_path,
            "stream_label": meta["label"],
            "stream_color": meta["color"],
            "event": event_type,
            "data": data,
            "is_heartbeat": is_heartbeat,
            "clients_count": clients_count,
            "client_ip": client_ip,
            "timestamp": time.time()
        }
        self._dispatch_to_listeners(event_obj)

    def _dispatch_to_listeners(self, msg_dict):
        with self._lock:
            listeners = list(self._monitor_listeners)

        for q in listeners:
            try:
                q.put_nowait(msg_dict)
            except queue.Full:
                pass

    def add_monitor_listener(self) -> queue.Queue:
        q = queue.Queue(maxsize=100)
        with self._lock:
            self._monitor_listeners.add(q)
        return q

    def remove_monitor_listener(self, q: queue.Queue):
        with self._lock:
            self._monitor_listeners.discard(q)


sse_hub = SSEMonitorHub()

def _get_current_wifi_mode():
    """Détermine si wlan0 est en mode AP ou Client."""
    try:
        import subprocess
        cmd = ["nmcli", "-t", "-f", "DEVICE,TYPE,CONNECTION", "dev"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if line.startswith("wlan0:"):
                parts = line.split(":")
                if len(parts) >= 3:
                    con = parts[2]
                    if "ap" in con.lower(): return "Access Point"
                    if con: return f"Client ({con})"
        return "Inactif"
    except:
        return "Inconnu"

def _format_dhcp_clients(clients, is_dhcp_server=True):
    """Formate la liste des clients DHCP en HTML pour le widget."""
    try:
        if not is_dhcp_server:
            return ""

        if not clients:
            return "<div style='opacity:0.6; font-size:0.85em;'>Aucun client connecté.</div>"

        html = "<div class='dhcp-clients-list' style='margin-top:10px; border-top:1px solid #eee; padding-top:8px;'>"
        html += "<div style='font-size:0.75rem; font-weight:bold; color:#999; text-transform:uppercase; margin-bottom:5px;'>Clients connectés</div>"
        for c in clients:
            html += f"""
                <div style='font-size:0.85em; display:flex; justify-content:space-between; margin-bottom:3px;'>
                    <span><b>{c.get('hostname', 'Inconnu')}</b></span>
                    <span style='font-family:monospace;'>{c.get('ip', '--')}</span>
                </div>
            """
        html += "</div>"
        return html
    except Exception as e:
        return f"<!-- Erreur formatage DHCP: {e} -->"

def handle_sse_stream(handler):
    """
    Gère une connexion SSE en s'appuyant sur MQTT pour recevoir les mises à jour.
    """
    handler.send_response(200)
    handler.send_header('Content-type', 'text/event-stream')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('Connection', 'keep-alive')
    handler.end_headers()

    client_id = f"gen_{uuid.uuid4().hex[:6]}"
    client_ip = _extract_client_ip(handler)
    sse_hub.register_client("/api/stream", client_id, client_ip)

    logger.info(f"Nouveau client SSE connecté (via MQTT) [{client_id}].")

    q = queue.Queue(maxsize=10)

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            topic = msg.topic
            q.put((topic, payload), block=False)
        except Exception as e:
            pass # On ignore les erreurs de queue ou parsing

    # Création d'un client MQTT dédié à cette connexion SSE
    # Utilisation de la version 2.0+ de paho-mqtt
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.mqtt.Client()

    client.on_message = on_message

    try:
        client.connect("127.0.0.1", 1883, 60)
        client.subscribe("rpinode/status/#")
        client.subscribe("rpinode/ipscan/#")
        client.subscribe("rpinode/modbus/#")
        client.loop_start()

        last_data = {}

        while True:
            try:
                # On attend un message de la queue (bloquant avec timeout pour garder la main)
                topic, data = q.get(timeout=5)

                if topic == "rpinode/ipscan/host_ready":
                    # Bypass le get_changed_items pour ce topic événementiel
                    message = f"event: host_ready\ndata: {json.dumps(data)}\n\n"
                    handler.wfile.write(message.encode("utf-8"))
                    handler.wfile.flush()
                    sse_hub.record_event("/api/stream", event_type="host_ready", data=data, client_ip=client_ip)
                    continue

                if topic.startswith("rpinode/modbus/point/"):
                    # Événement Modbus temps réel pour le suivi Live
                    message = f"event: modbus_point\ndata: {json.dumps(data)}\n\n"
                    handler.wfile.write(message.encode("utf-8"))
                    handler.wfile.flush()
                    sse_hub.record_event("/api/stream", event_type="modbus_point", data=data, client_ip=client_ip)
                    continue

                # On prépare le payload final en fonction du topic
                current_data = {}

                if topic == "rpinode/status/system":
                    current_data.update(data)
                elif topic == "rpinode/status/site":
                    current_data.update(data)
                elif topic == "rpinode/status/network":
                    net = data
                    current_data.update({
                        "net_wwan0_ip": net['wwan0']['ip'],
                        # "net_wwan0_mac": net['wwan0']['mac'], # Pas de mac pour wwan0
                        "net_wwan0_active": net['wwan0']['active'],
                        "net_wwan0_routes": "<br>".join(net['wwan0']['routes']) or "Aucune",
                        "net_eth0_ip": net['eth0']['ip'],
                        "net_eth0_mac": net['eth0']['mac'],
                        "net_eth0_active": net['eth0']['active'],
                        "net_eth0_cable": net['eth0'].get('cable', False),
                        "net_eth0_dhcp": net['eth0'].get('dhcp', False),
                        "net_eth0_has_ip": net['eth0'].get('has_ip', False),
                        "net_eth0_routes": "<br>".join(net['eth0']['routes']) or "Aucune",
                        "net_eth0_clients_html": _format_dhcp_clients(net['eth0'].get('clients', []), is_dhcp_server=net['eth0'].get('is_dhcp_server', False)),
                        "net_wlan0_ip": net['wlan0']['ip'],
                        "net_wlan0_mac": net['wlan0']['mac'],
                        "net_wlan0_active": net['wlan0']['active'],
                        "net_wlan0_cable": net['wlan0'].get('cable', False),
                        "net_wlan0_dhcp": net['wlan0'].get('dhcp', False),
                        "net_wlan0_has_ip": net['wlan0'].get('has_ip', False),
                        "net_wlan0_routes": "<br>".join(net['wlan0']['routes']) or "Aucune",
                        "net_wlan0_clients_html": _format_dhcp_clients(net['wlan0'].get('clients', []), is_dhcp_server=net['wlan0'].get('is_dhcp_server', False)),
                        "net_ts_ip": net['tailscale']['ip'],
                        "net_ts_name": net['tailscale']['name'],
                        "net_ts_active": net['tailscale']['active'],
                        "net_ts_routes": "<br>".join(net['tailscale']['routes']) or "Aucune",
                        "net_ts_exit": net.get('ts_exit', '')
                    })
                elif topic == "rpinode/status/gsm":
                    current_data["net"] = data.get("cell", "Pas de 4G")
                    current_data["gsm_cell"] = data.get("cell", "Pas de 4G")
                elif topic == "rpinode/status/sync":
                    current_data.update(data)
                elif topic == "rpinode/status/services":
                    current_data.update(data)
                    # Ajout dynamique du mode wifi (pas forcément dans le payload MQTT)
                    current_data["wifi_mode"] = _get_current_wifi_mode()

                # Calcul du delta
                payload = get_changed_items(last_data, current_data)

                if payload:
                    message = f"data: {json.dumps(payload)}\n\n"
                    handler.wfile.write(message.encode("utf-8"))
                    handler.wfile.flush()
                    last_data.update(current_data)
                    sse_hub.record_event("/api/stream", event_type="message", data=payload, client_ip=client_ip)

            except queue.Empty:
                # Heartbeat SSE pour garder la connexion vivante
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                sse_hub.record_event("/api/stream", event_type="heartbeat", data=": heartbeat", is_heartbeat=True, client_ip=client_ip)
                continue

    except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
        logger.info(f"Client SSE déconnecté [{client_id}].")
    except Exception as e:
        logger.error(f"Erreur flux SSE/MQTT: {e}")
    finally:
        sse_hub.unregister_client("/api/stream", client_id)
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

def handle_mqtt_stream(handler, query=None):
    """
    Gère une connexion SSE pour le moniteur MQTT temps réel.
    Diffuse les messages reçus sur le broker local selon le filtre de topic demandé.
    """
    handler.send_response(200)
    handler.send_header('Content-type', 'text/event-stream')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('Connection', 'keep-alive')
    handler.end_headers()

    topic_filter = "#"
    if query and "topic" in query and query["topic"]:
        topic_filter = query["topic"][0] or "#"

    client_id = f"rpinode_mqtt_mon_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    client_ip = _extract_client_ip(handler)
    sse_hub.register_client("/api/mqtt/stream", client_id, client_ip)

    logger.info(f"Nouveau client SSE Moniteur MQTT connecté (topic: {topic_filter}) [{client_id}].")

    q = queue.Queue(maxsize=100)

    def on_message(client, userdata, msg):
        try:
            payload_str = msg.payload.decode('utf-8', errors='replace')
            topic = msg.topic
            q.put((topic, payload_str), block=False)
        except Exception:
            pass

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        client = mqtt.Client(client_id=client_id)

    client.on_message = on_message

    try:
        client.connect("127.0.0.1", 1883, 60)
        client.subscribe(topic_filter)
        client.loop_start()

        while True:
            try:
                topic, payload_str = q.get(timeout=3)
                msg_payload = {"topic": topic, "payload": payload_str}
                data = json.dumps(msg_payload)
                message = f"data: {data}\n\n"
                handler.wfile.write(message.encode("utf-8"))
                handler.wfile.flush()
                sse_hub.record_event("/api/mqtt/stream", event_type="message", data=msg_payload, client_ip=client_ip)
            except queue.Empty:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                sse_hub.record_event("/api/mqtt/stream", event_type="heartbeat", data=": heartbeat", is_heartbeat=True, client_ip=client_ip)

    except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
        logger.info(f"Client SSE Moniteur MQTT déconnecté [{client_id}].")
    except Exception as e:
        logger.error(f"Erreur flux SSE Moniteur MQTT: {e}")
    finally:
        sse_hub.unregister_client("/api/mqtt/stream", client_id)
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass


def handle_bacnet_mstp_stream(handler):
    """
    Gère une connexion SSE pour la découverte BACnet MS/TP en direct.
    Diffuse les snapshots au client dès que l'état change ou à intervalle régulier.
    """
    from services.bacnet_mstp import get_mstp_stream_payload, get_mstp_signature

    handler.send_response(200)
    handler.send_header('Content-type', 'text/event-stream')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('Connection', 'keep-alive')
    handler.end_headers()

    client_id = f"mstp_{uuid.uuid4().hex[:6]}"
    client_ip = _extract_client_ip(handler)
    sse_hub.register_client("/api/bacnet/mstp/stream", client_id, client_ip)

    logger.info(f"Nouveau client SSE BACnet MS/TP connecté [{client_id}].")

    last_sig = None
    try:
        while True:
            sig = get_mstp_signature()
            if sig != last_sig:
                payload = get_mstp_stream_payload()
                message = f"data: {json.dumps(payload)}\n\n"
                handler.wfile.write(message.encode("utf-8"))
                handler.wfile.flush()
                last_sig = sig
                sse_hub.record_event("/api/bacnet/mstp/stream", event_type="message", data=payload, client_ip=client_ip)
            else:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                sse_hub.record_event("/api/bacnet/mstp/stream", event_type="heartbeat", data=": heartbeat", is_heartbeat=True, client_ip=client_ip)
            time.sleep(1.0)
    except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
        logger.info(f"Client SSE BACnet MS/TP déconnecté [{client_id}].")
    except Exception as e:
        logger.error(f"Erreur flux SSE BACnet MS/TP: {e}")
    finally:
        sse_hub.unregister_client("/api/bacnet/mstp/stream", client_id)


def handle_devices_stream(handler):
    """
    Gère une connexion SSE dédiée pour la page /devices.
    Inspecte l'état matériel uniquement lorsqu'un client est connecté,
    et diffuse les mises à jour lorsque l'inventaire change.
    """
    import hashlib
    from core.config import load_config
    from services.device_mgr import list_system_devices, render_devices_components

    handler.send_response(200)
    handler.send_header('Content-type', 'text/event-stream')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('Connection', 'keep-alive')
    handler.end_headers()

    client_id = f"dev_{uuid.uuid4().hex[:6]}"
    client_ip = _extract_client_ip(handler)
    sse_hub.register_client("/api/devices/stream", client_id, client_ip)

    logger.info(f"Nouveau client SSE /devices connecté [{client_id}].")

    config = load_config()
    base_url = config.get("base_url", "")
    last_sig = None

    try:
        while True:
            sys_devs = list_system_devices()
            # Signature rapide pour détecter les modifications matérielles ou de qualification
            sig_raw = json.dumps(sys_devs, sort_keys=True, default=str)
            sig = hashlib.md5(sig_raw.encode("utf-8")).hexdigest()

            if sig != last_sig:
                components = render_devices_components(sys_devs, base_url=base_url)
                message = f"data: {json.dumps(components)}\n\n"
                handler.wfile.write(message.encode("utf-8"))
                handler.wfile.flush()
                last_sig = sig
                sse_hub.record_event("/api/devices/stream", event_type="message", data=components, client_ip=client_ip)
            else:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                sse_hub.record_event("/api/devices/stream", event_type="heartbeat", data=": heartbeat", is_heartbeat=True, client_ip=client_ip)

            time.sleep(3.0)
    except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
        logger.info(f"Client SSE /devices déconnecté [{client_id}].")
    except Exception as e:
        logger.error(f"Erreur flux SSE /devices: {e}")
    finally:
        sse_hub.unregister_client("/api/devices/stream", client_id)


def handle_sse_monitor_stream(handler, query=None):
    """
    Gère une connexion SSE pour le moniteur de flux SSE temps réel.
    Diffuse les événements transitant sur les flux SSE actifs
    et les changements d'état des flux connectés.
    """
    handler.send_response(200)
    handler.send_header('Content-type', 'text/event-stream')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('Connection', 'keep-alive')
    handler.end_headers()

    client_id = f"sse_mon_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    logger.info(f"Nouveau client SSE Moniteur SSE connecté [{client_id}].")

    stream_filter = query.get("stream", [None])[0] if query and "stream" in query else None

    q = sse_hub.add_monitor_listener()

    try:
        # Envoi immédiat de l'état initial des flux actifs
        initial_status = {
            "type": "status",
            "active_streams": sse_hub.get_active_streams(),
            "timestamp": time.time()
        }
        handler.wfile.write(f"data: {json.dumps(initial_status)}\n\n".encode("utf-8"))
        handler.wfile.flush()

        while True:
            try:
                msg = q.get(timeout=3)

                # Filtrage optionnel par stream
                if stream_filter and msg.get("type") == "traffic":
                    if msg.get("stream") != stream_filter:
                        continue

                handler.wfile.write(f"data: {json.dumps(msg)}\n\n".encode("utf-8"))
                handler.wfile.flush()
            except queue.Empty:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()

    except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
        logger.info(f"Client SSE Moniteur SSE déconnecté [{client_id}].")
    except Exception as e:
        logger.error(f"Erreur flux SSE Moniteur SSE: {e}")
    finally:
        sse_hub.remove_monitor_listener(q)
