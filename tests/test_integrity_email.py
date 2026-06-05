"""Comprehensive tests for tm_integrity.py and tm_email.py."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tm_integrity import check_and_fix_journal, _is_valid_date, _try_fix_date_header
from tm_email import _to_bool, EmailResult, EmailConfig, load_email_config, _first_existing_config


# ═══════════════════════════════════════════════════════════════════════════════
# tm_integrity: check_and_fix_journal
# ═══════════════════════════════════════════════════════════════════════════════


class TestCheckAndFixJournalMissingFile(unittest.TestCase):
    """Test case 1: Missing file."""

    def test_missing_file_returns_issue(self):
        issues, fixes = check_and_fix_journal("/nonexistent/path/journal.txt")
        self.assertEqual(fixes, 0)
        self.assertEqual(len(issues), 1)
        self.assertIn("Journal file not found", issues[0])


class TestCheckAndFixJournalValidJournal(unittest.TestCase):
    """Test case 2: Valid journal with no issues."""

    def test_valid_journal_no_issues(self):
        content = (
            "## 25/12/2024\n"
            "- Buy groceries -- DONE\n"
            ": Bought milk and bread\n"
            "+ Sub item -- DONE\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertEqual(issues, [])
            self.assertEqual(fixes, 0)
        finally:
            os.unlink(path)


class TestCheckAndFixJournalConsecutiveBlanks(unittest.TestCase):
    """Test case 3: Consecutive blank lines."""

    def test_consecutive_blanks_detected(self):
        content = "## 01/01/2024\n- Task -- DONE\n\n\n- Another -- DONE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path, auto_fix=False)
            self.assertTrue(any("consecutive blank line" in i for i in issues))
            self.assertEqual(fixes, 0)
        finally:
            os.unlink(path)

    def test_consecutive_blanks_auto_fixed(self):
        content = "## 01/01/2024\n- Task -- DONE\n\n\n- Another -- DONE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path, auto_fix=True)
            self.assertGreater(fixes, 0)
            result = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("\n\n\n", result)
        finally:
            os.unlink(path)


class TestCheckAndFixJournalTrailingWhitespace(unittest.TestCase):
    """Test case 4: Trailing whitespace on lines."""

    def test_trailing_whitespace_detected(self):
        content = "## 01/01/2024\n- Task with trailing   \n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path, auto_fix=False)
            self.assertTrue(any("trailing whitespace" in i for i in issues))
        finally:
            os.unlink(path)

    def test_trailing_whitespace_auto_fixed(self):
        content = "## 01/01/2024\n- Task with trailing   \n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path, auto_fix=True)
            self.assertGreater(fixes, 0)
            result = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("trailing   ", result)
        finally:
            os.unlink(path)


class TestCheckAndFixJournalInvalidDateHeader(unittest.TestCase):
    """Test case 5: Invalid date header."""

    def test_invalid_date_header_reported(self):
        content = "## not-a-date\n- Task -- DONE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("invalid date header" in i or "malformed date header" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalMalformedDate(unittest.TestCase):
    """Test case 6: Malformed date like 32/13/2024."""

    def test_malformed_date_reported(self):
        content = "## 32/13/2024\n- Task -- DONE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("invalid date" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalSalvageableDate(unittest.TestCase):
    """Test case 7: Salvageable date can be fixed."""

    def test_salvageable_date_fixed(self):
        content = "## 5-6-2024\n- Task -- DONE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path, auto_fix=True)
            self.assertGreater(fixes, 0)
            result = Path(path).read_text(encoding="utf-8")
            self.assertIn("## 05/06/2024", result)
        finally:
            os.unlink(path)

    def test_salvageable_date_reported_without_fix(self):
        content = "## 5-6-2024\n- Task -- DONE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path, auto_fix=False)
            self.assertTrue(len(issues) > 0)
            self.assertEqual(fixes, 0)
        finally:
            os.unlink(path)


class TestCheckAndFixJournalOrphanNote(unittest.TestCase):
    """Test case 8: Note without parent task."""

    def test_orphan_note_reported(self):
        content = "## 01/01/2024\n: orphan note\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("note without parent task" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalOrphanSubtask(unittest.TestCase):
    """Test case 9: Subtask without parent task."""

    def test_orphan_subtask_reported(self):
        content = "## 01/01/2024\n+ orphan subtask\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("subtask without parent task" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalMetadataWithoutParent(unittest.TestCase):
    """Test case 10: Metadata without parent task."""

    def test_metadata_without_parent_reported(self):
        content = "## 01/01/2024\n-- key:value\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("metadata line without parent task" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalInvalidState(unittest.TestCase):
    """Test case 11: Invalid state on task line."""

    def test_invalid_state_reported(self):
        content = "## 01/01/2024\n- Task -- INVALIDSTATE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("invalid state" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalInvalidPriority(unittest.TestCase):
    """Test case 12: Invalid priority."""

    def test_invalid_priority_reported(self):
        content = "## 01/01/2024\n- Task -- DONE -- priority:SUPER\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("invalid priority" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalInvalidDueDate(unittest.TestCase):
    """Test case 13: Invalid due date."""

    def test_invalid_due_date_reported(self):
        content = "## 01/01/2024\n- Task -- DONE -- due:not-a-date\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("invalid due date" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalInvalidRecurrence(unittest.TestCase):
    """Test case 14: Invalid recurrence."""

    def test_invalid_recurrence_reported(self):
        content = "## 01/01/2024\n- Task -- DONE -- recur:hourly\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("invalid recurrence" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalAutoFixFalse(unittest.TestCase):
    """Test case 15: auto_fix=False always returns fixes=0."""

    def test_auto_fix_false_returns_zero_fixes(self):
        content = "## 01/01/2024\n- Task with trailing   \n\n\n- Another -- DONE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path, auto_fix=False)
            self.assertGreater(len(issues), 0)
            self.assertEqual(fixes, 0)
        finally:
            os.unlink(path)


class TestCheckAndFixJournalAutoFixTrue(unittest.TestCase):
    """Test case 16: auto_fix=True writes fixed content."""

    def test_auto_fix_true_writes_file(self):
        content = "## 01/01/2024\n- Task   \n\n\n- Another -- DONE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path, auto_fix=True)
            self.assertGreater(fixes, 0)
            result = Path(path).read_text(encoding="utf-8")
            # trailing whitespace removed
            self.assertNotIn("Task   ", result)
            # consecutive blanks removed
            self.assertNotIn("\n\n\n", result)
        finally:
            os.unlink(path)


class TestCheckAndFixJournalUnrecognizedLine(unittest.TestCase):
    """Test case 17: Unrecognized line outside task block."""

    def test_unrecognized_line_reported(self):
        content = "## 01/01/2024\nrandom garbage line\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("unrecognized line format" in i for i in issues))
        finally:
            os.unlink(path)


class TestCheckAndFixJournalLinesBeforeDateHeader(unittest.TestCase):
    """Test case 18: Lines before date header are allowed."""

    def test_lines_before_date_header_allowed(self):
        content = "# My Journal\nSome metadata\n\n## 01/01/2024\n- Task -- DONE\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertEqual(issues, [])
            self.assertEqual(fixes, 0)
        finally:
            os.unlink(path)


class TestCheckAndFixJournalEmptySubtask(unittest.TestCase):
    """Test case 19: Empty subtask reported."""

    def test_empty_subtask_reported(self):
        content = "## 01/01/2024\n- Parent task -- DONE\n+ \n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertTrue(any("empty subtask" in i for i in issues))
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════════
# tm_integrity: _is_valid_date
# ═══════════════════════════════════════════════════════════════════════════════


class TestIsValidDate(unittest.TestCase):
    """Test _is_valid_date helper."""

    def test_valid_date(self):
        self.assertTrue(_is_valid_date("25/12/2024"))

    def test_valid_leap_year(self):
        self.assertTrue(_is_valid_date("29/02/2024"))

    def test_invalid_day(self):
        self.assertFalse(_is_valid_date("30/02/2024"))

    def test_invalid_month(self):
        self.assertFalse(_is_valid_date("01/13/2024"))

    def test_invalid_format(self):
        self.assertFalse(_is_valid_date("2024-12-25"))

    def test_empty_string(self):
        self.assertFalse(_is_valid_date(""))

    def test_first_day_of_year(self):
        self.assertTrue(_is_valid_date("01/01/2024"))

    def test_last_day_of_year(self):
        self.assertTrue(_is_valid_date("31/12/2024"))


# ═══════════════════════════════════════════════════════════════════════════════
# tm_integrity: _try_fix_date_header
# ═══════════════════════════════════════════════════════════════════════════════


class TestTryFixDateHeader(unittest.TestCase):
    """Test _try_fix_date_header helper."""

    def test_fix_dash_separated(self):
        result = _try_fix_date_header("## 5-6-2024")
        self.assertEqual(result, "## 05/06/2024")

    def test_fix_dot_separated(self):
        result = _try_fix_date_header("## 5.6.2024")
        self.assertEqual(result, "## 05/06/2024")

    def test_garbage_returns_none(self):
        result = _try_fix_date_header("## garbage")
        self.assertIsNone(result)

    def test_invalid_date_returns_none(self):
        result = _try_fix_date_header("## 32-13-2024")
        self.assertIsNone(result)

    def test_already_correct_format(self):
        result = _try_fix_date_header("## 05/06/2024")
        self.assertEqual(result, "## 05/06/2024")

    def test_single_digit_day_and_month(self):
        result = _try_fix_date_header("## 1-2-2024")
        self.assertEqual(result, "## 01/02/2024")


# ═══════════════════════════════════════════════════════════════════════════════
# tm_email: _to_bool
# ═══════════════════════════════════════════════════════════════════════════════


class TestToBool(unittest.TestCase):
    """Test _to_bool conversion helper."""

    def test_none_returns_default_true(self):
        self.assertTrue(_to_bool(None, True))

    def test_none_returns_default_false(self):
        self.assertFalse(_to_bool(None, False))

    def test_bool_true_passthrough(self):
        self.assertTrue(_to_bool(True, False))

    def test_bool_false_passthrough(self):
        self.assertFalse(_to_bool(False, True))

    def test_string_true(self):
        self.assertTrue(_to_bool("true", False))

    def test_string_yes(self):
        self.assertTrue(_to_bool("yes", False))

    def test_string_1(self):
        self.assertTrue(_to_bool("1", False))

    def test_string_on(self):
        self.assertTrue(_to_bool("on", False))

    def test_string_y(self):
        self.assertTrue(_to_bool("y", False))

    def test_string_false(self):
        self.assertFalse(_to_bool("false", True))

    def test_string_no(self):
        self.assertFalse(_to_bool("no", True))

    def test_string_0(self):
        self.assertFalse(_to_bool("0", True))

    def test_string_off(self):
        self.assertFalse(_to_bool("off", True))

    def test_string_n(self):
        self.assertFalse(_to_bool("n", True))

    def test_unrecognized_returns_default(self):
        self.assertTrue(_to_bool("maybe", True))
        self.assertFalse(_to_bool("maybe", False))

    def test_int_1_true(self):
        self.assertTrue(_to_bool(1, False))

    def test_int_0_false(self):
        self.assertFalse(_to_bool(0, True))

    def test_case_insensitive_TRUE(self):
        self.assertTrue(_to_bool("TRUE", False))

    def test_case_insensitive_Yes(self):
        self.assertTrue(_to_bool("Yes", False))

    def test_case_insensitive_ON(self):
        self.assertTrue(_to_bool("ON", False))

    def test_case_insensitive_FALSE(self):
        self.assertFalse(_to_bool("FALSE", True))

    def test_case_insensitive_No(self):
        self.assertFalse(_to_bool("No", True))


# ═══════════════════════════════════════════════════════════════════════════════
# tm_email: EmailResult
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmailResult(unittest.TestCase):
    """Test EmailResult dataclass and success property."""

    def test_sent_is_success(self):
        r = EmailResult("sent", "OK")
        self.assertTrue(r.success)

    def test_draft_is_success(self):
        r = EmailResult("draft", "OK")
        self.assertTrue(r.success)

    def test_failed_is_not_success(self):
        r = EmailResult("failed", "err")
        self.assertFalse(r.success)

    def test_error_is_not_success(self):
        r = EmailResult("error", "err")
        self.assertFalse(r.success)

    def test_message_stored(self):
        r = EmailResult("sent", "All good")
        self.assertEqual(r.message, "All good")

    def test_status_stored(self):
        r = EmailResult("draft", "Opened")
        self.assertEqual(r.status, "draft")


# ═══════════════════════════════════════════════════════════════════════════════
# tm_email: EmailConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmailConfig(unittest.TestCase):
    """Test EmailConfig defaults and field access."""

    def test_default_smtp_port(self):
        cfg = EmailConfig()
        self.assertEqual(cfg.smtp_port, 587)

    def test_default_smtp_use_ssl(self):
        cfg = EmailConfig()
        self.assertFalse(cfg.smtp_use_ssl)

    def test_default_smtp_use_starttls(self):
        cfg = EmailConfig()
        self.assertTrue(cfg.smtp_use_starttls)

    def test_all_fields_accessible(self):
        cfg = EmailConfig(
            smtp_host="mail.example.com",
            smtp_port=465,
            smtp_user="user",
            smtp_password="pass",
            smtp_sender="sender@ex.com",
            smtp_use_ssl=True,
            smtp_use_starttls=False,
            default_recipient="rec@ex.com",
            subject_prefix="[Test]",
        )
        self.assertEqual(cfg.smtp_host, "mail.example.com")
        self.assertEqual(cfg.smtp_port, 465)
        self.assertEqual(cfg.smtp_user, "user")
        self.assertEqual(cfg.smtp_password, "pass")
        self.assertEqual(cfg.smtp_sender, "sender@ex.com")
        self.assertTrue(cfg.smtp_use_ssl)
        self.assertFalse(cfg.smtp_use_starttls)
        self.assertEqual(cfg.default_recipient, "rec@ex.com")
        self.assertEqual(cfg.subject_prefix, "[Test]")

    def test_default_recipient_none(self):
        cfg = EmailConfig()
        self.assertIsNone(cfg.default_recipient)

    def test_default_subject_prefix(self):
        cfg = EmailConfig()
        self.assertEqual(cfg.subject_prefix, "[TaskManager]")


# ═══════════════════════════════════════════════════════════════════════════════
# tm_email: load_email_config
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadEmailConfig(unittest.TestCase):
    """Test load_email_config with files and env vars."""

    def _write_config(self, data):
        """Write a JSON config to a temp file and return its Path."""
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(data, f)
        f.close()
        return Path(f.name)

    def test_load_from_config_file(self):
        config_data = {
            "smtp_host": "smtp.test.com",
            "smtp_port": 465,
            "smtp_user": "testuser",
            "smtp_password": "testpass",
            "smtp_sender": "test@test.com",
            "smtp_use_ssl": True,
            "smtp_use_starttls": False,
            "default_recipient": "r@test.com",
            "subject_prefix": "[Loaded]",
        }
        path = self._write_config(config_data)
        try:
            with patch.dict(os.environ, {}, clear=True):
                # Clear all TM_EMAIL env vars
                for key in list(os.environ.keys()):
                    if key.startswith("TM_EMAIL_"):
                        del os.environ[key]
                cfg = load_email_config(config_paths=[path])
            self.assertEqual(cfg.smtp_host, "smtp.test.com")
            self.assertEqual(cfg.smtp_port, 465)
            self.assertEqual(cfg.smtp_user, "testuser")
            self.assertEqual(cfg.smtp_password, "testpass")
            self.assertEqual(cfg.smtp_sender, "test@test.com")
            self.assertTrue(cfg.smtp_use_ssl)
            self.assertFalse(cfg.smtp_use_starttls)
            self.assertEqual(cfg.default_recipient, "r@test.com")
            self.assertEqual(cfg.subject_prefix, "[Loaded]")
        finally:
            os.unlink(path)

    def test_env_vars_override_file(self):
        config_data = {
            "smtp_host": "file-host.com",
            "smtp_port": 465,
        }
        path = self._write_config(config_data)
        env = {
            "TM_EMAIL_SMTP_HOST": "env-host.com",
            "TM_EMAIL_SMTP_PORT": "2525",
        }
        try:
            with patch.dict(os.environ, env, clear=False):
                cfg = load_email_config(config_paths=[path])
            self.assertEqual(cfg.smtp_host, "env-host.com")
            self.assertEqual(cfg.smtp_port, 2525)
        finally:
            os.unlink(path)

    def test_missing_config_file_uses_defaults(self):
        fake_path = Path("/nonexistent/config.json")
        with patch.dict(os.environ, {}, clear=False):
            # Remove any TM_EMAIL env vars
            clean_env = {k: v for k, v in os.environ.items() if not k.startswith("TM_EMAIL_")}
            with patch.dict(os.environ, clean_env, clear=True):
                cfg = load_email_config(config_paths=[fake_path])
        self.assertEqual(cfg.smtp_port, 587)
        self.assertIsNone(cfg.smtp_host)
        self.assertFalse(cfg.smtp_use_ssl)
        self.assertTrue(cfg.smtp_use_starttls)

    def test_invalid_port_string_defaults_to_587(self):
        env = {"TM_EMAIL_SMTP_PORT": "not_a_number"}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_email_config(config_paths=[Path("/nonexistent.json")])
        self.assertEqual(cfg.smtp_port, 587)

    def test_valid_port_int_parsed(self):
        env = {"TM_EMAIL_SMTP_PORT": "2525"}
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("TM_EMAIL_")}
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            cfg = load_email_config(config_paths=[Path("/nonexistent.json")])
        self.assertEqual(cfg.smtp_port, 2525)

    def test_env_ssl_override(self):
        env = {"TM_EMAIL_SMTP_SSL": "true"}
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("TM_EMAIL_")}
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            cfg = load_email_config(config_paths=[Path("/nonexistent.json")])
        self.assertTrue(cfg.smtp_use_ssl)

    def test_env_starttls_override(self):
        env = {"TM_EMAIL_SMTP_STARTTLS": "false"}
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("TM_EMAIL_")}
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            cfg = load_email_config(config_paths=[Path("/nonexistent.json")])
        self.assertFalse(cfg.smtp_use_starttls)


# ═══════════════════════════════════════════════════════════════════════════════
# tm_email: _first_existing_config
# ═══════════════════════════════════════════════════════════════════════════════


class TestFirstExistingConfig(unittest.TestCase):
    """Test _first_existing_config path resolution."""

    def test_returns_first_valid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump({"smtp_host": "first.com"}, f1)
            path1 = Path(f1.name)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump({"smtp_host": "second.com"}, f2)
            path2 = Path(f2.name)
        try:
            result = _first_existing_config([path1, path2])
            self.assertEqual(result["smtp_host"], "first.com")
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_skips_missing_returns_second(self):
        missing = Path("/nonexistent/path.json")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"smtp_host": "fallback.com"}, f)
            path = Path(f.name)
        try:
            result = _first_existing_config([missing, path])
            self.assertEqual(result["smtp_host"], "fallback.com")
        finally:
            os.unlink(path)

    def test_all_missing_returns_empty_dict(self):
        result = _first_existing_config([
            Path("/nonexistent/a.json"),
            Path("/nonexistent/b.json"),
        ])
        self.assertEqual(result, {})

    def test_invalid_json_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            f1.write("not valid json {{{")
            path_bad = Path(f1.name)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump({"smtp_host": "good.com"}, f2)
            path_good = Path(f2.name)
        try:
            result = _first_existing_config([path_bad, path_good])
            self.assertEqual(result["smtp_host"], "good.com")
        finally:
            os.unlink(path_bad)
            os.unlink(path_good)

    def test_empty_list_returns_empty_dict(self):
        result = _first_existing_config([])
        self.assertEqual(result, {})


# ═══════════════════════════════════════════════════════════════════════════════
# Additional edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegrityEdgeCases(unittest.TestCase):
    """Additional edge cases for integrity checker."""

    def test_multiple_issues_in_one_file(self):
        content = (
            "## 01/01/2024\n"
            ": orphan note\n"
            "+ orphan subtask\n"
            "-- orphan:meta\n"
            "random line\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertGreaterEqual(len(issues), 3)
        finally:
            os.unlink(path)

    def test_valid_task_with_subtasks_and_notes(self):
        content = (
            "## 15/06/2024\n"
            "- Main task -- DONE -- priority:HIGH -- due:20/06/2024 -- recur:weekly\n"
            ": A note on the task\n"
            "+ Subtask one -- DONE\n"
            "+ Subtask two -- BACKLOG\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertEqual(issues, [])
            self.assertEqual(fixes, 0)
        finally:
            os.unlink(path)

    def test_multiple_date_sections(self):
        content = (
            "## 01/01/2024\n"
            "- Task A -- DONE\n"
            "\n"
            "## 02/01/2024\n"
            "- Task B -- BACKLOG\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertEqual(issues, [])
        finally:
            os.unlink(path)

    def test_note_after_task_is_valid(self):
        content = "## 01/01/2024\n- A task -- DONE\n: A valid note\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            issues, _ = check_and_fix_journal(path)
            self.assertEqual(issues, [])
        finally:
            os.unlink(path)

    def test_empty_file_no_crash(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("")
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            self.assertEqual(fixes, 0)
        finally:
            os.unlink(path)

    def test_file_with_only_blank_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("\n\n\n")
            path = f.name
        try:
            issues, fixes = check_and_fix_journal(path)
            # Consecutive blanks reported
            self.assertTrue(any("consecutive blank" in i for i in issues))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
