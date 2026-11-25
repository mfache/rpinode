"""Vues de points BACnet (services/bacnet_vues.py) : motifs sur le dictionnaire,
plusieurs régulateurs, classement des valeurs lues en direct."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.paths as paths
from core.database import get_db_connection, init_db
from services.bacnet_catalog import upsert_device_points
from services import bacnet_vues as bv


class _BaseVues(unittest.TestCase):
    """Base temporaire, dictionnaire de test sur deux régulateurs et deux chantiers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = paths.DATABASE_FILE
        paths.DATABASE_FILE = Path(self.temp_dir.name) / "test_vues.db"
        init_db()
        with get_db_connection() as conn:
            conn.execute("INSERT INTO sites (name, external_id) VALUES ('VUES', '777')")
            conn.execute("INSERT INTO sites (name, external_id) VALUES ('AUTRE', '778')")
            conn.commit()
            self.site = conn.execute("SELECT id FROM sites WHERE name='VUES'").fetchone()["id"]
            self.autre = conn.execute("SELECT id FROM sites WHERE name='AUTRE'").fetchone()["id"]
        upsert_device_points(self.site, "192.168.1.202", 5002, [
            {"object_id": "analog-input:47", "name": "IFABEL03_D112_TmpAmb"},
            {"object_id": "analog-input:5", "name": "IFABEL03_D005_TmpAmb"},
            {"object_id": "analog-input:60", "name": "IFABEL03_ZonCom1_TmpAmb"},
            {"object_id": "analog-output:3", "name": "IFABEL03_D004_PcmTmpAmb"},
            {"object_id": "analog-input:3", "name": "IFABEL03_D004_TmpAmb"},
            {"object_id": "analog-output:13", "name": "IFABEL03_D004_ModV2v"},
            {"object_id": "analog-output:8", "name": "IFABEL03_D005_PcmTmpAmb"},
        ])
        upsert_device_points(self.site, "192.168.1.201", 5001, [
            {"object_id": "analog-input:1", "name": "IFABEL01_D201_TmpAmb"},
        ])
        upsert_device_points(self.autre, "10.0.0.1", 9, [
            {"object_id": "analog-input:1", "name": "IFABEL01_X_TmpAmb"},
        ])

    def tearDown(self):
        paths.DATABASE_FILE = self.original_db
        self.temp_dir.cleanup()


