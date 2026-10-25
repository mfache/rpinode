import json
import unittest
from unittest.mock import patch

from core.paths import CURRENT_SITE_FILE
from services import presence


class TestChantierConserveSansReboot(unittest.TestCase):
    """clear_current_location_if_new_boot : une relance de rpinode (même boot
    système) garde le chantier ; un vrai redémarrage du boîtier l'efface.

    run_tests.sh redirige CURRENT_SITE_FILE vers un fichier temporaire : le
    vrai fichier de session de production n'est jamais touché."""

    def tearDown(self):
        CURRENT_SITE_FILE.unlink(missing_ok=True)

    def _session(self, boot_id):
        CURRENT_SITE_FILE.write_text(json.dumps({"id": 3, "name": "H66", "is_provisional": False,
                                                 "boot_id": boot_id}), encoding="utf-8")

    @patch("services.presence.clear_current_location")
    @patch("services.presence._boot_id", return_value="boot-A")
    def test_relance_dans_le_meme_boot_conserve_le_chantier(self, _boot, effacer):
        self._session("boot-A")
        self.assertFalse(presence.clear_current_location_if_new_boot())
        effacer.assert_not_called()

    @patch("services.presence.clear_current_location")
    @patch("services.presence._boot_id", return_value="boot-B")
    def test_redemarrage_du_boitier_efface_le_chantier(self, _boot, effacer):
        self._session("boot-A")
        self.assertTrue(presence.clear_current_location_if_new_boot())
        effacer.assert_called_once()

    @patch("services.presence.clear_current_location")
    @patch("services.presence._boot_id", return_value="boot-A")
    def test_session_sans_boot_id_est_effacee(self, _boot, effacer):
        """Fichier écrit avant cette évolution : on reste prudent."""
        self._session(None)
        self.assertTrue(presence.clear_current_location_if_new_boot())
        effacer.assert_called_once()

    @patch("services.presence.clear_current_location")
    @patch("services.presence._boot_id", return_value=None)
    def test_boot_id_illisible_efface_par_prudence(self, _boot, effacer):
        self._session(None)
        self.assertTrue(presence.clear_current_location_if_new_boot())
        effacer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
