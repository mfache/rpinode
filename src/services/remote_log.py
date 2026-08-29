import logging
import threading
import time
import subprocess
from datetime import datetime
from services.fleet import fleet

# Récupération de la version git au chargement (une seule fois)
try:
    GIT_VERSION = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], 
        stderr=subprocess.STDOUT
    ).decode("utf-8").strip()
except Exception:
    GIT_VERSION = "unknown"

class FleetLogHandler(logging.Handler):
    """
    Envoie les logs par lots au serveur maître en arrière-plan.
    """
    def __init__(self, batch_size=50, flush_interval=30):
        super().__init__()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.buffer = []
        self.lock = threading.Lock()
        
        # Thread de vidage en arrière-plan
        self._stop_event = threading.Event()
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def emit(self, record):
        if not fleet.is_registered():
            return
            
        try:
            log_entry = {
                "level": record.levelname,
                "module": record.name,
                "message": self.format(record),
                "timestamp": datetime.utcfromtimestamp(record.created).isoformat(),
                "git_version": GIT_VERSION
            }
            
            with self.lock:
                self.buffer.append(log_entry)
                should_flush = len(self.buffer) >= self.batch_size
                
            if should_flush:
                self.flush()
        except Exception:
            self.handleError(record)
            
    def flush(self):
        with self.lock:
            if not self.buffer:
                return
            logs_to_send = self.buffer[:]
            self.buffer = []
            
        # Ne pas bloquer l'appelant avec le réseau
        threading.Thread(target=self._send_batch, args=(logs_to_send,), daemon=True).start()

    def _send_batch(self, logs):
        try:
            fleet.send_logs(logs)
        except Exception:
            pass # On perd silencieusement pour éviter une boucle de logs

    def _flush_loop(self):
        while not self._stop_event.is_set():
            time.sleep(self.flush_interval)
            self.flush()
            
    def close(self):
        self._stop_event.set()
        self.flush()
        super().close()
