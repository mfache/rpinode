import re

with open("src/services/ipscan.py", "r") as f:
    content = f.read()

# Allonger le timeout de la sonde individuelle (s'il y a 90 hôtes, le démon BACnet va recevoir 90 probes d'un coup)
# Et utiliser un délai plus espacé pour éviter de foudroyer le démon MQTT et le bus UDP BACnet
content = content.replace("info = await asyncio.wait_for(fut, timeout=2.5)", "info = await asyncio.wait_for(fut, timeout=6.0)")
content = content.replace("delay_ms=i * 50", "delay_ms=i * 100")

with open("src/services/ipscan.py", "w") as f:
    f.write(content)
