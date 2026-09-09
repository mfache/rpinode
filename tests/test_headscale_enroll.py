import unittest
from unittest.mock import patch, MagicMock, mock_open

from src.services import headscale_enroll as he


class TestDocsadminAccount(unittest.TestCase):
    @patch("src.services.headscale_enroll.subprocess.run")
    def test_account_creation_skipped_if_already_present(self, mock_run):
        """Si `docsadmin` existe déjà, on ne relance jamais useradd/passwd."""
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(he._ensure_docsadmin_account())
        mock_run.assert_called_once_with(
            ["id", he.DOCSADMIN_USER], capture_output=True, timeout=5
        )

    @patch("src.services.headscale_enroll.subprocess.run")
    def test_account_created_and_locked_when_missing(self, mock_run):
        """Compte absent : useradd puis verrouillage du mot de passe."""
        id_missing = MagicMock(returncode=1)
        useradd_ok = MagicMock(returncode=0, stderr="")
        passwd_lock_ok = MagicMock(returncode=0)
        mock_run.side_effect = [id_missing, useradd_ok, passwd_lock_ok]

        self.assertTrue(he._ensure_docsadmin_account())

        useradd_call = mock_run.call_args_list[1]
        self.assertIn("useradd", useradd_call.args[0])
        self.assertIn(he.DOCSADMIN_USER, useradd_call.args[0])
        lock_call = mock_run.call_args_list[2]
        self.assertIn("passwd", lock_call.args[0])
        self.assertIn("-l", lock_call.args[0])

    @patch("src.services.headscale_enroll.subprocess.run")
    def test_account_creation_failure_is_reported_not_raised(self, mock_run):
        id_missing = MagicMock(returncode=1)
        useradd_fail = MagicMock(returncode=1, stderr="boom")
        mock_run.side_effect = [id_missing, useradd_fail]

        self.assertFalse(he._ensure_docsadmin_account())


class TestDocsadminSudoers(unittest.TestCase):
    def test_noop_when_content_already_up_to_date(self):
        """Aucun appel systeme si le fichier en place est deja identique."""
        with patch("builtins.open", mock_open(read_data=he.DOCSADMIN_SUDOERS_CONTENT)):
            with patch("src.services.headscale_enroll.subprocess.run") as mock_run:
                self.assertTrue(he._ensure_docsadmin_sudoers())
                mock_run.assert_not_called()

    @patch("src.services.headscale_enroll.subprocess.run")
    def test_installs_validated_policy_when_missing(self, mock_run):
        """Fichier absent : la policy generee est validee (visudo -c) puis
        installee avec les bonnes permissions."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # visudo -c
            MagicMock(returncode=0),  # install
        ]
        with patch("builtins.open", side_effect=OSError("absent")):
            self.assertTrue(he._ensure_docsadmin_sudoers())

        check_call, install_call = mock_run.call_args_list
        self.assertIn("visudo", check_call.args[0])
        self.assertIn("install", install_call.args[0])
        self.assertIn("440", install_call.args[0])

    @patch("src.services.headscale_enroll.subprocess.run")
    def test_invalid_policy_is_never_installed(self, mock_run):
        """Une policy jugee invalide par visudo ne doit jamais etre copiee
        vers /etc/sudoers.d/docsadmin."""
        mock_run.return_value = MagicMock(returncode=1, stderr="syntax error")
        with patch("builtins.open", side_effect=OSError("absent")):
            self.assertFalse(he._ensure_docsadmin_sudoers())
        # Un seul appel : la validation. Jamais d'installation.
        self.assertEqual(mock_run.call_count, 1)
        self.assertIn("visudo", mock_run.call_args.args[0])


class TestTailscaleSshEnabled(unittest.TestCase):
    @patch("src.services.headscale_enroll.subprocess.run")
    @patch("src.services.headscale_enroll._tailscale_json")
    def test_noop_when_already_enabled(self, mock_prefs, mock_run):
        mock_prefs.return_value = {"RunSSH": True}
        self.assertTrue(he._ensure_tailscale_ssh_enabled())
        mock_run.assert_not_called()

    @patch("src.services.headscale_enroll.subprocess.run")
    @patch("src.services.headscale_enroll._tailscale_json")
    def test_enables_ssh_with_accept_risk_flag(self, mock_prefs, mock_run):
        mock_prefs.return_value = {"RunSSH": False}
        mock_run.return_value = MagicMock(returncode=0)

        self.assertTrue(he._ensure_tailscale_ssh_enabled())

        args = mock_run.call_args.args[0]
        self.assertIn("--ssh", args)
        self.assertIn("--accept-risk=lose-ssh", args)


class TestEnsureDocsAdminAccess(unittest.TestCase):
    @patch("src.services.headscale_enroll._ensure_tailscale_ssh_enabled")
    @patch("src.services.headscale_enroll._ensure_docsadmin_sudoers")
    @patch("src.services.headscale_enroll._ensure_docsadmin_account")
    def test_runs_all_three_steps_even_if_one_fails(self, mock_account, mock_sudoers, mock_ssh):
        """Chaque etape doit s'executer independamment des autres (best
        effort), pour ne pas bloquer l'activation SSH si, par exemple, le
        depot du sudoers echoue."""
        mock_account.return_value = True
        mock_sudoers.return_value = False
        mock_ssh.return_value = True

        result = he.ensure_docs_admin_access()

        mock_account.assert_called_once()
        mock_sudoers.assert_called_once()
        mock_ssh.assert_called_once()
        self.assertFalse(result)


class TestEnsureHeadscaleEnrolledCallsAdminAccess(unittest.TestCase):
    @patch("src.services.headscale_enroll.ensure_docs_admin_access")
    @patch("src.services.headscale_enroll.is_headscale_active")
    def test_admin_access_ensured_when_already_active(self, mock_active, mock_admin):
        """Meme si l'appareil est deja rattache a Headscale, on verifie/pose
        quand meme le compte docsadmin et l'activation SSH (idempotent)."""
        mock_active.return_value = True

        self.assertTrue(he.ensure_headscale_enrolled())

        mock_admin.assert_called_once()


if __name__ == "__main__":
    unittest.main()
