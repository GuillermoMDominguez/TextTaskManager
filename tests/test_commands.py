"""Comprehensive tests for private helper functions in tm_commands.py."""

import sys
import os
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tm_commands import (
    _strip_tags,
    _parse_meta_command,
    _strip_inline_tags,
    _apply_tags_to_text,
    _extract_inline_meta,
    _default_archive_path,
    ViewState,
    CommandOutcome,
)


# ---------------------------------------------------------------------------
# _strip_tags
# ---------------------------------------------------------------------------


class TestStripTags(unittest.TestCase):
    """Tests for _strip_tags(title) -> str."""

    def test_removes_single_tag(self):
        self.assertEqual(_strip_tags("Fix #backend bug"), "Fix bug")

    def test_removes_multiple_tags(self):
        self.assertEqual(_strip_tags("Fix #backend bug #urgent"), "Fix bug")

    def test_no_tags_unchanged(self):
        self.assertEqual(_strip_tags("No tags here"), "No tags here")

    def test_only_tag_returns_empty(self):
        self.assertEqual(_strip_tags("#only-tag"), "")

    def test_empty_string(self):
        self.assertEqual(_strip_tags(""), "")

    def test_tag_at_start(self):
        self.assertEqual(_strip_tags("#start word"), "word")

    def test_tag_at_end(self):
        self.assertEqual(_strip_tags("word #end"), "word")

    def test_tag_with_numbers(self):
        self.assertEqual(_strip_tags("Task #v2 done"), "Task done")

    def test_tag_with_underscore(self):
        self.assertEqual(_strip_tags("Task #my_tag done"), "Task done")

    def test_tag_with_hyphen(self):
        self.assertEqual(_strip_tags("Task #my-tag done"), "Task done")

    def test_hash_inside_word_not_stripped(self):
        # '#' preceded by a word char should NOT match
        self.assertEqual(_strip_tags("foo#bar"), "foo#bar")

    def test_multiple_spaces_collapsed(self):
        self.assertEqual(_strip_tags("a  #tag  b"), "a b")

    def test_only_whitespace_and_tag(self):
        self.assertEqual(_strip_tags("  #tag  "), "")

    def test_multiple_consecutive_tags(self):
        self.assertEqual(_strip_tags("#a #b #c"), "")


# ---------------------------------------------------------------------------
# _strip_inline_tags
# ---------------------------------------------------------------------------


class TestStripInlineTags(unittest.TestCase):
    """Tests for _strip_inline_tags(text) -> str."""

    def test_basic_removal(self):
        self.assertEqual(_strip_inline_tags("Task #foo #bar"), "Task")

    def test_no_tags(self):
        self.assertEqual(_strip_inline_tags("Hello world"), "Hello world")

    def test_only_tags(self):
        self.assertEqual(_strip_inline_tags("#alpha #beta"), "")

    def test_empty_string(self):
        self.assertEqual(_strip_inline_tags(""), "")

    def test_none_input(self):
        # The function handles None via `text or ""`
        self.assertEqual(_strip_inline_tags(None), "")

    def test_preserves_non_tag_hash(self):
        self.assertEqual(_strip_inline_tags("issue#42 #tag"), "issue#42")


# ---------------------------------------------------------------------------
# _apply_tags_to_text
# ---------------------------------------------------------------------------


class TestApplyTagsToText(unittest.TestCase):
    """Tests for _apply_tags_to_text(text, tags) -> str."""

    def test_replaces_existing_tags(self):
        result = _apply_tags_to_text("Fix bug #old", ["new", "shiny"])
        self.assertEqual(result, "Fix bug #new #shiny")

    def test_adds_to_plain_text(self):
        result = _apply_tags_to_text("Just text", ["tag1"])
        self.assertEqual(result, "Just text #tag1")

    def test_empty_text_with_tags(self):
        result = _apply_tags_to_text("", ["tag1"])
        self.assertEqual(result, "#tag1")

    def test_text_with_empty_tags(self):
        result = _apply_tags_to_text("Text", [])
        self.assertEqual(result, "Text")

    def test_empty_text_empty_tags(self):
        result = _apply_tags_to_text("", [])
        self.assertEqual(result, "")

    def test_multiple_existing_tags_replaced(self):
        result = _apply_tags_to_text("Do #a #b stuff", ["x"])
        self.assertEqual(result, "Do stuff #x")

    def test_only_tag_replaced(self):
        result = _apply_tags_to_text("#old", ["new"])
        self.assertEqual(result, "#new")


