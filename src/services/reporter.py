"""Reporter d'état : pont entre l'état interne du boîtier et le broker MQTT local.

Un thread démon collecte périodiquement (toutes les 2 s par défaut) l'état du
système, du site, du réseau, du GSM et des services, puis le publie en JSON sur
le broker Mosquitto local via `mqtt_client`.

Topics publiés (chaque message est un JSON, non retenu) :

- `rpinode/status/system`   : température CPU, uptime, heure de mise à jour
- `rpinode/status/site`     : nom du chantier courant et statut provisoire
- `rpinode/status/network`  : vue d'ensemble des interfaces (`get_network_overview`)
- `rpinode/status/gsm`      : cellule 4G (MCC-MNC-eNodeB) et infos brutes du modem
- `rpinode/status/services` : état du scan IP et configuration du point d'accès WiFi

Le consommateur est le pont SSE de `src/web/stream.py`, qui s'abonne à
`rpinode/status/#` et relaie vers le navigateur (voir `src/services/sse.md`).
Le topic `rpinode/status/sync` n'est pas publié ici mais par `fleet.py`.

Le module expose un singleton `reporter`, démarré depuis `src/main.py`.
"""
import logging
import threading
import time

from core.sys import get_sys
from services.gsm import get_gsm_info
from services.ipscan import is_ipscan_running, load_ipscan_results
from services.mqtt_service import mqtt_client
from services.network import get_network_overview
from services.presence import (get_current_site_name,
                               is_current_site_provisional)
from services.wifi_mgr import get_ap_config

logger = logging.getLogger(__name__)

class StatusReporter(threading.Thread):
    """Thread démon qui publie cycliquement l'état du boîtier sur MQTT."""

    def __init__(self, interval=2):
        """`interval` : délai en secondes entre deux cycles de publication."""
        super().__init__()
        self.interval = interval
        self.daemon = True
        self.running = False

    def run(self):
        """Boucle principale du thread.

        Tente une connexion MQTT initiale (un échec est journalisé mais
        n'empêche pas la boucle de tourner), puis appelle `report_status()`
        à chaque intervalle. Une exception dans un cycle est journalisée et
        n'interrompt pas le thread.
        """
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
        """Collecte l'état courant et le publie, un topic par catégorie.

        Les infos GSM ne sont interrogées que si l'interface `wwan0` est active.
        Voir le docstring du module pour la liste des topics.
        """
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
            "uptime": get_sys("uptime"),
            "update_time": time.strftime("%H:%M:%S")
        }
        mqtt_client.publish("rpinode/status/system", system_data)

        # Données Site
        site_data = {
            "site_name": site_name,
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
        """Demande l'arrêt de la boucle (effectif après le cycle en cours)."""
        self.running = False

# Singleton
reporter = StatusReporter()
