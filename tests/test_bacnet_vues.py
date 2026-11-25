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


class TestBacnetVues(unittest.TestCase):

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
        self.assertEqual(cles, [(5001, "analog-input:1"), (5002, "analog-input:47"),
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
                         [("ZonCom1", "defaut_capteur", "inf"), ("D005", "hors_plage", "13.35"),
                          ("D112", "hors_plage", "0")])
        self.assertEqual(res["resume"], {"sans_reponse": 0, "defaut_capteur": 1, "hors_plage": 2, "ok": 0})
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


if __name__ == "__main__":
    unittest.main()
