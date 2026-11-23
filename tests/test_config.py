import unittest
from unittest.mock import patch, mock_open
import json
from pathlib import Path
from src.core import config

class TestConfig(unittest.TestCase):
    def test_default_config(self):
        """Vérifie que la config par défaut est correcte."""
        self.assertEqual(config.DEFAULT_CONFIG["hostname"], "rpinode-01")
        self.assertTrue("port" in config.DEFAULT_CONFIG)

    @patch("src.core.config.CONFIG_FILE")
    def test_load_config_missing_file(self, mock_config_file):
        """Vérifie le chargement quand le fichier n'existe pas."""
        mock_config_file.exists.return_value = False
        
        with patch("src.core.config.save_config") as mock_save:
            cfg = config.load_config()
            self.assertEqual(cfg["hostname"], "rpinode-01")
            mock_save.assert_called_once()

    @patch("src.core.config.CONFIG_FILE")
    def test_load_config_with_file(self, mock_config_file):
        """Vérifie le chargement depuis un fichier JSON."""
        mock_config_file.exists.return_value = True
        custom_data = {"hostname": "custom-node", "new_key": "value"}
        
        with patch("builtins.open", mock_open(read_data=json.dumps(custom_data))):
            cfg = config.load_config()
            self.assertEqual(cfg["hostname"], "custom-node")
            self.assertEqual(cfg["new_key"], "value")
            # Vérifie que les valeurs par défaut sont toujours là
            self.assertEqual(cfg["port"], config.DEFAULT_CONFIG["port"])

    def test_lan_interface(self):
        """LAN filaire : eth0 par défaut (rpi01), br0 sur un boîtier à prises pontées."""
        with patch("src.core.config.load_config", return_value=dict(config.DEFAULT_CONFIG)):
            self.assertEqual(config.lan_interface(), "eth0")
            self.assertEqual(config.lan_connection_fallback(), "eth0-manual")
        pont = dict(config.DEFAULT_CONFIG, lan_interface="br0", lan_connection="lan-pont")
        with patch("src.core.config.load_config", return_value=pont):
            self.assertEqual(config.lan_interface(), "br0")
            self.assertEqual(config.lan_connection_fallback(), "lan-pont")

    def test_lan_default_route(self):
        """Par défaut le LAN ne fournit pas Internet (rpi01) ; activable (rpi02, sans SIM)."""
        with patch("src.core.config.load_config", return_value=dict(config.DEFAULT_CONFIG)):
            self.assertFalse(config.lan_default_route())
        with patch("src.core.config.load_config", return_value=dict(config.DEFAULT_CONFIG, lan_default_route=True)):
            self.assertTrue(config.lan_default_route())

    def test_adresse_du_chantier_preferee(self):
        """Pont br0 : l'adresse du chantier passe avant le lien local 169.254."""
        from unittest.mock import patch as p2
        from src.services import ipscan
        sortie = ("5: br0    inet 169.254.91.207/16 brd 169.254.255.255 scope link br0\n"
                  "5: br0    inet 192.168.77.2/24 brd 192.168.77.255 scope global br0\n")
        with p2("src.services.ipscan.subprocess.check_output", return_value=sortie.encode()):
            self.assertEqual(ipscan.iface_ipv4("br0"), ("192.168.77.2", "24"))
        with p2("src.services.ipscan.subprocess.check_output",
                return_value=b"5: br0    inet 169.254.91.207/16 scope link br0\n"):
            self.assertEqual(ipscan.iface_ipv4("br0"), ("169.254.91.207", "16"))

if __name__ == '__main__':
    unittest.main()
