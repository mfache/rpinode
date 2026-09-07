import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from services.presence import label_current_location
from services.tracker import check_and_update_site


SCHEMA_MINIMAL = """
CREATE TABLE sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    external_id TEXT UNIQUE,
    is_provisional BOOLEAN DEFAULT 0,
    is_dirty BOOLEAN DEFAULT 1
);
CREATE TABLE antennas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mcc TEXT,
    mnc TEXT,
    enodeb TEXT,
    lac_tac TEXT,
    cid TEXT,
    lat REAL,
    lon REAL,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(mcc, mnc, enodeb)
);
CREATE TABLE site_antennas (
    site_id INTEGER,
    antenna_id INTEGER,
    linked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (site_id, antenna_id)
);
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL UNIQUE
);
CREATE TABLE node_presence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL,
    site_id INTEGER NOT NULL,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_current BOOLEAN DEFAULT 1
);
"""


def _initialiser_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_MINIMAL)
        conn.commit()
    finally:
        conn.close()


def _fabrique_get_db_connection(db_path: Path):
    @contextmanager
    def _get_db_connection():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    return _get_db_connection


class TestPresenceExternalId(unittest.TestCase):
    @patch("services.presence.socket.gethostname", return_value="rpi-test")
    @patch("services.presence.get_gsm_info")
    def test_un_nom_existant_ne_perd_pas_son_external_id(self, mock_gsm, _mock_hostname):
        """Empêche un réveil sur une autre antenne d'écraser l'ID distant déjà acquis."""
        mock_gsm.return_value = {
            "mcc": "206",
            "mnc": "01",
            "enodeb": "401828",
            "cid": "0621A471",
            "lac": "0000",
            "tac": "00084D",
            "gps": None,
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "app.db"
            _initialiser_db(db_path)
            fake_get_db_connection = _fabrique_get_db_connection(db_path)

            with patch("services.presence.get_db_connection", fake_get_db_connection):
                self.assertTrue(label_current_location("H66", is_provisional=False, external_id="9"))
                self.assertTrue(label_current_location("H66", is_provisional=False, external_id="10"))

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT external_id FROM sites WHERE name = ?", ("H66",)).fetchone()
            finally:
                conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["external_id"], "9")


