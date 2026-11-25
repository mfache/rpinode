"""Vues de points BACnet : un nom, des motifs avec jokers (comme la recherche
du dictionnaire, `*` et `?`), et une plage normale facultative.

Une vue rassemble les points du dictionnaire du chantier dont le nom
correspond à l'un de ses motifs, sur un ou plusieurs régulateurs ; un point
ajouté plus tard au régulateur entre donc dans la vue sans rien refaire.
La lecture est en direct (pas d'historique) et chaque point est classé :
OK, sans réponse, capteur en défaut (inf / nan), hors plage.
"""
import math
import re
from html import escape

from core.database import get_db_connection

#: Nombre maximal de points lus pour une vue (protection des régulateurs).
MAX_POINTS = 200

#: Secondes entre deux lectures d'une vue ouverte (flux SSE).
PERIODE_LECTURE = 10

#: États d'un point, du plus grave au moins grave (ordre d'affichage).
ETATS = {
    "sans_reponse": ("Sans réponse", "badge-err"),
    "defaut_capteur": ("Capteur en défaut", "badge-err"),
    "hors_plage": ("Hors plage", "badge-warn"),
    # Le régulateur ne connaît plus l'objet (supprimé depuis la construction
    # du dictionnaire) : à reconstruire, ce n'est pas un défaut de l'installation.
    "objet_absent": ("Objet absent", "badge-warn"),
    "ok": ("OK", "badge-ok"),
}
ORDRE_ETATS = list(ETATS)


def _ligne(row):
    return dict(row) if row else None


def lister_vues(site_id):
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM bacnet_views WHERE site_id = ? ORDER BY name COLLATE NOCASE", (site_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def lire_vue(view_id, site_id):
    with get_db_connection() as conn:
        return _ligne(conn.execute(
            "SELECT * FROM bacnet_views WHERE id = ? AND site_id = ?", (view_id, site_id)
        ).fetchone())


def motifs_de(vue):
    return [m.strip() for m in (vue.get("patterns") or "").splitlines() if m.strip()]


def _nombre_ou_none(valeur):
    if valeur is None or str(valeur).strip() == "":
        return None
    return float(str(valeur).replace(",", "."))


def enregistrer_vue(site_id, nom, motifs, plage_min=None, plage_max=None, view_id=None):
    """Crée ou modifie une vue ; renvoie son id. Lève ValueError si incomplète."""
    nom = (nom or "").strip()
    if isinstance(motifs, str):
        motifs = motifs.splitlines()
    motifs = [m.strip() for m in motifs if m and m.strip()]
    if not nom:
        raise ValueError("Nom de la vue manquant")
    if not motifs:
        raise ValueError("Au moins un motif est nécessaire")
    pmin, pmax = _nombre_ou_none(plage_min), _nombre_ou_none(plage_max)
    if pmin is not None and pmax is not None and pmin > pmax:
        raise ValueError("Plage normale : le minimum dépasse le maximum")
    with get_db_connection() as conn:
        if view_id:
            cur = conn.execute(
                """UPDATE bacnet_views SET name = ?, patterns = ?, range_min = ?, range_max = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ? AND site_id = ?""",
                (nom, "\n".join(motifs), pmin, pmax, view_id, site_id))
            if cur.rowcount == 0:
                raise ValueError("Vue introuvable sur ce chantier")
        else:
            cur = conn.execute(
                "INSERT INTO bacnet_views (site_id, name, patterns, range_min, range_max) VALUES (?, ?, ?, ?, ?)",
                (site_id, nom, "\n".join(motifs), pmin, pmax))
            view_id = cur.lastrowid
        conn.commit()
    return int(view_id)


def supprimer_vue(view_id, site_id):
    with get_db_connection() as conn:
        conn.execute("DELETE FROM bacnet_views WHERE id = ? AND site_id = ?", (view_id, site_id))
        conn.commit()


