import json
import unittest
from unittest.mock import patch

from core.paths import FORCED_SITE_FILE
from services import presence, tracker


class TestChantierForce(unittest.TestCase):
    """Chantier forcé : rétabli après un redémarrage et jamais remplacé par le
    tracker. run_tests.sh redirige FORCED_SITE_FILE vers un fichier temporaire."""

    def setUp(self):
        FORCED_SITE_FILE.write_text(json.dumps({"name": "CCSE", "external_id": "23"}), encoding="utf-8")

    def tearDown(self):
        FORCED_SITE_FILE.unlink(missing_ok=True)

    @patch("services.presence.label_current_location")
    @patch("services.presence.clear_current_location")
    @patch("services.presence._boot_id", return_value="boot-B")
    def test_redemarrage_retablit_le_chantier_force(self, _boot, effacer, etiqueter):
        self.assertFalse(presence.clear_current_location_if_new_boot())
        effacer.assert_not_called()
        etiqueter.assert_called_once_with("CCSE", external_id="23")

    @patch("services.tracker.label_current_location")
    @patch("services.tracker.fleet")
    @patch("services.tracker.get_current_site_name", return_value="H66")
    @patch("services.tracker._find_local_site_for_gsm", return_value=None)
    @patch("services.tracker.get_gsm_info", return_value={"mcc": "206", "mnc": "01", "enodeb": "1"})
    def test_tracker_respecte_le_chantier_force(self, _gsm, _local, _courant, fleet, etiqueter):
        fleet.is_registered.return_value = True
        tracker.check_and_update_site()
        fleet.sync_location.assert_called_once_with({"mcc": "206", "mnc": "01", "enodeb": "1"}, site_hint_name="CCSE")
        etiqueter.assert_called_once_with("CCSE", external_id="23")

    def test_liberer_supprime_le_forcage(self):
        presence.release_forced_site()
        self.assertIsNone(presence.get_forced_site())


if __name__ == "__main__":
    unittest.main()