# ---------------------------------------------------------------------------
# _extract_inline_meta
# ---------------------------------------------------------------------------


class TestExtractInlineMeta(unittest.TestCase):
    """Tests for _extract_inline_meta(text) -> (base_text, tags, due, priority)."""

    def test_full_meta(self):
        text = "Fix bug #backend [due=25/12/2024] [priority=HIGH]"
        base, tags, due, priority = _extract_inline_meta(text)
        self.assertEqual(base, "Fix bug")
        self.assertEqual(tags, ["backend"])
        self.assertEqual(due, datetime(2024, 12, 25))
        self.assertEqual(priority, "HIGH")

    def test_plain_text(self):
        base, tags, due, priority = _extract_inline_meta("Plain text")
        self.assertEqual(base, "Plain text")
        self.assertEqual(tags, [])
        self.assertIsNone(due)
        self.assertIsNone(priority)

    def test_tag_only(self):
        base, tags, due, priority = _extract_inline_meta("#tag only")
        self.assertEqual(base, "only")
        self.assertEqual(tags, ["tag"])
        self.assertIsNone(due)
        self.assertIsNone(priority)

    def test_due_only(self):
        base, tags, due, priority = _extract_inline_meta("[due=01/01/2025]")
        self.assertEqual(base, "")
        self.assertEqual(tags, [])
        self.assertEqual(due, datetime(2025, 1, 1))
        self.assertIsNone(priority)

    def test_priority_only(self):
        base, tags, due, priority = _extract_inline_meta("[priority=LOW]")
        self.assertEqual(base, "")
        self.assertEqual(tags, [])
        self.assertIsNone(due)
        self.assertEqual(priority, "LOW")

    def test_multiple_tags(self):
        base, tags, due, priority = _extract_inline_meta("Task #api #backend")
        self.assertEqual(base, "Task")
        self.assertIn("api", tags)
        self.assertIn("backend", tags)
        self.assertEqual(len(tags), 2)

    def test_invalid_priority_returns_none(self):
        base, tags, due, priority = _extract_inline_meta("[priority=BOGUS]")
        self.assertEqual(base, "")
        self.assertIsNone(priority)

    def test_due_with_spaces_in_brackets(self):
        base, tags, due, priority = _extract_inline_meta("[ due = 15/06/2025 ]")
        self.assertEqual(due, datetime(2025, 6, 15))

    def test_meta_stripped_from_base_text(self):
        text = "My task [due=01/01/2025] [priority=MEDIUM] #tag"
        base, tags, due, priority = _extract_inline_meta(text)
        self.assertEqual(base, "My task")
        self.assertNotIn("[", base)
        self.assertNotIn("#", base)


# ---------------------------------------------------------------------------
# _default_archive_path
# ---------------------------------------------------------------------------


class TestDefaultArchivePath(unittest.TestCase):
    """Tests for _default_archive_path(journal_path) -> str."""

    def test_simple_filename(self):
        self.assertEqual(_default_archive_path("journal.txt"), "journal_archive.txt")

    def test_path_with_directory(self):
        result = _default_archive_path("/path/to/my-tasks.txt")
        self.assertEqual(result, "/path/to/my-tasks_archive.txt")

    def test_relative_path(self):
        result = _default_archive_path("data/notes.md")
        self.assertEqual(result, "data/notes_archive.md")

    def test_dotfile(self):
        result = _default_archive_path(".tasks.txt")
        self.assertEqual(result, ".tasks_archive.txt")

    def test_no_extension(self):
        result = _default_archive_path("tasks")
        self.assertEqual(result, "tasks_archive")

    def test_multiple_dots(self):
        result = _default_archive_path("my.journal.txt")
        self.assertEqual(result, "my.journal_archive.txt")


# ---------------------------------------------------------------------------
# ViewState dataclass
# ---------------------------------------------------------------------------


