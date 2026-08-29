import json
import logging

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

class MqttClient:
    def __init__(self, host="127.0.0.1", port=1883, client_id="rpinode_service"):
        self.host = host
        self.port = port
        self.client_id = client_id
        # Version 2.0+ de paho-mqtt nécessite CallbackAPIVersion
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:
            # Repli pour les anciennes versions
            self.client = mqtt.Client(client_id=client_id)
            
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info(f"Connecté au broker MQTT ({self.host})")
        else:
            logger.error(f"Erreur de connexion MQTT, code: {rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        logger.warning("Déconnecté du broker MQTT")

    def connect(self):
        try:
            self.client.connect(self.host, self.port, 60)
            self.client.loop_start()
            return True
        except Exception as e:
            logger.error(f"Impossible de se connecter au broker MQTT: {e}")
            return False

    def publish(self, topic, data, retain=False):
        try:
            payload = json.dumps(data)
            self.client.publish(topic, payload, retain=retain)
        except Exception as e:
            logger.error(f"Erreur lors de la publication MQTT sur {topic}: {e}")

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

# Instance globale pour usage simple
mqtt_client = MqttClient()
