"""Tests for tm_sync.py — git-based journal sync."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import src.tm_sync as _tm
from src.tm_sync import (
    _sanitize_error,
    _is_git_repo,
    is_configured,
    get_sync_info,
    init_sync,
    shutdown,
    sync_pull,
    sync_push_async,
    sync_push_blocking,
    sync_status,
)


def _reset():
    _tm._sync_config = None
    _tm._journals_dir = None


class TestSanitizeError(unittest.TestCase):
    def test_strips_oauth_token(self):
        msg = "error: https://oauth2:ghp_abc123@github.com/user/repo.git"
        result = _sanitize_error(msg)
        self.assertNotIn("oauth2:", result)
        self.assertNotIn("ghp_abc123", result)
        self.assertIn("https://github.com/user/repo.git", result)

    def test_strips_https_token(self):
        msg = "fatal: https://token@github.com/user/repo.git"
        result = _sanitize_error(msg)
        self.assertEqual(result, "fatal: https://github.com/user/repo.git")

    def test_no_token_unchanged(self):
        msg = "fatal: not a git repository"
        self.assertEqual(_sanitize_error(msg), msg)

    def test_empty_string(self):
        self.assertEqual(_sanitize_error(""), "")


class TestIsGitRepoEarly(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_git_dir(self):
        self.assertFalse(_is_git_repo(self.path))

    def test_with_git_dir(self):
        (self.path / ".git").mkdir()
        self.assertTrue(_is_git_repo(self.path))

    def test_git_is_file_not_dir(self):
        (self.path / ".git").write_text("gitdir: ../other/.git")
        self.assertFalse(_is_git_repo(self.path))


class TestInitSync(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.jdir = Path(self.tmpdir.name)
        self.pdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_disabled_sync_returns_false(self):
        self.assertFalse(init_sync(self.jdir, {}, self.pdir))

    def test_no_sync_config_returns_false(self):
        self.assertFalse(init_sync(self.jdir, {"sync": {"enabled": False}}, self.pdir))

    def test_no_remote_returns_false(self):
        self.assertFalse(init_sync(self.jdir, {"sync": {"enabled": True, "remote": ""}}, self.pdir))

    @patch("src.tm_sync._is_git_repo", return_value=True)
    def test_already_git_repo(self, mock_is_git):
        result = init_sync(self.jdir, {"sync": {"enabled": True, "remote": "git@github.com:user/repo.git"}}, self.pdir)
        self.assertTrue(result)
        mock_is_git.assert_called_once_with(self.jdir)

    @patch("src.tm_sync._is_git_repo", return_value=False)
    @patch("src.tm_sync._git_init")
    def test_init_creates_repo(self, mock_git_init, mock_is_git):
        result = init_sync(
            self.jdir,
            {"sync": {"enabled": True, "remote": "git@github.com:user/repo.git", "branch": "main"}},
            self.pdir,
        )
        self.assertTrue(result)
        mock_git_init.assert_called_once_with(self.jdir, "git@github.com:user/repo.git", "main")


class TestIsConfigured(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.jdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_not_configured_by_default(self):
        self.assertFalse(is_configured())

    @patch("src.tm_sync._is_git_repo", return_value=True)
    def test_configured_after_init(self, mock_is_git):
        init_sync(self.jdir, {"sync": {"enabled": True, "remote": "git@github.com:user/r.git"}}, Path(self.tmpdir.name))
        self.assertTrue(is_configured())

    def test_disabled_not_configured(self):
        init_sync(self.jdir, {}, Path(self.tmpdir.name))
        self.assertFalse(is_configured())


class TestSyncStatus(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.jdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_not_configured(self):
        self.assertEqual(sync_status(), "Sync: not configured")

    @patch("src.tm_sync._is_git_repo", return_value=True)
    @patch("src.tm_sync._run_git", return_value="")
    def test_configured_up_to_date(self, mock_git, mock_is_git):
        init_sync(self.jdir, {"sync": {"enabled": True, "remote": "git@github.com:u/r.git", "branch": "main"}}, Path(self.tmpdir.name))
        self.assertIn("up to date", sync_status())

    @patch("src.tm_sync._is_git_repo", return_value=True)
    @patch("src.tm_sync._run_git", return_value=" M file.txt\n")
    def test_configured_pending(self, mock_git, mock_is_git):
        init_sync(self.jdir, {"sync": {"enabled": True, "remote": "git@github.com:u/r.git", "branch": "main"}}, Path(self.tmpdir.name))
        self.assertIn("pending changes", sync_status())

    @patch("src.tm_sync._is_git_repo", return_value=True)
    @patch("src.tm_sync._run_git", return_value=None)
    def test_configured_git_error(self, mock_git, mock_is_git):
        init_sync(self.jdir, {"sync": {"enabled": True, "remote": "git@github.com:u/r.git", "branch": "main"}}, Path(self.tmpdir.name))
        self.assertIn("git error", sync_status())


class TestGetSyncInfo(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.jdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_not_configured(self):
        info = get_sync_info()
        self.assertFalse(info["configured"])
        self.assertEqual(info["status"], "not_configured")
        self.assertEqual(info["history"], [])

    @patch("src.tm_sync._is_git_repo", return_value=True)
    @patch("src.tm_sync._run_git", return_value="")
    def test_configured_up_to_date(self, mock_git, mock_is_git):
        init_sync(self.jdir, {"sync": {"enabled": True, "remote": "git@github.com:user/repo.git", "branch": "main"}}, Path(self.tmpdir.name))
        info = get_sync_info()
        self.assertTrue(info["configured"])
        self.assertEqual(info["status"], "up_to_date")
        self.assertEqual(info["pending_files"], 0)

    @patch("src.tm_sync._is_git_repo", return_value=True)
    @patch("src.tm_sync._run_git", return_value=" M f1.txt\n M f2.txt\n")
    def test_configured_pending_files(self, mock_git, mock_is_git):
        init_sync(self.jdir, {"sync": {"enabled": True, "remote": "git@github.com:user/repo.git", "branch": "main"}}, Path(self.tmpdir.name))
        info = get_sync_info()
        self.assertEqual(info["status"], "pending")
        self.assertEqual(info["pending_files"], 2)

    @patch("src.tm_sync._is_git_repo", return_value=True)
    @patch("src.tm_sync._run_git", return_value=None)
    def test_configured_offline(self, mock_git, mock_is_git):
        init_sync(self.jdir, {"sync": {"enabled": True, "remote": "git@github.com:user/repo.git", "branch": "main"}}, Path(self.tmpdir.name))
        info = get_sync_info()
        self.assertEqual(info["status"], "offline")

    def test_remote_url_sanitized(self):
        with patch("src.tm_sync._is_git_repo", return_value=True), patch("src.tm_sync._run_git", return_value=""):
            init_sync(self.jdir, {"sync": {"enabled": True, "remote": "https://oauth2:token@github.com/user/repo.git", "branch": "main"}}, Path(self.tmpdir.name))
            info = get_sync_info()
            self.assertNotIn("token", info["remote"])
            self.assertNotIn("oauth2:", info["remote"])


class TestShutdown(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_shutdown_noop_when_not_active(self):
        shutdown()

    @patch("src.tm_sync._do_push")
    @patch("src.tm_sync._is_git_repo", return_value=True)
    @patch("src.tm_sync._run_git", return_value="")
    def test_shutdown_triggers_push_when_active(self, mock_git, mock_is_git, mock_push):
        jdir = Path(self.tmpdir.name)
        init_sync(jdir, {"sync": {"enabled": True, "remote": "git@github.com:u/r.git", "branch": "main"}}, Path(self.tmpdir.name))
        shutdown()
        mock_push.assert_called_once()


class TestSyncPull(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_not_active_returns_false(self):
        self.assertFalse(sync_pull())

    @patch("src.tm_sync._is_git_repo", return_value=True)
    @patch("src.tm_sync._run_git")
    def test_pull_no_remote(self, mock_git, mock_is_git):
        mock_git.return_value = None
        jdir = Path(self.tmpdir.name)
        init_sync(jdir, {"sync": {"enabled": True, "remote": "git@github.com:u/r.git", "branch": "main"}}, Path(self.tmpdir.name))
        self.assertFalse(sync_pull(interactive=False))


class TestSyncPushAsync(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("src.tm_sync.threading.Timer")
    @patch("src.tm_sync._is_git_repo", return_value=True)
    @patch("src.tm_sync._run_git", return_value="")
    def test_push_async_schedules_timer(self, mock_git, mock_is_git, mock_timer):
        jdir = Path(self.tmpdir.name)
        init_sync(jdir, {"sync": {"enabled": True, "remote": "git@github.com:u/r.git", "branch": "main"}}, Path(self.tmpdir.name))
        sync_push_async()
        mock_timer.assert_called_once()


class TestSyncPushBlocking(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_not_active_returns_false(self):
        self.assertFalse(sync_push_blocking())


class TestIsGitRepo(unittest.TestCase):
    def setUp(self):
        _reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_git_dir(self):
        self.assertFalse(_is_git_repo(self.path))

    def test_with_git_dir(self):
        (self.path / ".git").mkdir()
        self.assertTrue(_is_git_repo(self.path))


if __name__ == "__main__":
    unittest.main()
