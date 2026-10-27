import unittest
from unittest.mock import patch

from services import wifi_mgr

H66 = {"ssid": "H66", "psk": "x", "method": "auto", "addresses": None, "gateway": None, "dhcp_range": None}


@patch("services.wifi_mgr._connect_site_wifi")
@patch("services.wifi_mgr._ensure_client_mode")
@patch("services.wifi_mgr._get_site_wifi_config", return_value=H66)
@patch("services.wifi_mgr._get_current_site_info", return_value={"id": 3, "name": "H66"})
@patch("services.wifi_mgr.ping_check", return_value=False)
class TestSans4G(unittest.TestCase):
    """Règle A de manage_wifi quand wwan0 est hors ligne (voir core/wifi.md).

    Uniquement des simulations : ni base, ni nmcli, sans risque sur un boîtier en service.
    """

    def test_rpirescue_visible_reste_prioritaire(self, _ping, _site, _cfg, client, chantier):
        with patch("services.wifi_mgr._is_ssid_visible", return_value=True):
            wifi_mgr.manage_wifi()
        client.assert_called_once_with(wifi_mgr.RESCUE_CON_NAME, force=False)
        chantier.assert_not_called()

    def test_wifi_du_chantier_visible(self, _ping, _site, _cfg, client, chantier):
        with patch("services.wifi_mgr._is_ssid_visible", side_effect=lambda ssid: ssid == "H66"):
            wifi_mgr.manage_wifi()
        chantier.assert_called_once_with(3, H66, force=False)
        client.assert_not_called()

    def test_rien_de_visible_ne_touche_pas_au_wifi(self, _ping, _site, _cfg, client, chantier):
        with patch("services.wifi_mgr._is_ssid_visible", return_value=False):
            wifi_mgr.manage_wifi()
        client.assert_not_called()
        chantier.assert_not_called()

    def test_sans_chantier_courant_ne_touche_pas_au_wifi(self, _ping, site, _cfg, client, chantier):
        site.return_value = None
        with patch("services.wifi_mgr._is_ssid_visible", side_effect=lambda ssid: ssid == "H66"):
            wifi_mgr.manage_wifi()
        client.assert_not_called()
        chantier.assert_not_called()

    def test_plus_de_point_d_acces(self, *_):
        self.assertFalse(hasattr(wifi_mgr, "_ensure_ap_mode"))
        self.assertFalse(hasattr(wifi_mgr, "get_ap_config"))


if __name__ == "__main__":
    unittest.main()
