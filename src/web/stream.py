import time
import logging
import json
from core.sys import get_sys
from core.utils import get_changed_items
from services.network import get_network_overview
from services.gsm import get_gsm_info
from services.presence import get_current_site_name, is_current_site_provisional

logger = logging.getLogger(__name__)

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
            
            current_data = {
                "cpu_temp": f"{get_sys('cpu_temp')}°C",
                "update_time": time.strftime("%H:%M:%S"),
                "site_name": site_name,
                "is_provisional": is_prov,
                "net_data": site_name,
                "net": gsm_info,
                "cpu_data": f"Temp: {get_sys('cpu_temp')}°C",
                
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

                "net_wlan0_ip": net['wlan0']['ip'],
                "net_wlan0_mac": net['wlan0']['mac'],
                "net_wlan0_active": net['wlan0']['active'],
                "net_wlan0_routes": "<br>".join(net['wlan0']['routes']) or "Aucune",

                "net_ts_ip": net['tailscale']['ip'],
                "net_ts_name": net['tailscale']['name'],
                "net_ts_active": net['tailscale']['active'],
                "net_ts_routes": "<br>".join(net['tailscale']['routes']) or "Aucune",
                
                "net_ts_exit": net['ts_exit']
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