class TestViewState(unittest.TestCase):
    """Tests for ViewState dataclass defaults and attributes."""

    def test_default_show_done(self):
        vs = ViewState()
        self.assertFalse(vs.show_done)

    def test_default_only_in_progress(self):
        vs = ViewState()
        self.assertFalse(vs.only_in_progress)

    def test_default_only_testing(self):
        vs = ViewState()
        self.assertFalse(vs.only_testing)

    def test_default_search_query(self):
        vs = ViewState()
        self.assertIsNone(vs.search_query)

    def test_default_sort_by(self):
        vs = ViewState()
        self.assertEqual(vs.sort_by, "none")

    def test_default_sort_direction(self):
        vs = ViewState()
        self.assertEqual(vs.sort_direction, "asc")

    def test_custom_values(self):
        vs = ViewState(show_done=True, search_query="test", sort_by="priority")
        self.assertTrue(vs.show_done)
        self.assertEqual(vs.search_query, "test")
        self.assertEqual(vs.sort_by, "priority")

    def test_mutation(self):
        vs = ViewState()
        vs.show_done = True
        self.assertTrue(vs.show_done)


# ---------------------------------------------------------------------------
# CommandOutcome dataclass
# ---------------------------------------------------------------------------


class TestCommandOutcome(unittest.TestCase):
    """Tests for CommandOutcome dataclass defaults and attributes."""

    def test_default_should_exit(self):
        co = CommandOutcome(tasks_by_date={}, view_state=ViewState())
        self.assertFalse(co.should_exit)

    def test_default_skip_redraw(self):
        co = CommandOutcome(tasks_by_date={}, view_state=ViewState())
        self.assertFalse(co.skip_redraw)

    def test_custom_should_exit(self):
        co = CommandOutcome(tasks_by_date={}, view_state=ViewState(), should_exit=True)
        self.assertTrue(co.should_exit)

    def test_tasks_by_date_accessible(self):
        data = {"2024-01-01": ["task1"]}
        co = CommandOutcome(tasks_by_date=data, view_state=ViewState())
        self.assertEqual(co.tasks_by_date, data)

    def test_view_state_accessible(self):
        vs = ViewState(show_done=True)
        co = CommandOutcome(tasks_by_date={}, view_state=vs)
        self.assertTrue(co.view_state.show_done)


# ---------------------------------------------------------------------------
# _parse_meta_command
# ---------------------------------------------------------------------------


