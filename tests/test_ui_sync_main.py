"""Comprehensive tests for pure functions in tm_ui.py, tm_sync.py, and task_manager.py."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tm_ui import (
    get_state_color, _title_without_tags, _format_title_cell,
    _format_tags_suffix, _format_task_meta_suffix, _max_id_length,
    Colors, get_stats,
)
from tm_models import Task, Subtask
import tm_sync
from task_manager import normalize_journal_name, list_journals, load_cached_journal, save_cached_journal


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_ui.get_state_color
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetStateColor(unittest.TestCase):
    """Tests for get_state_color mapping."""

    def test_backlog(self):
        self.assertEqual(get_state_color("BACKLOG"), "\033[90m")

    def test_in_progress(self):
        self.assertEqual(get_state_color("IN PROGRESS"), "\033[33m")

    def test_waiting(self):
        self.assertEqual(get_state_color("WAITING"), "\033[35m")

    def test_testing(self):
        self.assertEqual(get_state_color("TESTING"), "\033[36m")

    def test_done(self):
        self.assertEqual(get_state_color("DONE"), "\033[32m")

    def test_cancelled(self):
        self.assertEqual(get_state_color("CANCELLED"), "\033[91m")

    def test_unknown_state_returns_reset(self):
        result = get_state_color("UNKNOWN")
        # Returns Colors.RESET which starts with \033[0m
        self.assertIn("\033[0m", result)

    def test_empty_string_returns_reset(self):
        result = get_state_color("")
        self.assertIn("\033[0m", result)

    def test_lowercase_not_matched(self):
        result = get_state_color("backlog")
        self.assertIn("\033[0m", result)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_ui._title_without_tags
# ═══════════════════════════════════════════════════════════════════════════════

class TestTitleWithoutTags(unittest.TestCase):
    """Tests for _title_without_tags stripping hashtag tokens."""

    def test_single_tag_at_end(self):
        self.assertEqual(_title_without_tags("Buy milk #shopping"), "Buy milk")

    def test_no_tags(self):
        self.assertEqual(_title_without_tags("No tags"), "No tags")

    def test_only_tag(self):
        self.assertEqual(_title_without_tags("#only-tag"), "")

    def test_multiple_tags(self):
        self.assertEqual(_title_without_tags("Task #backend #api"), "Task")

    def test_tag_in_middle(self):
        self.assertEqual(_title_without_tags("Start #mid end"), "Start end")

    def test_tag_with_numbers(self):
        self.assertEqual(_title_without_tags("Fix #bug123"), "Fix")

    def test_tag_with_underscore(self):
        self.assertEqual(_title_without_tags("Deploy #prod_server"), "Deploy")

    def test_tag_with_hyphen(self):
        self.assertEqual(_title_without_tags("Review #code-review"), "Review")

    def test_hash_inside_word_not_stripped(self):
        # Lookbehind: preceded by word char -> not a tag
        self.assertEqual(_title_without_tags("C#sharp code"), "C#sharp code")

    def test_empty_string(self):
        self.assertEqual(_title_without_tags(""), "")

    def test_multiple_spaces_collapsed(self):
        self.assertEqual(_title_without_tags("A  #tag  B"), "A B")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_ui._format_title_cell
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatTitleCell(unittest.TestCase):
    """Tests for _format_title_cell truncation logic."""

    def test_short_text_fits(self):
        self.assertEqual(_format_title_cell("Hello World", 20), "Hello World")

    def test_exact_width_fits(self):
        self.assertEqual(_format_title_cell("12345", 5), "12345")

    def test_truncated_with_tilde(self):
        result = _format_title_cell("Very long title here", 10)
        self.assertEqual(result, "Very long~")
        self.assertEqual(len(result), 10)

    def test_width_one(self):
        result = _format_title_cell("Hello", 1)
        self.assertEqual(result, "~")

    def test_width_two(self):
        result = _format_title_cell("Hello", 2)
        self.assertEqual(result, "H~")

    def test_empty_text(self):
        self.assertEqual(_format_title_cell("", 10), "")

    def test_single_char_with_large_width(self):
        self.assertEqual(_format_title_cell("X", 50), "X")

    def test_text_exactly_one_over(self):
        result = _format_title_cell("123456", 5)
        self.assertEqual(result, "1234~")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_ui._format_tags_suffix
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatTagsSuffix(unittest.TestCase):
    """Tests for _format_tags_suffix extraction and formatting."""

    def test_single_tag(self):
        result = _format_tags_suffix("Task #backend")
        self.assertEqual(result, " [#backend]")

    def test_multiple_tags(self):
        result = _format_tags_suffix("Task #backend #api")
        self.assertEqual(result, " [#backend #api]")

    def test_no_tags(self):
        self.assertEqual(_format_tags_suffix("No tags here"), "")

    def test_empty_string(self):
        self.assertEqual(_format_tags_suffix(""), "")

    def test_tags_normalized_lowercase(self):
        result = _format_tags_suffix("Fix #BugFix")
        self.assertEqual(result, " [#bugfix]")

    def test_duplicate_tags_deduplicated(self):
        result = _format_tags_suffix("#api #API task")
        self.assertEqual(result, " [#api]")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_ui._format_task_meta_suffix
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatTaskMetaSuffix(unittest.TestCase):
    """Tests for _format_task_meta_suffix badge rendering."""

    def test_no_metadata(self):
        task = Task(title="Simple task")
        self.assertEqual(_format_task_meta_suffix(task), "")

    def test_priority_only(self):
        task = Task(title="Task", priority="HIGH")
        result = _format_task_meta_suffix(task)
        self.assertIn("[P:HIGH]", result)

    def test_due_date_only(self):
        task = Task(title="Task", due_date=datetime(2025, 3, 15))
        result = _format_task_meta_suffix(task)
        self.assertIn("[DUE:15/03/2025]", result)

    def test_recurrence_only(self):
        task = Task(title="Task", recurrence="weekly")
        result = _format_task_meta_suffix(task)
        self.assertIn("[↻weekly]", result)

    def test_time_spent_only(self):
        task = Task(title="Task", time_spent=90)
        result = _format_task_meta_suffix(task)
        self.assertIn("[⏱1h30m]", result)

    def test_blocked_by(self):
        task = Task(title="Task", blocked_by=["Other task"])
        result = _format_task_meta_suffix(task)
        self.assertIn("[⛔ Other task]", result)

    def test_blocks(self):
        task = Task(title="Task", blocks=["Dependent"])
        result = _format_task_meta_suffix(task)
        self.assertIn("[→ Dependent]", result)

    def test_all_metadata(self):
        task = Task(
            title="Full task",
            priority="HIGH",
            due_date=datetime(2025, 6, 1),
            recurrence="weekly",
            time_spent=90,
            blocked_by=["X"],
        )
        result = _format_task_meta_suffix(task)
        self.assertIn("[P:HIGH]", result)
        self.assertIn("[DUE:01/06/2025]", result)
        self.assertIn("[↻weekly]", result)
        self.assertIn("[⏱1h30m]", result)
        self.assertIn("[⛔ X]", result)

    def test_result_starts_with_space(self):
        task = Task(title="Task", priority="LOW")
        result = _format_task_meta_suffix(task)
        self.assertTrue(result.startswith(" "))

    def test_multiple_blockers(self):
        task = Task(title="Task", blocked_by=["A", "B"])
        result = _format_task_meta_suffix(task)
        self.assertIn("[⛔ A]", result)
        self.assertIn("[⛔ B]", result)

    def test_time_spent_zero_not_shown(self):
        task = Task(title="Task", time_spent=0)
        result = _format_task_meta_suffix(task)
        self.assertEqual(result, "")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_ui._max_id_length
# ═══════════════════════════════════════════════════════════════════════════════

class TestMaxIdLength(unittest.TestCase):
    """Tests for _max_id_length computation."""

    def test_empty_dict(self):
        self.assertEqual(_max_id_length({}), 1)

    def test_single_task(self):
        task = Task(title="T", task_id="1")
        result = _max_id_length({datetime.now(): [task]})
        self.assertEqual(result, 1)

    def test_longer_id(self):
        task = Task(title="T", task_id="123")
        result = _max_id_length({datetime.now(): [task]})
        self.assertEqual(result, 3)

    def test_subtask_longer_than_parent(self):
        sub = Subtask(title="S", task_id="1a")
        task = Task(title="T", task_id="1", subtasks=[sub])
        result = _max_id_length({datetime.now(): [task]})
        self.assertEqual(result, 2)

    def test_multiple_dates(self):
        t1 = Task(title="T1", task_id="1")
        t2 = Task(title="T2", task_id="9999")
        result = _max_id_length({
            datetime(2025, 1, 1): [t1],
            datetime(2025, 1, 2): [t2],
        })
        self.assertEqual(result, 4)

    def test_none_task_id(self):
        task = Task(title="T", task_id=None)
        result = _max_id_length({datetime.now(): [task]})
        self.assertEqual(result, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_ui.Colors class
# ═══════════════════════════════════════════════════════════════════════════════

class TestColorsClass(unittest.TestCase):
    """Tests for Colors class attributes."""

    def test_reset_contains_escape(self):
        self.assertIn("\033[0m", Colors.RESET)

    def test_bold(self):
        self.assertEqual(Colors.BOLD, "\033[1m")

    def test_dim(self):
        self.assertEqual(Colors.DIM, "\033[2m")

    def test_backlog_color(self):
        self.assertEqual(Colors.BACKLOG, "\033[90m")

    def test_in_progress_color(self):
        self.assertEqual(Colors.IN_PROGRESS, "\033[33m")

    def test_waiting_color(self):
        self.assertEqual(Colors.WAITING, "\033[35m")

    def test_testing_color(self):
        self.assertEqual(Colors.TESTING, "\033[36m")

    def test_done_color(self):
        self.assertEqual(Colors.DONE, "\033[32m")

    def test_cancelled_color(self):
        self.assertEqual(Colors.CANCELLED, "\033[91m")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_ui.get_stats
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetStats(unittest.TestCase):
    """Tests for get_stats statistics computation."""

    def test_empty_dict(self):
        stats = get_stats({})
        self.assertEqual(stats["total"], 0)
        self.assertIn("by_state", stats)

    def test_single_task(self):
        task = Task(title="T", state="BACKLOG")
        stats = get_stats({datetime.now(): [task]})
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["by_state"]["BACKLOG"], 1)

    def test_multiple_states(self):
        tasks = [
            Task(title="T1", state="BACKLOG"),
            Task(title="T2", state="IN PROGRESS"),
            Task(title="T3", state="DONE"),
        ]
        stats = get_stats({datetime.now(): tasks})
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_state"]["BACKLOG"], 1)
        self.assertEqual(stats["by_state"]["IN PROGRESS"], 1)
        self.assertEqual(stats["by_state"]["DONE"], 1)

    def test_multiple_dates(self):
        t1 = Task(title="T1", state="BACKLOG")
        t2 = Task(title="T2", state="BACKLOG")
        stats = get_stats({
            datetime(2025, 1, 1): [t1],
            datetime(2025, 1, 2): [t2],
        })
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["by_state"]["BACKLOG"], 2)

    def test_all_states_present_in_result(self):
        stats = get_stats({})
        for state in ["BACKLOG", "IN PROGRESS", "WAITING", "TESTING", "DONE", "CANCELLED"]:
            self.assertIn(state, stats["by_state"])


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_sync.get_sync_user (via mocking _sync_config)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetSyncUser(unittest.TestCase):
    """Tests for get_sync_user username extraction from remote URL."""

    def _set_config(self, remote):
        """Helper to set module-level _sync_config."""
        tm_sync._sync_config = {"remote": remote}

    def tearDown(self):
        tm_sync._sync_config = None

    def test_https_github(self):
        self._set_config("https://github.com/Galerian84/ttm-journal.git")
        self.assertEqual(tm_sync.get_sync_user(), "Galerian84")

    def test_ssh_github(self):
        self._set_config("git@github.com:UserName/repo.git")
        self.assertEqual(tm_sync.get_sync_user(), "UserName")

    def test_https_gitlab(self):
        self._set_config("https://gitlab.com/user123/project")
        self.assertEqual(tm_sync.get_sync_user(), "user123")

    def test_https_no_git_suffix(self):
        self._set_config("https://github.com/dev-user/my-repo")
        self.assertEqual(tm_sync.get_sync_user(), "dev-user")

    def test_empty_remote(self):
        self._set_config("")
        self.assertEqual(tm_sync.get_sync_user(), "")

    def test_no_config(self):
        tm_sync._sync_config = None
        self.assertEqual(tm_sync.get_sync_user(), "")

    def test_config_without_remote_key(self):
        tm_sync._sync_config = {}
        self.assertEqual(tm_sync.get_sync_user(), "")

    def test_complex_username(self):
        self._set_config("https://github.com/My-User_42/project.git")
        self.assertEqual(tm_sync.get_sync_user(), "My-User_42")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for tm_sync regex pattern directly
# ═══════════════════════════════════════════════════════════════════════════════

class TestSyncRemoteRegex(unittest.TestCase):
    """Tests for the sync remote URL regex pattern used in get_sync_user."""

    PATTERN = re.compile(r"[/:]([^/:]+)/[^/]+(?:\.git)?$")

    def test_https_github_git_suffix(self):
        m = self.PATTERN.search("https://github.com/Galerian84/ttm-journal.git")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "Galerian84")

    def test_ssh_colon_separator(self):
        m = self.PATTERN.search("git@github.com:UserName/repo.git")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "UserName")

    def test_https_no_git_suffix(self):
        m = self.PATTERN.search("https://gitlab.com/user123/project")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "user123")

    def test_empty_string_no_match(self):
        m = self.PATTERN.search("")
        self.assertIsNone(m)

    def test_just_domain_no_match(self):
        m = self.PATTERN.search("https://github.com")
        self.assertIsNone(m)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for task_manager.normalize_journal_name
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeJournalName(unittest.TestCase):
    """Tests for normalize_journal_name validation and extension logic."""

    def test_adds_txt_extension(self):
        self.assertEqual(normalize_journal_name("my journal"), "my journal.txt")

    def test_keeps_existing_txt(self):
        self.assertEqual(normalize_journal_name("tasks.txt"), "tasks.txt")

    def test_empty_string(self):
        self.assertIsNone(normalize_journal_name(""))

    def test_whitespace_only(self):
        self.assertIsNone(normalize_journal_name("   "))

    def test_path_traversal_rejected(self):
        self.assertIsNone(normalize_journal_name("../evil"))

    def test_absolute_path_rejected(self):
        self.assertIsNone(normalize_journal_name("/etc/passwd"))

    def test_subdirectory_rejected(self):
        self.assertIsNone(normalize_journal_name("sub/file"))

    def test_strips_whitespace(self):
        self.assertEqual(normalize_journal_name("  notes  "), "notes.txt")

    def test_case_insensitive_extension(self):
        self.assertEqual(normalize_journal_name("file.TXT"), "file.TXT")

    def test_dot_file_with_traversal(self):
        self.assertIsNone(normalize_journal_name("../../../etc/passwd"))

    def test_simple_name_with_hyphens(self):
        self.assertEqual(normalize_journal_name("my-journal"), "my-journal.txt")

    def test_name_with_dots_not_txt(self):
        self.assertEqual(normalize_journal_name("v1.0"), "v1.0.txt")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for task_manager.list_journals
# ═══════════════════════════════════════════════════════════════════════════════

class TestListJournals(unittest.TestCase):
    """Tests for list_journals directory listing."""

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = list_journals(Path(tmpdir))
            self.assertEqual(result, [])

    def test_single_txt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "tasks.txt"
            p.write_text("content", encoding="utf-8")
            result = list_journals(Path(tmpdir))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].name, "tasks.txt")

    def test_multiple_txt_files_sorted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ["zebra.txt", "alpha.txt", "middle.txt"]:
                (Path(tmpdir) / name).write_text("x", encoding="utf-8")
            result = list_journals(Path(tmpdir))
            names = [p.name for p in result]
            self.assertEqual(names, ["alpha.txt", "middle.txt", "zebra.txt"])

    def test_non_txt_files_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "notes.txt").write_text("x", encoding="utf-8")
            (Path(tmpdir) / "config.json").write_text("{}", encoding="utf-8")
            (Path(tmpdir) / "readme.md").write_text("x", encoding="utf-8")
            result = list_journals(Path(tmpdir))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].name, "notes.txt")

    def test_nonexistent_directory(self):
        result = list_journals(Path("/nonexistent/path/xyz"))
        self.assertEqual(result, [])

    def test_subdirectories_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "subdir.txt"
            subdir.mkdir()
            (Path(tmpdir) / "real.txt").write_text("x", encoding="utf-8")
            result = list_journals(Path(tmpdir))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].name, "real.txt")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for task_manager.load_cached_journal
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadCachedJournal(unittest.TestCase):
    """Tests for load_cached_journal reading cached name."""

    def test_valid_cache(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("journal_name.txt\n")
            f.flush()
            result = load_cached_journal(Path(f.name))
            self.assertEqual(result, "journal_name.txt")
        os.unlink(f.name)

    def test_missing_file(self):
        result = load_cached_journal(Path("/nonexistent/cache_file_xyz"))
        self.assertIsNone(result)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("")
            f.flush()
            result = load_cached_journal(Path(f.name))
            self.assertIsNone(result)
        os.unlink(f.name)

    def test_whitespace_only_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("   \n")
            f.flush()
            result = load_cached_journal(Path(f.name))
            self.assertIsNone(result)
        os.unlink(f.name)

    def test_strips_whitespace(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("  my_journal.txt  \n")
            f.flush()
            result = load_cached_journal(Path(f.name))
            self.assertEqual(result, "my_journal.txt")
        os.unlink(f.name)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for task_manager.save_cached_journal
# ═══════════════════════════════════════════════════════════════════════════════

class TestSaveCachedJournal(unittest.TestCase):
    """Tests for save_cached_journal writing to disk."""

    def test_writes_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache"
            save_cached_journal(cache_path, "my_journal.txt")
            content = cache_path.read_text(encoding="utf-8")
            self.assertEqual(content, "my_journal.txt\n")

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache"
            save_cached_journal(cache_path, "old.txt")
            save_cached_journal(cache_path, "new.txt")
            content = cache_path.read_text(encoding="utf-8")
            self.assertEqual(content, "new.txt\n")

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache"
            save_cached_journal(cache_path, "roundtrip.txt")
            result = load_cached_journal(cache_path)
            self.assertEqual(result, "roundtrip.txt")

    def test_invalid_path_does_not_raise(self):
        # save_cached_journal silently handles OSError
        bad_path = Path("/nonexistent_dir_xyz/subdir/cache")
        # Should not raise
        save_cached_journal(bad_path, "test.txt")


if __name__ == "__main__":
    unittest.main()
