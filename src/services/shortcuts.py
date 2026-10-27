"""Raccourcis du chantier : adresses web d'équipements du LAN (GTC, automate…).

Le boîtier rend chaque adresse joignable par Tailscale (une route /32 par
équipement, voir network_config.publish_tailscale_routes) et envoie la liste à
docs, qui l'affiche aux personnes ayant accès au chantier (page « Boîtiers »).
Le clic ouvre l'adresse réelle : il faut Tailscale sur le poste.
Voir docs/operations/RACCOURCIS.md.
"""
import ipaddress
import logging
from urllib.parse import urlsplit

from core.database import get_db_connection

logger = logging.getLogger(__name__)

LIBELLE_MAX = 64
URL_MAX = 255


def valider(libelle, url):
    """Retourne (libellé, url, ip) nettoyés, ou lève ValueError avec un message
    affichable. L'hôte doit être une IPv4 privée : c'est elle qui est routée."""
    libelle = (libelle or "").strip()
    url = (url or "").strip()
    if not libelle:
        raise ValueError("Le libellé est obligatoire.")
    if len(libelle) > LIBELLE_MAX or len(url) > URL_MAX:
        raise ValueError("Libellé ou adresse trop long.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("L'adresse doit commencer par http:// ou https://.")
    try:
        ip = ipaddress.IPv4Address(parts.hostname or "")
        parts.port  # lève ValueError si le port est invalide
    except ValueError:
        raise ValueError("L'adresse doit contenir l'IP de l'équipement (ex. https://10.103.40.7/).")
    if not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError("L'IP doit être celle d'un équipement du réseau local du chantier.")
    return libelle, url, str(ip)


def list_shortcuts(site_id):
    if not site_id:
        return []
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, label, url FROM site_shortcuts WHERE site_id = ? ORDER BY label, id",
            (site_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_shortcut(site_id, libelle, url):
    libelle, url, _ip = valider(libelle, url)
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO site_shortcuts (site_id, label, url) VALUES (?, ?, ?)",
            (site_id, libelle, url),
        )
        conn.commit()
    logger.info(f"Raccourci ajouté : {libelle} -> {url}")


def delete_shortcut(site_id, shortcut_id):
    with get_db_connection() as conn:
        cur = conn.execute(
            "DELETE FROM site_shortcuts WHERE id = ? AND site_id = ?",
            (shortcut_id, site_id),
        )
        conn.commit()
    return cur.rowcount > 0


def _current_site():
    from services.presence import get_current_site_id
    site_id = get_current_site_id()
    if not site_id:
        return None
    with get_db_connection() as conn:
        row = conn.execute("SELECT id, external_id FROM sites WHERE id = ?", (site_id,)).fetchone()
    return dict(row) if row else None


def current_routes():
    """Routes /32 des raccourcis du chantier courant, triées (aucune sans chantier)."""
    site = _current_site()
    if not site:
        return []
    ips = set()
    for s in list_shortcuts(site["id"]):
        try:
            ips.add(valider(s["label"], s["url"])[2])
        except ValueError:
            continue
    return sorted(f"{ip}/32" for ip in ips)


def sync_payload():
    """Raccourcis du chantier courant pour /sync, ou None sans chantier.
    external_id désigne le chantier de docs auquel ils appartiennent."""
    site = _current_site()
    if not site:
        return None
    return {
        "external_id": site["external_id"],
        "items": [{"label": s["label"], "url": s["url"]} for s in list_shortcuts(site["id"])],
    }
