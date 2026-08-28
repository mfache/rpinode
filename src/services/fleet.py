import requests
import logging
import socket
import os
from core.config import load_config, save_config

logger = logging.getLogger(__name__)

class FleetClient:
    def __init__(self):
        self.config = load_config()
        self.base_url = self.config.get("fleet_url", "https://docs.deltathermic.be/reports/api")
        self.token = self.config.get("fleet_token")
        
        # Tentative de récupération du secret (Priorité Environnement > Fichier .env > Config)
        # On vérifie les deux noms possibles : FLEET_SECRET et FLEET_JOIN_SECRET
        self.secret = os.environ.get("FLEET_SECRET") or os.environ.get("FLEET_JOIN_SECRET")
        
        if not self.secret:
            # Essayer de lire le fichier d'environnement standard s'il existe
            env_path = "/etc/boitier/fleet.env"
            if os.path.exists(env_path):
                try:
                    with open(env_path, "r") as f:
                        for line in f:
                            if "FLEET_JOIN_SECRET=" in line:
                                self.secret = line.split("=", 1)[1].strip().strip('"').strip("'")
                                break
                except Exception:
                    pass

        if not self.secret:
            self.secret = self.config.get("fleet_secret")
        
        self.hostname = socket.gethostname()

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def is_registered(self):
        return bool(self.token)

    def register(self):
        """Enregistre le boîtier auprès du serveur maître."""
        if not self.secret:
            logger.warning("Aucun secret d'adhésion (fleet_secret) configuré.")
            return False

        url = f"{self.base_url}/register"
        payload = {
            "hostname": self.hostname,
            "tailscale_name": f"{self.hostname}.tailnet.ts.net" # Hypothèse basée sur la doc
        }
        headers = {"X-Join-Secret": self.secret}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            data = response.json()
            if data.get("ok") and data.get("token"):
                self.token = data["token"]
                self.config["fleet_token"] = self.token
                save_config(self.config)
                logger.info("Enregistrement réussi auprès de la flotte.")
                return True
            else:
                logger.error(f"Échec de l'enregistrement : {data.get('error', 'Inconnu')}")
        except Exception as e:
            logger.error(f"Erreur réseau lors de l'enregistrement : {e}")
        return False

    def get_chantiers(self, query=None):
        """Récupère la liste des chantiers depuis le serveur."""
        if not self.is_registered():
            return []

        url = f"{self.base_url}/chantiers"
        try:
            response = requests.get(url, headers=self._headers(), timeout=10)
            data = response.json()
            if data.get("ok"):
                chantiers = data.get("chantiers", [])
                if query:
                    query = query.lower()
                    return [c for c in chantiers if query in c["ref"].lower()]
                return chantiers
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des chantiers : {e}")
        return []

    def sync_location(self, gsm_info, site_hint_name=None):
        """Synchronise l'antenne actuelle et récupère le chantier associé."""
        if not self.is_registered():
            return None

        url = f"{self.base_url}/sync"
        payload = {
            "cell": {
                "mcc": gsm_info.get("mcc"),
                "mnc": gsm_info.get("mnc"),
                "enodeb": gsm_info.get("enodeb")
            }
        }
        if site_hint_name:
            payload["site_hint_name"] = site_hint_name

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=10)
            data = response.json()
            if data.get("ok"):
                return data # On retourne tout le dict pour avoir chantier, net_profiles, etc.
        except Exception as e:
            logger.error(f"Erreur lors de la synchronisation : {e}")
        return None

    def rename_chantier(self, external_id, new_name):
        """Informe le serveur du renommage d'un chantier."""
        if not self.is_registered():
            return False

        url = f"{self.base_url}/chantier/rename"
        payload = {
            "chantier_id": external_id,
            "ref": new_name
        }

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=10)
            data = response.json()
            return data.get("ok", False)
        except Exception as e:
            logger.error(f"Erreur lors du renommage sur le serveur : {e}")
        return False

# Instance globale
fleet = FleetClient()