class TestBacnetVues(_BaseVues):

    def test_enregistrer_modifier_supprimer(self):
        vid = bv.enregistrer_vue(self.site, "Thermostats", "IFABEL*_*_TmpAmb\n\n", "15", "28,5")
        vue = bv.lire_vue(vid, self.site)
        self.assertEqual((vue["name"], vue["patterns"], vue["range_min"], vue["range_max"]),
                         ("Thermostats", "IFABEL*_*_TmpAmb", 15.0, 28.5))
        self.assertIsNone(bv.lire_vue(vid, self.autre))  # une vue appartient à son chantier

        bv.enregistrer_vue(self.site, "Ambiances", ["IFABEL03_*"], view_id=vid)
        self.assertEqual(bv.lire_vue(vid, self.site)["range_min"], None)
        self.assertEqual([v["name"] for v in bv.lister_vues(self.site)], ["Ambiances"])

        with self.assertRaises(ValueError):
            bv.enregistrer_vue(self.site, "", "x")
        with self.assertRaises(ValueError):
            bv.enregistrer_vue(self.site, "Vide", " \n ")
        with self.assertRaises(ValueError):
            bv.enregistrer_vue(self.site, "Inversée", "x", 30, 10)

        bv.supprimer_vue(vid, self.site)
        self.assertEqual(bv.lister_vues(self.site), [])

    def test_points_plusieurs_regulateurs_sans_doublon(self):
        vid = bv.enregistrer_vue(self.site, "T", "IFABEL*_*_TmpAmb\nIFABEL03_D112_*")
        points = bv.points_de_la_vue(bv.lire_vue(vid, self.site))
        cles = sorted((p["device_instance"], p["object_id"]) for p in points)
        # Les deux régulateurs, D112 une seule fois, rien de l'autre chantier,
        # et PcmTmpAmb exclu (le motif exige « _TmpAmb » en fin).
        self.assertEqual(cles, [(5001, "analog-input:1"), (5002, "analog-input:3"), (5002, "analog-input:47"),
                                (5002, "analog-input:5"), (5002, "analog-input:60")])

    def test_reperes(self):
        self.assertEqual(bv.reperes(["IFABEL03_D112_TmpAmb", "IFABEL03_D005_TmpAmb", "IFABEL03_ZonCom1_TmpAmb"]),
                         {"IFABEL03_D112_TmpAmb": "D112", "IFABEL03_D005_TmpAmb": "D005",
                          "IFABEL03_ZonCom1_TmpAmb": "ZonCom1"})
        # Régulateurs différents : le préfixe n'est plus commun, il reste.
        self.assertEqual(bv.reperes(["IFABEL03_D112_TmpAmb", "IFABEL01_D201_TmpAmb"]),
                         {"IFABEL03_D112_TmpAmb": "IFABEL03_D112", "IFABEL01_D201_TmpAmb": "IFABEL01_D201"})
        self.assertEqual(bv.reperes(["BExt25'R'Flr01'RTemp", "BExt25'R'Flr02'RTemp"]),
                         {"BExt25'R'Flr01'RTemp": "Flr01", "BExt25'R'Flr02'RTemp": "Flr02"})
        self.assertEqual(bv.reperes(["A_X.1_Z", "A_Y.2_Z"]), {"A_X.1_Z": "X.1", "A_Y.2_Z": "Y.2"})
        self.assertEqual(bv.reperes(["Seul_Point"]), {"Seul_Point": "Seul_Point"})
        self.assertEqual(bv.reperes(["A_B", "A_B"]), {"A_B": "A_B"})

    def test_classer(self):
        self.assertEqual(bv.classer(None, "Timeout"), "sans_reponse")
        self.assertEqual(bv.classer("21.5", "Err"), "sans_reponse")
        self.assertEqual(bv.classer(None, "BACnet Error: object: unknown-object"), "objet_absent")
        self.assertEqual(bv.classer("inf", None), "defaut_capteur")
        self.assertEqual(bv.classer("nan", None), "defaut_capteur")
        self.assertEqual(bv.classer("0.0", None, 15, 28), "hors_plage")
        self.assertEqual(bv.classer("29", None, None, 28), "hors_plage")
        self.assertEqual(bv.classer("21.5", None, 15, 28), "ok")
        self.assertEqual(bv.classer("active", None, 15, 28), "ok")

    def test_lecture_en_direct_triee_par_gravite(self):
        vid = bv.enregistrer_vue(self.site, "T", "IFABEL03_*_TmpAmb", 15, 28)
        reponses = {
            ("192.168.1.202", "analog-input:47"): {"value": "0.0", "error": None},
            ("192.168.1.202", "analog-input:5"): {"value": "13.34941291809082", "error": None},
            ("192.168.1.202", "analog-input:60"): {"value": "inf", "error": None},
        }

        def lecture(points, timeout):
            out = {}
            for p in points:
                r = reponses.get((p["address"], p["object_id"]))
                out[p["key"]] = r if r else {"value": None, "error": "Timeout"}
            return out

        with patch("services.bacnet_mgr.read_bacnet_points_live_raw", side_effect=lecture):
            res = bv.lire_vue_en_direct(bv.lire_vue(vid, self.site))
        self.assertEqual([(l["repere"], l["etat"], l["affichage"]) for l in res["lignes"]],
                         [("D004", "sans_reponse", "Timeout"),
                          ("ZonCom1", "defaut_capteur", "inf"), ("D005", "hors_plage", "13.35"),
                          ("D112", "hors_plage", "0")])
        self.assertEqual(res["resume"], {"sans_reponse": 1, "defaut_capteur": 1, "hors_plage": 2,
                                         "objet_absent": 0, "ok": 0})
        html = bv.rendre_lignes(res) + bv.rendre_resume(res)
        self.assertIn("Capteur en défaut", html)
        self.assertIn('title="IFABEL03_D112_TmpAmb"', html)

    def test_contexte_page(self):
        ctx = bv.contexte_page(self.site)
        self.assertEqual((ctx["vue_id"], ctx["vide_display"], ctx["table_display"]), ("", "block", "none"))
        ctx = bv.contexte_page(self.site, motif="IFABEL*_<b>")
        self.assertEqual(ctx["form_display"], "block")
        self.assertEqual(ctx["form_motifs"], "IFABEL*_&lt;b&gt;")
        vid = bv.enregistrer_vue(self.site, "Thermostats <E>", "A*\nB*", 15)
        ctx = bv.contexte_page(self.site)
        self.assertEqual(ctx["vue_id"], str(vid))
        self.assertIn("Thermostats &lt;E&gt;", ctx["options_html"])
        self.assertEqual(ctx["form_motifs"], "A*\nB*")
        self.assertEqual(ctx["plage_affichee"], "15 à …")