def points_de_la_vue(vue):
    """Points du dictionnaire correspondant aux motifs de la vue, sans doublon
    (un point visé par deux motifs n'apparaît qu'une fois). Renvoie
    (points, tronque) ; tronque est vrai au-delà de MAX_POINTS."""
    from services.bacnet_catalog import search_points

    vus, points = set(), []
    for motif in motifs_de(vue):
        for p in search_points(motif, site_id=vue["site_id"], limit=MAX_POINTS + 1):
            cle = (p["device_instance"], p["object_id"])
            if cle in vus:
                continue
            vus.add(cle)
            points.append(p)
    return points[:MAX_POINTS], len(points) > MAX_POINTS


def _jetons(nom):
    return [j for j in re.split(r"[_\s]+", nom or "") if j]


def reperes(noms):
    """Partie utile des noms de points : on retire les mots (séparés par « _ »)
    communs à tous en tête et en fin. IFABEL03_D112_TmpAmb et IFABEL03_D005_TmpAmb
    donnent D112 et D005. {nom: repère} ; un nom réduit à rien reste entier."""
    listes = [_jetons(n) for n in noms]
    if len(listes) < 2:
        return {n: n for n in noms}
    court = min(len(l) for l in listes)
    tete = 0
    while tete < court and len({l[tete] for l in listes}) == 1:
        tete += 1
    fin = 0
    while fin < court - tete and len({l[-1 - fin] for l in listes}) == 1:
        fin += 1
    out = {}
    for n, l in zip(noms, listes):
        milieu = l[tete:len(l) - fin]
        out[n] = "_".join(milieu) if milieu else n
    return out


def classer(valeur, erreur, plage_min=None, plage_max=None):
    """État d'un point lu en direct (clé de ETATS)."""
    if erreur and "unknown-object" in str(erreur):
        return "objet_absent"
    if erreur or valeur is None:
        return "sans_reponse"
    try:
        v = float(valeur)
    except (TypeError, ValueError):
        return "ok"  # texte ou état (active / inactive) : pas de plage
    if math.isinf(v) or math.isnan(v):
        return "defaut_capteur"
    if (plage_min is not None and v < plage_min) or (plage_max is not None and v > plage_max):
        return "hors_plage"
    return "ok"


def _cle_naturelle(texte):
    return [(0, int(x), "") if x.isdigit() else (1, 0, x.casefold())
            for x in re.split(r"(\d+)", str(texte)) if x]


def lire_vue_en_direct(vue, timeout=15.0):
    """Lit les points de la vue et renvoie {lignes, resume, tronque} ; les
    lignes sont triées par gravité, puis appareil et repère (tri naturel)."""
    from services.bacnet_mgr import format_display, read_bacnet_points_live_raw

    points, tronque = points_de_la_vue(vue)
    brut = read_bacnet_points_live_raw(
        [{"key": str(i), "address": p["network_address"], "object_id": p["object_id"],
          "device_id": p["device_instance"]} for i, p in enumerate(points)],
        timeout=timeout) if points else {}
    rep = reperes([p["object_name"] or p["object_id"] for p in points])
    lignes = []
    for i, p in enumerate(points):
        item = brut.get(str(i)) or {}
        valeur, erreur = item.get("value"), item.get("error") or (None if item else "Non lu")
        etat = classer(valeur, erreur, vue.get("range_min"), vue.get("range_max"))
        nom = p["object_name"] or p["object_id"]
        lignes.append({
            "appareil": p.get("device_name") or f"Instance {p['device_instance']}",
            "device_instance": p["device_instance"], "object_id": p["object_id"],
            "nom": nom, "repere": rep.get(nom, nom), "etat": etat,
            "affichage": {"sans_reponse": erreur or "Non lu",
                          "objet_absent": "absent du régulateur"}.get(etat) or format_display(valeur),
        })
    lignes.sort(key=lambda l: (ORDRE_ETATS.index(l["etat"]), _cle_naturelle(l["appareil"]),
                               _cle_naturelle(l["repere"])))
    resume = {e: sum(1 for l in lignes if l["etat"] == e) for e in ORDRE_ETATS}
    return {"lignes": lignes, "resume": resume, "total": len(lignes), "tronque": tronque}


