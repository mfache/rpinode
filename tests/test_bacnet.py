import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import core.paths as paths
from core.database import get_db_connection, init_db
from services.bacnet_mgr import (
    save_template,
    delete_template,
    add_device_to_site,
    save_device_points_selection,
    get_site_bacnet_points,
    update_point_settings,
    delete_bacnet_point,
    normalize_fleet_definition_to_local,
    format_local_template_for_fleet
)

class TestBacnetPoints(unittest.TestCase):
    site_id: int
    tpl_id: int
    device_id: int

    def setUp(self):
        self.fleet_patcher = patch("services.fleet.fleet.sync_bacnet_templates")
        self.mock_sync = self.fleet_patcher.start()
        self.mock_sync.return_value = True

        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = paths.DATABASE_FILE
        paths.DATABASE_FILE = Path(self.temp_dir.name) / "test_rpinode_bacnet.db"
        init_db()

        with get_db_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO sites (name, external_id) VALUES ('TEST_SITE_BACNET', '8888')")
            conn.commit()
            row = conn.execute("SELECT id FROM sites WHERE name = 'TEST_SITE_BACNET'").fetchone()
            self.site_id = row["id"]

        self.tpl_id = save_template("Test TPL BACnet", "Test Manu BACnet", [{"obj": "analogInput:1", "name": "Temp Test"}])
        self.device_id = add_device_to_site(self.site_id, self.tpl_id, "Test Device BACnet", 1234, "192.168.1.100")

    def tearDown(self):
        self.fleet_patcher.stop()
        paths.DATABASE_FILE = self.original_db
        self.temp_dir.cleanup()

    def test_save_and_update_bacnet_points(self):
        # 1. Sélectionner un point à suivre
        points = [{
            "object_id": "analogInput:1",
            "name": "Temp Ambiance BACnet",
            "is_monitored": True
        }]
        save_device_points_selection(self.device_id, self.site_id, points)

        # 2. Vérifier récupération du point pour le site
        site_points = get_site_bacnet_points(self.site_id, only_monitored=True)
        self.assertEqual(len(site_points), 1)
        p = site_points[0]
        self.assertEqual(p["object_id"], "analogInput:1")
        self.assertEqual(p["name"], "Temp Ambiance BACnet")
        self.assertEqual(p["is_monitored"], 1)
        self.assertEqual(p["is_recorded"], 0)

        # 3. Activer l'enregistrement et changer la cadence
        point_id = p["id"]
        update_point_settings(point_id, is_recorded=True, cadence="30s")

        site_points = get_site_bacnet_points(self.site_id)
        self.assertEqual(site_points[0]["is_recorded"], 1)
        self.assertEqual(site_points[0]["cadence"], "30s")

        # 4. Supprimer le point
        delete_bacnet_point(point_id)
        site_points = get_site_bacnet_points(self.site_id)
        self.assertEqual(len(site_points), 0)

    def test_delete_template(self):
        # 1. Tentative de suppression d'un template utilisé par un appareil -> Doit échouer
        ok, err = delete_template(self.tpl_id)
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertIn("utilisé", err or "")

        # 2. Création d'un template orphelin et suppression -> Doit réussir
        orphan_tpl_id = save_template("Orphan TPL BACnet", "Orphan Manu", [{"obj": "binaryInput:1", "name": "Orphan Point"}])
        ok, err = delete_template(orphan_tpl_id)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_fleet_format_conversions(self):
        fleet_def = {
            "name": "BACnet Router",
            "notes": "Tridium",
            "reads": [
                {"label": "Temp Pulsion", "obj": "analogInput:1"}
            ]
        }
        local_norm = normalize_fleet_definition_to_local("BACnet Router", fleet_def)
        self.assertEqual(local_norm["name"], "BACnet Router")
        self.assertEqual(local_norm["manufacturer"], "Tridium")
        self.assertEqual(len(local_norm["objects"]), 1)
        self.assertEqual(local_norm["objects"][0]["obj"], "analogInput:1")

        # Test conversion inverse
        tpl_dict = {
            "name": "BACnet Router",
            "manufacturer": "Tridium",
            "objects_json": '[{"obj": "analogInput:1", "name": "Temp Pulsion"}]'
        }
        fleet_out = format_local_template_for_fleet(tpl_dict)
        self.assertEqual(fleet_out["name"], "BACnet Router")
        self.assertEqual(len(fleet_out["objects"]), 1)
        self.assertEqual(fleet_out["objects"][0]["obj"], "analogInput:1")

if __name__ == '__main__':
    unittest.main()
