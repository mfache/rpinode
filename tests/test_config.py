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

if __name__ == '__main__':
    unittest.main()