class TestTrackerHints(unittest.TestCase):
    def _with_db(self):
        tmp_dir = tempfile.TemporaryDirectory()
        db_path = Path(tmp_dir.name) / "app.db"
        _initialiser_db(db_path)
        return tmp_dir, db_path, _fabrique_get_db_connection(db_path)

    @patch("services.tracker.label_current_location", return_value=False)
    @patch("services.tracker.apply_site_network_profiles")
    @patch("services.tracker.is_current_site_provisional", return_value=False)
    @patch("services.tracker.get_current_site_name", return_value="H66")
    @patch("services.tracker.get_gsm_info", return_value={"mcc": "206", "mnc": "01", "enodeb": "401828"})
    def test_un_deplacement_sur_antenne_inconnue_n_envoie_pas_l_ancien_chantier_comme_hint(
        self,
        mock_gsm,
        _mock_current_site,
        _mock_current_is_prov,
        _mock_apply_profiles,
        _mock_label,
    ):
        """Empêche la création distante d'un faux chantier H66-2 après déplacement."""
        tmp_dir, _db_path, fake_get_db_connection = self._with_db()
        try:
            with patch("services.tracker.get_db_connection", fake_get_db_connection), \
                 patch("services.tracker.fleet.is_registered", return_value=True), \
                 patch("services.tracker.fleet.sync_location", return_value=None) as mock_sync:
                check_and_update_site()
        finally:
            tmp_dir.cleanup()

        mock_sync.assert_called_once_with(mock_gsm.return_value, site_hint_name=None)

    @patch("services.tracker.label_current_location", return_value=True)
    @patch("services.tracker.apply_site_network_profiles")
    @patch("services.tracker.is_current_site_provisional", return_value=False)
    @patch("services.tracker.get_current_site_name", return_value="H66")
    @patch("services.tracker.get_gsm_info", return_value={"mcc": "206", "mnc": "01", "enodeb": "401828"})
    def test_une_antenne_connue_localement_prime_sur_le_dernier_chantier_memorise(
        self,
        mock_gsm,
        _mock_current_site,
        _mock_current_is_prov,
        mock_apply_profiles,
        mock_label,
    ):
        """Le hint doit suivre l'antenne courante, pas le chantier de la veille."""
        tmp_dir, db_path, fake_get_db_connection = self._with_db()
        try:
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "INSERT INTO sites (id, name, external_id, is_provisional, is_dirty) VALUES (?, ?, ?, 0, 0)",
                    (12, "VALTH", "42"),
                )
                conn.execute(
                    "INSERT INTO antennas (id, mcc, mnc, enodeb) VALUES (?, ?, ?, ?)",
                    (5, "206", "01", "401828"),
                )
                conn.execute("INSERT INTO site_antennas (site_id, antenna_id) VALUES (?, ?)", (12, 5))
                conn.commit()
            finally:
                conn.close()

            with patch("services.tracker.get_db_connection", fake_get_db_connection), \
                 patch("services.tracker.fleet.is_registered", return_value=True), \
                 patch("services.tracker.fleet.sync_location", return_value=None) as mock_sync:
                check_and_update_site()
        finally:
            tmp_dir.cleanup()

        mock_sync.assert_called_once_with(mock_gsm.return_value, site_hint_name="VALTH")
        mock_label.assert_called_once_with("VALTH", is_provisional=False)
        mock_apply_profiles.assert_called_once_with(12)

    @patch("services.tracker.label_current_location", return_value=True)
    @patch("services.tracker.apply_site_network_profiles")
    @patch("services.tracker.is_current_site_provisional", return_value=False)
    @patch("services.tracker.get_current_site_name", return_value="VALTH")
    @patch("services.tracker.get_gsm_info", return_value={"mcc": "206", "mnc": "01", "enodeb": "403869"})
    def test_un_auto_distant_ne_recycle_pas_le_dernier_chantier_si_un_site_local_fiable_existe(
        self,
        mock_gsm,
        _mock_current_site,
        _mock_current_is_prov,
        _mock_apply_profiles,
        mock_label,
    ):
        """Évite de rebaptiser une nouvelle antenne H66 avec le chantier VALTH de la veille."""
        tmp_dir, db_path, fake_get_db_connection = self._with_db()
        try:
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "INSERT INTO sites (id, name, external_id, is_provisional, is_dirty) VALUES (?, ?, ?, 0, 0)",
                    (3, "H66", "9"),
                )
                conn.execute(
                    "INSERT INTO antennas (id, mcc, mnc, enodeb) VALUES (?, ?, ?, ?)",
                    (7, "206", "01", "403869"),
                )
                conn.execute("INSERT INTO site_antennas (site_id, antenna_id) VALUES (?, ?)", (3, 7))
                conn.commit()
            finally:
                conn.close()

            with patch("services.tracker.get_db_connection", fake_get_db_connection), \
                 patch("services.tracker.fleet.is_registered", return_value=True), \
                 patch("services.tracker.fleet.sync_location", return_value={"chantier": {"id": 10, "ref": "AUTO-403869"}}):
                check_and_update_site()
        finally:
            tmp_dir.cleanup()

        mock_label.assert_called_once_with("H66", is_provisional=False, external_id="10")

    @patch("services.tracker.label_current_location", return_value=True)
    @patch("services.tracker.apply_site_network_profiles")
    @patch("services.tracker.is_current_site_provisional", return_value=False)
    @patch("services.tracker.get_current_site_name", return_value="VALTH")
    @patch("services.tracker.get_gsm_info", return_value={"mcc": "206", "mnc": "01", "enodeb": "401828"})
    def test_un_auto_distant_sur_antenne_inconnue_devient_un_site_provisoire_et_non_le_site_precedent(
        self,
        mock_gsm,
        _mock_current_site,
        _mock_current_is_prov,
        _mock_apply_profiles,
        mock_label,
    ):
        """Empêche un réveil déplacé de conserver VALTH quand l'antenne courante est inconnue."""
        tmp_dir, _db_path, fake_get_db_connection = self._with_db()
        try:
            with patch("services.tracker.get_db_connection", fake_get_db_connection), \
                 patch("services.tracker.fleet.is_registered", return_value=True), \
                 patch("services.tracker.fleet.sync_location", return_value={"chantier": {"id": 21, "ref": "AUTO-401828"}}):
                check_and_update_site()
        finally:
            tmp_dir.cleanup()

        mock_label.assert_called_once_with("AUTO-401828", is_provisional=True, external_id="21")


if __name__ == "__main__":
    unittest.main()
