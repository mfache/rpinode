import logging
import threading
import time

from core.config import load_config
from core.sys import get_sys
from services.gsm import get_gsm_info
from services.ipscan import is_ipscan_running
from services.mqtt_service import mqtt_client
from services.network import get_network_overview
from services.presence import (get_current_site_name,
                               is_current_site_provisional)
from services.wifi_mgr import get_ap_config

logger = logging.getLogger(__name__)

class StatusReporter(threading.Thread):
    def __init__(self, interval=2):
        super().__init__()
        self.interval = interval
        self.daemon = True
        self.running = False

    def run(self):
        self.running = True
        logger.info(f"Démarrage du reporter MQTT (intervalle: {self.interval}s)")
        
        if not mqtt_client.connect():
            logger.error("Échec de connexion MQTT initiale, le reporter va quand même tourner.")

        while self.running:
            try:
                self.report_status()
            except Exception as e:
                logger.error(f"Erreur dans le cycle du reporter: {e}")
            
            time.sleep(self.interval)

    def report_status(self):
        # On regroupe les données par catégories pour les topics
        net = get_network_overview()
        gsm = get_gsm_info() if net['wwan0']['active'] else {}
        site_name = get_current_site_name()
        is_prov = is_current_site_provisional()
        ap_config = get_ap_config()

        # Données Système
        system_data = {
            "cpu_temp": f"{get_sys('cpu_temp')}°C",
            "cpu_data": f"Temp: {get_sys('cpu_temp')}°C",
            "update_time": time.strftime("%H:%M:%S")
        }
        mqtt_client.publish("rpinode/status/system", system_data)

        # Données Site
        site_data = {
            "site_name": site_name,
            "site_name_html": f"<b>{site_name}</b>",
            "is_provisional": is_prov
        }
        mqtt_client.publish("rpinode/status/site", site_data)

        # Données Réseau (global)
        mqtt_client.publish("rpinode/status/network", net)

        # Données GSM
        gsm_payload = {
            "cell": f"{gsm.get('mcc', '-')}-{gsm.get('mnc', '-')}-{gsm.get('enodeb', '-')}" if gsm.get('mcc') else "Pas de 4G",
            "info": gsm
        }
        mqtt_client.publish("rpinode/status/gsm", gsm_payload)

        # Données Services
        ipscan_info = load_ipscan_results()
        services_data = {
            "ipscan_running": is_ipscan_running(),
            "ipscan_last_at": ipscan_info.get("scanned_at", ""),
            "wifi_ap_ssid": ap_config["ssid"],
            "wifi_ap_pass": ap_config["password"]
        }
        mqtt_client.publish("rpinode/status/services", services_data)

    def stop(self):
        self.running = False

# Singleton
reporter = StatusReporter()
