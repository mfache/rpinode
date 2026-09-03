import logging
import sqlite3
from contextlib import contextmanager

from . import paths

logger = logging.getLogger(__name__)

@contextmanager
def get_db_connection():
    """Retourne une connexion à la base de données SQLite et gère sa fermeture."""
    # Timeout plus long (20s) pour éviter les erreurs "database is locked"
    # lors d'accès concurrents (web, logger, fleet, etc.)
    conn = sqlite3.connect(paths.DATABASE_FILE, timeout=20.0)

    # Activation du mode WAL pour la concurrence des lectures/écritures
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=20000;")

    conn.row_factory = sqlite3.Row  # Permet d'accéder aux colonnes par nom
    
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()

def init_db():
    """
    Initialise la structure de la base de données.
    Lit le fichier schema.sql et exécute les commandes.
    Permet d'ajouter des tables ou des colonnes (si l'utilisateur édite schema.sql).
    """
    logger.info(f"Vérification de la structure de la base de données : {paths.DATABASE_FILE}")
    
    if not paths.SCHEMA_FILE.exists():
        logger.error(f"Fichier de schéma introuvable : {paths.SCHEMA_FILE}")
        return

    with paths.SCHEMA_FILE.open("r", encoding="utf-8") as f:
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

        # Migration des colonnes de modbus_devices si nécessaire
        try:
            cursor.execute("PRAGMA table_info(modbus_devices)")
            dev_cols = [r["name"] for r in cursor.fetchall()]
            if dev_cols and "slave_unit" not in dev_cols:
                cursor.execute("ALTER TABLE modbus_devices ADD COLUMN slave_unit INTEGER DEFAULT 1")
                # Initialiser les valeurs existantes pour les périphériques existants
                cursor.execute("UPDATE modbus_devices SET slave_unit = CASE WHEN protocol = 'mstp' AND address GLOB '[0-9]*' THEN CAST(address AS INTEGER) ELSE 1 END WHERE slave_unit IS NULL")
                conn.commit()
        except Exception as e:
            logger.warning(f"Erreur lors de la migration des colonnes modbus_devices : {e}")

        # Migration des colonnes de modbus_templates si nécessaire
        try:
            cursor.execute("PRAGMA table_info(modbus_templates)")
            cols = [r["name"] for r in cursor.fetchall()]
            if cols:
                import uuid
                import socket
                if "template_uuid" not in cols:
                    cursor.execute("ALTER TABLE modbus_templates ADD COLUMN template_uuid TEXT")
                if "revision_uuid" not in cols:
                    cursor.execute("ALTER TABLE modbus_templates ADD COLUMN revision_uuid TEXT")
                if "parent_revision_uuid" not in cols:
                    cursor.execute("ALTER TABLE modbus_templates ADD COLUMN parent_revision_uuid TEXT")
                if "version" not in cols:
                    cursor.execute("ALTER TABLE modbus_templates ADD COLUMN version INTEGER DEFAULT 1")
                if "is_shared" not in cols:
                    cursor.execute("ALTER TABLE modbus_templates ADD COLUMN is_shared BOOLEAN DEFAULT 0")
                if "is_local_hidden" not in cols:
                    cursor.execute("ALTER TABLE modbus_templates ADD COLUMN is_local_hidden BOOLEAN DEFAULT 0")
                if "created_by_node" not in cols:
                    cursor.execute("ALTER TABLE modbus_templates ADD COLUMN created_by_node TEXT")

                # Peupler les colonnes manquantes pour les enregistrements existants
                cursor.execute("SELECT id, name, template_uuid, revision_uuid FROM modbus_templates")
                for row in cursor.fetchall():
                    t_uuid = row["template_uuid"] or str(uuid.uuid5(uuid.NAMESPACE_DNS, f"template-{row['name']}"))
                    r_uuid = row["revision_uuid"] or str(uuid.uuid4())
                    cursor.execute("""
                        UPDATE modbus_templates
                        SET template_uuid = COALESCE(template_uuid, ?),
                            revision_uuid = COALESCE(revision_uuid, ?),
                            version = COALESCE(version, 1),
                            is_shared = COALESCE(is_shared, 0),
                            is_local_hidden = COALESCE(is_local_hidden, 0),
                            created_by_node = COALESCE(created_by_node, ?)
                        WHERE id = ?
                    """, (t_uuid, r_uuid, socket.gethostname(), row["id"]))
                conn.commit()
        except Exception as e:
            logger.warning(f"Erreur lors de la migration des colonnes modbus_templates : {e}")

        # Migration des colonnes de bacnet_templates si nécessaire
        try:
            cursor.execute("PRAGMA table_info(bacnet_templates)")
            bcols = [r["name"] for r in cursor.fetchall()]
            if bcols:
                import uuid
                import socket
                if "template_uuid" not in bcols:
                    cursor.execute("ALTER TABLE bacnet_templates ADD COLUMN template_uuid TEXT")
                if "revision_uuid" not in bcols:
                    cursor.execute("ALTER TABLE bacnet_templates ADD COLUMN revision_uuid TEXT")
                if "parent_revision_uuid" not in bcols:
                    cursor.execute("ALTER TABLE bacnet_templates ADD COLUMN parent_revision_uuid TEXT")
                if "version" not in bcols:
                    cursor.execute("ALTER TABLE bacnet_templates ADD COLUMN version INTEGER DEFAULT 1")
                if "is_shared" not in bcols:
                    cursor.execute("ALTER TABLE bacnet_templates ADD COLUMN is_shared BOOLEAN DEFAULT 0")
                if "is_local_hidden" not in bcols:
                    cursor.execute("ALTER TABLE bacnet_templates ADD COLUMN is_local_hidden BOOLEAN DEFAULT 0")
                if "created_by_node" not in bcols:
                    cursor.execute("ALTER TABLE bacnet_templates ADD COLUMN created_by_node TEXT")

                cursor.execute("SELECT id, name, template_uuid, revision_uuid FROM bacnet_templates")
                for row in cursor.fetchall():
                    t_uuid = row["template_uuid"] or str(uuid.uuid5(uuid.NAMESPACE_DNS, f"bacnet-template-{row['name']}"))
                    r_uuid = row["revision_uuid"] or str(uuid.uuid4())
                    cursor.execute("""
                        UPDATE bacnet_templates
                        SET template_uuid = COALESCE(template_uuid, ?),
                            revision_uuid = COALESCE(revision_uuid, ?),
                            version = COALESCE(version, 1),
                            is_shared = COALESCE(is_shared, 0),
                            is_local_hidden = COALESCE(is_local_hidden, 0),
                            created_by_node = COALESCE(created_by_node, ?)
                        WHERE id = ?
                    """, (t_uuid, r_uuid, socket.gethostname(), row["id"]))
                conn.commit()
        except Exception as e:
            logger.warning(f"Erreur lors de la migration des colonnes bacnet_templates : {e}")

        try:
            cursor.execute("PRAGMA table_info(site_network_profiles)")
            cols = [r["name"] for r in cursor.fetchall()]
            if cols:
                if "dhcp_range" not in cols:
                    cursor.execute("ALTER TABLE site_network_profiles ADD COLUMN dhcp_range TEXT")
                conn.commit()
        except Exception as e:
            logger.warning(f"Erreur lors de la migration des colonnes site_network_profiles : {e}")

        logger.info("Base de données synchronisée avec le schéma.")
