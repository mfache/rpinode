"""Forçages BACnet : imposer une valeur à des points (par exemple toutes les
vannes chaudes ouvertes pendant un équilibrage), pour une durée limitée ou
non, puis les remettre en automatique.

Un forçage écrit la valeur actuelle (present-value) à la priorité
PRIORITE_FORCAGE ; remettre en automatique y écrit Null (relâchement), et le
régulateur reprend la commande de priorité inférieure (sa régulation). On ne
réécrit jamais une ancienne valeur.

Chaque forçage réussi est enregistré (table bacnet_forcings) : on sait
toujours quoi relâcher, même après une relance de rpinode, et une tâche de
fond relâche ceux dont la durée est écoulée (relacher_expires).
"""
import json
import logging
from html import escape
import time
import uuid

from core.database import get_db_connection

logger = logging.getLogger(__name__)

#: Priorité BACnet des forçages : 8 = Manual Operator (1 à 7 : sécurité).
PRIORITE_FORCAGE = 8

#: Durées proposées (minutes ; None = sans limite).
DUREES = [(None, "Sans limite"), (15, "15 min"), (30, "30 min"), (60, "1 h"), (120, "2 h"),
          (240, "4 h"), (480, "8 h"), (1440, "24 h")]


def _ecrire(points, valeur, timeout=None):
    """Envoie les écritures au démon BACnet (valeur None = relâcher) et renvoie
    {(device_instance, object_id): résultat}. Sans réponse : erreur Timeout."""
    import queue
    import threading
    import paho.mqtt.client as mqtt
    from services.mqtt_service import mqtt_client

    if not points:
        return {}
    timeout = timeout or (10.0 + 8.0 * len(points))  # écritures une par une, avec relecture
    job_id = str(uuid.uuid4())
    file, abonne = queue.Queue(), threading.Event()
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()
    client.on_message = lambda c, u, m: file.put(m.payload.decode("utf-8"))
    client.on_subscribe = lambda *a, **k: abonne.set()
    resultats = []
    try:
        client.connect("127.0.0.1", 1883, 60)
        client.loop_start()
        client.subscribe(f"rpinode/bacnet/res/write/{job_id}")
        abonne.wait(timeout=1.0)
        mqtt_client.publish("rpinode/bacnet/cmd/write", {"job_id": job_id, "points": [
            {"address": p["network_address"], "object_id": p["object_id"], "device_id": p["device_instance"],
             "value": valeur, "priority": PRIORITE_FORCAGE} for p in points]})
        try:
            resultats = json.loads(file.get(timeout=timeout))
        except queue.Empty:
            resultats = []
    finally:
        client.loop_stop()
        client.disconnect()
    par_cle = {(str(r.get("device_id")), r.get("object_id")): r for r in resultats if isinstance(r, dict)}
    return {(p["device_instance"], p["object_id"]):
            par_cle.get((str(p["device_instance"]), p["object_id"]), {"ok": False, "error": "Timeout"})
            for p in points}


