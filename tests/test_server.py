import unittest
import threading
import time
import urllib.request
import socket
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

if __name__ == '__main__':
    unittest.main()