class TestParseMetaCommand(unittest.TestCase):
    """Tests for _parse_meta_command(raw_command) -> tuple."""

    # -- Successful cases --

    def test_due_date(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 3 --due 25/12/2024")
        )
        self.assertEqual(task_id, "3")
        self.assertTrue(has_due)
        self.assertEqual(due_date, datetime(2024, 12, 25))
        self.assertFalse(has_prio)
        self.assertIsNone(prio)
        self.assertFalse(has_tags)
        self.assertIsNone(tags)
        self.assertIsNone(err)

    def test_priority_high(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 3 --priority high")
        )
        self.assertEqual(task_id, "3")
        self.assertFalse(has_due)
        self.assertIsNone(due_date)
        self.assertTrue(has_prio)
        self.assertEqual(prio, "HIGH")
        self.assertFalse(has_tags)
        self.assertIsNone(tags)
        self.assertIsNone(err)

    def test_tags_comma_separated(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 3 --tags backend,api")
        )
        self.assertEqual(task_id, "3")
        self.assertFalse(has_due)
        self.assertTrue(has_tags)
        self.assertEqual(tags, ["backend", "api"])
        self.assertIsNone(err)

    def test_due_none_means_remove(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 3 --due none")
        )
        self.assertEqual(task_id, "3")
        self.assertTrue(has_due)
        self.assertIsNone(due_date)  # None means "remove the due date"
        self.assertIsNone(err)

    def test_priority_none_means_remove(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 3 --priority none")
        )
        self.assertEqual(task_id, "3")
        self.assertTrue(has_prio)
        self.assertIsNone(prio)  # None means "remove priority"
        self.assertIsNone(err)

    def test_tags_none_means_remove_all(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 3 --tags none")
        )
        self.assertEqual(task_id, "3")
        self.assertTrue(has_tags)
        self.assertEqual(tags, [])  # Empty list means "remove all tags"
        self.assertIsNone(err)

    def test_no_flags_form_mode(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 3")
        )
        self.assertEqual(task_id, "3")
        self.assertFalse(has_due)
        self.assertIsNone(due_date)
        self.assertFalse(has_prio)
        self.assertIsNone(prio)
        self.assertFalse(has_tags)
        self.assertIsNone(tags)
        self.assertIsNone(err)

    def test_priority_alias_h(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 5 --priority H")
        )
        self.assertEqual(task_id, "5")
        self.assertTrue(has_prio)
        self.assertEqual(prio, "HIGH")
        self.assertIsNone(err)

    def test_priority_medium(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 7 --priority medium")
        )
        self.assertTrue(has_prio)
        self.assertEqual(prio, "MEDIUM")
        self.assertIsNone(err)

    def test_priority_urgent(self):
        _, _, _, has_prio, prio, _, _, err = _parse_meta_command("md 1 --priority urgent")
        self.assertTrue(has_prio)
        self.assertEqual(prio, "URGENT")
        self.assertIsNone(err)

    def test_short_priority_flag(self):
        _, _, _, has_prio, prio, _, _, err = _parse_meta_command("md 2 -p low")
        self.assertTrue(has_prio)
        self.assertEqual(prio, "LOW")
        self.assertIsNone(err)

    def test_short_tags_flag(self):
        _, _, _, _, _, has_tags, tags, err = _parse_meta_command("md 2 -t ui,frontend")
        self.assertTrue(has_tags)
        self.assertEqual(tags, ["ui", "frontend"])
        self.assertIsNone(err)

    def test_combined_due_and_priority(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 10 --due 01/06/2025 --priority high")
        )
        self.assertEqual(task_id, "10")
        self.assertTrue(has_due)
        self.assertEqual(due_date, datetime(2025, 6, 1))
        self.assertTrue(has_prio)
        self.assertEqual(prio, "HIGH")
        self.assertIsNone(err)

    def test_combined_all_flags(self):
        task_id, has_due, due_date, has_prio, prio, has_tags, tags, err = (
            _parse_meta_command("md 4 --due 15/03/2025 --priority low --tags bug,fix")
        )
        self.assertEqual(task_id, "4")
        self.assertTrue(has_due)
        self.assertEqual(due_date, datetime(2025, 3, 15))
        self.assertTrue(has_prio)
        self.assertEqual(prio, "LOW")
        self.assertTrue(has_tags)
        self.assertEqual(tags, ["bug", "fix"])
        self.assertIsNone(err)

    def test_dotted_task_id(self):
        task_id, *_, err = _parse_meta_command("md 3.1 --priority high")
        self.assertEqual(task_id, "3.1")
        self.assertIsNone(err)

    def test_tags_with_hash_prefix_stripped(self):
        _, _, _, _, _, has_tags, tags, err = _parse_meta_command("md 1 --tags #frontend,#api")
        self.assertTrue(has_tags)
        self.assertEqual(tags, ["frontend", "api"])
        self.assertIsNone(err)

    def test_tags_deduplicated(self):
        _, _, _, _, _, has_tags, tags, err = _parse_meta_command("md 1 --tags bug,bug,fix")
        self.assertTrue(has_tags)
        self.assertEqual(tags, ["bug", "fix"])
        self.assertIsNone(err)

    # -- Error cases --

    def test_missing_id(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("md")
        self.assertIsNotNone(err)

    def test_missing_due_value(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("md 3 --due")
        self.assertIsNotNone(err)
        self.assertIn("Missing value", err)

    def test_missing_priority_value(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("md 3 --priority")
        self.assertIsNotNone(err)
        self.assertIn("Missing value", err)

    def test_missing_tags_value(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("md 3 --tags")
        self.assertIsNotNone(err)
        self.assertIn("Missing value", err)

    def test_unknown_flag(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("md 3 --unknown")
        self.assertIsNotNone(err)
        self.assertIn("Unknown option", err)

    def test_invalid_date(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("md 3 --due invalid")
        self.assertIsNotNone(err)
        self.assertIn("Invalid due date", err)

    def test_invalid_priority(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("md 3 --priority invalid")
        self.assertIsNotNone(err)
        self.assertIn("Invalid priority", err)

    def test_invalid_tags_special_chars(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("md 3 --tags !!!")
        self.assertIsNotNone(err)
        self.assertIn("Invalid tags", err)

    def test_empty_command(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("")
        self.assertIsNotNone(err)

    def test_invalid_date_format_slash_wrong(self):
        _, _, _, _, _, _, _, err = _parse_meta_command("md 3 --due 2024-12-25")
        self.assertIsNotNone(err)

    def test_malformed_quotes(self):
        _, _, _, _, _, _, _, err = _parse_meta_command('md 3 --due "unbalanced')
        self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
