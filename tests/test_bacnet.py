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
    format_local_template_for_fleet,
    read_bacnet_points_live_raw,
    add_points_to_suivi
)
from services.bacnet_catalog import upsert_device_points, search_points, count_search_points

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

    @patch("services.mqtt_service.mqtt_client.publish")
    @patch("paho.mqtt.client.Client")
    def test_read_bacnet_points_live_raw(self, mock_client_cls, mock_publish):
        pts = [
            {"key": "k1", "address": "192.168.1.100", "object_id": "analogInput:1", "device_id": 1234}
        ]
        # Test avec liste vide
        self.assertEqual(read_bacnet_points_live_raw([]), {})

        # Test formatage float
        res = read_bacnet_points_live_raw(pts, timeout=0.01)
        self.assertIn("k1", res)
        self.assertEqual(res["k1"]["display"], "—")
        self.assertEqual(res["k1"]["error"], "Timeout")

    @patch("services.bacnet_catalog.get_current_site_id")
    def test_search_points_includes_device_name(self, mock_site_id):
        mock_site_id.return_value = self.site_id

        # Insérer un device découvert avec son nom BACnet
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO discovered_devices (site_id, mac, last_ip, bacnet_instance, bacnet_name)
                VALUES (?, '00:11:22:33:44:55', '172.31.13.179', 1329, 'Automate_Etage1')
                """,
                (self.site_id,)
            )
            conn.commit()

        # Insérer des points dans le dictionnaire
        objects = [
            {"object_id": "analog-input:1", "name": "BExt25'R'Flr01'R01'RHvacCoo'RTemp"},
            {"object_id": "analog-value:2", "name": "BExt25'R'Flr02'R01'RHvacHeat'RTemp"}
        ]
        upsert_device_points(self.site_id, "172.31.13.179", 1329, objects)

        # Recherche avec jokers
        results = search_points("*RHvacCoo*")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["device_instance"], 1329)
        self.assertEqual(results[0]["device_name"], "Automate_Etage1")
        self.assertEqual(results[0]["object_id"], "analog-input:1")
        self.assertEqual(results[0]["is_monitored"], 0)

        # Marquer comme suivi via add_points_to_suivi
        pts_to_track = [{
            "network_address": "172.31.13.179",
            "device_instance": 1329,
            "object_id": "analog-input:1",
            "name": "BExt25'R'Flr01'R01'RHvacCoo'RTemp"
        }]
        added = add_points_to_suivi(self.site_id, pts_to_track)
        self.assertEqual(added, 1)

        # Vérifier qu'il apparaît bien dans get_site_bacnet_points (only_monitored=True)
        monitored = get_site_bacnet_points(self.site_id, only_monitored=True)
        self.assertTrue(any(p["object_id"] == "analog-input:1" and p["device_instance"] == 1329 for p in monitored))
        tracked_pt = next(p for p in monitored if p["object_id"] == "analog-input:1")
        self.assertEqual(tracked_pt["device_name"], "Automate_Etage1")

        # Vérifier que search_points retourne maintenant is_monitored = 1
        results_after = search_points("*RHvacCoo*")
        self.assertEqual(len(results_after), 1)
        self.assertEqual(results_after[0]["is_monitored"], 1)

    def test_add_points_to_suivi_with_and_without_device(self):
        # 1. Point avec appareil existant (device_id 1234 -> self.device_id)
        pts = [
            {
                "network_address": "192.168.1.100",
                "device_instance": 1234,
                "object_id": "analogInput:1",
                "name": "Temp Test Suivi"
            },
            {
                "network_address": "10.0.0.50",
                "device_instance": 9999,
                "object_id": "analogValue:10",
                "name": "Point Sans Appareil"
            }
        ]
        count = add_points_to_suivi(self.site_id, pts)
        self.assertEqual(count, 2)

        monitored = get_site_bacnet_points(self.site_id, only_monitored=True)
        self.assertEqual(len(monitored), 2)
        
        # Le premier point doit être lié à self.device_id et avoir le nom du device
        p1 = next(p for p in monitored if p["device_instance"] == 1234)
        self.assertEqual(p1["device_id"], self.device_id)
        self.assertEqual(p1["device_name"], "Test Device BACnet")

        # Le second point a device_id NULL et le fallback 'Appareil BACnet'
        p2 = next(p for p in monitored if p["device_instance"] == 9999)
        self.assertIsNone(p2["device_id"])
        self.assertEqual(p2["device_name"], "Appareil BACnet")

    @patch("services.bacnet_catalog.get_current_site_id")
    def test_search_points_pagination(self, mock_site_id):
        mock_site_id.return_value = self.site_id

        # Insérer 15 points
        objects = [{"object_id": f"analog-input:{i}", "name": f"Sensor_Temp_{i:02d}"} for i in range(15)]
        upsert_device_points(self.site_id, "172.31.13.100", 2000, objects)

        total = count_search_points("*Sensor_Temp*", site_id=self.site_id)
        self.assertEqual(total, 15)

        # Page 1 (limit 10, offset 0)
        page1 = search_points("*Sensor_Temp*", site_id=self.site_id, limit=10, offset=0)
        self.assertEqual(len(page1), 10)

        # Page 2 (limit 10, offset 10)
        page2 = search_points("*Sensor_Temp*", site_id=self.site_id, limit=10, offset=10)
        self.assertEqual(len(page2), 5)

        # Vérifier qu'il n'y a pas de doublon entre les pages
        p1_objs = {p["object_id"] for p in page1}
        p2_objs = {p["object_id"] for p in page2}
        self.assertEqual(len(p1_objs.intersection(p2_objs)), 0)

if __name__ == '__main__':
    unittest.main()
