"""Vues de points BACnet : un nom, des motifs avec jokers (comme la recherche
du dictionnaire, `*` et `?`), une regex de découpage facultative, une plage
normale facultative et, pour une vue par pièce, des rôles.

Une vue rassemble les points du dictionnaire du chantier dont le nom
correspond à l'un de ses motifs, sur un ou plusieurs régulateurs ; un point
ajouté plus tard au régulateur entre donc dans la vue sans rien refaire.
La lecture est en direct (pas d'historique) et chaque point est classé :
sans réponse, capteur en défaut (inf / nan), hors plage, objet absent, OK.

Regex de découpage : groupes nommés `piece` et `role`, par exemple
``^IFABEL03_(?P<piece>[^_]+)_(?P<role>.+)$`` (IFABEL03_D111A_PcmTmpAmb donne
la pièce D111A et le rôle PcmTmpAmb). Les motifs choisissent les points dans
le dictionnaire (SQLite ne connaît pas les regex), la regex découpe leur nom.
Sans regex, le repère d'un point est deviné (mots communs retirés).

Vue par pièce : des rôles (valeurs du groupe `role`, avec libellé et plage
normale) ; le tableau montre une ligne par pièce, une colonne par rôle.
"""
import json
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

#: Séparateurs de mots pour deviner un repère sans regex : tout ce qui n'est
#: ni lettre ni chiffre (_ ' . - espace…), selon la convention de la station.
_SEPARATEUR = re.compile(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ]+")


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------

def _depuis_ligne(row):
    if not row:
        return None
    vue = dict(row)
    try:
        vue["roles"] = json.loads(vue.get("roles_json") or "[]") or []
    except ValueError:
        vue["roles"] = []
    return vue


def lister_vues(site_id):
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM bacnet_views WHERE site_id = ? ORDER BY name COLLATE NOCASE", (site_id,)
        ).fetchall()
    return [_depuis_ligne(r) for r in rows]


def lire_vue(view_id, site_id):
    with get_db_connection() as conn:
        return _depuis_ligne(conn.execute(
            "SELECT * FROM bacnet_views WHERE id = ? AND site_id = ?", (view_id, site_id)
        ).fetchone())


def motifs_de(vue):
    return [m.strip() for m in (vue.get("patterns") or "").splitlines() if m.strip()]


def _nombre_ou_none(valeur):
    if valeur is None or str(valeur).strip() == "":
        return None
    return float(str(valeur).replace(",", "."))


def compiler_regex(texte):
    """Regex de découpage compilée, ou None si vide. Accepte aussi la forme
    (?<nom>…) des autres langages. Lève ValueError si invalide."""
    texte = (texte or "").strip()
    if not texte:
        return None
    texte = re.sub(r"\(\?<(?![=!])", "(?P<", texte)
    try:
        return re.compile(texte)
    except re.error as e:
        raise ValueError(f"Regex invalide : {e}")


def normaliser_roles(roles):
    """Rôles d'une vue par pièce, nettoyés : valeur du groupe `role`, sans
    doublon, libellé par défaut = le rôle. Lignes sans rôle ignorées ; liste
    vide = vue par point."""
    propres, vus = [], set()
    for r in roles or []:
        role = (r.get("role") or "").strip()
        if not role:
            continue
        if role in vus:
            raise ValueError(f"Rôle {role} en double")
        vus.add(role)
        pmin, pmax = _nombre_ou_none(r.get("min")), _nombre_ou_none(r.get("max"))
        if pmin is not None and pmax is not None and pmin > pmax:
            raise ValueError(f"Rôle {role} : le minimum dépasse le maximum")
        propres.append({"role": role, "libelle": (r.get("libelle") or "").strip() or role,
                        "min": pmin, "max": pmax})
    return propres


