import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.services.device_mgr import (
    list_serial_ports,
    list_usb_devices,
    list_system_devices,
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
        """Vérifie le parsing de lsusb."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Bus 001 Device 005: ID 110a:1150 Moxa Technologies Co., Ltd. UPort 1150\n"
                "Bus 001 Device 003: ID 1e0e:9001 Qualcomm / Option SimTech, Incorporated\n"
            )
        )
        usb_list = list_usb_devices()
        self.assertTrue(any(u["is_moxa"] for u in usb_list))
        self.assertTrue(any(u["is_modem"] for u in usb_list))

if __name__ == "__main__":
    unittest.main()
