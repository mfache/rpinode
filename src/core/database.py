import sqlite3
import logging
from .paths import DATABASE_FILE, SCHEMA_FILE

logger = logging.getLogger(__name__)

def get_db_connection():
    """Retourne une connexion à la base de données SQLite."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row  # Permet d'accéder aux colonnes par nom
    return conn

def init_db():
    """Initialise la structure de la base de données si elle n'existe pas."""
    logger.info(f"Initialisation de la base de données : {DATABASE_FILE}")
    
    if not SCHEMA_FILE.exists():
        logger.error(f"Fichier de schéma introuvable : {SCHEMA_FILE}")
        return

    with SCHEMA_FILE.open("r", encoding="utf-8") as f:
        schema_sql = f.read()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.executescript(schema_sql)
            conn.commit()
            logger.info("Base de données initialisée avec succès.")
        except sqlite3.Error as e:
            logger.error(f"Erreur lors de l'exécution du schéma SQL : {e}")
            conn.rollback()
