import unittest
from unittest.mock import patch, MagicMock
from src.services.bacnet_mstp import (
    _crc8_header,
    parse_mstp_stream,
    _bus_health,
    get_mstp_snapshot,
    get_mstp_stream_payload,
    get_mstp_signature,
    start_mstp_session,
    stop_mstp_session,
    mstp_available,
    FRAME_TOKEN,
    FRAME_POLL_FOR_MASTER,
)

class TestBacnetMstp(unittest.TestCase):
    def test_crc8_header(self):
        """Vérifie le calcul CRC8 d'en-tête MS/TP."""
        # Trame Token type 0, dest 1, src 2, len 0
        hdr_data = bytes([0x00, 0x01, 0x02, 0x00, 0x00])
        crc = _crc8_header(hdr_data)
        self.assertIsInstance(crc, int)
        self.assertTrue(0 <= crc <= 255)

    def test_parse_mstp_stream_empty(self):
        """Vérifie le parsing d'un flux vide."""
        frames, stats = parse_mstp_stream(b"")
        self.assertEqual(frames, [])
        self.assertEqual(stats["crc_ok"], 0)
        self.assertEqual(stats["crc_bad"], 0)
        self.assertEqual(stats["bytes"], 0)

    def test_parse_mstp_stream_valid_frame(self):
        """Vérifie le décodage d'une trame Token valide."""
        hdr_body = bytes([0x00, 0x01, 0x02, 0x00, 0x00])
        crc = _crc8_header(hdr_body)
        raw_frame = b"\x55\xff" + hdr_body + bytes([crc])

        frames, stats = parse_mstp_stream(raw_frame)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["type"], FRAME_TOKEN)
        self.assertEqual(frames[0]["dest"], 1)
        self.assertEqual(frames[0]["src"], 2)
        self.assertEqual(stats["crc_ok"], 1)
        self.assertEqual(stats["crc_bad"], 0)

    def test_bus_health_no_bytes(self):
        """Vérifie le diagnostic sur bus sans signal."""
        health = _bus_health({"crc_ok": 0, "crc_bad": 0, "stray_bytes": 0, "frames_with_stray": 0, "bytes": 0}, {}, 1000.0)
        self.assertFalse(health["ring_ok"])
        self.assertTrue(any("Aucun octet" in a["text"] for a in health["anomalies"]))

    def test_bus_health_with_token(self):
        """Vérifie le diagnostic sur bus sain avec jeton."""
        types = {FRAME_TOKEN: 10, FRAME_POLL_FOR_MASTER: 2}
        stats = {"crc_ok": 12, "crc_bad": 0, "stray_bytes": 0, "frames_with_stray": 0, "bytes": 100}
        health = _bus_health(stats, types, 1000.0)
        self.assertTrue(health["ring_ok"])
        self.assertEqual(health["tokens"], 10)

    def test_snapshot_and_stream_payload(self):
        """Vérifie la génération de snapshot et payload SSE."""
        snap = get_mstp_snapshot()
        self.assertIsInstance(snap, dict)
        self.assertIn("running", snap)
        self.assertIn("devices", snap)

        payload = get_mstp_stream_payload()
        self.assertIsInstance(payload, dict)
        self.assertIn("running", payload)
        self.assertIn("devices", payload)

        sig = get_mstp_signature()
        self.assertIsInstance(sig, dict)
        self.assertIn("running", sig)

    def test_start_session_validation(self):
        """Vérifie le rejet de paramètres invalides au démarrage."""
        ok, msg = start_mstp_session({"device": ""})
        self.assertFalse(ok)
        self.assertIn("Choisissez un périphérique", msg)

        ok, msg = start_mstp_session({"device": "/dev/non_existent_serial_device_xyz"})
        self.assertFalse(ok)
        self.assertIn("introuvable", msg)

if __name__ == "__main__":
    unittest.main()
