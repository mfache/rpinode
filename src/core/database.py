import logging
import sqlite3

from .paths import DATABASE_FILE, SCHEMA_FILE

logger = logging.getLogger(__name__)

def get_db_connection():
    """Retourne une connexion à la base de données SQLite."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row  # Permet d'accéder aux colonnes par nom
    return conn

def init_db():
    """
    Initialise la structure de la base de données.
    Lit le fichier schema.sql et exécute les commandes.
    Permet d'ajouter des tables ou des colonnes (si l'utilisateur édite schema.sql).
    """
    logger.info(f"Vérification de la structure de la base de données : {DATABASE_FILE}")
    
    if not SCHEMA_FILE.exists():
        logger.error(f"Fichier de schéma introuvable : {SCHEMA_FILE}")
        return

    with SCHEMA_FILE.open("r", encoding="utf-8") as f:
        schema_sql = f.read()

    # On découpe le script pour exécuter chaque commande séparément.
    # Cela permet d'être plus souple si certaines commandes échouent (ex: colonne déjà existante).
    commands = schema_sql.split(";")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd:
                continue
            
            try:
                cursor.execute(cmd)
            except sqlite3.Error as e:
                # On ignore l'erreur si c'est une table ou colonne déjà existante
                error_msg = str(e).lower()
                if "already exists" in error_msg or "duplicate column name" in error_msg:
                    logger.debug(f"Commande ignorée (déjà appliquée) : {cmd[:50]}...")
                else:
                    logger.warning(f"Erreur SQL sur la commande [{cmd[:50]}...] : {e}")
        
        conn.commit()
        logger.info("Base de données synchronisée avec le schéma.")
