import json
import logging

from core.database import get_db_connection

logger = logging.getLogger(__name__)

def get_all_templates():
    """Récupère tous les templates Modbus disponibles."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM modbus_templates ORDER BY name")
        return [dict(row) for row in cursor.fetchall()]

def get_template(template_id):
    """Récupère un template spécifique."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM modbus_templates WHERE id = ?", (template_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def save_template(name, manufacturer, registers, template_id=None, external_id=None):
    """Crée ou met à jour un template Modbus."""
    registers_json = json.dumps(registers, ensure_ascii=False)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if template_id:
            cursor.execute(
                """
                UPDATE modbus_templates 
                SET name = ?, manufacturer = ?, registers_json = ?, external_id = COALESCE(?, external_id), updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (name, manufacturer, registers_json, external_id, template_id)
            )
        else:
            cursor.execute(
                """
                INSERT INTO modbus_templates (name, manufacturer, registers_json, external_id)
                VALUES (?, ?, ?, ?)
                """,
                (name, manufacturer, registers_json, external_id)
            )
            template_id = cursor.lastrowid
        conn.commit()
        return template_id

def get_site_devices(site_id):
    """Récupère les appareils Modbus configurés pour un chantier."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.*, t.name as template_name, t.manufacturer as template_manufacturer
            FROM modbus_devices d
            JOIN modbus_templates t ON d.template_id = t.id
            WHERE d.site_id = ?
            """,
            (site_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def add_device_to_site(site_id, template_id, name, protocol, address, port=502):
    """Ajoute un appareil Modbus à un chantier."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO modbus_devices (site_id, template_id, name, protocol, address, port)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (site_id, template_id, name, protocol, address, port)
        )
        conn.commit()
        return cursor.lastrowid
