import json
import logging
from core.database import get_db_connection

logger = logging.getLogger(__name__)

def get_all_templates():
    """Récupère tous les templates BACnet disponibles."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bacnet_templates ORDER BY name")
        return [dict(row) for row in cursor.fetchall()]

def save_template(name, manufacturer, objects, template_id=None, external_id=None):
    """Crée ou met à jour un template BACnet."""
    objects_json = json.dumps(objects, ensure_ascii=False)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if template_id:
            cursor.execute(
                """
                UPDATE bacnet_templates 
                SET name = ?, manufacturer = ?, objects_json = ?, external_id = COALESCE(?, external_id), updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (name, manufacturer, objects_json, external_id, template_id)
            )
        else:
            cursor.execute(
                """
                INSERT INTO bacnet_templates (name, manufacturer, objects_json, external_id)
                VALUES (?, ?, ?, ?)
                """,
                (name, manufacturer, objects_json, external_id)
            )
            template_id = cursor.lastrowid
        conn.commit()
        return template_id

def get_site_devices(site_id):
    """Récupère les appareils BACnet configurés pour un chantier."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.*, t.name as template_name, t.manufacturer as template_manufacturer
            FROM bacnet_devices d
            JOIN bacnet_templates t ON d.template_id = t.id
            WHERE d.site_id = ?
            """,
            (site_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def add_device_to_site(site_id, template_id, name, device_instance, network_address):
    """Ajoute un appareil BACnet à un chantier."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bacnet_devices (site_id, template_id, name, device_instance, network_address)
            VALUES (?, ?, ?, ?, ?)
            """,
            (site_id, template_id, name, device_instance, network_address)
        )
        conn.commit()
        return cursor.lastrowid
