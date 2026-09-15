import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import core.paths as paths
from core.database import get_db_connection, init_db
from services.live_view import LiveViewService


class TestLiveViewBacnet(unittest.TestCase):
    """Non-regression : la scrutation Live View pour des points BACnet doit
    reellement lire une valeur (protocole longtemps reste un stub `pass`,
    jamais branche sur bacnet_daemon, cf. incident 'les points ne changent
    pas sur la vue' remonte depuis reports-dev)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = paths.DATABASE_FILE
        paths.DATABASE_FILE = Path(self.temp_dir.name) / "test_rpinode_live_view.db"
        init_db()

        with get_db_connection() as conn:
            conn.execute("INSERT INTO sites (name, external_id) VALUES ('TEST_SITE_LIVEVIEW', '9999')")
            conn.commit()
            site_id = conn.execute("SELECT id FROM sites WHERE name = 'TEST_SITE_LIVEVIEW'").fetchone()["id"]
            conn.execute(
                """INSERT INTO bacnet_points
                   (site_id, network_address, device_instance, object_id, name)
                   VALUES (?, ?, ?, ?, ?)""",
                (site_id, "172.31.12.150", 1042, "analog-value:20", "Point test"),
            )
            conn.commit()

        self.service = LiveViewService()

    def tearDown(self):
        paths.DATABASE_FILE = self.original_db
        self.temp_dir.cleanup()

    def test_poll_bacnet_resout_adresse_et_publie_la_valeur(self):
        points = [{"protocol": "bacnet", "device": "1042", "obj": "analog-value:20"}]
        results = {}

        with patch("services.bacnet_mgr.read_bacnet_points_live_raw") as mock_read:
            mock_read.return_value = {
                "1042|analog-value:20": {"value": 21.5, "display": "21.5", "error": None}
            }
            self.service._poll_bacnet(points, results)

            # L'adresse reseau resolue depuis la base locale doit bien etre
            # transmise a la lecture groupee (et pas la valeur brute non resolue).
            called_points = mock_read.call_args[0][0]
            self.assertEqual(len(called_points), 1)
            self.assertEqual(called_points[0]["address"], "172.31.12.150")
            self.assertEqual(called_points[0]["device_id"], 1042)

        expected_key = f"{self.service.boitier_id}|bacnet|1042|analog-value:20"
        self.assertIn(expected_key, results)
        self.assertEqual(results[expected_key], {"v": "21.5", "c": 0})

    def test_poll_bacnet_point_introuvable_en_base_ne_plante_pas(self):
        points = [{"protocol": "bacnet", "device": "9999", "obj": "analog-value:1"}]
        results = {}

        with patch("services.bacnet_mgr.read_bacnet_points_live_raw") as mock_read:
            self.service._poll_bacnet(points, results)
            mock_read.assert_not_called()

        self.assertEqual(results, {})


if __name__ == "__main__":
    unittest.main()
