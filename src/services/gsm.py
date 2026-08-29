import json
import logging
import subprocess

logger = logging.getLogger(__name__)

def get_gsm_info():
    """
    Récupère les informations de la cellule GSM/LTE et le GPS via ModemManager (mmcli).
    Cette méthode reprend la logique de l'ancienne version (admin_boitier/webadmin/location.py).
    """
    info = {
        "mcc": None,
        "mnc": None,
        "lac": None,
        "tac": None,
        "cid": None,
        "enodeb": None,
        "sector": None,
        "gps": None
    }
    
    # 1. S'assurer que les sources de localisation sont activées
    # On le fait en deux appels distincts car l'échec du GPS ne doit pas bloquer la cellule.
    for source in ("--location-enable-3gpp", "--location-enable-gps-raw"):
        try:
            subprocess.run(
                ["mmcli", "-m", "any", source],
                capture_output=True,
                timeout=5,
                check=False
            )
        except Exception:
            pass

    # 2. Récupérer les données de localisation
    try:
        # On tente avec sudo car ModemManager restreint souvent l'accès à la localisation
        cmd = ["sudo", "mmcli", "-m", "any", "--location-get", "--output-keyvalue"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=True
        )
        
        fields = {}
        for line in result.stdout.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fields[k.strip()] = v.strip()
        
        # --- GPS ---
        lat = fields.get("modem.location.gps.latitude")
        lon = fields.get("modem.location.gps.longitude")
        if lat and lat != "--" and lon and lon != "--":
            try:
                info["gps"] = {
                    "lat": float(lat),
                    "lon": float(lon),
                    "alt": fields.get("modem.location.gps.altitude"),
                    "utc": fields.get("modem.location.gps.utc")
                }
            except ValueError:
                pass

        # --- 3GPP (Cellule) ---
        mcc = fields.get("modem.location.3gpp.mcc")
        mnc = fields.get("modem.location.3gpp.mnc")
        lac = fields.get("modem.location.3gpp.lac")
        tac = fields.get("modem.location.3gpp.tac")
        cid = fields.get("modem.location.3gpp.cid")
        
        if mcc and mcc != "--":
            info["mcc"] = mcc
        if mnc and mnc != "--":
            info["mnc"] = mnc
        if lac and lac != "--":
            info["lac"] = lac
        if tac and tac != "--":
            info["tac"] = tac
            
        if cid and cid != "--":
            info["cid"] = cid
            try:
                # ModemManager retourne souvent le CID en hexadécimal
                cid_dec = int(cid, 16) if (isinstance(cid, str) and (cid.startswith("0x") or any(c in cid.upper() for c in 'ABCDEF'))) else int(cid)
                info["cid_dec"] = cid_dec
                # LTE : ECI (28 bits) = eNodeB (20 bits) << 8 | secteur (8 bits)
                info["enodeb"] = cid_dec // 256
                info["sector"] = cid_dec % 256
            except ValueError:
                logger.warning(f"Format de CID non supporté : {cid}")
                
        return info
    except subprocess.CalledProcessError as e:
        logger.warning(f"mmcli location-get a échoué (code {e.returncode})")
        return info
    except Exception as e:
        logger.warning(f"Erreur lors de la récupération des infos GSM : {e}")
        return info

if __name__ == "__main__":
    # Petit test si lancé directement
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(get_gsm_info(), indent=2))
