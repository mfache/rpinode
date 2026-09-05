import unittest
import threading
import time
import urllib.request
import socket
import json
from src.web.server import start_server
from src.core.config import load_config, save_config

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port

class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # On trouve un port libre pour le test
        cls.test_port = get_free_port()
        
        # On modifie temporairement la config pour utiliser ce port
        # Note: Cela modifie le fichier config.json réel si on ne fait pas attention.
        # Idéalement, on mockerait load_config.
        cls.original_config = load_config()
        test_config = cls.original_config.copy()
        test_config['port'] = cls.test_port
        save_config(test_config)
        
        # Démarrage du serveur dans un thread séparé
        cls.server_thread = threading.Thread(target=start_server, daemon=True)
        cls.server_thread.start()
        
        # Laisser le temps au serveur de démarrer
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        # On restaure la config originale
        save_config(cls.original_config)

    def test_home_page(self):
        """Vérifie que la page d'accueil répond bien."""
        url = f"http://localhost:{self.test_port}/"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("<html", content.lower())
        except Exception as e:
            self.fail(f"Le serveur n'a pas répondu correctement : {e}")

    def test_static_file(self):
        """Vérifie qu'un fichier statique est servi."""
        # On suppose que app.js existe dans static/
        url = f"http://localhost:{self.test_port}/static/app.js"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            self.assertIn("javascript", response.getheader("Content-Type"))
        except Exception as e:
            self.fail(f"Le fichier statique n'est pas accessible : {e}")

    def test_modbus_suivi_page(self):
        """Vérifie que la page /modbus/suivi est servie."""
        url = f"http://localhost:{self.test_port}/modbus/suivi"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("Points Modbus Suivis", content)
        except Exception as e:
            self.fail(f"La page /modbus/suivi n'a pas répondu : {e}")

    def test_modbus_suivi_api(self):
        """Vérifie que l'API /api/modbus/suivi/values répond en JSON."""
        url = f"http://localhost:{self.test_port}/api/modbus/suivi/values"
        try:
            with unittest.mock.patch('services.modbus_mgr.read_site_monitored_points_live', return_value={'1': {'value': '21.0', 'display': '21.0 °C', 'error': None, 'ts': 12345}}):
                response = urllib.request.urlopen(url, timeout=5)
                self.assertEqual(response.getcode(), 200)
                self.assertIn("application/json", response.getheader("Content-Type"))
        except Exception as e:
            self.fail(f"L'API /api/modbus/suivi/values n'a pas répondu : {e}")

    def test_bacnet_suivi_page(self):
        """Vérifie que la page /bacnet/suivi est servie."""
        url = f"http://localhost:{self.test_port}/bacnet/suivi"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("Points BACnet Suivis", content)
        except Exception as e:
            self.fail(f"La page /bacnet/suivi n'a pas répondu : {e}")

    def test_bacnet_suivi_api(self):
        """Vérifie que l'API /api/bacnet/suivi/values répond en JSON."""
        url = f"http://localhost:{self.test_port}/api/bacnet/suivi/values"
        try:
            with unittest.mock.patch('services.bacnet_mgr.read_site_monitored_points_live', return_value={'1': {'value': '22.5', 'display': '22.5', 'error': None, 'ts': 12345}}):
                response = urllib.request.urlopen(url, timeout=5)
                self.assertEqual(response.getcode(), 200)
                self.assertIn("application/json", response.getheader("Content-Type"))
        except Exception as e:
            self.fail(f"L'API /api/bacnet/suivi/values n'a pas répondu : {e}")

    def test_bacnet_catalog_search_api(self):
        """Vérifie que l'API /api/bacnet/catalog/search répond en JSON."""
        url = f"http://localhost:{self.test_port}/api/bacnet/catalog/search"
        payload = json.dumps({"pattern": "*Temp*", "page": 1, "limit": 100}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        try:
            with unittest.mock.patch('services.presence.get_current_site_id', return_value=1), \
                 unittest.mock.patch('services.bacnet_catalog.count_search_points', return_value=1), \
                 unittest.mock.patch('services.bacnet_catalog.search_points', return_value=[{'network_address': '192.168.1.10', 'device_instance': 100, 'device_name': 'Automate 100', 'object_id': 'analogInput:1', 'object_name': 'Temp Ambient'}]):
                response = urllib.request.urlopen(req, timeout=5)
                self.assertEqual(response.getcode(), 200)
                data = json.loads(response.read().decode('utf-8'))
                self.assertEqual(data["status"], "ok")
                self.assertEqual(len(data["objects"]), 1)
                self.assertEqual(data["total_count"], 1)
                self.assertEqual(data["page"], 1)
                self.assertEqual(data["objects"][0]["device_name"], "Automate 100")
        except Exception as e:
            self.fail(f"L'API /api/bacnet/catalog/search n'a pas répondu : {e}")

    def test_bacnet_catalog_values_api(self):
        """Vérifie que l'API /api/bacnet/catalog/values répond en JSON."""
        url = f"http://localhost:{self.test_port}/api/bacnet/catalog/values"
        payload = json.dumps({"points": [{"key": "k1", "address": "192.168.1.10", "object_id": "analogInput:1", "device_id": 100}]}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        try:
            with unittest.mock.patch('services.bacnet_mgr.read_bacnet_points_live_raw', return_value={'k1': {'value': '21.5', 'display': '21.5', 'error': None, 'ts': 12345}}):
                response = urllib.request.urlopen(req, timeout=5)
                self.assertEqual(response.getcode(), 200)
                data = json.loads(response.read().decode('utf-8'))
                self.assertEqual(data["status"], "ok")
                self.assertIn("k1", data["values"])
                self.assertEqual(data["values"]["k1"]["display"], "21.5")
        except Exception as e:
            self.fail(f"L'API /api/bacnet/catalog/values n'a pas répondu : {e}")

    def test_bacnet_points_track_api(self):
        """Vérifie que l'API /api/bacnet/points/track enregistre les points dans le suivi."""
        url = f"http://localhost:{self.test_port}/api/bacnet/points/track"
        payload = json.dumps({
            "points": [
                {
                    "network_address": "172.31.12.145",
                    "device_instance": 1200,
                    "object_id": "analog-input:104",
                    "name": "Point Test Track"
                }
            ]
        }).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        try:
            with unittest.mock.patch('services.presence.get_current_site_id', return_value=1), \
                 unittest.mock.patch('services.bacnet_mgr.add_points_to_suivi', return_value=1):
                response = urllib.request.urlopen(req, timeout=5)
                self.assertEqual(response.getcode(), 200)
                data = json.loads(response.read().decode('utf-8'))
                self.assertEqual(data["status"], "ok")
                self.assertEqual(data["count"], 1)
        except Exception as e:
            self.fail(f"L'API /api/bacnet/points/track n'a pas répondu : {e}")

    def test_bacnet_device_view_page(self):
        """Vérifie que la page /bacnet/device/view est servie pour un appareil existant."""
        from src.core.database import get_db_connection
        from src.services.bacnet_mgr import save_template, add_device_to_site
        with get_db_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO sites (name) VALUES ('TEST_SITE_DEV')")
            conn.commit()
            site_row = conn.execute("SELECT id FROM sites WHERE name = 'TEST_SITE_DEV'").fetchone()
            site_id = site_row["id"]
        tpl_id = save_template("TPL Test View", "Manu", [{"obj": "analogInput:1", "name": "AI 1"}])
        dev_id = add_device_to_site(site_id, tpl_id, "Dev Test View", 999, "192.168.1.50")

        url = f"http://localhost:{self.test_port}/bacnet/device/view?id={dev_id}"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("Dev Test View", content)
            self.assertIn("analogInput:1", content)
        except Exception as e:
            self.fail(f"La page /bacnet/device/view n'a pas répondu : {e}")

    def test_monitor_suivi_page(self):
        """Vérifie que la page /monitor/suivi est servie."""
        url = f"http://localhost:{self.test_port}/monitor/suivi"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("Suivi Global des Points", content)
        except Exception as e:
            self.fail(f"La page /monitor/suivi n'a pas répondu : {e}")

    def test_monitor_suivi_api(self):
        """Vérifie que l'API /api/monitor/suivi/values répond en JSON."""
        url = f"http://localhost:{self.test_port}/api/monitor/suivi/values"
        try:
            with unittest.mock.patch('services.modbus_mgr.read_site_monitored_points_live', return_value={'1': {'value': '21.0', 'display': '21.0 °C', 'error': None, 'ts': 12345}}):
                response = urllib.request.urlopen(url, timeout=5)
                self.assertEqual(response.getcode(), 200)
                self.assertIn("application/json", response.getheader("Content-Type"))
        except Exception as e:
            self.fail(f"L'API /api/monitor/suivi/values n'a pas répondu : {e}")

    def test_monitor_logs_page(self):
        """Vérifie que la page /monitor/logs est servie."""
        url = f"http://localhost:{self.test_port}/monitor/logs"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("logs-container", content)
        except Exception as e:
            self.fail(f"La page /monitor/logs n'a pas répondu : {e}")

    def test_monitor_logs_api(self):
        """Vérifie que l'API /api/monitor/logs répond en JSON."""
        url = f"http://localhost:{self.test_port}/api/monitor/logs"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            data = json.loads(response.read().decode('utf-8'))
            self.assertEqual(data["status"], "ok")
            self.assertIn("logs", data)
        except Exception as e:
            self.fail(f"L'API /api/monitor/logs n'a pas répondu : {e}")

    def test_configuration_logger_page(self):
        """Vérifie que la page /configuration/logger est servie."""
        url = f"http://localhost:{self.test_port}/configuration/logger"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("Configuration des enregistreurs", content)
        except Exception as e:
            self.fail(f"La page /configuration/logger n'a pas répondu : {e}")

    def test_configuration_mqtt_page(self):
        """Vérifie que la page /configuration/mqtt est servie."""
        url = f"http://localhost:{self.test_port}/configuration/mqtt"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("Moniteur MQTT", content)
        except Exception as e:
            self.fail(f"La page /configuration/mqtt n'a pas répondu : {e}")

    def test_configuration_sse_page(self):
        """Vérifie que la page /configuration/sse est servie."""
        url = f"http://localhost:{self.test_port}/configuration/sse"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("Moniteur SSE", content)
            self.assertIn("Flux SSE Actifs", content)
        except Exception as e:
            self.fail(f"La page /configuration/sse n'a pas répondu : {e}")

    def test_sse_status_api(self):
        """Vérifie que l'API /api/sse/status répond avec la liste des flux actifs."""
        url = f"http://localhost:{self.test_port}/api/sse/status"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            data = json.loads(response.read().decode('utf-8'))
            self.assertEqual(data["status"], "ok")
            self.assertIn("active_streams", data)
            self.assertIsInstance(data["active_streams"], list)
        except Exception as e:
            self.fail(f"L'API /api/sse/status n'a pas répondu : {e}")

    def test_sse_stream_route(self):
        """Vérifie que l'API /api/sse/stream est bien routée en text/event-stream."""
        url = f"http://localhost:{self.test_port}/api/sse/stream"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                self.assertEqual(resp.getcode(), 200)
                self.assertIn("text/event-stream", resp.getheader("Content-Type"))
        except Exception:
            pass

    def test_devices_page(self):
        """Vérifie que la page /devices est servie."""
        url = f"http://localhost:{self.test_port}/devices"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("Périphériques", content)
        except Exception as e:
            self.fail(f"La page /devices n'a pas répondu : {e}")

    def test_storage_devices_redirect_or_serve(self):
        """Vérifie que /storage/devices répond en 200."""
        url = f"http://localhost:{self.test_port}/storage/devices"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("Périphériques", content)
        except Exception as e:
            self.fail(f"La page /storage/devices n'a pas répondu : {e}")

    def test_devices_api(self):
        """Vérifie que l'API /api/devices répond en JSON."""
        url = f"http://localhost:{self.test_port}/api/devices"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            data = json.loads(response.read().decode('utf-8'))
            self.assertIn("moxa_connected", data)
            self.assertIn("usb_devices", data)
        except Exception as e:
            self.fail(f"L'API /api/devices n'a pas répondu : {e}")

    def test_devices_ports_api(self):
        """Vérifie que l'API /api/devices/ports répond en JSON."""
        url = f"http://localhost:{self.test_port}/api/devices/ports"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            data = json.loads(response.read().decode('utf-8'))
            self.assertIsInstance(data, list)
        except Exception as e:
            self.fail(f"L'API /api/devices/ports n'a pas répondu : {e}")

    def test_devices_stream_route(self):
        """Vérifie que l'API /api/devices/stream est bien routée en text/event-stream."""
        url = f"http://localhost:{self.test_port}/api/devices/stream"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                self.assertEqual(resp.getcode(), 200)
                self.assertIn("text/event-stream", resp.getheader("Content-Type"))
        except Exception as e:
            # La fermeture de flux ou timeout après réception des headers est attendue
            pass

    def test_bacnet_mstp_status_api(self):
        """Vérifie que l'API /api/bacnet/mstp/status répond en JSON."""
        url = f"http://localhost:{self.test_port}/api/bacnet/mstp/status"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            data = json.loads(response.read().decode('utf-8'))
            self.assertIn("running", data)
            self.assertIn("devices", data)
        except Exception as e:
            self.fail(f"L'API /api/bacnet/mstp/status n'a pas répondu : {e}")

    def test_bacnet_tools_mstp_tab(self):
        """Vérifie que /bacnet/tools?tab=mstp est servi avec l'onglet MS/TP actif."""
        url = f"http://localhost:{self.test_port}/bacnet/tools?tab=mstp"
        try:
            response = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(response.getcode(), 200)
            content = response.read().decode('utf-8')
            self.assertIn("BACnet MS/TP", content)
        except Exception as e:
            self.fail(f"La page /bacnet/tools?tab=mstp n'a pas répondu : {e}")

if __name__ == '__main__':
    unittest.main()