def rendre_resume(resultat):
    """HTML du résumé (compteurs par état)."""
    morceaux = [f'<span class="vue-total"><strong>{resultat["total"]}</strong> point(s)</span>']
    for etat, (libelle, classe) in ETATS.items():
        n = resultat["resume"].get(etat, 0)
        if n or etat == "ok":
            morceaux.append(f'<span class="{classe}">{n} {escape(libelle.lower())}</span>')
    if resultat.get("tronque"):
        morceaux.append(f'<span class="badge-warn">limité aux {MAX_POINTS} premiers points</span>')
    return " ".join(morceaux)


def rendre_lignes(resultat):
    """HTML des lignes du tableau de la vue."""
    if not resultat["lignes"]:
        return ('<tr><td colspan="5" class="vue-vide">Aucun point du dictionnaire ne correspond '
                'aux motifs de cette vue.</td></tr>')
    html = []
    for l in resultat["lignes"]:
        libelle, classe = ETATS[l["etat"]]
        html.append(
            f'<tr class="vue-{l["etat"]}">'
            f'<td><span class="{classe}">{escape(libelle)}</span></td>'
            f'<td>{escape(l["appareil"])}</td>'
            f'<td title="{escape(l["nom"])}"><strong>{escape(l["repere"])}</strong></td>'
            f'<td class="vue-valeur">{escape(str(l["affichage"]))}</td>'
            f'<td><code>{escape(l["object_id"])}</code></td>'
            '</tr>')
    return "".join(html)


def _fmt_nombre(v):
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else str(v)


def contexte_page(site_id, view_id=None, motif=None):
    """Variables du gabarit bacnet_vues.html. `motif` (venu de la recherche
    par jokers) ouvre le formulaire d'une nouvelle vue pré-rempli."""
    vues = lister_vues(site_id) if site_id else []
    vue = None
    if view_id:
        vue = lire_vue(view_id, site_id)
    if vue is None and vues and not motif:
        vue = vues[0]
    options = "".join(
        f'<option value="{v["id"]}"{" selected" if vue and v["id"] == vue["id"] else ""}>{escape(v["name"])}</option>'
        for v in vues) or '<option value="">Aucune vue</option>'
    ctx = {
        "options_html": options,
        "vue_id": str(vue["id"]) if vue else "",
        "vue_actions_disabled": "" if vue else "disabled",
        "table_display": "block" if vue else "none",
        "vide_display": "none" if (vue or motif) else "block",
        "periode": str(PERIODE_LECTURE),
        "motifs_affiches": ", ".join(f"<code>{escape(m)}</code>" for m in motifs_de(vue)) if vue else "",
        "plage_affichee": "—",
    }
    if vue and (vue.get("range_min") is not None or vue.get("range_max") is not None):
        ctx["plage_affichee"] = escape(f'{_fmt_nombre(vue.get("range_min")) or "…"} à {_fmt_nombre(vue.get("range_max")) or "…"}')
    if motif:
        ctx.update(form_display="block", form_titre="Nouvelle vue", form_id="", form_nom="",
                   form_min="", form_max="", form_motifs=escape(motif))
    elif vue:
        ctx.update(form_display="none", form_titre=f"Modifier la vue « {escape(vue['name'])} »",
                   form_id=str(vue["id"]), form_nom=escape(vue["name"]),
                   form_min=_fmt_nombre(vue.get("range_min")), form_max=_fmt_nombre(vue.get("range_max")),
                   form_motifs=escape("\n".join(motifs_de(vue))))
    else:
        ctx.update(form_display="none", form_titre="Nouvelle vue", form_id="", form_nom="",
                   form_min="", form_max="", form_motifs="")
    return ctx
