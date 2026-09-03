import unittest
from core.database import get_db_connection, init_db
from services.modbus_mgr import (
    save_template,
    delete_template,
    add_device_to_site,
    save_device_points_selection,
    get_site_modbus_points,
    update_point_settings,
    delete_modbus_point,
    normalize_fleet_definition_to_local,
    format_local_template_for_fleet
)

class TestModbusPoints(unittest.TestCase):
    site_id: int
    tpl_id: int
    device_id: int

    def setUp(self):
        init_db()
        with get_db_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO sites (name, external_id) VALUES ('TEST_SITE_MODBUS', '9999')")
            conn.commit()
            row = conn.execute("SELECT id FROM sites WHERE name = 'TEST_SITE_MODBUS'").fetchone()
            self.site_id = row["id"]

        self.tpl_id = save_template("Test TPL", "Test Manu", [{"reg": 100, "name": "Temp Test", "function": 3}])
        self.device_id = add_device_to_site(self.site_id, self.tpl_id, "Test Device", "tcp", "127.0.0.1", 502)

    def tearDown(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM modbus_points WHERE site_id = ?", (self.site_id,))
            conn.execute("DELETE FROM modbus_devices WHERE site_id = ?", (self.site_id,))
            conn.execute("DELETE FROM modbus_templates WHERE id = ?", (self.tpl_id,))
            conn.execute("DELETE FROM sites WHERE id = ?", (self.site_id,))
            conn.commit()

    def test_save_and_update_points(self):
        # 1. Sélectionner un point à suivre
        points = [{
            "reg": 100,
            "function": 3,
            "name": "Temp Test",
            "type": "int16",
            "scale": 0.1,
            "unit": "°C",
            "is_monitored": True
        }]
        save_device_points_selection(self.device_id, self.site_id, points)

        # 2. Vérifier récupération du point pour le site
        site_points = get_site_modbus_points(self.site_id, only_monitored=True)
        self.assertEqual(len(site_points), 1)
        p = site_points[0]
        self.assertEqual(p["reg"], 100)
        self.assertEqual(p["is_monitored"], 1)
        self.assertEqual(p["is_recorded"], 0)

        # 3. Activer l'enregistrement et changer la cadence
        point_id = p["id"]
        update_point_settings(point_id, is_recorded=True, cadence="30s")

        site_points = get_site_modbus_points(self.site_id)
        self.assertEqual(site_points[0]["is_recorded"], 1)
        self.assertEqual(site_points[0]["cadence"], "30s")

        # 4. Supprimer le point
        delete_modbus_point(point_id)
        site_points = get_site_modbus_points(self.site_id)
        self.assertEqual(len(site_points), 0)

    def test_delete_template(self):
        # 1. Tentative de suppression d'un template utilisé par un appareil -> Doit échouer
        ok, err = delete_template(self.tpl_id)
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertIn("utilisé", err or "")

        # 2. Création d'un template orphelin et suppression -> Doit réussir
        orphan_tpl_id = save_template("Orphan TPL", "Orphan Manu", [{"reg": 10, "name": "Orphan Point", "function": 3}])
        ok, err = delete_template(orphan_tpl_id)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_fleet_format_conversions(self):
        fleet_def = {
            "name": "CAREL pcos",
            "notes": "GTA Aermec",
            "reads": [
                {"label": "Temp Pulsion", "function": 3, "address": 10, "type": "u16", "scale": 0.1, "unit": "°C"}
            ]
        }
        local_norm = normalize_fleet_definition_to_local("CAREL pcos", fleet_def)
        self.assertEqual(local_norm["name"], "CAREL pcos")
        self.assertEqual(local_norm["manufacturer"], "GTA Aermec")
        self.assertEqual(len(local_norm["registers"]), 1)
        self.assertEqual(local_norm["registers"][0]["reg"], 10)
        self.assertEqual(local_norm["registers"][0]["type"], "uint16")

        # Test conversion inverse
        tpl_dict = {
            "name": "CAREL pcos",
            "manufacturer": "GTA Aermec",
            "registers_json": '[{"reg": 10, "function": 3, "name": "Temp Pulsion", "type": "uint16", "scale": 0.1, "unit": "°C"}]'
        }
        fleet_out = format_local_template_for_fleet(tpl_dict)
        self.assertEqual(fleet_out["name"], "CAREL pcos")
        self.assertEqual(len(fleet_out["reads"]), 1)
        self.assertEqual(fleet_out["reads"][0]["address"], 10)

if __name__ == '__main__':
    unittest.main()
