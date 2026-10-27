import unittest
from unittest.mock import patch

from services import network_config, shortcuts


class TestValidation(unittest.TestCase):

    def test_adresse_de_gtc_acceptee(self):
        self.assertEqual(shortcuts.valider(" GTC ", "https://10.103.40.7/UI/"),
                         ("GTC", "https://10.103.40.7/UI/", "10.103.40.7"))

    def test_port_accepte(self):
        self.assertEqual(shortcuts.valider("Automate", "http://192.168.1.20:8080/")[2], "192.168.1.20")

    def test_refus(self):
        for libelle, url in [
            ("", "https://10.103.40.7/"),             # libellé vide
            ("GTC", "ftp://10.103.40.7/"),            # schéma
            ("GTC", "https://gtc.local/"),            # nom d'hôte : rien à router
            ("GTC", "https://8.8.8.8/"),              # IP publique
            ("GTC", "http://127.0.0.1:8081/"),        # le boîtier lui-même
            ("GTC", "https://10.103.40.7:99999/"),    # port invalide
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                shortcuts.valider(libelle, url)


class TestRoutes(unittest.TestCase):

    @patch("services.shortcuts.list_shortcuts", return_value=[
        {"id": 1, "label": "GTC", "url": "https://10.103.40.7/UI/"},
        {"id": 2, "label": "GTC (http)", "url": "http://10.103.40.7/"},
        {"id": 3, "label": "Automate", "url": "http://10.103.40.20/"},
    ])
    @patch("services.shortcuts._current_site", return_value={"id": 1286, "external_id": "23"})
    def test_une_route_par_equipement(self, _site, _liste):
        self.assertEqual(shortcuts.current_routes(), ["10.103.40.20/32", "10.103.40.7/32"])
        self.assertEqual(shortcuts.sync_payload()["external_id"], "23")

    @patch("services.shortcuts._current_site", return_value=None)
    def test_sans_chantier_aucune_route(self, _site):
        self.assertEqual(shortcuts.current_routes(), [])
        self.assertIsNone(shortcuts.sync_payload())


@patch("services.headscale_enroll.is_headscale_active", return_value=True)
@patch("services.fleet.fleet")
@patch("services.network_config.subprocess.run")
class TestPublication(unittest.TestCase):
    """publish_tailscale_routes : uniquement des simulations (ni tailscale, ni docs)."""

    def setUp(self):
        network_config._routes_publiees = None

    def test_publie_puis_ne_republie_pas_a_l_identique(self, run, fleet, _hs):
        run.return_value.returncode = 0
        fleet.headscale_sync_routes.return_value = True
        with patch("services.shortcuts.current_routes", return_value=["10.103.40.7/32"]):
            network_config.publish_tailscale_routes()
            network_config.publish_tailscale_routes()
        run.assert_called_once()
        self.assertIn("--advertise-routes=10.103.40.7/32", run.call_args.args[0])
        fleet.headscale_sync_routes.assert_called_once_with(["10.103.40.7/32"])

    def test_aucun_raccourci_retire_les_routes(self, run, fleet, _hs):
        run.return_value.returncode = 0
        fleet.headscale_sync_routes.return_value = True
        with patch("services.shortcuts.current_routes", return_value=[]):
            network_config.publish_tailscale_routes()
        self.assertIn("--advertise-routes=", run.call_args.args[0])
        fleet.headscale_sync_routes.assert_called_once_with([])

    def test_echec_cote_docs_retente_au_passage_suivant(self, run, fleet, _hs):
        run.return_value.returncode = 0
        fleet.headscale_sync_routes.return_value = False
        with patch("services.shortcuts.current_routes", return_value=["10.103.40.7/32"]):
            network_config.publish_tailscale_routes()
            network_config.publish_tailscale_routes()
        self.assertEqual(fleet.headscale_sync_routes.call_count, 2)


if __name__ == "__main__":
    unittest.main()
