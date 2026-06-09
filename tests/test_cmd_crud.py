"""Tests for command handler functions in tm_cmd_crud.py.

Tests the integration between user commands and journal file mutations,
verifying that handlers correctly parse input, mutate files, and return
proper CommandOutcome objects.
"""

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from src.tm_cmd_common import CommandContext, CommandOutcome, ViewState
from src.tm_cmd_crud import (
    handle_change_state,
    handle_delete,
    handle_new,
)
from src.tm_email import EmailConfig
from src.tm_journal import parse_journal
from src.tm_logic import assign_task_ids


def _make_journal(content: str) -> str:
    """Write content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def _parse_with_ids(path: str) -> dict:
    """Parse journal and assign task IDs (same as the main app does)."""
    tasks = parse_journal(path)
    assign_task_ids(tasks)
    return tasks


def _make_context(path: str) -> CommandContext:
    """Create a CommandContext pointing at the given journal path."""
    return CommandContext(
        journal_path=path,
        email_config=EmailConfig(),
        refresh_tasks=lambda: _parse_with_ids(path),
        undo_stack=[],
        max_undo=20,
    )


# Patch targets for suppressing terminal output
_PATCH_CLEAR = "src.tm_cmd_crud.clear_screen"
_PATCH_RENDER = "src.tm_cmd_crud._render"
_PATCH_LOG = "src.tm_cmd_crud._log"
_PATCH_CONFIRM = "src.tm_cmd_crud._confirm_action"


class TestHandleNew(unittest.TestCase):
    """Tests for handle_new command handler."""

    def _run(self, command: str, journal_content: str = "") -> tuple:
        """Helper: run handle_new and return (outcome, file_contents)."""
        path = _make_journal(journal_content)
        try:
            ctx = _make_context(path)
            tasks = _parse_with_ids(path)
            vs = ViewState()
            with patch(_PATCH_CLEAR), patch(_PATCH_RENDER), patch(_PATCH_LOG):
                outcome = handle_new(command, tasks, vs, ctx)
            content = open(path, encoding="utf-8").read()
            return outcome, content
        finally:
            os.unlink(path)

    def test_returns_none_for_non_matching_command(self):
        outcome, _ = self._run("cs 1 done")
        self.assertIsNone(outcome)

    def test_creates_task_with_inline_title(self):
        outcome, content = self._run("n Buy milk")
        self.assertIsNotNone(outcome)
        self.assertIsInstance(outcome, CommandOutcome)
        self.assertIn("Buy milk", content)
        self.assertIn("BACKLOG", content)

    def test_creates_task_with_specified_state(self):
        outcome, content = self._run("n Fix bug --state IN PROGRESS")
        self.assertIsNotNone(outcome)
        self.assertIn("Fix bug", content)
        self.assertIn("IN PROGRESS", content)

    def test_creates_task_with_priority(self):
        outcome, content = self._run("n Urgent task --priority high")
        self.assertIsNotNone(outcome)
        self.assertIn("Urgent task", content)
        self.assertIn("HIGH", content)

    def test_creates_task_with_due_date(self):
        outcome, content = self._run("n Report --due 25/12/2025")
        self.assertIsNotNone(outcome)
        self.assertIn("Report", content)
        self.assertIn("25/12/2025", content)

    def test_creates_task_with_target_date(self):
        outcome, content = self._run("n Plan --date 15/01/2025")
        self.assertIsNotNone(outcome)
        self.assertIn("Plan", content)
        self.assertIn("15/01/2025", content)

    def test_creates_task_with_recurrence(self):
        outcome, content = self._run("n Standup --recur daily")
        self.assertIsNotNone(outcome)
        self.assertIn("Standup", content)
        self.assertIn("daily", content.lower())

    def test_empty_title_without_form_returns_error(self):
        """'n' with no title and form cancelled returns outcome."""
        with patch("src.tm_form.show_form", return_value=None):
            outcome, content = self._run("n")
        self.assertIsNotNone(outcome)
        # No task should have been added
        self.assertEqual(content.strip(), "")

    def test_creates_task_adds_to_existing_journal(self):
        existing = "## 01/01/2025\n- Existing task -- DONE\n"
        outcome, content = self._run("n New task", existing)
        self.assertIsNotNone(outcome)
        self.assertIn("Existing task", content)
        self.assertIn("New task", content)

    def test_parse_error_returns_outcome_with_skip_redraw(self):
        """Invalid flags should return an outcome with skip_redraw=True."""
        outcome, _ = self._run("n Fix --state INVALIDSTATE")
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.skip_redraw)

    def test_updated_tasks_in_outcome_contains_new_task(self):
        path = _make_journal("")
        try:
            ctx = _make_context(path)
            tasks = _parse_with_ids(path)
            vs = ViewState()
            with patch(_PATCH_CLEAR), patch(_PATCH_RENDER), patch(_PATCH_LOG):
                outcome = handle_new("n Test task", tasks, vs, ctx)
            # Outcome should have the new task in its tasks_by_date
            all_titles = [t.title for ts in outcome.tasks_by_date.values() for t in ts]
            self.assertTrue(any("Test task" in title for title in all_titles))
        finally:
            os.unlink(path)


class TestHandleChangeState(unittest.TestCase):
    """Tests for handle_change_state command handler."""

    JOURNAL = "## 01/01/2025\n- Buy groceries -- BACKLOG\n- Fix bug -- IN PROGRESS\n"

    def _run(self, command: str, journal_content: str = None) -> tuple:
        content = journal_content or self.JOURNAL
        path = _make_journal(content)
        try:
            ctx = _make_context(path)
            tasks = _parse_with_ids(path)
            vs = ViewState()
            with patch(_PATCH_CLEAR), patch(_PATCH_RENDER), patch(_PATCH_LOG):
                outcome = handle_change_state(command, tasks, vs, ctx)
            file_content = open(path, encoding="utf-8").read()
            return outcome, file_content
        finally:
            os.unlink(path)

    def test_returns_none_for_non_matching_command(self):
        outcome, _ = self._run("n Buy milk")
        self.assertIsNone(outcome)

    def test_changes_state_by_id(self):
        outcome, content = self._run("cs 1 done")
        self.assertIsNotNone(outcome)
        self.assertIn("DONE", content)
        # First task should now be DONE
        self.assertNotIn("BACKLOG", content)

    def test_changes_state_alias(self):
        outcome, content = self._run("cs 1 dn")
        self.assertIsNotNone(outcome)
        self.assertIn("DONE", content)

    def test_invalid_id_returns_error_outcome(self):
        outcome, content = self._run("cs 99 done")
        self.assertIsNotNone(outcome)
        # File should be unchanged
        self.assertIn("BACKLOG", content)

    def test_invalid_state_without_form_prompts(self):
        """Invalid state name without form should trigger form or error."""
        with patch("src.tm_form.show_form", return_value=None):
            outcome, content = self._run("cs 1 NOTASTATE")
        self.assertIsNotNone(outcome)
        # File unchanged since form was cancelled
        self.assertIn("BACKLOG", content)

    def test_change_state_with_recurrence_creates_new_task(self):
        """Completing a recurring task should create the next occurrence."""
        journal = "## 01/01/2025\n- Daily standup -- BACKLOG -- recur:daily\n"
        outcome, content = self._run("cs 1 done", journal)
        self.assertIsNotNone(outcome)
        # Original should be DONE
        self.assertIn("DONE", content)
        # A new BACKLOG task should exist for the next day
        self.assertIn("BACKLOG", content)
        # Both entries should exist
        lines = [l for l in content.splitlines() if "standup" in l.lower()]
        self.assertGreaterEqual(len(lines), 2)

    def test_change_state_preserves_second_task(self):
        outcome, content = self._run("cs 1 done")
        self.assertIn("Fix bug", content)
        self.assertIn("IN PROGRESS", content)


class TestHandleDelete(unittest.TestCase):
    """Tests for handle_delete command handler."""

    JOURNAL = "## 01/01/2025\n- Task one -- BACKLOG\n- Task two -- DONE\n"

    def _run(self, command: str, journal_content: str = None, confirm: bool = True) -> tuple:
        content = journal_content or self.JOURNAL
        path = _make_journal(content)
        try:
            ctx = _make_context(path)
            tasks = _parse_with_ids(path)
            vs = ViewState()
            with patch(_PATCH_CLEAR), patch(_PATCH_RENDER), patch(_PATCH_LOG), \
                 patch(_PATCH_CONFIRM, return_value=confirm):
                outcome = handle_delete(command, tasks, vs, ctx)
            file_content = open(path, encoding="utf-8").read()
            return outcome, file_content
        finally:
            os.unlink(path)

    def test_returns_none_for_non_matching_command(self):
        outcome, _ = self._run("n Buy milk")
        self.assertIsNone(outcome)

    def test_deletes_task_by_id(self):
        outcome, content = self._run("del 1")
        self.assertIsNotNone(outcome)
        self.assertNotIn("Task one", content)
        self.assertIn("Task two", content)

    def test_deletes_second_task(self):
        outcome, content = self._run("del 2")
        self.assertIsNotNone(outcome)
        self.assertIn("Task one", content)
        self.assertNotIn("Task two", content)

    def test_cancelled_delete_preserves_task(self):
        outcome, content = self._run("del 1", confirm=False)
        self.assertIsNotNone(outcome)
        self.assertIn("Task one", content)
        self.assertIn("Task two", content)

    def test_invalid_id_returns_error(self):
        outcome, content = self._run("del 99")
        self.assertIsNotNone(outcome)
        # Both tasks should remain
        self.assertIn("Task one", content)
        self.assertIn("Task two", content)

    def test_delete_with_notes(self):
        journal = "## 01/01/2025\n- Task with note -- BACKLOG\n  > This is a note\n- Keep me -- DONE\n"
        outcome, content = self._run("del 1", journal)
        self.assertIsNotNone(outcome)
        self.assertNotIn("Task with note", content)
        self.assertNotIn("This is a note", content)
        self.assertIn("Keep me", content)

    def test_missing_id_returns_usage_error(self):
        outcome, content = self._run("del")
        self.assertIsNotNone(outcome)
        # File unchanged
        self.assertIn("Task one", content)


class TestHandleNewEdgeCases(unittest.TestCase):
    """Additional edge case tests for handle_new."""

    def test_creates_task_with_tags(self):
        path = _make_journal("")
        try:
            ctx = _make_context(path)
            tasks = _parse_with_ids(path)
            vs = ViewState()
            with patch(_PATCH_CLEAR), patch(_PATCH_RENDER), patch(_PATCH_LOG):
                outcome = handle_new("n Deploy #backend #urgent", tasks, vs, ctx)
            content = open(path, encoding="utf-8").read()
            self.assertIn("#backend", content)
            self.assertIn("#urgent", content)
        finally:
            os.unlink(path)

    def test_new_command_alias(self):
        """'new' should work the same as 'n'."""
        path = _make_journal("")
        try:
            ctx = _make_context(path)
            tasks = _parse_with_ids(path)
            vs = ViewState()
            with patch(_PATCH_CLEAR), patch(_PATCH_RENDER), patch(_PATCH_LOG):
                outcome = handle_new("new Deploy to prod", tasks, vs, ctx)
            content = open(path, encoding="utf-8").read()
            self.assertIn("Deploy to prod", content)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
