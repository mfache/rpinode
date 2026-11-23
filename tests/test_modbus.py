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


class TestDashboardPoints(unittest.TestCase):
    """Points affichés et modifiables sur le tableau de bord (on_dashboard)."""

    def setUp(self):
        from services.modbus_mgr import save_template, add_device_to_site, save_device_points_selection, get_site_modbus_points
        init_db()
        with get_db_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO sites (name, external_id) VALUES ('TEST_SITE_DASH', '9998')")
            conn.commit()
            self.site_id = conn.execute("SELECT id FROM sites WHERE name = 'TEST_SITE_DASH'").fetchone()["id"]
        regs = [
            {"reg": 332, "function": 3, "name": "Consigne CO2", "type": "int16", "scale": 1.0, "unit": "ppm"},
            {"reg": 93, "function": 3, "name": "Consigne T", "type": "int16", "scale": 0.1, "unit": "°C", "base": 1},
            {"reg": 211, "function": 4, "name": "CO2", "type": "int16", "scale": 1.0, "unit": "ppm"},
            {"reg": 6, "function": 1, "name": "Commande", "type": "int16", "scale": 1.0, "unit": ""},
        ]
        self.tpl_id = save_template("Test TPL Dash", "Test", regs)
        self.device_id = add_device_to_site(self.site_id, self.tpl_id, "GPE-TEST", "tcp", "127.0.0.1", 502, slave_unit=2)
        save_device_points_selection(self.device_id, self.site_id, [dict(r, is_monitored=True) for r in regs])
        self.pts = {(p["function"], p["reg"]): p for p in get_site_modbus_points(self.site_id)}

    def tearDown(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM modbus_points WHERE site_id = ?", (self.site_id,))
            conn.execute("DELETE FROM modbus_devices WHERE site_id = ?", (self.site_id,))
            conn.execute("DELETE FROM modbus_templates WHERE id = ?", (self.tpl_id,))
            conn.execute("DELETE FROM sites WHERE id = ?", (self.site_id,))
            conn.commit()

    def test_choix_des_points_du_tableau_de_bord(self):
        from services.modbus_mgr import get_dashboard_points
        pid = self.pts[(3, 332)]["id"]
        self.assertEqual(get_dashboard_points(self.site_id), [])

        update_point_settings(pid, on_dashboard=True)
        dash = get_dashboard_points(self.site_id)
        self.assertEqual([p["id"] for p in dash], [pid])
        self.assertEqual(dash[0]["device_name"], "GPE-TEST")

        # Un autre réglage (enregistrement) ne doit pas retirer le point du tableau de bord
        update_point_settings(pid, is_recorded=True)
        self.assertEqual(len(get_dashboard_points(self.site_id)), 1)

        update_point_settings(pid, on_dashboard=False)
        self.assertEqual(get_dashboard_points(self.site_id), [])

    def test_points_modifiables(self):
        from services.modbus_mgr import is_point_writable
        self.assertTrue(is_point_writable(self.pts[(3, 332)]))
        self.assertTrue(is_point_writable(self.pts[(1, 6)]))
        self.assertFalse(is_point_writable(self.pts[(4, 211)]))
        self.assertFalse(is_point_writable({"function": 3, "type": "float32"}))

    def test_conversion_de_la_valeur_saisie(self):
        from services.modbus_mgr import raw_value_for_write
        self.assertEqual(raw_value_for_write({"function": 3, "type": "int16", "scale": 1.0}, 350), 350)
        self.assertEqual(raw_value_for_write({"function": 3, "type": "int16", "scale": 0.1}, 21.5), 215)
        self.assertEqual(raw_value_for_write({"function": 3, "type": "int16", "scale": 0.1}, -5), (-50) & 0xFFFF)
        self.assertEqual(raw_value_for_write({"function": 1}, 1), 1)
        with self.assertRaises(ValueError):
            raw_value_for_write({"function": 3, "type": "int16", "scale": 1.0}, 40000)
        with self.assertRaises(ValueError):
            raw_value_for_write({"function": 3, "type": "uint16", "scale": 1.0}, -1)
        with self.assertRaises(ValueError):
            raw_value_for_write({"function": 1}, 2)

    def test_ecriture_relue_et_diffusee(self):
        from unittest import mock
        from services import modbus_mgr
        pid = self.pts[(3, 93)]["id"]  # échelle 0.1, base 1
        with mock.patch.object(modbus_mgr, "write_single_register") as w, \
             mock.patch.object(modbus_mgr, "read_point_value", return_value=("21.5", "21.5")) as r, \
             mock.patch("services.mqtt_service.mqtt_client.publish") as pub:
            res = modbus_mgr.write_point_value(pid, 21.5)
        # base 1 : registre 93 de la doc = adresse 92 sur le bus ; esclave 2 ; 21.5 °C -> 215
        self.assertEqual(w.call_args[0][:6], ("tcp", "127.0.0.1", 502, 2, 92, 215))
        r.assert_called_once()
        self.assertEqual(res["display"], "21.5 °C")
        self.assertIsNone(res["error"])
        self.assertEqual(pub.call_args[0][0], f"rpinode/modbus/point/{pid}")
        with get_db_connection() as conn:
            self.assertEqual(conn.execute("SELECT last_value FROM modbus_points WHERE id = ?", (pid,)).fetchone()[0], "21.5")

    def test_ecriture_refusee_en_lecture_seule(self):
        from unittest import mock
        from services import modbus_mgr
        with mock.patch.object(modbus_mgr, "write_single_register") as w:
            with self.assertRaises(ValueError):
                modbus_mgr.write_point_value(self.pts[(4, 211)]["id"], 500)
            with self.assertRaises(LookupError):
                modbus_mgr.write_point_value(999999999, 1)
        w.assert_not_called()
