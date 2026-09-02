import json
import gzip
import logging
import os
import socket
import sqlite3

import requests

from services.mqtt_service import mqtt_client
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

        # 1. Collecte des annotations "dirty" et colonnes à pousser
        dirty_annotations = []
        table_columns_to_push = []
        try:
            with get_db_connection() as conn:
                # Colonnes personnalisées
                rows = conn.execute("SELECT table_id, column_key, column_label FROM custom_column_definitions").fetchall()
                for row in rows:
                    table_columns_to_push.append({
                        "table_id": row["table_id"],
                        "column_key": row["column_key"],
                        "column_label": row["column_label"]
                    })

                # Fabricants globaux (mac_vendors)
                rows = conn.execute("SELECT prefix, vendor FROM mac_vendors WHERE is_dirty = 1").fetchall()
                for row in rows:
                    dirty_annotations.append({
                        "kind": "ip_vendor",
                        "key": row["prefix"],
                        "field": "",
                        "value": row["vendor"]
                    })
                
                # Fabricants BACnet globaux (bacnet_vendors)
                rows = conn.execute("SELECT vendor_id, name FROM bacnet_vendors WHERE is_dirty = 1").fetchall()
                for row in rows:
                    dirty_annotations.append({
                        "kind": "bacnet_vendor",
                        "key": str(row["vendor_id"]),
                        "field": "",
                        "value": row["name"]
                    })
                
                # Annotations spécifiques (discovered_devices)
                rows = conn.execute("""
                    SELECT mac, vendor, annotations_json 
                    FROM discovered_devices 
                    WHERE is_dirty = 1
                """).fetchall()
                for row in rows:
                    mac = row["mac"]
                    if row["vendor"]:
                        dirty_annotations.append({
                            "kind": "ip_device_vendor", # Kind spécial pour différencier du global OUI
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
                
                # Profils réseau "dirty"
                net_profiles_to_push = []
                rows = conn.execute("SELECT * FROM site_network_profiles WHERE is_dirty = 1").fetchall()
                for row in rows:
                    try:
                        addrs = json.loads(row["addresses"]) if row["addresses"] else []
                        if not isinstance(addrs, list):
                            addrs = [str(addrs)]
                    except Exception:
                        addrs = [a.strip() for a in (row["addresses"] or "").split(",") if a.strip()]
                        
                    net_profiles_to_push.append({
                        "name": f"Config {row['interface']} ({socket.gethostname()})",
                        "iface": row["interface"],
                        "method": row["method"],
                        "addresses": addrs,
                        "gateway": row["gateway"],
                        "dhcp_range": row["dhcp_range"] if "dhcp_range" in row.keys() else None,
                        "wifi_ssid": row["ssid"],
                        "wifi_psk": row["psk"],
                        "updated_by": socket.gethostname()
                    })

                # Télémétrie d'usage des templates Modbus
                template_usages = []
                try:
                    usage_rows = conn.execute("""
                        SELECT d.name as device_name, d.site_id, s.external_id as site_external_id,
                               t.template_uuid, t.revision_uuid, t.name as template_name
                        FROM modbus_devices d
                        JOIN modbus_templates t ON d.template_id = t.id
                        LEFT JOIN sites s ON d.site_id = s.id
                    """).fetchall()
                    for r in usage_rows:
                        if r["template_uuid"] and r["revision_uuid"]:
                            c_id = r["site_external_id"]
                            try:
                                c_id = int(c_id) if c_id else 0
                            except (ValueError, TypeError):
                                c_id = 0
                            template_usages.append({
                                "template_uuid": r["template_uuid"],
                                "revision_uuid": r["revision_uuid"],
                                "device_name": r["device_name"] or r["template_name"],
                                "chantier_id": c_id
                            })
                except Exception as e:
                    logger.debug(f"Erreur collecte usage templates : {e}")
        except Exception as e:
            logger.error(f"Erreur lors de la lecture des annotations dirty : {e}")

        url = f"{self.base_url}/sync"
        payload = {
            "cell": {
                "mcc": gsm_info.get("mcc"),
                "mnc": gsm_info.get("mnc"),
                "enodeb": gsm_info.get("enodeb")
            },
            "annotations": dirty_annotations,
            "table_columns": table_columns_to_push,
            "net_profiles": net_profiles_to_push,
            "template_usage": template_usages
        }
        if site_hint_name:
            payload["site_hint_name"] = site_hint_name

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=10)
            data = response.json()
            success = data.get("ok", False)
            mqtt_client.publish("rpinode/status/sync", {"sync_ok": success})

            if success:
                # 2. Traitement des retours (Pull)
                received_annots = data.get("annotations", [])
                if received_annots:
                    self._apply_annotations_pull(received_annots)
                
                received_cols = data.get("table_columns", [])
                if received_cols:
                    self._apply_columns_pull(received_cols)
                
                # 3. Marquer les locales comme synchronisées
                if dirty_annotations:
                    with get_db_connection() as conn:
                        # Marquer mac_vendors
                        prefixes = [a["key"] for a in dirty_annotations if a["kind"] == "ip_vendor"]
                        for p in prefixes:
                            conn.execute("UPDATE mac_vendors SET is_dirty = 0, sync_updated_at = CURRENT_TIMESTAMP WHERE prefix = ?", (p,))
                        
                        # Marquer bacnet_vendors
                        vendor_ids = [a["key"] for a in dirty_annotations if a["kind"] == "bacnet_vendor"]
                        for vid in vendor_ids:
                            conn.execute("UPDATE bacnet_vendors SET is_dirty = 0, sync_updated_at = CURRENT_TIMESTAMP WHERE vendor_id = ?", (vid,))
                        
                        # Marquer discovered_devices
                        macs = list(set(a["key"] for a in dirty_annotations if a["kind"] in ("ip_device_vendor", "ip_annot")))
                        for mac in macs:
                            conn.execute("UPDATE discovered_devices SET is_dirty = 0, sync_updated_at = CURRENT_TIMESTAMP WHERE mac = ?", (mac,))
                        
                        # Marquer les profils réseau
                        conn.execute("UPDATE site_network_profiles SET is_dirty = 0, updated_at = CURRENT_TIMESTAMP WHERE is_dirty = 1")
                        
                        conn.commit()
                
                return data
            return None
        except Exception as e:
            logger.error(f"Erreur lors de la synchronisation : {e}")
            mqtt_client.publish("rpinode/status/sync", {"sync_ok": False})
        return None

    def _apply_annotations_pull(self, annotations):
        """Met à jour la base locale avec les annotations reçues du serveur."""
        try:
            with get_db_connection() as conn:
                for a in annotations:
                    kind = a.get("kind")
                    key = a.get("key", "").lower()
                    field = a.get("field", "")
                    value = a.get("value")
                    
                    if not key or kind not in ("ip_vendor", "ip_annot", "ip_device_vendor", "bacnet_vendor"):
                        continue
                        
                    if kind == "ip_vendor":
                        # C'est un préfixe constructeur global
                        conn.execute("""
                            INSERT INTO mac_vendors (prefix, vendor, is_dirty, sync_updated_at)
                            VALUES (?, ?, 0, CURRENT_TIMESTAMP)
                            ON CONFLICT(prefix) DO UPDATE SET
                                vendor = EXCLUDED.vendor,
                                is_dirty = 0,
                                sync_updated_at = CURRENT_TIMESTAMP
                            WHERE is_dirty = 0
                        """, (key, value))
                    elif kind == "bacnet_vendor":
                        # C'est un fabricant BACnet global
                        try:
                            vendor_id = int(key)
                            conn.execute("""
                                INSERT INTO bacnet_vendors (vendor_id, name, is_dirty, sync_updated_at)
                                VALUES (?, ?, 0, CURRENT_TIMESTAMP)
                                ON CONFLICT(vendor_id) DO UPDATE SET
                                    name = EXCLUDED.name,
                                    is_dirty = 0,
                                    sync_updated_at = CURRENT_TIMESTAMP
                                WHERE is_dirty = 0
                            """, (vendor_id, value))
                        except ValueError:
                            continue
                    elif kind == "ip_device_vendor":
                        # C'est une annotation spécifique à un équipement
                        conn.execute("""
                            UPDATE discovered_devices SET 
                                vendor = ?, 
                                is_dirty = 0, 
                                sync_updated_at = CURRENT_TIMESTAMP
                            WHERE mac = ? AND is_dirty = 0
                        """, (value, key))
                    elif kind == "ip_annot":
                        # Pour les annotations JSON, c'est plus délicat car on stocke tout dans un champ
                        # On récupère tous les enregistrements pour cette MAC (sur tous les sites)
                        rows = conn.execute("SELECT site_id, annotations_json FROM discovered_devices WHERE mac = ? AND is_dirty = 0", (key,)).fetchall()
                        for row in rows:
                            annots = json.loads(row["annotations_json"]) if row["annotations_json"] else {}
                            if annots.get(field) != value:
                                annots[field] = value
                                conn.execute("""
                                    UPDATE discovered_devices SET 
                                        annotations_json = ?, 
                                        is_dirty = 0, 
                                        sync_updated_at = CURRENT_TIMESTAMP 
                                    WHERE site_id = ? AND mac = ?
                                """, (json.dumps(annots), row["site_id"], key))
                conn.commit()
        except Exception as e:
            logger.error(f"Erreur lors de l'application des annotations reçues : {e}")

    def _apply_columns_pull(self, columns):
        """Met à jour les définitions de colonnes locales avec celles du serveur."""
        try:
            with get_db_connection() as conn:
                # 1. Mise à jour / Insertion
                received_keys = []
                for col in columns:
                    table_id = col.get("table_id")
                    key = col.get("column_key")
                    label = col.get("column_label")
                    if not table_id or not key or not label:
                        continue
                    
                    received_keys.append((table_id, key))
                        
                    conn.execute("""
                        INSERT INTO custom_column_definitions (table_id, column_key, column_label)
                        VALUES (?, ?, ?)
                        ON CONFLICT(table_id, column_key) DO UPDATE SET
                            column_label = EXCLUDED.column_label
                    """, (table_id, key, label))
                
                # 2. Réconciliation (Suppression locale si absent du serveur)
                # On ne le fait que si on a reçu au moins une colonne (pour éviter de tout vider en cas d'erreur API vide)
                # Ou alors on vérifie par table_id.
                if received_keys:
                    local_cols = conn.execute("SELECT table_id, column_key FROM custom_column_definitions").fetchall()
                    for lc in local_cols:
                        if (lc["table_id"], lc["column_key"]) not in received_keys:
                            logger.info(f"Suppression locale de la colonne {lc['table_id']}:{lc['column_key']} (absente du serveur)")
                            conn.execute("DELETE FROM custom_column_definitions WHERE table_id = ? AND column_key = ?", 
                                         (lc["table_id"], lc["column_key"]))

                conn.commit()
        except Exception as e:
            logger.error(f"Erreur lors de l'application des colonnes reçues : {e}")

    def delete_table_column(self, table_id, column_key):
        """Informe le serveur de la suppression d'une colonne."""
        if not self.is_registered():
            return False
            
        url = f"{self.base_url}/table/column/delete"
        payload = {
            "table_id": table_id,
            "column_key": column_key,
            "hostname": socket.gethostname()
        }
        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=10)
            return response.json().get("ok", False)
        except Exception as e:
            logger.error(f"Erreur suppression colonne fleet: {e}")
            return False

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

    def get_remote_templates(self):
        """Récupère la bibliothèque de templates Modbus disponible sur le serveur central."""
        if not self.is_registered():
            return {}
        try:
            resp = requests.post(f"{self.base_url}/sync", json={}, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data.get("modbus_templates", {})
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des templates distants : {e}")
        return {}

    def sync_modbus_templates(self, templates):
        """Envoie des templates Modbus locaux au serveur maître."""
        if not self.is_registered():
            return False

        url = f"{self.base_url}/sync"
        payload = {
            "modbus_templates": templates,
            "hostname": socket.gethostname()
        }

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=10)
            return response.json().get("ok", False)
        except Exception as e:
            logger.error(f"Erreur lors de la synchro des templates Modbus : {e}")
            return False

    def get_remote_bacnet_templates(self):
        """Récupère la bibliothèque de templates BACnet disponible sur le serveur central."""
        if not self.is_registered():
            return {}
        try:
            resp = requests.post(f"{self.base_url}/sync", json={}, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data.get("bacnet_templates", {})
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des templates BACnet distants : {e}")
        return {}

    def sync_bacnet_templates(self, templates):
        """Envoie des templates BACnet locaux au serveur maître."""
        if not self.is_registered():
            return False

        url = f"{self.base_url}/sync"
        payload = {
            "bacnet_templates": templates,
            "hostname": socket.gethostname()
        }

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=10)
            return response.json().get("ok", False)
        except Exception as e:
            logger.error(f"Erreur lors de la synchro des templates BACnet : {e}")
            return False

    def send_logs(self, logs):
        """Envoie des logs applicatifs vers le serveur central (avec compression gzip)."""
        if not self.is_registered():
            return False
            
        try:
            payload = json.dumps({"logs": logs}).encode("utf-8")
            compressed_payload = gzip.compress(payload)
            
            headers = self._headers()
            headers["Content-Encoding"] = "gzip"
            headers["Content-Type"] = "application/json"
            
            resp = requests.post(
                f"{self.base_url}/logs",
                data=compressed_payload,
                headers=headers,
                timeout=10
            )
            success = False
            if resp.status_code == 200:
                res = resp.json()
                success = res.get("ok", False)
            
            mqtt_client.publish("rpinode/status/sync", {"sync_ok": success})
            return success
        except Exception as e:
            logger.debug(f"Erreur envoi logs: {e}")
            mqtt_client.publish("rpinode/status/sync", {"sync_ok": False})
            return False

    def get_logs(self, limit=50, level=None):
        """Récupère les logs depuis le serveur."""
        if not self.is_registered():
            return None
        
        try:
            params = {"limit": limit}
            if level:
                params["level"] = level
                
            resp = requests.get(
                f"{self.base_url}/logs",
                headers=self._headers(),
                params=params,
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json().get("logs", [])
            return None
        except Exception as e:
            logger.debug(f"Erreur récupération logs: {e}")
            return None

    def send_trends(self, trends):
        """Envoie les relevés historiques au serveur maître."""
        if not self.is_registered():
            return False

        url = f"{self.base_url}/trends"
        payload = {"trends": trends}

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=15)
            success = response.json().get("ok", False)
            mqtt_client.publish("rpinode/status/sync", {"sync_ok": success})
            return success
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi des trends : {e}")
            mqtt_client.publish("rpinode/status/sync", {"sync_ok": False})
            return False

    def sync_all(self):
        """Force une synchronisation complète des connaissances (Fabricants, etc.)."""
        return self.sync_location({}) # Appel avec cell vide pour juste tirer/pousser les données globales

# Instance globale
fleet = FleetClient()
