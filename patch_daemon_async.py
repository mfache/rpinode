import re

with open("src/services/bacnet_daemon.py", "r") as f:
    content = f.read()

# Importer queue
content = content.replace(
    "import socket",
    "import socket\nimport queue"
)

# Initialiser la file dans __init__
init_replace = """        self.mqtt_client.on_message = self.on_mqtt_message
        self.loop = asyncio.get_event_loop()
        self.msg_queue = queue.Queue()"""
content = content.replace(
    "        self.mqtt_client.on_message = self.on_mqtt_message\n        self.loop = asyncio.get_event_loop()",
    init_replace
)

# Modifier on_mqtt_message pour juste mettre en queue
new_on_message = """    def on_mqtt_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
        except json.JSONDecodeError:
            return
        # Paho tourne dans un thread séparé. On transmet à la boucle asyncio via une Queue thread-safe
        self.msg_queue.put((topic, payload))"""

content = re.sub(
    r"    def on_mqtt_message\(self, client, userdata, msg\):.*?(?=\n\n    def on_i_am_received)",
    new_on_message,
    content,
    flags=re.DOTALL
)

# Modifier la boucle start() pour dépiler
new_start_loop = """        # Boucle infinie pour maintenir le démon en vie et traiter les messages MQTT
        while True:
            while not self.msg_queue.empty():
                topic, payload = self.msg_queue.get()
                if topic == "rpinode/bacnet/cmd/whois":
                    low = payload.get("low")
                    high = payload.get("high")
                    asyncio.create_task(self.send_whois(low, high))
                elif topic == "rpinode/bacnet/cmd/read":
                    job_id = payload.get("job_id", "default")
                    points = payload.get("points", [])
                    asyncio.create_task(self.process_reads(job_id, points))
                elif topic == "rpinode/bacnet/cmd/probe":
                    job_id = payload.get("job_id", "default")
                    ip = payload.get("ip")
                    if ip:
                        asyncio.create_task(self.process_probe(job_id, ip))
            await asyncio.sleep(0.1)"""

content = re.sub(
    r"        # Boucle infinie pour maintenir le démon en vie\n        while True:\n            await asyncio\.sleep\(1\)",
    new_start_loop,
    content
)

with open("src/services/bacnet_daemon.py", "w") as f:
    f.write(content)