def forcages_actifs(site_id):
    """{(device_instance, object_id): forçage} des points forcés du chantier."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM bacnet_forcings WHERE site_id = ?", (site_id,)).fetchall()
    return {(r["device_instance"], r["object_id"]): dict(r) for r in rows}


def forcer(site_id, points, valeur, duree_min=None, view_id=None):
    """Force les points à `valeur` (pendant `duree_min` minutes, ou sans
    limite). Renvoie {forces, erreurs: [(nom, erreur)]}. Seuls les points
    dont l'écriture a réussi sont enregistrés comme forcés."""
    if valeur is None or str(valeur).strip() == "":
        raise ValueError("Valeur de forçage manquante")
    valeur = str(valeur).strip()
    if duree_min is not None and str(duree_min).strip() != "":
        duree_min = int(duree_min)
        if duree_min <= 0:
            raise ValueError("Durée invalide")
    else:
        duree_min = None
    maintenant = int(time.time())
    echeance = maintenant + duree_min * 60 if duree_min else None
    resultats = _ecrire(points, valeur)
    forces, erreurs = 0, []
    with get_db_connection() as conn:
        for p in points:
            r = resultats[(p["device_instance"], p["object_id"])]
            nom = p.get("object_name") or p["object_id"]
            if not r.get("ok"):
                erreurs.append((nom, r.get("error") or "Échec"))
                continue
            forces += 1
            conn.execute(
                """INSERT INTO bacnet_forcings (site_id, network_address, device_instance, object_id,
                       object_name, value, priority, started_at, expires_at, view_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(site_id, device_instance, object_id) DO UPDATE SET
                       value = excluded.value, priority = excluded.priority, started_at = excluded.started_at,
                       expires_at = excluded.expires_at, view_id = excluded.view_id,
                       network_address = excluded.network_address, object_name = excluded.object_name""",
                (site_id, p["network_address"], p["device_instance"], p["object_id"], p.get("object_name"),
                 valeur, PRIORITE_FORCAGE, maintenant, echeance, view_id))
        conn.commit()
    logger.info(f"Forçage à {valeur} ({duree_min or 'sans limite'} min) : {forces} point(s), "
                f"{len(erreurs)} erreur(s)")
    return {"forces": forces, "erreurs": erreurs}


def relacher(site_id, points):
    """Remet en automatique (Null à PRIORITE_FORCAGE) les points donnés qui
    sont forcés. Renvoie {relaches, erreurs}. Un point que le régulateur ne
    connaît plus est retiré de la liste (rien à relâcher)."""
    actifs = forcages_actifs(site_id)
    cibles = [actifs[k] for k in {(p["device_instance"], p["object_id"]) for p in points} if k in actifs]
    resultats = _ecrire(cibles, None)
    relaches, erreurs = 0, []
    with get_db_connection() as conn:
        for f in cibles:
            r = resultats[(f["device_instance"], f["object_id"])]
            if r.get("ok") or "unknown-object" in str(r.get("error")):
                relaches += 1
                conn.execute("DELETE FROM bacnet_forcings WHERE id = ?", (f["id"],))
            else:
                erreurs.append((f["object_name"] or f["object_id"], r.get("error") or "Échec"))
        conn.commit()
    logger.info(f"Remise en automatique : {relaches} point(s), {len(erreurs)} erreur(s)")
    return {"relaches": relaches, "erreurs": erreurs}


def relacher_expires():
    """Relâche les forçages dont la durée est écoulée (tous chantiers)."""
    maintenant = int(time.time())
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM bacnet_forcings WHERE expires_at IS NOT NULL AND expires_at <= ?", (maintenant,))]
    par_site = {}
    for r in rows:
        par_site.setdefault(r["site_id"], []).append(r)
    for site_id, pts in par_site.items():
        res = relacher(site_id, pts)
        logger.info(f"Fin de forçage (durée écoulée) chantier {site_id} : {res}")


def run_forcages_scheduler(intervalle=30):
    """Tâche de fond : relâche les forçages arrivés à échéance."""
    while True:
        try:
            relacher_expires()
        except Exception as e:
            logger.error(f"Relâchement des forçages expirés : {e}")
        time.sleep(intervalle)


def rendre_bilan(res, relacher):
    """Message HTML du bilan d'un forçage ou d'une remise en automatique."""
    n = res.get("relaches" if relacher else "forces", 0)
    quoi = "remis en automatique" if relacher else "forcé(s)"
    if not n and not res["erreurs"]:
        return "Aucun point concerné." if not relacher else "Aucun point forcé parmi ces points."
    texte = f'<span class="{"badge-ok" if not res["erreurs"] else "badge-warn"}">{n} point(s) {quoi}</span>'
    if res["erreurs"]:
        details = "".join(f"<li><code>{escape(nom)}</code> : {escape(str(err))}</li>" for nom, err in res["erreurs"][:20])
        texte += f' <span class="badge-err">{len(res["erreurs"])} échec(s)</span><ul>{details}</ul>'
    return texte