class TestVueParPiece(_BaseVues):
    """Regex de découpage et vue par pièce (une ligne par pièce, une colonne par rôle)."""

    REGEX = r"^IFABEL03_(?P<piece>[^_]+)_(?P<role>.+)$"
    ROLES = [{"role": "TmpAmb", "libelle": "Température ambiante", "min": "15", "max": "28"},
             {"role": "PcmTmpAmb", "libelle": "Consigne"},
             {"role": "ModV2v", "libelle": ""},
             {"role": "", "libelle": "ligne vide ignorée"}]

    def test_proposition_depuis_les_points_coches(self):
        motif, regex, roles = bv.proposer_vue_piece(
            ["IFABEL03_D001_TmpAmb", "IFABEL03_D001_ModV2v", "IFABEL03_D001_PcmTmpAmb"])
        self.assertEqual((motif, regex), ("IFABEL03_*", self.REGEX))
        self.assertEqual([r["role"] for r in roles], ["TmpAmb", "ModV2v", "PcmTmpAmb"])
        # Un seul point : le dernier mot est le rôle, l'avant-dernier la pièce.
        self.assertEqual(bv.proposer_vue_piece(["IFABEL03_D001_TmpAmb"])[1], self.REGEX)
        # Autre convention (Siemens) : séparateurs « ' » repris.
        motif, regex, roles = bv.proposer_vue_piece(["BExt25'R'Flr01'RTemp", "BExt25'R'Flr01'RHum"])
        self.assertEqual(motif, "BExt25'R'*")
        self.assertEqual(bv.compiler_regex(regex).match("BExt25'R'Flr07'RTemp").groupdict(),
                         {"piece": "Flr07", "role": "RTemp"})

    def test_regex_et_roles_valides(self):
        self.assertEqual(bv.compiler_regex(r"^(?<piece>\w+)$").pattern, r"^(?P<piece>\w+)$")
        self.assertIsNone(bv.compiler_regex("  "))
        with self.assertRaises(ValueError):
            bv.compiler_regex("^(?P<piece>[")
        roles = bv.normaliser_roles(self.ROLES)
        self.assertEqual([(r["role"], r["libelle"], r["min"], r["max"]) for r in roles],
                         [("TmpAmb", "Température ambiante", 15.0, 28.0), ("PcmTmpAmb", "Consigne", None, None),
                          ("ModV2v", "ModV2v", None, None)])
        for mauvais in ([{"role": "A"}, {"role": "A"}], [{"role": "A", "min": 5, "max": 1}]):
            with self.assertRaises(ValueError):
                bv.normaliser_roles(mauvais)
        # Vue par pièce sans regex, ou regex sans groupe role : refusée.
        with self.assertRaises(ValueError):
            bv.enregistrer_vue(self.site, "P", "IFABEL03_*", roles=self.ROLES)
        with self.assertRaises(ValueError):
            bv.enregistrer_vue(self.site, "P", "IFABEL03_*", roles=self.ROLES, regex=r"^(?P<piece>.+)$")
        with self.assertRaises(ValueError):
            bv.enregistrer_vue(self.site, "P", "IFABEL03_*", regex=r"^(?P<role>.+)$")

    def _lire(self, vue, valeurs):
        def lecture(points, timeout):
            return {p["key"]: {"value": valeurs.get(p["object_id"]), "error": None} for p in points}
        with patch("services.bacnet_mgr.read_bacnet_points_live_raw", side_effect=lecture) as m:
            res = bv.lire_vue_en_direct(vue)
        return res, m

    def test_lecture_par_piece(self):
        vid = bv.enregistrer_vue(self.site, "Pièces", "IFABEL03_*", roles=self.ROLES, regex=self.REGEX)
        vue = bv.lire_vue(vid, self.site)
        res, lecture = self._lire(vue, {
            "analog-input:47": "0.0", "analog-input:5": "19.5", "analog-input:60": "21",
            "analog-input:3": "20", "analog-output:3": "24", "analog-output:13": "100", "analog-output:8": "22"})
        # Seuls les points des rôles sont lus (7 sur les 7 du périmètre ici).
        self.assertEqual(len(lecture.call_args[0][0]), 7)
        self.assertEqual(res["mode"], "pieces")
        lignes = {l["repere"]: l for l in res["lignes"]}
        self.assertEqual(sorted(lignes), ["D004", "D005", "D112", "ZonCom1"])
        self.assertEqual(res["lignes"][0]["repere"], "D112")  # 0 °C : hors plage, en tête
        self.assertEqual({i: c["affichage"] for i, c in lignes["D004"]["cellules"].items()},
                         {0: "20", 1: "24", 2: "100"})
        self.assertEqual(lignes["D005"]["etat"], "ok")
        self.assertEqual(res["non_reconnus"], [])
        html = bv.rendre_lignes(res, vue)
        self.assertIn("vue-manque", html)  # D112 n'a ni consigne ni vanne
        self.assertIn("<th>Température ambiante<br>", bv.rendre_entete(vue))
        self.assertIn("pièce(s)", bv.rendre_resume(res))

    def test_vue_par_point_avec_regex_et_noms_non_reconnus(self):
        vid = bv.enregistrer_vue(self.site, "T", "IFABEL*_*_TmpAmb", regex=self.REGEX)
        res, _ = self._lire(bv.lire_vue(vid, self.site), {"analog-input:47": "21"})
        reperes = {l["object_id"]: l["repere"] for l in res["lignes"]}
        self.assertEqual(reperes["analog-input:47"], "D112")       # groupe piece
        self.assertEqual(res["non_reconnus"], ["IFABEL01_D201_TmpAmb"])  # autre station
        self.assertIn("1 nom(s) non reconnu(s)", bv.rendre_resume(res))

    def test_apercu_du_decoupage(self):
        res = bv.apercu(self.site, "IFABEL*_*", self.REGEX, roles=[{"role": "TmpAmb"}])
        self.assertEqual(res["total"], 8)
        self.assertEqual(res["reconnus"], 7)          # IFABEL01_D201_TmpAmb : autre station
        self.assertEqual(res["pieces"], 4)            # D004, D005, D112, ZonCom1
        self.assertEqual(res["roles_trouves"], {"TmpAmb": 4, "PcmTmpAmb": 2, "ModV2v": 1})
        self.assertEqual(res["lignes"][0]["nom"], "IFABEL01_D201_TmpAmb")  # non reconnu en tête
        html = bv.rendre_apercu(res)
        self.assertIn("7 reconnu(s) par la regex", html)
        self.assertIn('data-role-ajout="PcmTmpAmb"', html)
        self.assertIn("✓ TmpAmb", html)               # déjà dans la vue
        # Regex invalide : message, mais les noms restent visibles pour la corriger.
        res = bv.apercu(self.site, "IFABEL03_*", "^(?P<piece>[")
        self.assertIn("Regex invalide", res["erreur"])
        self.assertEqual(len(res["lignes"]), 7)
        self.assertIn("Saisissez un motif", bv.rendre_apercu(bv.apercu(self.site, "", "")))

    def test_contexte_nouvelle_vue_par_piece(self):
        ctx = bv.contexte_page(self.site, noms=["IFABEL03_D001_TmpAmb", "IFABEL03_D001_ModV2v"])
        self.assertEqual((ctx["form_display"], ctx["form_motifs"]), ("block", "IFABEL03_*"))
        self.assertIn('value="ModV2v"', ctx["roles_html"])
        self.assertIn("(?P&lt;piece&gt;", ctx["form_regex"])


if __name__ == "__main__":
    unittest.main()
