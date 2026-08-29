#!/usr/bin/env python3
import sys
import json
import logging

# On essaie d'importer BAC0, mais on ne veut pas faire planter le script au démarrage
# si la dépendance est absente (permet de rester "léger" au check initial).
try:
    import BAC0
    HAS_BAC0 = True
except ImportError:
    HAS_BAC0 = False

def read_bacnet_points(requests):
    """
    Exécute les lectures BACnet.
    requests: list of {"addr": "IP", "instance": 123, "obj": "AI:1"}
    """
    if not HAS_BAC0:
        return {"error": "BAC0 non installé", "results": []}

    results = []
    # Initialisation de la pile BACnet (lourd)
    try:
        # On utilise une instance locale minimale. 
        # En production, on pourrait vouloir passer l'interface réseau en paramètre.
        bacnet = BAC0.lite()
    except Exception as e:
        return {"error": f"Erreur initialisation BACnet : {str(e)}", "results": []}

    for req in requests:
        addr = req.get("addr")
        instance = req.get("instance")
        obj_id = req.get("obj")
        
        if not all([addr, instance, obj_id]):
            continue

        try:
            # Format BAC0: "IP_ADDR device INSTANCE OBJECT_TYPE OBJECT_INST"
            # Ou plus simple si on a l'IP et le point : "IP_ADDR OBJECT_TYPE OBJECT_INST"
            # On part sur une lecture simple : bacnet.read("192.168.1.10 analogInput 1 presentValue")
            
            # Conversion simple du format "AI:1" vers "analogInput 1"
            obj_type_map = {
                "AI": "analogInput",
                "AO": "analogOutput",
                "AV": "analogValue",
                "BI": "binaryInput",
                "BO": "binaryOutput",
                "BV": "binaryValue",
                "MSI": "multiStateInput",
                "MSO": "multiStateOutput",
                "MSV": "multiStateValue"
            }
            
            parts = obj_id.split(":")
            if len(parts) == 2:
                t, i = parts
                obj_type = obj_type_map.get(t, t)
                read_path = f"{addr} {obj_type} {i} presentValue"
                val = bacnet.read(read_path)
                results.append({
                    "addr": addr,
                    "instance": instance,
                    "obj": obj_id,
                    "value": str(val),
                    "status": "ok"
                })
        except Exception as e:
            results.append({
                "addr": addr,
                "instance": instance,
                "obj": obj_id,
                "error": str(e),
                "status": "error"
            })

    bacnet.disconnect()
    return {"ok": True, "results": results}

def get_device_info(addr):
    """
    Récupère l'ID d'instance BACnet d'un device à une adresse précise via UDP brut.
    Évite de charger toute la pile BAC0 pour une simple sonde.
    """
    import socket
    import struct
    
    whois_pkt = bytes.fromhex("810a000c0120ffff00ff1008")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    try:
        sock.sendto(whois_pkt, (addr, 47808))
        data, _ = sock.recvfrom(1024)
        
        # Tentative de parsing minimal du paquet I-Am (Response to Who-Is)
        # Un paquet I-Am ressemble à: ... 10 00 c4 02 <instance_bytes> ...
        # On cherche le marqueur 'c4 02' (Object Identifier: Device, Instance)
        # On cherche '10 00' (Unconfirmed-Service-Request: I-Am)
        if data and b'\x10\x00' in data:
            idx = data.find(b'\xc4\x02')
            if idx != -1 and len(data) >= idx + 6:
                # L'Object Identifier BACnet (4 octets) est composé de :
                # - 10 bits : Object Type (Device = 8)
                # - 22 bits : Instance Number
                # Le tag c4 indique un type 'Application Tag 4' (Object Identifier)
                
                # On récupère les 4 octets de l'ID
                val = struct.unpack(">I", data[idx+2 : idx+6])[0]
                
                # On extrait les 22 bits de l'instance (0x3FFFFF)
                instance = val & 0x3FFFFF
                
                # Tentative d'extraction du Vendor ID (Tag 2: Unsigned Integer)
                # Il arrive généralement après le Device ID, Max ADU et Segmentation.
                # On cherche le tag 21 (Unsigned, length 1) ou 22 (Unsigned, length 2)
                vendor_id = None
                # On cherche après l'ID du device (6 octets après idx)
                search_data = data[idx+6:]
                # Le Vendor ID est souvent le dernier tag de type 2 (Unsigned)
                # On cherche 21 XX (1 octet) ou 22 XX XX (2 octets)
                vidx = search_data.find(b'\x21')
                if vidx != -1 and len(search_data) >= vidx + 2:
                    vendor_id = search_data[vidx+1]
                else:
                    vidx = search_data.find(b'\x22')
                    if vidx != -1 and len(search_data) >= vidx + 3:
                        vendor_id = struct.unpack(">H", search_data[vidx+1:vidx+3])[0]
                
                return {
                    "instance": instance, 
                    "vendor_id": vendor_id,
                    "name": "Automate BACnet"
                }
    except:
        pass
    finally:
        sock.close()
    return None

if __name__ == "__main__":
    # Support d'un mode "probe" pour une seule IP
    if len(sys.argv) > 2 and sys.argv[1] == "probe":
        target_ip = sys.argv[2]
        info = get_device_info(target_ip)
        if info:
            print(json.dumps(info))
        else:
            print(json.dumps({"error": "not found"}))
        sys.exit(0)

    # Lecture des requêtes depuis stdin (mode batch pour le recorder)
    try:
        input_data = sys.stdin.read()
        if not input_data:
            sys.exit(0)
            
        requests = json.loads(input_data)
        output = read_bacnet_points(requests)
        print(json.dumps(output))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
