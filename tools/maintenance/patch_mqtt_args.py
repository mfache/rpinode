import re

with open("src/services/bacnet_daemon.py", "r") as f:
    content = f.read()

# paho mqtt v2 sends more arguments to the connect callback
content = content.replace(
    "def on_mqtt_connect(self, client, userdata, flags, rc):",
    "def on_mqtt_connect(self, client, userdata, flags, rc, properties=None):"
)

with open("src/services/bacnet_daemon.py", "w") as f:
    f.write(content)
