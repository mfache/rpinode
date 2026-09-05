import unittest
from unittest.mock import patch
from core.database import get_db_connection, init_db
from services.presence import (
    label_current_location,
    get_current_site_name,
    get_current_site_id,
    is_current_site_provisional
)

class TestPresence(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_db_connection() as conn:
            row = conn.execute("SELECT site_id FROM node_presence WHERE is_current = 1 LIMIT 1").fetchone()
            self.orig_site_id = row["site_id"] if row else None

    def tearDown(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM site_antennas WHERE antenna_id IN (SELECT id FROM antennas WHERE enodeb = 999999)")
            conn.execute("DELETE FROM antennas WHERE enodeb = 999999")
            conn.execute("DELETE FROM sites WHERE name IN ('AUTO-999999', 'Chantier Test Alpha')")
            if self.orig_site_id:
                conn.execute("UPDATE node_presence SET is_current = CASE WHEN site_id = ? THEN 1 ELSE 0 END", (self.orig_site_id,))
            conn.commit()

    @patch("services.presence.get_gsm_info")
    def test_auto_site_is_provisional(self, mock_gsm):
        mock_gsm.return_value = {
            "mcc": "206",
            "mnc": "01",
            "enodeb": 999999,
            "cid": "0DBDB179",
            "lac": "0000",
            "tac": "00084D",
            "gps": None
        }
        
        # 1. Étiquetage avec nom AUTO
        res = label_current_location("AUTO-999999", is_provisional=False, external_id="999999")
        self.assertTrue(res)
        self.assertEqual(get_current_site_name(), "AUTO-999999")
        # Doit être considéré comme provisoire car préfixé par AUTO-
        self.assertTrue(is_current_site_provisional())

        # 2. Renommage en nom réel
        res2 = label_current_location("Chantier Test Alpha")
        self.assertTrue(res2)
        self.assertEqual(get_current_site_name(), "Chantier Test Alpha")
        # Ne doit plus être provisoire
        self.assertFalse(is_current_site_provisional())

        # Vérification en base : le site doit avoir conservé external_id="999999"
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM sites WHERE name = 'Chantier Test Alpha'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["external_id"], "999999")
            self.assertEqual(row["is_provisional"], 0)

if __name__ == "__main__":
    unittest.main()
