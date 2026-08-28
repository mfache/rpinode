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

# Dossier de logs
LOG_DIR = PROJECT_ROOT / "log"
LOG_FILE = LOG_DIR / "rpinode.log"

# Créer les dossiers s'ils n'existent pas
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
