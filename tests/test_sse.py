import unittest
from unittest.mock import MagicMock

from src.web.stream import SSEMonitorHub, handle_sse_monitor_stream


class TestSSEMonitorHub(unittest.TestCase):
    def setUp(self):
        self.hub = SSEMonitorHub()

    def test_initial_state_empty(self):
        self.assertEqual(self.hub.get_active_streams(), [])
        self.assertFalse(self.hub.is_stream_active("/api/stream"))
        self.assertFalse(self.hub.is_stream_active("/api/devices/stream"))

    def test_register_and_unregister_client(self):
        # Register a client on /api/devices/stream
        self.hub.register_client("/api/devices/stream", "client_1", "192.168.1.42")
        self.assertTrue(self.hub.is_stream_active("/api/devices/stream"))
        
        active = self.hub.get_active_streams()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["path"], "/api/devices/stream")
        self.assertEqual(active[0]["clients_count"], 1)

        # Register second client on same stream
        self.hub.register_client("/api/devices/stream", "client_2", "192.168.1.43")
        active = self.hub.get_active_streams()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["clients_count"], 2)

        # Unregister first client
        self.hub.unregister_client("/api/devices/stream", "client_1")
        active = self.hub.get_active_streams()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["clients_count"], 1)

        # Unregister second client
        self.hub.unregister_client("/api/devices/stream", "client_2")
        self.assertFalse(self.hub.is_stream_active("/api/devices/stream"))
        self.assertEqual(self.hub.get_active_streams(), [])

    def test_multiple_streams_active(self):
        self.hub.register_client("/api/stream", "c1", "10.0.0.1")
        self.hub.register_client("/api/devices/stream", "c2", "10.0.0.2")
        self.hub.register_client("/api/bacnet/mstp/stream", "c3", "10.0.0.3")

        active = self.hub.get_active_streams()
        self.assertEqual(len(active), 3)
        paths = [s["path"] for s in active]
        self.assertIn("/api/stream", paths)
        self.assertIn("/api/devices/stream", paths)
        self.assertIn("/api/bacnet/mstp/stream", paths)

        # Stop mstp stream
        self.hub.unregister_client("/api/bacnet/mstp/stream", "c3")
        active = self.hub.get_active_streams()
        self.assertEqual(len(active), 2)
        self.assertNotIn("/api/bacnet/mstp/stream", [s["path"] for s in active])

    def test_monitor_listener_receives_status_and_events(self):
        q = self.hub.add_monitor_listener()
        
        # When a client connects, a status message should be queued
        self.hub.register_client("/api/devices/stream", "dev_client_1", "127.0.0.1")
        msg = q.get_nowait()
        self.assertEqual(msg["type"], "status")
        self.assertEqual(len(msg["active_streams"]), 1)
        self.assertEqual(msg["active_streams"][0]["path"], "/api/devices/stream")

        # When an event is recorded on active stream
        payload = {"usb_count": 2, "serial_count": 1}
        self.hub.record_event("/api/devices/stream", event_type="message", data=payload, client_ip="127.0.0.1")
        
        evt = q.get_nowait()
        self.assertEqual(evt["type"], "traffic")
        self.assertEqual(evt["stream"], "/api/devices/stream")
        self.assertEqual(evt["event"], "message")
        self.assertEqual(evt["data"], payload)
        self.assertEqual(evt["clients_count"], 1)

        # Cleanup listener
        self.hub.remove_monitor_listener(q)
        self.hub.unregister_client("/api/devices/stream", "dev_client_1")

    def test_handle_sse_monitor_stream_handler(self):
        handler = MagicMock()
        written = []

        def fake_write(data):
            written.append(data)
            raise ConnectionResetError()

        handler.wfile.write.side_effect = fake_write

        handle_sse_monitor_stream(handler)

        handler.send_response.assert_called_with(200)
        handler.send_header.assert_any_call('Content-type', 'text/event-stream')
        self.assertTrue(any(b"data:" in w for w in written))
        self.assertTrue(any(b"active_streams" in w for w in written))


if __name__ == "__main__":
    unittest.main()
