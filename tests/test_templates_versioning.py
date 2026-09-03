import os
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import sqlite3
import json
import uuid
from pathlib import Path

import core.paths as paths
from core.database import init_db, get_db_connection
from services.modbus_mgr import (
    save_template,
    get_template,
    get_all_templates,
    delete_template,
    format_local_template_for_fleet,
    normalize_fleet_definition_to_local,
    get_templates_overview
)

class TestTemplatesVersioning(unittest.TestCase):
    def setUp(self):
        # Mock FleetClient pour éviter toute interaction réseau avec docs durant les tests
        self.fleet_patcher = patch("services.fleet.fleet.sync_modbus_templates")
        self.mock_sync = self.fleet_patcher.start()
        self.mock_sync.return_value = True

        # Create a temporary directory for test database
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = paths.DATABASE_FILE
        paths.DATABASE_FILE = Path(self.temp_dir.name) / "test_rpinode.db"
        init_db()

    def tearDown(self):
        self.fleet_patcher.stop()
        paths.DATABASE_FILE = self.original_db
        self.temp_dir.cleanup()

    def test_create_local_template(self):
        regs = [
            {"reg": 10, "function": 3, "base": 0, "name": "Température", "type": "int16", "scale": 0.1, "unit": "°C"}
        ]
        t_id = save_template(name="Sonde Test", manufacturer="Acme", registers=regs, is_shared=False)
        self.assertIsNotNone(t_id)

        tpl = get_template(t_id)
        self.assertEqual(tpl["name"], "Sonde Test")
        self.assertEqual(tpl["manufacturer"], "Acme")
        self.assertEqual(tpl["version"], 1)
        self.assertEqual(tpl["is_shared"], 0)
        self.assertEqual(tpl["is_local_hidden"], 0)
        self.assertIsNotNone(tpl["template_uuid"])
        self.assertIsNotNone(tpl["revision_uuid"])
        self.assertIsNone(tpl["parent_revision_uuid"])

    def test_update_shared_template_increments_version(self):
        regs = [
            {"reg": 10, "function": 3, "base": 0, "name": "Temp", "type": "int16", "scale": 0.1, "unit": "°C"}
        ]
        t_id = save_template(name="Sonde Flotte", manufacturer="Acme", registers=regs, is_shared=True)
        tpl_v1 = get_template(t_id)
        self.assertEqual(tpl_v1["version"], 1)
        self.assertEqual(tpl_v1["is_shared"], 1)
        rev1_uuid = tpl_v1["revision_uuid"]
        tpl_uuid = tpl_v1["template_uuid"]

        # Modifier le template partagé
        regs_v2 = [
            {"reg": 10, "function": 3, "base": 0, "name": "Température Ambiante", "type": "int16", "scale": 0.1, "unit": "°C"},
            {"reg": 11, "function": 3, "base": 0, "name": "Humidité", "type": "uint16", "scale": 1.0, "unit": "%"}
        ]
        save_template(name="Sonde Flotte", manufacturer="Acme", registers=regs_v2, template_id=t_id)

        tpl_v2 = get_template(t_id)
        self.assertEqual(tpl_v2["version"], 2)
        self.assertEqual(tpl_v2["template_uuid"], tpl_uuid) # UUID racine conservé
        self.assertNotEqual(tpl_v2["revision_uuid"], rev1_uuid) # Nouvelle révision
        self.assertEqual(tpl_v2["parent_revision_uuid"], rev1_uuid) # Chaîne de révisions

    def test_delete_template_with_devices_fails(self):
        regs = [{"reg": 1, "function": 3, "name": "R1", "type": "int16", "scale": 1.0, "unit": ""}]
        t_id = save_template(name="TPL Linked", manufacturer="Test", registers=regs)

        # Créer un faux site et appareil lié
        with get_db_connection() as conn:
            site = conn.execute("SELECT id FROM sites LIMIT 1").fetchone()
            if site:
                site_id = site["id"]
            else:
                conn.execute("INSERT INTO sites (name) VALUES ('Chantier Test Modbus')")
                site_id = conn.execute("SELECT id FROM sites WHERE name = 'Chantier Test Modbus'").fetchone()["id"]
            conn.execute("INSERT INTO modbus_devices (site_id, template_id, name, protocol, address) VALUES (?, ?, 'Dev1', 'tcp', '192.168.1.50')",
                         (site_id, t_id))
            conn.commit()

        ok, err = delete_template(t_id)
        self.assertFalse(ok)
        self.assertIn("utilisé par 1 appareil", err)

        # Template toujours présent
        self.assertIsNotNone(get_template(t_id))

    def test_delete_shared_template_marks_hidden(self):
        regs = [{"reg": 1, "function": 3, "name": "R1", "type": "int16", "scale": 1.0, "unit": ""}]
        t_id = save_template(name="TPL Shared Unused", manufacturer="Test", registers=regs, is_shared=True)

        ok, err = delete_template(t_id)
        self.assertTrue(ok)

        # Non visible dans get_all_templates par défaut
        visible_templates = get_all_templates(include_hidden=False)
        self.assertFalse(any(t["id"] == t_id for t in visible_templates))

        # Mais présent dans la base avec is_local_hidden = 1
        tpl = get_template(t_id)
        self.assertEqual(tpl["is_local_hidden"], 1)

    def test_format_and_normalize(self):
        regs = [{"reg": 100, "function": 3, "base": 1, "name": "Puissance", "type": "float32", "scale": 0.01, "unit": "kW"}]
        t_id = save_template(name="Compteur Elec", manufacturer="Schneider", registers=regs)
        tpl = get_template(t_id)

        fleet_payload = format_local_template_for_fleet(tpl)
        self.assertEqual(fleet_payload["template_uuid"], tpl["template_uuid"])
        self.assertEqual(fleet_payload["name"], "Compteur Elec")
        self.assertEqual(fleet_payload["definition"]["reads"][0]["address"], 100)
        self.assertEqual(fleet_payload["definition"]["reads"][0]["type"], "f32")

        # Normaliser depuis docs vers local
        norm = normalize_fleet_definition_to_local("Compteur Elec", fleet_payload["definition"])
        self.assertEqual(norm["name"], "Compteur Elec")
        self.assertEqual(norm["registers"][0]["reg"], 100)
        self.assertEqual(norm["registers"][0]["type"], "float32")
        self.assertEqual(norm["registers"][0]["scale"], 0.01)

if __name__ == "__main__":
    unittest.main()
