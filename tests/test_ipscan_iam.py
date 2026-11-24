"""Scan IP : hôtes BACnet qui répondent I-Am en broadcast (merge_iams)."""
import unittest

from services.ipscan import merge_iams


class TestMergeIams(unittest.TestCase):
    def test_ajoute_le_port_et_l_instance(self):
        devices = [{"ip": "192.168.1.1", "ports": [80]}]
        changed = merge_iams(devices, {"192.168.1.1": {"instance": 80856, "vendor_id": 264}})
        self.assertEqual(changed, devices)
        self.assertEqual(devices[0]["ports"], [80, 47808])
        self.assertEqual(devices[0]["bacnet_instance"], 80856)

    def test_deja_vu_par_la_sonde_unicast(self):
        devices = [{"ip": "192.168.1.201", "ports": [443, 47808], "bacnet_instance": 5001}]
        changed = merge_iams(devices, {"192.168.1.201": {"instance": 5001, "vendor_id": 36}})
        self.assertEqual(changed, [])
        self.assertEqual(devices[0]["ports"], [443, 47808])

    def test_port_vu_sans_instance(self):
        devices = [{"ip": "192.168.1.4", "ports": [80, 47808]}]
        changed = merge_iams(devices, {"192.168.1.4": {"instance": 20406, "vendor_id": 264}})
        self.assertEqual(len(changed), 1)
        self.assertEqual(devices[0]["ports"], [80, 47808])
        self.assertEqual(devices[0]["bacnet_instance"], 20406)

    def test_hote_sans_reponse_inchange(self):
        devices = [{"ip": "192.168.1.100", "ports": [445]}]
        self.assertEqual(merge_iams(devices, {"192.168.1.1": {"instance": 1, "vendor_id": 264}}), [])
        self.assertEqual(devices[0]["ports"], [445])


if __name__ == "__main__":
    unittest.main()
