import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.services.device_mgr import (
    list_serial_ports,
    list_usb_devices,
    list_system_devices,
    render_devices_components,
    is_moxa_driver_installed,
    get_moxa_driver_info,
)

class TestDeviceMgr(unittest.TestCase):
    def test_list_system_devices_structure(self):
        """Vérifie la structure retournée par list_system_devices."""
        devices = list_system_devices()
        self.assertIsInstance(devices, dict)
        self.assertIn("moxa_connected", devices)
        self.assertIn("moxa_device", devices)
        self.assertIn("moxa_driver", devices)
        self.assertIn("rs485_ports", devices)
        self.assertIn("modem_ports", devices)
        self.assertIn("usb_devices", devices)
        self.assertIn("total_serial", devices)
        self.assertIn("total_usb", devices)

    def test_moxa_driver_info(self):
        """Vérifie les informations du pilote Moxa."""
        info = get_moxa_driver_info()
        self.assertIn("installed", info)
        self.assertEqual(info["module_name"], "ti_usb_3410_5052")
        self.assertTrue(info["custom_rs485"])

    def test_list_serial_ports(self):
        """Vérifie la liste des ports série sans erreur."""
        ports = list_serial_ports(include_modems=True)
        self.assertIsInstance(ports, list)
        for p in ports:
            self.assertIn("path", p)
            self.assertIn("capabilities", p)
            self.assertIn("is_moxa", p)

    @patch("subprocess.run")
    def test_list_usb_devices_mock(self, mock_run):
        """Vérifie le parsing de lsusb et le filtrage des root hubs internes."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
                "Bus 001 Device 005: ID 110a:1150 Moxa Technologies Co., Ltd. UPort 1150\n"
                "Bus 001 Device 003: ID 1e0e:9001 Qualcomm / Option SimTech, Incorporated\n"
                "Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub\n"
            )
        )
        usb_list = list_usb_devices()
        self.assertTrue(any(u["is_moxa"] for u in usb_list))
        self.assertTrue(any(u["is_modem"] for u in usb_list))
        self.assertFalse(any(u["vid"] == "1d6b" for u in usb_list))
        self.assertEqual(len(usb_list), 2)

        usb_list_all = list_usb_devices(include_root_hubs=True)
        self.assertEqual(len(usb_list_all), 4)
        self.assertTrue(any(u["vid"] == "1d6b" for u in usb_list_all))

    def test_render_devices_components(self):
        """Vérifie la génération des fragments HTML pour SSE et vue serveur."""
        sys_devs = {
            "moxa_connected": True,
            "moxa_device": {"description": "Moxa Test", "path": "/dev/ttyUSB5", "driver": "ti_usb_3410_5052", "by_id_name": "moxa-by-id"},
            "rs485_ports": [{"path": "/dev/ttyUSB5", "description": "Moxa Test", "driver": "ti_usb_3410_5052", "is_moxa": True, "is_rs485": True, "capabilities": ["bacnet_mstp"]}],
            "modem_ports": [],
            "usb_devices": [{"bus": "1", "device": "5", "vendor_id": "0x110a", "product_id": "0x1150", "description": "Moxa Test", "category": "Passerelle RS-485 / Série", "driver_str": "ti_usb_3410_5052"}],
            "total_serial": 1,
            "total_usb": 1,
        }
        res = render_devices_components(sys_devs, base_url="")
        self.assertIn("moxa-hero-card", res["moxa_card_html"])
        self.assertIn("ttyUSB5", res["serial_ports_table_html"])
        self.assertIn("0x110a:0x1150", res["usb_devices_rows_html"])
        self.assertEqual(res["total_serial"], 1)
        self.assertEqual(res["total_usb"], 1)

        # Test avec passerelle CP210x / MBus (sans Moxa)
        sys_devs_cp210x = {
            "moxa_connected": False,
            "moxa_device": None,
            "gateways": [{"path": "/dev/ttyUSB0", "description": "Silicon Labs CP2102 USB-to-UART (Passerelle M-Bus)", "driver": "cp210x", "driver_label": "Silicon Labs CP210x USB-Série", "by_id_name": "cp2102-by-id", "is_moxa": False, "is_rs485": True, "capabilities": ["bacnet_mstp", "modbus_rtu", "mbus"]}],
            "rs485_ports": [{"path": "/dev/ttyUSB0", "description": "Silicon Labs CP2102 USB-to-UART (Passerelle M-Bus)", "driver": "cp210x", "driver_label": "Silicon Labs CP210x USB-Série", "by_id_name": "cp2102-by-id", "is_moxa": False, "is_rs485": True, "capabilities": ["bacnet_mstp", "modbus_rtu", "mbus"]}],
            "modem_ports": [],
            "usb_devices": [{"bus": "1", "device": "2", "vendor_id": "0x10c4", "product_id": "0xea60", "description": "Silicon Labs CP210x UART Bridge", "category": "Adaptateur Série", "driver_str": "cp210x"}],
            "total_serial": 1,
            "total_usb": 1,
        }
        res_cp210x = render_devices_components(sys_devs_cp210x, base_url="")
        self.assertIn("moxa-hero-card", res_cp210x["moxa_card_html"])
        self.assertIn("CP2102", res_cp210x["moxa_card_html"])
        self.assertIn("cp210x", res_cp210x["moxa_card_html"])
        self.assertIn("M-Bus", res_cp210x["moxa_card_html"])
        self.assertIn("ttyUSB0", res_cp210x["serial_ports_table_html"])
        self.assertIn("M-Bus", res_cp210x["serial_ports_table_html"])

        # Test sans aucune passerelle / aucun port série
        sys_devs_no_moxa = {
            "moxa_connected": False,
            "moxa_device": None,
            "gateways": [],
            "rs485_ports": [],
            "modem_ports": [],
            "usb_devices": [],
            "total_serial": 0,
            "total_usb": 0,
        }
        res_no_moxa = render_devices_components(sys_devs_no_moxa, base_url="")
        self.assertEqual(res_no_moxa["moxa_card_html"], "")
        self.assertIn("Aucun port série", res_no_moxa["serial_ports_table_html"])
        self.assertIn("Aucun périphérique USB", res_no_moxa["usb_devices_rows_html"])

if __name__ == "__main__":
    unittest.main()
