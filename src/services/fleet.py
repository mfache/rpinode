import requests
import logging
import socket
import os
import json
import sqlite3
from core.config import load_config, save_config
from core.database import get_db_connection

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

        # 1. Collecte des annotations "dirty" à pousser
        dirty_annotations = []
        try:
            with get_db_connection() as conn:
                rows = conn.execute("""
                    SELECT mac, vendor, annotations_json 
                    FROM discovered_devices 
                    WHERE is_dirty = 1
                """).fetchall()
                for row in rows:
                    mac = row["mac"]
                    if row["vendor"]:
                        dirty_annotations.append({
                            "kind": "ip_vendor",
                            "key": mac,
                            "field": "",
                            "value": row["vendor"]
                        })
                    if row["annotations_json"]:
                        annots = json.loads(row["annotations_json"])
                        for k, v in annots.items():
                            dirty_annotations.append({
                                "kind": "ip_annot",
                                "key": mac,
                                "field": k,
                                "value": v
                            })
        except Exception as e:
            logger.error(f"Erreur lors de la lecture des annotations dirty : {e}")

        url = f"{self.base_url}/sync"
        payload = {
            "cell": {
                "mcc": gsm_info.get("mcc"),
                "mnc": gsm_info.get("mnc"),
                "enodeb": gsm_info.get("enodeb")
            },
            "annotations": dirty_annotations
        }
        if site_hint_name:
            payload["site_hint_name"] = site_hint_name

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=10)
            data = response.json()
            if data.get("ok"):
                # 2. Traitement des annotations reçues (Pull)
                received = data.get("annotations", [])
                if received:
                    self._apply_annotations_pull(received)
                
                # 3. Marquer les locales comme synchronisées
                if dirty_annotations:
                    macs = list(set(a["key"] for a in dirty_annotations))
                    with get_db_connection() as conn:
                        for mac in macs:
                            conn.execute("""
                                UPDATE discovered_devices 
                                SET is_dirty = 0, sync_updated_at = CURRENT_TIMESTAMP 
                                WHERE mac = ?
                            """, (mac,))
                        conn.commit()
                
                return data
        except Exception as e:
            logger.error(f"Erreur lors de la synchronisation : {e}")
        return None

    def _apply_annotations_pull(self, annotations):
        """Met à jour la base locale avec les annotations reçues du serveur."""
        try:
            with get_db_connection() as conn:
                for a in annotations:
                    kind = a.get("kind")
                    mac = a.get("key", "").lower()
                    field = a.get("field", "")
                    value = a.get("value")
                    
                    if not mac or kind not in ("ip_vendor", "ip_annot"):
                        continue
                        
                    if kind == "ip_vendor":
                        conn.execute("""
                            INSERT INTO discovered_devices (mac, vendor, is_dirty, updated_at)
                            VALUES (?, ?, 0, CURRENT_TIMESTAMP)
                            ON CONFLICT(mac) DO UPDATE SET
                                vendor = EXCLUDED.vendor,
                                is_dirty = 0,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE is_dirty = 0 OR updated_at < EXCLUDED.updated_at
                        """, (mac, value))
                    elif kind == "ip_annot":
                        # Pour les annotations JSON, c'est plus délicat car on stocke tout dans un champ
                        # On récupère l'existant
                        row = conn.execute("SELECT annotations_json FROM discovered_devices WHERE mac = ?", (mac,)).fetchone()
                        annots = json.loads(row["annotations_json"]) if row and row["annotations_json"] else {}
                        
                        if annots.get(field) != value:
                            annots[field] = value
                            conn.execute("""
                                INSERT INTO discovered_devices (mac, annotations_json, is_dirty, updated_at)
                                VALUES (?, ?, 0, CURRENT_TIMESTAMP)
                                ON CONFLICT(mac) DO UPDATE SET
                                    annotations_json = EXCLUDED.annotations_json,
                                    is_dirty = 0,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE is_dirty = 0 OR updated_at < EXCLUDED.updated_at
                            """, (mac, json.dumps(annots)))
                conn.commit()
        except Exception as e:
            logger.error(f"Erreur lors de l'application des annotations reçues : {e}")

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

    def sync_modbus_templates(self, templates):
        """Envoie les templates Modbus locaux au serveur."""
        if not self.is_registered():
            return False

        url = f"{self.base_url}/modbus-templates"
        payload = {"templates": templates}

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=10)
            return response.json().get("ok", False)
        except Exception as e:
            logger.error(f"Erreur lors de la synchro des templates Modbus : {e}")
            return False

    def send_trends(self, trends):
        """Envoie les relevés historiques au serveur maître."""
        if not self.is_registered():
            return False

        url = f"{self.base_url}/trends"
        payload = {"trends": trends}

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=15)
            return response.json().get("ok", False)
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi des trends : {e}")
            return False

# Instance globale
fleet = FleetClient()
