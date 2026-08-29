import json
import logging
import queue
import time

import paho.mqtt.client as mqtt

from core.utils import get_changed_items

logger = logging.getLogger(__name__)

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

def _format_dhcp_clients(clients):
    """Formate la liste des clients DHCP en HTML pour le widget."""
    try:
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

    logger.info("Nouveau client SSE connecté (via MQTT).")
    
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
                        "net_wwan0_mac": net['wwan0']['mac'],
                        "net_wwan0_active": net['wwan0']['active'],
                        "net_wwan0_routes": "<br>".join(net['wwan0']['routes']) or "Aucune",
                        "net_eth0_ip": net['eth0']['ip'],
                        "net_eth0_mac": net['eth0']['mac'],
                        "net_eth0_active": net['eth0']['active'],
                        "net_eth0_cable": net['eth0'].get('cable', False),
                        "net_eth0_dhcp": net['eth0'].get('dhcp', False),
                        "net_eth0_has_ip": net['eth0'].get('has_ip', False),
                        "net_eth0_routes": "<br>".join(net['eth0']['routes']) or "Aucune",
                        "net_eth0_clients_html": _format_dhcp_clients(net['eth0'].get('clients', [])),
                        "net_wlan0_ip": net['wlan0']['ip'],
                        "net_wlan0_mac": net['wlan0']['mac'],
                        "net_wlan0_active": net['wlan0']['active'],
                        "net_wlan0_routes": "<br>".join(net['wlan0']['routes']) or "Aucune",
                        "net_wlan0_clients_html": _format_dhcp_clients(net['wlan0'].get('clients', [])),
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

            except queue.Empty:
                # Heartbeat SSE pour garder la connexion vivante
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                continue

    except (ConnectionAbortedError, BrokenPipeError):
        logger.info("Client SSE déconnecté.")
    except Exception as e:
        logger.error(f"Erreur flux SSE/MQTT: {e}")
    finally:
        client.loop_stop()
        client.disconnect()
