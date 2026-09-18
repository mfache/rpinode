import os
from pathlib import Path

# Racine du projet (2 niveaux au-dessus de src/core/paths.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Dossiers principaux
DATA_DIR = PROJECT_ROOT / "data"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

# Fichiers de configuration et bases de données
CONFIG_FILE = DATA_DIR / "config.json"
DATABASE_FILE = DATA_DIR / "app.db"
SCHEMA_FILE = PROJECT_ROOT / "src" / "core" / "schema.sql"

# IP Scan
IPSCAN_RUNNING_FILE = DATA_DIR / "ip_last_scan.json.running"

# Fichier en RAM contenant la session du chantier actif (effacé au redémarrage).
# Peut être redirigé via la variable d'environnement RPINODE_CURRENT_SITE_FILE :
# c'est ce que fait run_tests.sh, pour garantir que la suite de tests ne touche
# JAMAIS le vrai fichier de session utilisé en production par le tracker (sous
# peine de faire croire, même brièvement, que le boîtier se trouve sur un
# chantier de test et de polluer des enregistrements BACnet/Modbus en cours).
CURRENT_SITE_FILE = Path(os.environ.get("RPINODE_CURRENT_SITE_FILE") or "/tmp/rpinode_current_site.json")

# Dossier de logs (en RAM pour préserver la carte SD)
LOG_DIR = Path("/tmp/rpinode/log")
LOG_FILE = LOG_DIR / "rpinode.log"

# Créer les dossiers s'ils n'existent pas
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
