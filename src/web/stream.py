import time
import logging
import json
import os
from core.sys import get_sys
from core.utils import get_changed_items
from core.paths import IPSCAN_RESULTS_FILE
from services.network import get_network_overview
from services.gsm import get_gsm_info
from services.ipscan import is_ipscan_running
from services.presence import get_current_site_name, is_current_site_provisional
from services.wifi_mgr import get_ap_config

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
    Gère une connexion Server-Sent Events (SSE) pour l'envoi de mises à jour en temps réel.
    Cette fonction boucle et maintient la connexion HTTP ouverte.
    """
    handler.send_response(200)
    handler.send_header('Content-type', 'text/event-stream')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('Connection', 'keep-alive')
    handler.end_headers()

    logger.info("Client connecté au flux SSE.")

    # Mémoire du dernier état envoyé pour ne transmettre que les changements
    last_data = {}

    try:
        count = 0
        while True:
            count += 1
            net = get_network_overview()
            gsm = get_gsm_info() if net['wwan0']['active'] else {}
            site_name = get_current_site_name()
            is_prov = is_current_site_provisional()

            # 1. Préparation des données actuelles complètes
            gsm_info = f"{gsm.get('mcc', '-')}-{gsm.get('mnc', '-')}-{gsm.get('enodeb', '-')}" if gsm.get('mcc') else "Pas de 4G"
            wifi_mode = _get_current_wifi_mode()
            ap_config = get_ap_config()

            current_data = {
                "cpu_temp": f"{get_sys('cpu_temp')}°C",
                "update_time": time.strftime("%H:%M:%S"),
                "site_name": site_name,
                "is_provisional": is_prov,
                "site_name_html": f"<b>{site_name}</b>",
                "net": gsm_info,
                "cpu_data": f"Temp: {get_sys('cpu_temp')}°C",
                "wifi_mode": wifi_mode,
                "wifi_ap_ssid": ap_config["ssid"],
                "wifi_ap_pass": ap_config["password"],
                
                # Données réseau
                "net_wwan0_ip": net['wwan0']['ip'],
                "net_wwan0_mac": net['wwan0']['mac'],
                "net_wwan0_active": net['wwan0']['active'],
                "net_wwan0_routes": "<br>".join(net['wwan0']['routes']) or "Aucune",
                "gsm_cell": f"{gsm.get('mcc', '-')}-{gsm.get('mnc', '-')}-{gsm.get('enodeb', '-')}" if gsm.get('mcc') else "Pas de 4G",

                "net_eth0_ip": net['eth0']['ip'],
                "net_eth0_mac": net['eth0']['mac'],
                "net_eth0_active": net['eth0']['active'],
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
                
                "net_ts_exit": net['ts_exit'],
                "ipscan_running": is_ipscan_running(),
                "ipscan_mtime": os.path.getmtime(IPSCAN_RESULTS_FILE) if os.path.exists(IPSCAN_RESULTS_FILE) else 0
            }

            # 2. Calcul du delta (ce qui a changé depuis le dernier envoi)
            payload = get_changed_items(last_data, current_data)

            # 3. Envoi uniquement si des données ont changé
            if payload:
                # Format SSE : 'data: {json}\n\n'
                message = f"data: {json.dumps(payload)}\n\n"
                handler.wfile.write(message.encode("utf-8"))
                handler.wfile.flush()

                # Mise à jour de la mémoire pour le prochain tour
                last_data.update(current_data)

            time.sleep(2)  # Envoi toutes les 2 secondes

    except (ConnectionAbortedError, BrokenPipeError):
        logger.info("Client SSE déconnecté (connexion fermée).")
    except Exception as e:
        logger.error(f"Erreur dans le flux SSE : {e}")
