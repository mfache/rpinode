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
        points, tronque = bv.points_de_la_vue(bv.lire_vue(vid, self.site))
        cles = sorted((p["device_instance"], p["object_id"]) for p in points)
        # Les deux régulateurs, D112 une seule fois, rien de l'autre chantier,
        # et PcmTmpAmb exclu (le motif exige « _TmpAmb » en fin).
        self.assertEqual(cles, [(5001, "analog-input:1"), (5002, "analog-input:3"), (5002, "analog-input:47"),
                                (5002, "analog-input:5"), (5002, "analog-input:60")])
        self.assertFalse(tronque)

    def test_reperes(self):
        self.assertEqual(bv.reperes(["IFABEL03_D112_TmpAmb", "IFABEL03_D005_TmpAmb", "IFABEL03_ZonCom1_TmpAmb"]),
                         {"IFABEL03_D112_TmpAmb": "D112", "IFABEL03_D005_TmpAmb": "D005",
                          "IFABEL03_ZonCom1_TmpAmb": "ZonCom1"})
        # Régulateurs différents : le préfixe n'est plus commun, il reste.
        self.assertEqual(bv.reperes(["IFABEL03_D112_TmpAmb", "IFABEL01_D201_TmpAmb"]),
                         {"IFABEL03_D112_TmpAmb": "IFABEL03_D112", "IFABEL01_D201_TmpAmb": "IFABEL01_D201"})
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
    """Vue par pièce : rôles = fins de nom, une ligne par pièce."""

    ROLES = [{"suffixe": "_TmpAmb", "libelle": "Température ambiante", "min": "15", "max": "28"},
             {"suffixe": "PcmTmpAmb", "libelle": "Consigne"},
             {"suffixe": "_ModV2v", "libelle": ""},
             {"suffixe": "", "libelle": "ligne vide ignorée"}]

    def test_proposition_depuis_les_points_coches(self):
        motif, roles = bv.proposer_vue_piece(
            ["IFABEL03_D001_TmpAmb", "IFABEL03_D001_ModV2v", "IFABEL03_D001_PcmTmpAmb"])
        self.assertEqual(motif, "IFABEL03_*")
        self.assertEqual([r["suffixe"] for r in roles], ["_TmpAmb", "_ModV2v", "_PcmTmpAmb"])
        self.assertEqual(bv.proposer_vue_piece(["IFABEL03_D001_TmpAmb"])[1][0]["suffixe"], "_TmpAmb")
        self.assertEqual(bv.proposer_vue_piece(["D001_TmpAmb", "D001_ModV2v"])[0], "*")

    def test_roles_normalises_et_refus(self):
        roles = bv.normaliser_roles(self.ROLES)
        self.assertEqual([(r["suffixe"], r["libelle"], r["min"], r["max"]) for r in roles],
                         [("_TmpAmb", "Température ambiante", 15.0, 28.0), ("_PcmTmpAmb", "Consigne", None, None),
                          ("_ModV2v", "ModV2v", None, None)])
        for mauvais in ([{"suffixe": "_A*"}], [{"suffixe": "_A"}, {"suffixe": "A"}],
                        [{"suffixe": "_A", "min": 5, "max": 1}]):
            with self.assertRaises(ValueError):
                bv.normaliser_roles(mauvais)

    def test_role_du_nom_suffixe_le_plus_long(self):
        roles = bv.normaliser_roles(self.ROLES)
        self.assertEqual(bv.role_du_nom("IFABEL03_D004_PcmTmpAmb", roles), (1, "IFABEL03_D004"))
        self.assertEqual(bv.role_du_nom("IFABEL03_D004_TmpAmb", roles), (0, "IFABEL03_D004"))
        self.assertEqual(bv.role_du_nom("IFABEL03_D004_Autre", roles), (None, None))

    def test_lecture_par_piece(self):
        vid = bv.enregistrer_vue(self.site, "Pièces", "IFABEL03_*", roles=self.ROLES)
        vue = bv.lire_vue(vid, self.site)
        self.assertEqual(bv._motifs_de_recherche(vue),
                         ["IFABEL03_*_TmpAmb", "IFABEL03_*_PcmTmpAmb", "IFABEL03_*_ModV2v"])
        valeurs = {"analog-input:47": "0.0", "analog-input:5": "19.5", "analog-input:60": "21",
                   "analog-input:3": "20", "analog-output:3": "24", "analog-output:13": "100",
                   "analog-output:8": "22"}

        def lecture(points, timeout):
            return {p["key"]: {"value": valeurs.get(p["object_id"]), "error": None} for p in points}

        with patch("services.bacnet_mgr.read_bacnet_points_live_raw", side_effect=lecture):
            res = bv.lire_vue_en_direct(vue)
        self.assertEqual(res["mode"], "pieces")
        lignes = {l["repere"]: l for l in res["lignes"]}
        self.assertEqual(sorted(lignes), ["D004", "D005", "D112", "ZonCom1"])
        self.assertEqual(res["lignes"][0]["repere"], "D112")  # 0 °C : hors plage, en tête
        self.assertEqual({i: c["affichage"] for i, c in lignes["D004"]["cellules"].items()},
                         {0: "20", 1: "24", 2: "100"})
        self.assertEqual(lignes["D005"]["etat"], "ok")
        self.assertEqual(res["resume"]["hors_plage"], 1)
        html = bv.rendre_lignes(res, vue)
        self.assertIn("vue-manque", html)  # D112 n'a ni consigne ni vanne
        self.assertIn("<th>Température ambiante<br>", bv.rendre_entete(vue))
        self.assertIn("pièce(s)", bv.rendre_resume(res))

    def test_contexte_nouvelle_vue_par_piece(self):
        ctx = bv.contexte_page(self.site, noms=["IFABEL03_D001_TmpAmb", "IFABEL03_D001_ModV2v"])
        self.assertEqual((ctx["form_display"], ctx["form_motifs"]), ("block", "IFABEL03_*"))
        self.assertIn('value="_ModV2v"', ctx["roles_html"])


if __name__ == "__main__":
    unittest.main()
