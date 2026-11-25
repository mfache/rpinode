"""Forçages BACnet (services/bacnet_forcages.py) depuis une vue : cibles,
enregistrement des seuls succès, remise en automatique, échéances.
Les écritures BACnet sont simulées (_ecrire)."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import core.paths as paths
from core.database import get_db_connection, init_db
from services.bacnet_catalog import upsert_device_points
from services import bacnet_forcages as bf
from services import bacnet_vues as bv

REGEX = r"^IFABEL03_(?P<piece>[^_]+)_(?P<role>.+)$"


class TestForcages(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = paths.DATABASE_FILE
        paths.DATABASE_FILE = Path(self.temp_dir.name) / "test_forcages.db"
        init_db()
        with get_db_connection() as conn:
            conn.execute("INSERT INTO sites (name, external_id) VALUES ('F', '1')")
            conn.commit()
            self.site = conn.execute("SELECT id FROM sites WHERE name='F'").fetchone()["id"]
        upsert_device_points(self.site, "192.168.1.202", 5002, [
            {"object_id": "analog-output:13", "name": "IFABEL03_D001_ModV2v"},
            {"object_id": "analog-output:15", "name": "IFABEL03_D002_ModV2v"},
            {"object_id": "analog-output:14", "name": "IFABEL03_D001_ModVitVen"},
            {"object_id": "analog-input:1", "name": "IFABEL03_D001_TmpAmb"},
            {"object_id": "analog-output:99", "name": "AUTRE_ModV2v"},
        ])
        vid = bv.enregistrer_vue(self.site, "Pièces", "IFABEL03_*", regex=REGEX,
                                 roles=[{"role": "ModV2v", "libelle": "Vanne"}, {"role": "ModVitVen"}])
        self.vue = bv.lire_vue(vid, self.site)
        self.ecritures = []

    def tearDown(self):
        paths.DATABASE_FILE = self.original_db
        self.temp_dir.cleanup()

    def _ecrire(self, refus=()):
        def faux(points, valeur, timeout=None):
            self.ecritures.append((sorted(p["object_id"] for p in points), valeur))
            return {(p["device_instance"], p["object_id"]):
                    ({"ok": False, "error": "BACnet Error: write-access-denied"} if p["object_id"] in refus
                     else {"ok": True, "error": None}) for p in points}
        return patch("services.bacnet_forcages._ecrire", side_effect=faux)

    def test_cibles(self):
        vannes = bv.cibles_forcage(self.vue, "ModV2v")
        self.assertEqual(sorted(p["object_id"] for p in vannes), ["analog-output:13", "analog-output:15"])
        self.assertEqual(len(bv.cibles_forcage(self.vue)), 3)  # les deux rôles de la vue
        # Vue par point avec regex : seuls les noms reconnus (pas AUTRE_ModV2v).
        vid = bv.enregistrer_vue(self.site, "V", "*_ModV2v", regex=REGEX)
        self.assertEqual(len(bv.cibles_forcage(bv.lire_vue(vid, self.site))), 2)

    def test_forcer_puis_remettre_en_automatique(self):
        vannes = bv.cibles_forcage(self.vue, "ModV2v")
        with self._ecrire(refus={"analog-output:15"}):
            res = bf.forcer(self.site, vannes, "100", "30")
        self.assertEqual(res["forces"], 1)
        self.assertEqual(res["erreurs"], [("IFABEL03_D002_ModV2v", "BACnet Error: write-access-denied")])
        actifs = bf.forcages_actifs(self.site)
        self.assertEqual(list(actifs), [(5002, "analog-output:13")])  # seul le succès est retenu
        f = actifs[(5002, "analog-output:13")]
        self.assertEqual((f["value"], f["priority"]), ("100", 8))
        self.assertAlmostEqual(f["expires_at"] - f["started_at"], 1800)
        self.assertIn("1 point(s) forcé(s)", bf.rendre_bilan(res, False))

        # Remettre en automatique : Null (None) écrit sur les seuls points forcés.
        with self._ecrire():
            res = bf.relacher(self.site, vannes)
        self.assertEqual(self.ecritures[-1], (["analog-output:13"], None))
        self.assertEqual(res["relaches"], 1)
        self.assertEqual(bf.forcages_actifs(self.site), {})

    def test_sans_limite_et_valeur_obligatoire(self):
        with self._ecrire():
            bf.forcer(self.site, bv.cibles_forcage(self.vue, "ModVitVen"), "50", "")
            with self.assertRaises(ValueError):
                bf.forcer(self.site, bv.cibles_forcage(self.vue), " ", None)
        self.assertIsNone(bf.forcages_actifs(self.site)[(5002, "analog-output:14")]["expires_at"])

    def test_echeance(self):
        with self._ecrire():
            bf.forcer(self.site, bv.cibles_forcage(self.vue, "ModV2v"), "100", 15)
            bf.forcer(self.site, bv.cibles_forcage(self.vue, "ModVitVen"), "100", None)
        with get_db_connection() as conn:
            conn.execute("UPDATE bacnet_forcings SET expires_at = ? WHERE expires_at IS NOT NULL",
                         (int(time.time()) - 1,))
            conn.commit()
        with self._ecrire():
            bf.relacher_expires()
        self.assertEqual(list(bf.forcages_actifs(self.site)), [(5002, "analog-output:14")])

    def test_relachement_refuse_garde_le_forcage(self):
        vannes = bv.cibles_forcage(self.vue, "ModV2v")
        with self._ecrire():
            bf.forcer(self.site, vannes, "100")
        with self._ecrire(refus={"analog-output:13"}):
            res = bf.relacher(self.site, vannes)
        self.assertEqual(res["relaches"], 1)
        self.assertEqual(list(bf.forcages_actifs(self.site)), [(5002, "analog-output:13")])

    def test_marquage_dans_la_vue(self):
        with self._ecrire():
            bf.forcer(self.site, bv.cibles_forcage(self.vue, "ModV2v"), "100", 60)

        def lecture(points, timeout):
            return {p["key"]: {"value": "100", "error": None} for p in points}

        with patch("services.bacnet_mgr.read_bacnet_points_live_raw", side_effect=lecture):
            res = bv.lire_vue_en_direct(self.vue)
        self.assertEqual(res["forces"], 2)
        self.assertIn("2 point(s) forcé(s)", bv.rendre_resume(res))
        self.assertIn("Forcé à 100 (priorité 8)", bv.rendre_lignes(res, self.vue))
        ctx = bv.contexte_forcage(self.vue)
        self.assertIn("Vanne (2 point(s))", ctx["forcage_cibles_html"])


if __name__ == "__main__":
    unittest.main()
