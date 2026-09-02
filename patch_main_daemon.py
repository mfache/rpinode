import re

with open("src/main.py", "r") as f:
    content = f.read()

new_daemon_logic = """    # Démarrage du démon BACnet unifié (MQTT)
    import subprocess
    import sys
    import os
    
    def run_bacnet_daemon():
        import logging
        logger = logging.getLogger("BACnetDaemonRunner")
        daemon_path = os.path.join(os.path.dirname(__file__), "services", "bacnet_daemon.py")
        bacnet_python = "/opt/boitier-bacnet/venv/bin/python"
        
        if not os.path.exists(bacnet_python):
            bacnet_python = sys.executable
            
        logger.info(f"Démarrage de bacnet_daemon via {bacnet_python}")
        try:
            # Lancer le démon comme un sous-processus continu
            subprocess.Popen([bacnet_python, daemon_path])
        except Exception as e:
            logger.error(f"Impossible de démarrer le démon BACnet: {e}")

    bacnet_thread = threading.Thread(target=run_bacnet_daemon, daemon=True)
    bacnet_thread.start()"""

content = re.sub(
    r"    # Démarrage du démon BACnet unifié \(MQTT\).*?bacnet_thread\.start\(\)",
    new_daemon_logic,
    content,
    flags=re.DOTALL
)

with open("src/main.py", "w") as f:
    f.write(content)
