import unittest
from unittest.mock import patch, MagicMock

from src.services.reporter import StatusReporter

class TestStatusReporter(unittest.TestCase):
    @patch("src.services.reporter.mqtt_client.publish")
    @patch("src.services.reporter.get_network_overview")
    @patch("src.services.reporter.get_gsm_info")
    @patch("src.services.reporter.get_current_site_name")
    @patch("src.services.reporter.is_current_site_provisional")
    @patch("src.services.reporter.get_ap_config")
    @patch("src.services.reporter.get_sys")
    @patch("src.services.reporter.load_ipscan_results")
    @patch("src.services.reporter.is_ipscan_running")
    def test_report_status_publishes_lightweight_telemetry_only(
        self,
        mock_ipscan_running,
        mock_load_ipscan,
        mock_get_sys,
        mock_get_ap,
        mock_is_prov,
        mock_get_site_name,
        mock_get_gsm,
        mock_get_net,
        mock_publish,
    ):
        mock_ipscan_running.return_value = False
        mock_load_ipscan.return_value = {"scanned_at": "2026-09-05 12:00:00"}
        mock_get_sys.return_value = "45.0"
        mock_get_ap.return_value = {"ssid": "RPINODE-TEST", "password": "secretpassword"}
        mock_is_prov.return_value = False
        mock_get_site_name.return_value = "Chantier Test"
        mock_get_gsm.return_value = {"mcc": "208", "mnc": "10", "enodeb": "12345"}
        mock_get_net.return_value = {
            "wwan0": {"active": True, "ip": "10.0.0.1"},
            "eth0": {"active": True, "ip": "192.168.1.50", "mac": "aa:bb:cc:dd:ee:ff", "routes": []},
            "wlan0": {"active": False, "ip": "Non détectée", "mac": "-", "routes": []},
            "tailscale": {"active": True, "ip": "100.64.0.1", "name": "rpinode-test", "routes": []},
        }

        reporter = StatusReporter(interval=1)
        reporter.report_status()

        # Collecter tous les appels de publication MQTT
        published = {}
        for call_args in mock_publish.call_args_list:
            topic = call_args[0][0]
            payload = call_args[0][1]
            published[topic] = payload

        # Vérifier que les topics télémétrie légers sont publiés
        self.assertIn("rpinode/status/system", published)
        self.assertIn("rpinode/status/site", published)
        self.assertIn("rpinode/status/network", published)
        self.assertIn("rpinode/status/gsm", published)
        self.assertIn("rpinode/status/services", published)

        # Vérifier que rpinode/status/devices N'EST PLUS publié en tâche de fond (on-demand SSE)
        self.assertNotIn("rpinode/status/devices", published)

        # Vérifier rpinode/status/site : pas de balise HTML
        site_payload = published["rpinode/status/site"]
        self.assertNotIn("site_name_html", site_payload)
        self.assertEqual(site_payload["site_name"], "Chantier Test")

    @patch("services.device_mgr.list_system_devices")
    def test_devices_stream_handler(self, mock_list_devices):
        """Vérifie que handle_devices_stream génère un flux SSE lors de la connexion."""
        mock_list_devices.return_value = {
            "moxa_connected": False,
            "moxa_device": None,
            "gateways": [],
            "rs485_ports": [],
            "modem_ports": [],
            "usb_devices": [],
            "total_serial": 0,
            "total_usb": 0,
            "dirty_count": 0,
        }

        from src.web.stream import handle_devices_stream
        handler = MagicMock()
        written = []

        def fake_write(data):
            written.append(data)
            # Simule une déconnexion du client après le premier envoi
            raise ConnectionResetError()

        handler.wfile.write.side_effect = fake_write

        handle_devices_stream(handler)

        handler.send_response.assert_called_with(200)
        handler.send_header.assert_any_call('Content-type', 'text/event-stream')
        self.assertTrue(any(b"data:" in w for w in written))
        self.assertTrue(any(b"total_serial" in w for w in written))

if __name__ == "__main__":
    unittest.main()
