import unittest
from unittest.mock import patch

from services import wifi_mgr

H66 = {"ssid": "H66", "psk": "x", "method": "auto", "addresses": None, "gateway": None, "dhcp_range": None}


@patch("services.wifi_mgr._ensure_ap_mode")
@patch("services.wifi_mgr._ensure_client_mode")
@patch("services.wifi_mgr._get_site_wifi_config", return_value=H66)
@patch("services.wifi_mgr._get_current_site_info", return_value={"id": 3, "name": "H66"})
@patch("services.wifi_mgr.ping_check", return_value=False)
class TestSans4G(unittest.TestCase):
    """Règle A de manage_wifi quand wwan0 est hors ligne (adaptation du 2026-10-25, voir core/wifi.md).

    Uniquement des simulations : ni base, ni nmcli, sans risque sur un boîtier en service.
    """

    def test_rpirescue_visible_reste_prioritaire(self, _ping, _site, _cfg, client, ap):
        with patch("services.wifi_mgr._is_ssid_visible", return_value=True):
            wifi_mgr.manage_wifi()
        client.assert_called_once_with(wifi_mgr.RESCUE_CON_NAME, force=False)
        ap.assert_not_called()

    def test_wifi_du_chantier_visible_passe_avant_l_ap(self, _ping, _site, _cfg, client, ap):
        with patch("services.wifi_mgr._is_ssid_visible", side_effect=lambda ssid: ssid == "H66"):
            wifi_mgr.manage_wifi()
        self.assertEqual(client.call_args.args[:2], ("site-3-wifi", "H66"))
        ap.assert_not_called()

    def test_wifi_du_chantier_absent_donne_l_ap(self, _ping, _site, _cfg, client, ap):
        with patch("services.wifi_mgr._is_ssid_visible", return_value=False):
            wifi_mgr.manage_wifi()
        client.assert_not_called()
        ap.assert_called_once()

    def test_sans_chantier_courant_donne_l_ap(self, _ping, site, _cfg, client, ap):
        site.return_value = None
        with patch("services.wifi_mgr._is_ssid_visible", side_effect=lambda ssid: ssid == "H66"):
            wifi_mgr.manage_wifi()
        client.assert_not_called()
        ap.assert_called_once()


if __name__ == "__main__":
    unittest.main()
