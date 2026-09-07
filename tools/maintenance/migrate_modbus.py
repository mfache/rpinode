import json
import sqlite3
import os

# Connexion DB
db_path = '/home/marc/rpinode/data/app.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Trouver le site H66
cursor.execute("SELECT id FROM sites WHERE name = 'H66'")
site_row = cursor.fetchone()
if not site_row:
    print("Site H66 non trouvé !")
    exit(1)
site_id = site_row['id']
print(f"Site H66 trouvé, ID={site_id}")

# Lire l'ancien fichier json
with open('/home/marc/admin_boitier/data/modbus_templates.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Les templates à migrer
templates_to_migrate = ["Thermostat RS485", "WS eth Modbus IO", "SWEGON HR Série"]

for t_name in templates_to_migrate:
    if t_name not in data.get('templates', {}):
        continue
    
    t_data = data['templates'][t_name]
    
    # 1. Convertir les registres pour le nouveau format
    # L'ancien format avait : "function", "address", "label", "type", "scale"
    # Le nouveau format attend : "reg", "name", "type" (et idéalement func, etc.)
    new_registers = []
    
    for r in t_data.get('reads', []):
        new_registers.append({
            "reg": r["address"],
            "name": r["label"],
            "type": r.get("type", "int16"),  # L'ancien c'était u16, on va uniformiser plus bas ou garder tel quel
            "function": r.get("function", 3),
            "scale": r.get("scale", 1.0)
        })
        
    registers_json = json.dumps(new_registers, ensure_ascii=False)
    
    # 2. Insérer le Template Modbus
    cursor.execute("""
        INSERT INTO modbus_templates (name, manufacturer, registers_json)
        VALUES (?, ?, ?)
    """, (t_name, "Inconnu", registers_json))
    
    template_id = cursor.lastrowid
    print(f"Template '{t_name}' migré (ID={template_id}).")
    
    # 3. Assigner l'appareil au site H66
    protocol = t_data.get('mode', 'tcp')
    if protocol == 'rtu': 
        protocol = 'mstp' # Le nouveau système utilise 'mstp' pour le rtu/serie
        address = str(t_data.get('unit', 1)) # Pour RTU, l'adresse c'est le Slave ID
        port = None
    else:
        address = '192.168.1.100' # Adresse par defaut, à corriger depuis l'UI
        port = t_data.get('port', 502)
        
    cursor.execute("""
        INSERT INTO modbus_devices (site_id, template_id, name, protocol, address, port)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (site_id, template_id, t_name, protocol, address, port))
    print(f"  -> Appareil assigné au site H66 avec protocole {protocol}")

conn.commit()
conn.close()
print("Migration terminée.")
