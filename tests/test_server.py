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

if __name__ == '__main__':
    unittest.main()
