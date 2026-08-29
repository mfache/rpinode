import logging
import threading
import time
import subprocess
import queue
from datetime import datetime

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
    Envoie les logs par lots au serveur maître en arrière-plan via une file (Queue).
    L'utilisation d'une file et d'un thread worker séparé évite tout risque de 
    deadlock ou de récursion infinie lors des appels à logging.info.
    """
    def __init__(self, batch_size=50, flush_interval=30):
        super().__init__()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.queue = queue.Queue()
        
        # Thread de traitement asynchrone
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def emit(self, record):
        try:
            # On se contente d'empiler le record. Pas de formattage ou d'appel externe ici.
            self.queue.put(record)
        except Exception:
            self.handleError(record)

    def _worker_loop(self):
        buffer = []
        last_flush = time.time()
        
        while not self._stop_event.is_set():
            try:
                # On attend un record (timeout court pour vérifier régulièrement le stop_event et le flush_interval)
                record = self.queue.get(timeout=1.0)
                
                # Formatage du log hors du thread principal
                try:
                    log_entry = {
                        "level": record.levelname,
                        "module": record.name,
                        "message": record.getMessage(),
                        "timestamp": datetime.utcfromtimestamp(record.created).isoformat(),
                        "git_version": GIT_VERSION
                    }
                    buffer.append(log_entry)
                except Exception:
                    pass
            except queue.Empty:
                pass
            
            # Vérification si on doit envoyer le lot (par taille ou par temps)
            now = time.time()
            if len(buffer) >= self.batch_size or (buffer and now - last_flush >= self.flush_interval):
                self._send_batch(buffer)
                buffer = []
                last_flush = now

    def _send_batch(self, logs):
        if not logs:
            return
        try:
            from services.fleet import fleet
            # On vérifie seulement si on a un token sans appeler is_registered() qui pourrait logger
            if hasattr(fleet, 'token') and fleet.token:
                fleet.send_logs(logs)
        except Exception:
            # On échoue silencieusement pour les logs
            pass

    def close(self):
        self._stop_event.set()
        super().close()