def enregistrer_vue(site_id, nom, motifs, plage_min=None, plage_max=None, view_id=None,
                    roles=None, regex=None):
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
    rx = compiler_regex(regex)
    roles = normaliser_roles(roles)
    groupes = set(rx.groupindex) if rx else set()
    if roles and not {"piece", "role"} <= groupes:
        raise ValueError("Une vue par pièce demande une regex avec les groupes (?P<piece>…) et (?P<role>…)")
    if rx and "piece" not in groupes:
        raise ValueError("La regex doit avoir un groupe (?P<piece>…)")
    valeurs = (nom, "\n".join(motifs), pmin, pmax,
               json.dumps(roles, ensure_ascii=False) if roles else None, rx.pattern if rx else None)
    with get_db_connection() as conn:
        if view_id:
            cur = conn.execute(
                """UPDATE bacnet_views SET name = ?, patterns = ?, range_min = ?, range_max = ?,
                   roles_json = ?, name_regex = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND site_id = ?""",
                valeurs + (view_id, site_id))
            if cur.rowcount == 0:
                raise ValueError("Vue introuvable sur ce chantier")
        else:
            cur = conn.execute(
                "INSERT INTO bacnet_views (site_id, name, patterns, range_min, range_max, roles_json, name_regex) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (site_id,) + valeurs)
            view_id = cur.lastrowid
        conn.commit()
    return int(view_id)


def supprimer_vue(view_id, site_id):
    with get_db_connection() as conn:
        conn.execute("DELETE FROM bacnet_views WHERE id = ? AND site_id = ?", (view_id, site_id))
        conn.commit()


# ---------------------------------------------------------------------------
# Proposition d'une vue par pièce depuis les points cochés
# ---------------------------------------------------------------------------

def _morceaux(nom):
    """[mot, séparateur, mot, …] : les séparateurs d'origine sont gardés."""
    return [m for m in re.split(f"({_SEPARATEUR.pattern})", nom or "") if m]


def proposer_vue_piece(noms):
    """(motif, regex, rôles) proposés à partir des noms des points d'une pièce
    cochés dans la recherche : les mots communs en tête sont la station puis
    la pièce, le reste est le rôle ; les séparateurs de la station sont repris.
    IFABEL03_D001_TmpAmb + IFABEL03_D001_ModV2v → « IFABEL03_* »,
    ^IFABEL03_(?P<piece>[^_]+)_(?P<role>.+)$, rôles TmpAmb et ModV2v."""
    listes = [_morceaux(n) for n in noms if n]
    if not listes:
        return "*", "", []
    # Indice (dans les morceaux) du mot de la pièce : le dernier mot commun en
    # tête, ou l'avant-dernier mot s'il n'y a qu'un nom.
    if len(listes) == 1:
        mots = [i for i, m in enumerate(listes[0]) if not _SEPARATEUR.fullmatch(m)]
        i_piece = mots[-2] if len(mots) >= 2 else None
    else:
        court = min(len(l) for l in listes)
        i = 0
        while i < court - 1 and len({l[i] for l in listes}) == 1:
            i += 1
        # i = premier morceau qui diffère (début du rôle) ; la pièce est le
        # dernier mot commun avant lui.
        communs = [k for k in range(i) if not _SEPARATEUR.fullmatch(listes[0][k])]
        i_piece = communs[-1] if communs else None
    if i_piece is None or i_piece + 1 >= len(listes[0]):
        return "*", "", []
    ref = listes[0]
    station, sep = "".join(ref[:i_piece]), ref[i_piece + 1]
    if not _SEPARATEUR.fullmatch(sep):
        return "*", "", []
    regex = (f"^{re.escape(station)}(?P<piece>[^{re.escape(sep[0])}]+){re.escape(sep)}(?P<role>.+)$")
    motif = station + "*" if station else "*"
    rx = re.compile(regex)
    roles, vus = [], set()
    for n in noms:
        m = rx.match(n or "")
        if m and m.group("role") not in vus:
            vus.add(m.group("role"))
            roles.append({"role": m.group("role"), "libelle": "", "min": None, "max": None})
    return motif, regex, roles


# ---------------------------------------------------------------------------
# Lecture et classement
# ---------------------------------------------------------------------------

def points_de_la_vue(vue):
    """Points du dictionnaire correspondant aux motifs de la vue, sans doublon
    (un point visé par deux motifs n'apparaît qu'une fois). Renvoie
    (points, tronque) ; tronque est vrai au-delà de MAX_POINTS."""
    from services.bacnet_catalog import search_points

    vus, points = set(), []
    for motif in motifs_de(vue):
        for p in search_points(motif, site_id=vue["site_id"], limit=MAX_POINTS * 5):
            cle = (p["device_instance"], p["object_id"])
            if cle in vus:
                continue
            vus.add(cle)
            points.append(p)
    return points


def reperes(noms):
    """Repère deviné (sans regex) : on retire les mots communs à tous en tête et
    en fin, quel que soit le séparateur (_ ' . - …). IFABEL03_D112_TmpAmb et
    IFABEL03_D005_TmpAmb donnent D112 et D005. {nom: repère} ; un nom réduit à
    rien reste entier."""
    listes = [_morceaux(n) for n in noms]
    mots = [[m for m in l if not _SEPARATEUR.fullmatch(m)] for l in listes]
    if len(mots) < 2:
        return {n: n for n in noms}
    court = min(len(l) for l in mots)
    tete = 0
    while tete < court and len({l[tete] for l in mots}) == 1:
        tete += 1
    fin = 0
    while fin < court - tete and len({l[-1 - fin] for l in mots}) == 1:
        fin += 1
    out = {}
    for n, l in zip(noms, listes):
        # On garde les séparateurs d'origine entre les mots du milieu.
        idx = [i for i, m in enumerate(l) if not _SEPARATEUR.fullmatch(m)]
        milieu = idx[tete:len(idx) - fin]
        out[n] = "".join(l[milieu[0]:milieu[-1] + 1]) if milieu else n
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


def _lire_points(points, timeout):
    from services.bacnet_mgr import read_bacnet_points_live_raw
    if not points:
        return {}
    return read_bacnet_points_live_raw(
        [{"key": str(i), "address": p["network_address"], "object_id": p["object_id"],
          "device_id": p["device_instance"]} for i, p in enumerate(points)],
        timeout=timeout)


def _cellule(item, plage_min, plage_max):
    from services.bacnet_mgr import format_display
    valeur, erreur = item.get("value"), item.get("error") or (None if item else "Non lu")
    etat = classer(valeur, erreur, plage_min, plage_max)
    affichage = {"sans_reponse": erreur or "Non lu",
                 "objet_absent": "absent du régulateur"}.get(etat) or format_display(valeur)
    return etat, affichage


def lire_vue_en_direct(vue, timeout=15.0):
    """Lit les points de la vue. Vue par point : {mode: "points", lignes, …},
    une ligne par point. Vue par pièce : mode "pieces", une ligne par pièce
    avec une cellule par rôle, l'état de la pièce étant le plus grave de ses
    cellules ; seuls les points des rôles sont lus. Tri par gravité, puis
    appareil et repère (tri naturel). `non_reconnus` liste les noms que la
    regex ne découpe pas."""
    rx = compiler_regex(vue.get("name_regex"))
    roles = vue.get("roles") or []
    points = points_de_la_vue(vue)
    decoupes, non_reconnus = {}, []
    for p in points:
        nom = p["object_name"] or ""
        m = rx.match(nom) if rx else None
        if rx and not m:
            non_reconnus.append(nom or p["object_id"])
        decoupes[(p["device_instance"], p["object_id"])] = m.groupdict() if m else None

    if roles:
        index_role = {r["role"]: i for i, r in enumerate(roles)}
        points = [p for p in points if (decoupes[(p["device_instance"], p["object_id"])] or {}).get("role")
                  in index_role]
    tronque = len(points) > MAX_POINTS
    points = points[:MAX_POINTS]
    brut = _lire_points(points, timeout)

    if roles:
        resultat = _par_piece(roles, index_role, points, decoupes, brut)
    else:
        resultat = _par_point(vue, points, decoupes, brut)
    resultat.update(tronque=tronque, non_reconnus=non_reconnus)
    return resultat


def _par_point(vue, points, decoupes, brut):
    devines = reperes([p["object_name"] or p["object_id"] for p in points])
    roles_vus = {(d or {}).get("role") for d in decoupes.values() if d}
    lignes = []
    for i, p in enumerate(points):
        nom = p["object_name"] or p["object_id"]
        d = decoupes.get((p["device_instance"], p["object_id"]))
        if d and d.get("piece"):
            repere = d["piece"] + (f" · {d['role']}" if d.get("role") and len(roles_vus) > 1 else "")
        else:
            repere = devines.get(nom, nom)
        etat, affichage = _cellule(brut.get(str(i)) or {}, vue.get("range_min"), vue.get("range_max"))
        lignes.append({
            "appareil": p.get("device_name") or f"Instance {p['device_instance']}",
            "device_instance": p["device_instance"], "object_id": p["object_id"],
            "nom": nom, "repere": repere, "etat": etat, "affichage": affichage,
        })
    return _trier_et_resumer(lignes, "points")


def _par_piece(roles, index_role, points, decoupes, brut):
    pieces = {}
    for i, p in enumerate(points):
        d = decoupes[(p["device_instance"], p["object_id"])]
        idx = index_role[d["role"]]
        piece = pieces.setdefault((p["device_instance"], d["piece"]), {
            "appareil": p.get("device_name") or f"Instance {p['device_instance']}",
            "device_instance": p["device_instance"], "nom": d["piece"], "repere": d["piece"],
            "cellules": {}})
        etat, affichage = _cellule(brut.get(str(i)) or {}, roles[idx]["min"], roles[idx]["max"])
        piece["cellules"][idx] = {"etat": etat, "affichage": affichage,
                                  "nom": p["object_name"], "object_id": p["object_id"]}
    lignes = []
    for pc in pieces.values():
        pc["etat"] = min((c["etat"] for c in pc["cellules"].values()), key=ORDRE_ETATS.index)
        lignes.append(pc)
    return _trier_et_resumer(lignes, "pieces")


def _trier_et_resumer(lignes, mode):
    lignes.sort(key=lambda l: (ORDRE_ETATS.index(l["etat"]), _cle_naturelle(l["appareil"]),
                               _cle_naturelle(l["repere"])))
    resume = {e: sum(1 for l in lignes if l["etat"] == e) for e in ORDRE_ETATS}
    return {"mode": mode, "lignes": lignes, "resume": resume, "total": len(lignes)}


# ---------------------------------------------------------------------------
# Rendu HTML (le gabarit ne fait qu'insérer)
# ---------------------------------------------------------------------------

def rendre_entete(vue):
    """Ligne d'en-tête du tableau : fixe, ou une colonne par rôle."""
    if vue and vue.get("roles"):
        colonnes = ["État", "Appareil", "Pièce"] + [
            f'{escape(r["libelle"])}<br><small><code>{escape(r["role"])}</code></small>' for r in vue["roles"]]
    else:
        colonnes = ["État", "Appareil", "Repère", "Valeur", "Objet"]
    return "<tr>" + "".join(f"<th>{c}</th>" for c in colonnes) + "</tr>"


def rendre_resume(resultat):
    """HTML du résumé (compteurs par état, en points ou en pièces)."""
    unite = "pièce(s)" if resultat.get("mode") == "pieces" else "point(s)"
    morceaux = [f'<span class="vue-total"><strong>{resultat["total"]}</strong> {unite}</span>']
    for etat, (libelle, classe) in ETATS.items():
        n = resultat["resume"].get(etat, 0)
        if n or etat == "ok":
            morceaux.append(f'<span class="{classe}">{n} {escape(libelle.lower())}</span>')
    if resultat.get("tronque"):
        morceaux.append(f'<span class="badge-warn">limité aux {MAX_POINTS} premiers points</span>')
    non_reconnus = resultat.get("non_reconnus") or []
    if non_reconnus:
        exemples = ", ".join(non_reconnus[:5]) + (" …" if len(non_reconnus) > 5 else "")
        morceaux.append(f'<span class="badge-warn" title="{escape(exemples)}">'
                        f'{len(non_reconnus)} nom(s) non reconnu(s) par la regex</span>')
    return " ".join(morceaux)


def rendre_lignes(resultat, vue=None):
    """HTML des lignes du tableau de la vue."""
    roles = (vue or {}).get("roles") or []
    nb_col = 3 + len(roles) if resultat.get("mode") == "pieces" else 5
    if not resultat["lignes"]:
        return (f'<tr><td colspan="{nb_col}" class="vue-vide">Aucun point du dictionnaire ne correspond '
                'à cette vue.</td></tr>')
    html = []
    for l in resultat["lignes"]:
        libelle, classe = ETATS[l["etat"]]
        debut = (f'<tr class="vue-{l["etat"]}">'
                 f'<td><span class="{classe}">{escape(libelle)}</span></td>'
                 f'<td>{escape(l["appareil"])}</td>'
                 f'<td title="{escape(l["nom"])}"><strong>{escape(l["repere"])}</strong></td>')
        if resultat.get("mode") == "pieces":
            cellules = []
            for i in range(len(roles)):
                c = l["cellules"].get(i)
                if not c:
                    cellules.append('<td class="vue-cellule vue-manque">—</td>')
                    continue
                c_libelle, c_classe = ETATS[c["etat"]]
                titre = f'{c["nom"]} ({c["object_id"]})' + ("" if c["etat"] == "ok" else f" : {c_libelle}")
                contenu = (escape(str(c["affichage"])) if c["etat"] == "ok"
                           else f'<span class="{c_classe}">{escape(str(c["affichage"]))}</span>')
                cellules.append(f'<td class="vue-cellule vue-valeur" title="{escape(titre)}">{contenu}</td>')
            html.append(debut + "".join(cellules) + "</tr>")
        else:
            html.append(debut
                        + f'<td class="vue-valeur">{escape(str(l["affichage"]))}</td>'
                        + f'<td><code>{escape(l["object_id"])}</code></td></tr>')
    return "".join(html)


def _fmt_nombre(v):
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else str(v)


def rendre_lignes_roles(roles):
    """Lignes de l'éditeur des rôles (une par rôle, plus une vide à remplir)."""
    lignes = list(roles or []) + [{"role": "", "libelle": "", "min": None, "max": None}]
    return "".join(
        '<div class="role-ligne">'
        f'<input type="text" data-role="role" value="{escape(r["role"])}" placeholder="TmpAmb">'
        f'<input type="text" data-role="libelle" value="{escape(r["libelle"])}" placeholder="Température ambiante">'
        f'<input type="text" data-role="min" value="{_fmt_nombre(r.get("min"))}" placeholder="min" inputmode="decimal">'
        f'<input type="text" data-role="max" value="{_fmt_nombre(r.get("max"))}" placeholder="max" inputmode="decimal">'
        '<button type="button" class="btn-secondary btn-sm" onclick="this.parentElement.remove()" title="Retirer ce rôle">✕</button>'
        '</div>'
        for r in lignes)


def contexte_page(site_id, view_id=None, motif=None, noms=None):
    """Variables du gabarit bacnet_vues.html. `motif` (recherche par jokers)
    ouvre une nouvelle vue par point pré-remplie ; `noms` (points d'une pièce
    cochés dans la recherche) ouvre une nouvelle vue par pièce, avec motif,
    regex et rôles proposés."""
    vues = lister_vues(site_id) if site_id else []
    nouvelle = bool(motif or noms)
    vue = lire_vue(view_id, site_id) if view_id else None
    if vue is None and vues and not nouvelle:
        vue = vues[0]
    options = "".join(
        f'<option value="{v["id"]}"{" selected" if vue and v["id"] == vue["id"] else ""}>{escape(v["name"])}</option>'
        for v in vues) or '<option value="">Aucune vue</option>'
    ctx = {
        "options_html": options,
        "vue_id": str(vue["id"]) if vue else "",
        "vue_actions_disabled": "" if vue else "disabled",
        "table_display": "block" if vue else "none",
        "vide_display": "none" if (vue or nouvelle) else "block",
        "periode": str(PERIODE_LECTURE),
        "entete_html": rendre_entete(vue),
        "motifs_affiches": ", ".join(f"<code>{escape(m)}</code>" for m in motifs_de(vue)) if vue else "",
        "regex_affichee": f"<code>{escape(vue['name_regex'])}</code>" if vue and vue.get("name_regex") else "—",
        "plage_affichee": "—",
        "roles_vides_html": rendre_lignes_roles([]),
    }
    if vue and vue.get("roles"):
        ctx["plage_affichee"] = "par rôle"
    elif vue and (vue.get("range_min") is not None or vue.get("range_max") is not None):
        ctx["plage_affichee"] = escape(f'{_fmt_nombre(vue.get("range_min")) or "…"} à {_fmt_nombre(vue.get("range_max")) or "…"}')
    vide = dict(form_id="", form_nom="", form_min="", form_max="", form_motifs="", form_regex="",
                roles_html=rendre_lignes_roles([]))
    if noms:
        motif_piece, regex, roles = proposer_vue_piece(noms)
        ctx.update(vide, form_display="block", form_titre="Nouvelle vue par pièce",
                   form_motifs=escape(motif_piece), form_regex=escape(regex), roles_html=rendre_lignes_roles(roles))
    elif motif:
        ctx.update(vide, form_display="block", form_titre="Nouvelle vue", form_motifs=escape(motif))
    elif vue:
        ctx.update(form_display="none", form_titre=f"Modifier la vue « {escape(vue['name'])} »",
                   form_id=str(vue["id"]), form_nom=escape(vue["name"]),
                   form_min=_fmt_nombre(vue.get("range_min")), form_max=_fmt_nombre(vue.get("range_max")),
                   form_motifs=escape("\n".join(motifs_de(vue))), form_regex=escape(vue.get("name_regex") or ""),
                   roles_html=rendre_lignes_roles(vue["roles"]))
    else:
        ctx.update(vide, form_display="none", form_titre="Nouvelle vue")
    return ctx
