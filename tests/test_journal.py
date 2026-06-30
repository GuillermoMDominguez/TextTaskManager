"""Comprehensive tests for tm_journal.py — journal parsing and file operations."""

import sys
import os

import tempfile
import unittest
from datetime import datetime
from src.tm_journal import (
    split_comments, append_unique_comments, parse_date, parse_task_line,
    parse_subtask_line, parse_journal, write_journal, add_task_to_file,
    update_task_state_in_file, add_note_to_task_in_file, edit_task_title_in_file,
    delete_task_in_file, update_task_metadata_in_file, register_post_write_hook,
    JournalError, JournalFileNotFoundError, JournalReadError,
    _write_lines, _read_lines, _post_write_hooks,
    _parse_priority_value, _parse_due_value, _parse_recurrence_value,
    _apply_task_metadata, _apply_subtask_metadata,
    _render_task_line, _render_subtask_line,
    _task_line_indent, _find_task_block_bounds, _find_parent_task_start,
    _find_note_line_index, _render_task_block, _insert_task_block,
    add_subtask_to_file, add_subtask_to_task,
    edit_subtask_title_in_file, delete_subtask_in_file,
    update_subtask_metadata_in_file, add_note_to_subtask_in_file,
    delete_note_in_file, edit_note_in_file,
    move_task_to_date_in_file, duplicate_task_in_file,
    mark_all_subtasks_done_in_file, archive_finished_tasks_in_file,
    read_journal_snapshot, restore_journal_snapshot, lint_journal,
    update_dependency_references,
)
from src.tm_models import Task, Subtask


# ─── Parsing: split_comments ──────────────────────────────────────────────────


class TestSplitComments(unittest.TestCase):
    def test_basic_split(self):
        result = split_comments("foo: bar: baz")
        self.assertEqual(result, ["foo", "bar", "baz"])

    def test_empty_segments_filtered(self):
        result = split_comments("  :  :  ")
        self.assertEqual(result, [])

    def test_single_value(self):
        result = split_comments("single")
        self.assertEqual(result, ["single"])

    def test_empty_string(self):
        result = split_comments("")
        self.assertEqual(result, [])

    def test_whitespace_only(self):
        result = split_comments("   ")
        self.assertEqual(result, [])

    def test_colon_only(self):
        result = split_comments(":")
        self.assertEqual(result, [])

    def test_mixed_empty_and_values(self):
        result = split_comments(": hello : : world :")
        self.assertEqual(result, ["hello", "world"])

    def test_strips_whitespace_from_values(self):
        result = split_comments("  alpha  :  beta  ")
        self.assertEqual(result, ["alpha", "beta"])


# ─── Parsing: append_unique_comments ──────────────────────────────────────────


class TestAppendUniqueComments(unittest.TestCase):
    def test_appends_new_comments(self):
        target = ["a", "b"]
        append_unique_comments(target, ["c", "d"])
        self.assertEqual(target, ["a", "b", "c", "d"])

    def test_skips_duplicates(self):
        target = ["a", "b"]
        append_unique_comments(target, ["b", "c", "a"])
        self.assertEqual(target, ["a", "b", "c"])

    def test_empty_new_list(self):
        target = ["x"]
        append_unique_comments(target, [])
        self.assertEqual(target, ["x"])

    def test_empty_target(self):
        target = []
        append_unique_comments(target, ["one", "two"])
        self.assertEqual(target, ["one", "two"])

    def test_all_duplicates(self):
        target = ["a", "b", "c"]
        append_unique_comments(target, ["a", "b", "c"])
        self.assertEqual(target, ["a", "b", "c"])

    def test_preserves_order(self):
        target = []
        append_unique_comments(target, ["z", "a", "m"])
        self.assertEqual(target, ["z", "a", "m"])


# ─── Parsing: parse_date ──────────────────────────────────────────────────────


class TestParseDate(unittest.TestCase):
    def test_valid_date(self):
        result = parse_date("## 25/12/2024")
        self.assertEqual(result, datetime(2024, 12, 25))

    def test_valid_date_with_trailing_space(self):
        result = parse_date("## 01/01/2025  ")
        self.assertEqual(result, datetime(2025, 1, 1))

    def test_invalid_date_string(self):
        result = parse_date("## invalid")
        self.assertIsNone(result)

    def test_single_hash(self):
        result = parse_date("# not double hash")
        self.assertIsNone(result)

    def test_empty_string(self):
        result = parse_date("")
        self.assertIsNone(result)

    def test_no_hash(self):
        result = parse_date("25/12/2024")
        self.assertIsNone(result)

    def test_triple_hash(self):
        result = parse_date("### 25/12/2024")
        self.assertIsNone(result)

    def test_invalid_day(self):
        result = parse_date("## 32/12/2024")
        self.assertIsNone(result)

    def test_invalid_month(self):
        result = parse_date("## 01/13/2024")
        self.assertIsNone(result)

    def test_single_digit_day_month(self):
        result = parse_date("## 1/1/2024")
        self.assertEqual(result, datetime(2024, 1, 1))

    def test_leading_whitespace(self):
        result = parse_date("  ## 15/06/2024  ")
        self.assertEqual(result, datetime(2024, 6, 15))


# ─── Parsing: parse_task_line ─────────────────────────────────────────────────


class TestParseTaskLine(unittest.TestCase):
    def test_simple_task_done(self):
        task = parse_task_line("- Buy milk -- DONE")
        self.assertIsNotNone(task)
        self.assertEqual(task.title, "Buy milk")
        self.assertEqual(task.state, "DONE")

    def test_task_with_comments(self):
        task = parse_task_line("- Task with notes: note1: note2 -- IN PROGRESS")
        self.assertIsNotNone(task)
        self.assertEqual(task.title, "Task with notes")
        self.assertEqual(task.state, "IN PROGRESS")
        self.assertIn("note1", task.comments)
        self.assertIn("note2", task.comments)

    def test_task_with_metadata(self):
        task = parse_task_line("- Task -- BACKLOG -- due:25/12/2024 -- priority:HIGH -- recur:weekly")
        self.assertIsNotNone(task)
        self.assertEqual(task.title, "Task")
        self.assertEqual(task.state, "BACKLOG")
        self.assertEqual(task.due_date, datetime(2024, 12, 25))
        self.assertEqual(task.priority, "HIGH")
        self.assertEqual(task.recurrence, "weekly")

    def test_task_state_alias_ip(self):
        task = parse_task_line("- Task -- IP")
        self.assertIsNotNone(task)
        self.assertEqual(task.state, "IN PROGRESS")

    def test_task_with_time_spent(self):
        task = parse_task_line("- Task -- DONE -- spent:2h30m")
        self.assertIsNotNone(task)
        self.assertEqual(task.state, "DONE")
        self.assertEqual(task.time_spent, 150)

    def test_task_with_blockedby(self):
        task = parse_task_line("- Task -- BACKLOG -- blockedby:Other Task")
        self.assertIsNotNone(task)
        self.assertIn("Other Task", task.blocked_by)

    def test_task_with_blocks(self):
        task = parse_task_line("- Task -- BACKLOG -- blocks:Dependent Task")
        self.assertIsNotNone(task)
        self.assertIn("Dependent Task", task.blocks)

    def test_not_a_task(self):
        result = parse_task_line("not a task")
        self.assertIsNone(result)

    def test_empty_content_with_space(self):
        result = parse_task_line("- ")
        self.assertIsNone(result)

    def test_dash_only(self):
        result = parse_task_line("-")
        self.assertIsNone(result)

    def test_default_state(self):
        task = parse_task_line("- Just a task")
        self.assertIsNotNone(task)
        self.assertEqual(task.title, "Just a task")
        self.assertEqual(task.state, "BACKLOG")

    def test_arrow_separator(self):
        task = parse_task_line("- Task -> DONE")
        self.assertIsNotNone(task)
        self.assertEqual(task.state, "DONE")

    def test_priority_alias(self):
        task = parse_task_line("- Task -- BACKLOG -- priority:H")
        self.assertIsNotNone(task)
        self.assertEqual(task.priority, "HIGH")

    def test_recurrence_alias(self):
        task = parse_task_line("- Daily standup -- BACKLOG -- recur:D")
        self.assertIsNotNone(task)
        self.assertEqual(task.recurrence, "daily")

    def test_state_alias_bl(self):
        task = parse_task_line("- Task -- BL")
        self.assertIsNotNone(task)
        self.assertEqual(task.state, "BACKLOG")

    def test_state_alias_dn(self):
        task = parse_task_line("- Task -- DN")
        self.assertIsNotNone(task)
        self.assertEqual(task.state, "DONE")

    def test_task_with_due_equals(self):
        task = parse_task_line("- Task -- BACKLOG -- due=01/06/2025")
        self.assertIsNotNone(task)
        self.assertEqual(task.due_date, datetime(2025, 6, 1))


# ─── Parsing: parse_subtask_line ──────────────────────────────────────────────


class TestParseSubtaskLine(unittest.TestCase):
    def test_simple_subtask_done(self):
        subtask = parse_subtask_line("+ Sub item -- DONE")
        self.assertIsNotNone(subtask)
        self.assertEqual(subtask.title, "Sub item")
        self.assertEqual(subtask.state, "DONE")

    def test_subtask_with_inline_priority(self):
        subtask = parse_subtask_line("+ Sub [priority=HIGH] -- BACKLOG")
        self.assertIsNotNone(subtask)
        self.assertEqual(subtask.priority, "HIGH")
        self.assertNotIn("[", subtask.title)
        self.assertEqual(subtask.state, "BACKLOG")

    def test_subtask_with_due_date(self):
        subtask = parse_subtask_line("+ Sub -- due:01/01/2025")
        self.assertIsNotNone(subtask)
        self.assertEqual(subtask.due_date, datetime(2025, 1, 1))

    def test_not_a_subtask(self):
        result = parse_subtask_line("not a subtask")
        self.assertIsNone(result)

    def test_empty_subtask(self):
        result = parse_subtask_line("+")
        self.assertIsNone(result)

    def test_subtask_empty_content(self):
        result = parse_subtask_line("+ ")
        self.assertIsNone(result)

    def test_subtask_default_state(self):
        subtask = parse_subtask_line("+ Something")
        self.assertIsNotNone(subtask)
        self.assertEqual(subtask.state, "BACKLOG")

    def test_subtask_with_state_alias(self):
        subtask = parse_subtask_line("+ Item -- IP")
        self.assertIsNotNone(subtask)
        self.assertEqual(subtask.state, "IN PROGRESS")

    def test_subtask_priority_prio_alias(self):
        subtask = parse_subtask_line("+ Item [prio=M] -- BACKLOG")
        self.assertIsNotNone(subtask)
        self.assertEqual(subtask.priority, "MEDIUM")

    def test_subtask_with_metadata_priority(self):
        subtask = parse_subtask_line("+ Item -- priority:LOW")
        self.assertIsNotNone(subtask)
        self.assertEqual(subtask.priority, "LOW")


# ─── Parsing: parse_journal (full file parsing) ──────────────────────────────


class TestParseJournal(unittest.TestCase):
    def _write_temp(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def tearDown(self):
        # Clean up temp files if stored
        if hasattr(self, "_tmpfile") and os.path.exists(self._tmpfile):
            os.unlink(self._tmpfile)

    def test_multi_date_journal(self):
        content = """\
## 01/01/2025
- Task A -- DONE
- Task B -- IN PROGRESS

## 02/01/2025
- Task C -- BACKLOG
"""
        path = self._write_temp(content)
        self._tmpfile = path
        result = parse_journal(path)
        self.assertIn(datetime(2025, 1, 1), result)
        self.assertIn(datetime(2025, 1, 2), result)
        self.assertEqual(len(result[datetime(2025, 1, 1)]), 2)
        self.assertEqual(len(result[datetime(2025, 1, 2)]), 1)

    def test_tasks_before_date_header(self):
        content = """\
- Orphan task -- BACKLOG
## 01/01/2025
- Normal task -- DONE
"""
        path = self._write_temp(content)
        self._tmpfile = path
        result = parse_journal(path)
        self.assertIn(None, result)
        self.assertEqual(len(result[None]), 1)
        self.assertEqual(result[None][0].title, "Orphan task")

    def test_note_continuation_lines(self):
        content = """\
## 01/01/2025
- Task -- DONE
: First note
: Second note
"""
        path = self._write_temp(content)
        self._tmpfile = path
        result = parse_journal(path)
        tasks = result[datetime(2025, 1, 1)]
        self.assertEqual(len(tasks[0].comments), 2)
        self.assertEqual(tasks[0].comments[0], "First note")
        self.assertEqual(tasks[0].comments[1], "Second note")

    def test_metadata_continuation_lines(self):
        content = """\
## 01/01/2025
- Task -- BACKLOG
-- due:25/12/2024
-- priority:HIGH
"""
        path = self._write_temp(content)
        self._tmpfile = path
        result = parse_journal(path)
        tasks = result[datetime(2025, 1, 1)]
        self.assertEqual(tasks[0].due_date, datetime(2024, 12, 25))
        self.assertEqual(tasks[0].priority, "HIGH")

    def test_subtasks_parsed(self):
        content = """\
## 01/01/2025
- Parent -- IN PROGRESS
+ Child 1 -- DONE
+ Child 2 -- BACKLOG
"""
        path = self._write_temp(content)
        self._tmpfile = path
        result = parse_journal(path)
        task = result[datetime(2025, 1, 1)][0]
        self.assertEqual(len(task.subtasks), 2)
        self.assertEqual(task.subtasks[0].title, "Child 1")
        self.assertEqual(task.subtasks[0].state, "DONE")
        self.assertEqual(task.subtasks[1].title, "Child 2")

    def test_source_line_tracking(self):
        content = """\
## 01/01/2025
- First -- DONE
- Second -- BACKLOG
"""
        path = self._write_temp(content)
        self._tmpfile = path
        result = parse_journal(path)
        tasks = result[datetime(2025, 1, 1)]
        self.assertEqual(tasks[0].source_line, 2)
        self.assertEqual(tasks[1].source_line, 3)

    def test_file_not_found_raises(self):
        with self.assertRaises(JournalFileNotFoundError):
            parse_journal("/nonexistent/path/journal.md")

    def test_state_continuation_line(self):
        content = """\
## 01/01/2025
- Task -- BACKLOG
-- DONE
"""
        path = self._write_temp(content)
        self._tmpfile = path
        result = parse_journal(path)
        task = result[datetime(2025, 1, 1)][0]
        self.assertEqual(task.state, "DONE")

    def test_empty_file(self):
        path = self._write_temp("")
        self._tmpfile = path
        result = parse_journal(path)
        self.assertEqual(result, {})


# ─── File Operations: write_journal ───────────────────────────────────────────


class TestWriteJournal(unittest.TestCase):
    def test_writes_content_correctly(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name

        try:
            write_journal(path, "## 01/01/2025\n- Task -- DONE\n")
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, "## 01/01/2025\n- Task -- DONE\n")
        finally:
            os.unlink(path)

    def test_no_tmp_file_left_behind(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name

        try:
            write_journal(path, "content")
            self.assertFalse(os.path.exists(path + ".tmp"))
        finally:
            os.unlink(path)

    def test_overwrites_existing_content(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("old content")
            path = f.name

        try:
            write_journal(path, "new content")
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, "new content")
        finally:
            os.unlink(path)


# ─── File Operations: _write_lines ───────────────────────────────────────────


class TestWriteLines(unittest.TestCase):
    def test_writes_lines_correctly(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name

        try:
            _write_lines(path, ["line 1\n", "line 2\n"])
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, "line 1\nline 2\n")
        finally:
            os.unlink(path)

    def test_no_tmp_file_left(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name

        try:
            _write_lines(path, ["hello\n"])
            self.assertFalse(os.path.exists(path + ".tmp"))
        finally:
            os.unlink(path)

    def test_empty_lines_list(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name

        try:
            _write_lines(path, [])
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, "")
        finally:
            os.unlink(path)


# ─── File Operations: add_task_to_file ────────────────────────────────────────


class TestAddTaskToFile(unittest.TestCase):
    def _make_journal(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_add_task_to_existing_date(self):
        path = self._make_journal("## 01/01/2025\n- Existing -- DONE\n")
        try:
            result = add_task_to_file(path, "New task", "BACKLOG", datetime(2025, 1, 1))
            self.assertTrue(result)
            content = open(path, encoding="utf-8").read()
            self.assertIn("New task", content)
            self.assertIn("BACKLOG", content)
        finally:
            os.unlink(path)

    def test_add_task_creates_date_section(self):
        path = self._make_journal("## 01/01/2025\n- Existing -- DONE\n")
        try:
            result = add_task_to_file(path, "Future task", "BACKLOG", datetime(2025, 6, 15))
            self.assertTrue(result)
            content = open(path, encoding="utf-8").read()
            self.assertIn("## 15/06/2025", content)
            self.assertIn("Future task", content)
        finally:
            os.unlink(path)

    def test_add_task_with_metadata(self):
        path = self._make_journal("## 01/01/2025\n")
        try:
            result = add_task_to_file(
                path, "Important", "BACKLOG", datetime(2025, 1, 1),
                due_date=datetime(2025, 3, 1), priority="HIGH", recurrence="weekly"
            )
            self.assertTrue(result)
            content = open(path, encoding="utf-8").read()
            self.assertIn("due:01/03/2025", content)
            self.assertIn("priority:HIGH", content)
            self.assertIn("recur:weekly", content)
        finally:
            os.unlink(path)

    def test_add_task_empty_title_fails(self):
        path = self._make_journal("## 01/01/2025\n")
        try:
            result = add_task_to_file(path, "   ", "BACKLOG", datetime(2025, 1, 1))
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── File Operations: update_task_state_in_file ───────────────────────────────


class TestUpdateTaskStateInFile(unittest.TestCase):
    def _setup_journal(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_updates_state(self):
        content = "## 01/01/2025\n- Buy groceries -- BACKLOG\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = update_task_state_in_file(path, task, "DONE")
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertIn("DONE", new_content)
            self.assertNotIn("BACKLOG", new_content)
        finally:
            os.unlink(path)

    def test_no_source_line_returns_false(self):
        task = Task(title="No line", state="BACKLOG", source_line=None)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = update_task_state_in_file(path, task, "DONE")
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_preserves_other_tasks(self):
        content = "## 01/01/2025\n- Task A -- BACKLOG\n- Task B -- IN PROGRESS\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task_a = parsed[datetime(2025, 1, 1)][0]
            update_task_state_in_file(path, task_a, "DONE")
            new_content = open(path, encoding="utf-8").read()
            self.assertIn("Task B -- IN PROGRESS", new_content)
        finally:
            os.unlink(path)


# ─── File Operations: add_note_to_task_in_file ────────────────────────────────


class TestAddNoteToTaskInFile(unittest.TestCase):
    def _setup_journal(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_adds_note(self):
        content = "## 01/01/2025\n- My task -- BACKLOG\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = add_note_to_task_in_file(path, task, "This is a note")
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertIn(": This is a note", new_content)
        finally:
            os.unlink(path)

    def test_empty_note_fails(self):
        content = "## 01/01/2025\n- My task -- BACKLOG\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = add_note_to_task_in_file(path, task, "   ")
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_no_source_line_fails(self):
        task = Task(title="T", state="BACKLOG", source_line=None)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = add_note_to_task_in_file(path, task, "note")
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_note_inserted_before_subtasks(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ Child -- DONE\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            add_note_to_task_in_file(path, task, "My note")
            lines = open(path, encoding="utf-8").readlines()
            # Note should come before subtask
            note_idx = next(i for i, l in enumerate(lines) if "My note" in l)
            sub_idx = next(i for i, l in enumerate(lines) if "Child" in l)
            self.assertLess(note_idx, sub_idx)
        finally:
            os.unlink(path)


# ─── File Operations: edit_task_title_in_file ─────────────────────────────────


class TestEditTaskTitleInFile(unittest.TestCase):
    def _setup_journal(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_renames_task(self):
        content = "## 01/01/2025\n- Old title -- DONE\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = edit_task_title_in_file(path, task, "New title")
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertIn("New title", new_content)
            self.assertNotIn("Old title", new_content)
        finally:
            os.unlink(path)

    def test_empty_title_fails(self):
        content = "## 01/01/2025\n- Task -- DONE\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = edit_task_title_in_file(path, task, "  ")
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_no_source_line_fails(self):
        task = Task(title="T", state="DONE", source_line=None)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = edit_task_title_in_file(path, task, "X")
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_preserves_state(self):
        content = "## 01/01/2025\n- Task -- IN PROGRESS\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            edit_task_title_in_file(path, task, "Renamed")
            new_content = open(path, encoding="utf-8").read()
            self.assertIn("IN PROGRESS", new_content)
        finally:
            os.unlink(path)


# ─── File Operations: delete_task_in_file ─────────────────────────────────────


class TestDeleteTaskInFile(unittest.TestCase):
    def _setup_journal(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_deletes_task(self):
        content = "## 01/01/2025\n- Task A -- DONE\n- Task B -- BACKLOG\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task_a = parsed[datetime(2025, 1, 1)][0]
            result = delete_task_in_file(path, task_a)
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertNotIn("Task A", new_content)
            self.assertIn("Task B", new_content)
        finally:
            os.unlink(path)

    def test_deletes_task_block_with_children(self):
        content = "## 01/01/2025\n- Parent -- DONE\n: A note\n+ Subtask -- DONE\n- Other -- BACKLOG\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            parent = parsed[datetime(2025, 1, 1)][0]
            result = delete_task_in_file(path, parent)
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertNotIn("Parent", new_content)
            self.assertNotIn("A note", new_content)
            self.assertNotIn("Subtask", new_content)
            self.assertIn("Other", new_content)
        finally:
            os.unlink(path)

    def test_no_source_line_fails(self):
        task = Task(title="T", state="DONE", source_line=None)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = delete_task_in_file(path, task)
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── File Operations: update_task_metadata_in_file ────────────────────────────


class TestUpdateTaskMetadataInFile(unittest.TestCase):
    def _setup_journal(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_set_due_date(self):
        content = "## 01/01/2025\n- Task -- BACKLOG\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = update_task_metadata_in_file(path, task, due_date=datetime(2025, 3, 15), priority=None)
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertIn("due:15/03/2025", new_content)
        finally:
            os.unlink(path)

    def test_set_priority(self):
        content = "## 01/01/2025\n- Task -- BACKLOG\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = update_task_metadata_in_file(path, task, due_date=None, priority="URGENT")
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertIn("priority:URGENT", new_content)
        finally:
            os.unlink(path)

    def test_remove_recurrence(self):
        content = "## 01/01/2025\n- Task -- BACKLOG -- recur:weekly\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            # recurrence="" means remove
            result = update_task_metadata_in_file(path, task, due_date=None, priority=None, recurrence="")
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertNotIn("recur", new_content)
        finally:
            os.unlink(path)

    def test_keep_existing_recurrence(self):
        content = "## 01/01/2025\n- Task -- BACKLOG -- recur:monthly\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            # recurrence=None means keep existing
            result = update_task_metadata_in_file(path, task, due_date=None, priority=None, recurrence=None)
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertIn("recur:monthly", new_content)
        finally:
            os.unlink(path)

    def test_set_new_recurrence(self):
        content = "## 01/01/2025\n- Task -- BACKLOG\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = update_task_metadata_in_file(path, task, due_date=None, priority=None, recurrence="daily")
            self.assertTrue(result)
            new_content = open(path, encoding="utf-8").read()
            self.assertIn("recur:daily", new_content)
        finally:
            os.unlink(path)

    def test_no_source_line_fails(self):
        task = Task(title="T", state="BACKLOG", source_line=None)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = update_task_metadata_in_file(path, task, due_date=None, priority=None)
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── Post-write hooks ─────────────────────────────────────────────────────────


class TestPostWriteHooks(unittest.TestCase):
    def setUp(self):
        # Save original hooks and clear
        self._original_hooks = _post_write_hooks.copy()
        _post_write_hooks.clear()

    def tearDown(self):
        # Restore original hooks
        _post_write_hooks.clear()
        _post_write_hooks.extend(self._original_hooks)

    def test_register_hook(self):
        called = []
        register_post_write_hook(lambda: called.append(True))
        self.assertEqual(len(_post_write_hooks), 1)

    def test_hook_called_after_write_journal(self):
        called = []
        register_post_write_hook(lambda: called.append("written"))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            write_journal(path, "test")
            self.assertEqual(called, ["written"])
        finally:
            os.unlink(path)

    def test_hook_called_after_write_lines(self):
        called = []
        register_post_write_hook(lambda: called.append("lines"))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            _write_lines(path, ["line\n"])
            self.assertEqual(called, ["lines"])
        finally:
            os.unlink(path)

    def test_multiple_hooks_called(self):
        results = []
        register_post_write_hook(lambda: results.append("first"))
        register_post_write_hook(lambda: results.append("second"))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            write_journal(path, "x")
            self.assertEqual(results, ["first", "second"])
        finally:
            os.unlink(path)

    def test_hook_exception_does_not_crash(self):
        def bad_hook():
            raise RuntimeError("hook error")

        called = []
        register_post_write_hook(bad_hook)
        register_post_write_hook(lambda: called.append("ok"))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            write_journal(path, "x")
            # Second hook should still be called
            self.assertEqual(called, ["ok"])
        finally:
            os.unlink(path)


# ─── _read_lines ──────────────────────────────────────────────────────────────


class TestReadLines(unittest.TestCase):
    def test_reads_lines_with_newlines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")
            path = f.name
        try:
            lines = _read_lines(path)
            self.assertEqual(lines, ["line1\n", "line2\n", "line3\n"])
        finally:
            os.unlink(path)

    def test_reads_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            path = f.name
        try:
            lines = _read_lines(path)
            self.assertEqual(lines, [])
        finally:
            os.unlink(path)


# ─── Integration: round-trip parse → modify → re-parse ───────────────────────


class TestIntegrationRoundTrip(unittest.TestCase):
    def _setup_journal(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_full_workflow(self):
        """Add task, update state, add note, rename, delete — verify each step."""
        content = "## 05/06/2025\n- Existing task -- IN PROGRESS\n"
        path = self._setup_journal(content)
        try:
            # Add a task
            add_task_to_file(path, "New task", "BACKLOG", datetime(2025, 6, 5))
            parsed = parse_journal(path)
            tasks = parsed[datetime(2025, 6, 5)]
            self.assertEqual(len(tasks), 2)

            # Find new task
            new_task = next(t for t in tasks if t.title == "New task")

            # Update state
            update_task_state_in_file(path, new_task, "DONE")
            parsed = parse_journal(path)
            updated = next(t for t in parsed[datetime(2025, 6, 5)] if t.title == "New task")
            self.assertEqual(updated.state, "DONE")

            # Add note
            add_note_to_task_in_file(path, updated, "Completed successfully")
            parsed = parse_journal(path)
            updated = next(t for t in parsed[datetime(2025, 6, 5)] if t.title == "New task")
            self.assertIn("Completed successfully", updated.comments)

            # Rename
            edit_task_title_in_file(path, updated, "Renamed task")
            parsed = parse_journal(path)
            renamed = next(t for t in parsed[datetime(2025, 6, 5)] if t.title == "Renamed task")
            self.assertIsNotNone(renamed)

            # Delete
            delete_task_in_file(path, renamed)
            parsed = parse_journal(path)
            titles = [t.title for t in parsed[datetime(2025, 6, 5)]]
            self.assertNotIn("Renamed task", titles)
            self.assertIn("Existing task", titles)
        finally:
            os.unlink(path)

    def test_metadata_roundtrip(self):
        """Set metadata, re-parse, verify it persists correctly."""
        content = "## 01/06/2025\n- Important meeting -- BACKLOG\n"
        path = self._setup_journal(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 6, 1)][0]

            update_task_metadata_in_file(
                path, task,
                due_date=datetime(2025, 7, 1),
                priority="HIGH",
                recurrence="weekly",
            )

            parsed = parse_journal(path)
            task = parsed[datetime(2025, 6, 1)][0]
            self.assertEqual(task.due_date, datetime(2025, 7, 1))
            self.assertEqual(task.priority, "HIGH")
            self.assertEqual(task.recurrence, "weekly")
        finally:
            os.unlink(path)


# ─── Jira key metadata ────────────────────────────────────────────────────────


class TestJiraKeyMetadata(unittest.TestCase):
    """Tests for jira_key parsing, rendering, and round-tripping through file ops."""

    def test_parse_jira_key(self):
        """parse_task_line extracts jira_key from metadata."""
        task = parse_task_line("- Fix login -- IN PROGRESS -- jira:BD-123")
        self.assertIsNotNone(task)
        self.assertEqual(task.jira_key, "BD-123")
        self.assertEqual(task.title, "Fix login")
        self.assertEqual(task.state, "IN PROGRESS")

    def test_parse_jira_key_with_other_metadata(self):
        """jira_key parsed alongside due and priority."""
        task = parse_task_line("- Deploy -- TODO -- due:15/01/2025 -- priority:high -- jira:PROJ-99")
        self.assertIsNotNone(task)
        self.assertEqual(task.jira_key, "PROJ-99")
        self.assertEqual(task.priority, "HIGH")

    def test_parse_no_jira_key(self):
        """Tasks without jira metadata have jira_key=None."""
        task = parse_task_line("- Normal task -- TODO")
        self.assertIsNotNone(task)
        self.assertIsNone(task.jira_key)

    def test_add_task_with_jira_key(self):
        """add_task_to_file writes jira: metadata."""
        fd, path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        try:
            with open(path, "w") as f:
                f.write("# 09/06/2025\n\n")
            result = add_task_to_file(
                path, "Imported task", jira_key="TEAM-42",
                target_date=datetime(2025, 6, 9),
            )
            self.assertTrue(result)
            with open(path) as f:
                content = f.read()
            self.assertIn("jira:TEAM-42", content)
            self.assertIn("Imported task", content)
        finally:
            os.unlink(path)

    def test_update_metadata_preserves_jira_key(self):
        """update_task_metadata_in_file preserves existing jira_key."""
        fd, path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        try:
            with open(path, "w") as f:
                f.write("# 09/06/2025\n\n- Fix bug -- TODO -- jira:BD-55\n")
            tasks = parse_journal(path)
            task_list = list(tasks.values())[0]
            task = task_list[0]
            self.assertEqual(task.jira_key, "BD-55")
            # Update priority — jira_key should survive
            result = update_task_metadata_in_file(path, task, None, "high")
            self.assertTrue(result)
            with open(path) as f:
                content = f.read()
            self.assertIn("jira:BD-55", content)
            self.assertIn("priority:high", content)
        finally:
            os.unlink(path)

    def test_update_metadata_adds_jira_key(self):
        """Setting task.jira_key before update_task_metadata_in_file writes it."""
        fd, path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        try:
            with open(path, "w") as f:
                f.write("# 09/06/2025\n\n- Plain task -- TODO\n")
            tasks = parse_journal(path)
            task_list = list(tasks.values())[0]
            task = task_list[0]
            self.assertIsNone(task.jira_key)
            # Link it
            task.jira_key = "NEW-1"
            result = update_task_metadata_in_file(path, task, task.due_date, task.priority)
            self.assertTrue(result)
            with open(path) as f:
                content = f.read()
            self.assertIn("jira:NEW-1", content)
        finally:
            os.unlink(path)

    def test_update_metadata_removes_jira_key(self):
        """Setting task.jira_key=None before update removes jira: metadata."""
        fd, path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        try:
            with open(path, "w") as f:
                f.write("# 09/06/2025\n\n- Linked -- IN PROGRESS -- jira:OLD-9\n")
            tasks = parse_journal(path)
            task_list = list(tasks.values())[0]
            task = task_list[0]
            self.assertEqual(task.jira_key, "OLD-9")
            # Unlink
            task.jira_key = None
            result = update_task_metadata_in_file(path, task, task.due_date, task.priority)
            self.assertTrue(result)
            with open(path) as f:
                content = f.read()
            self.assertNotIn("jira:", content)
            self.assertIn("Linked -- IN PROGRESS", content)
        finally:
            os.unlink(path)

    def test_edit_title_preserves_jira_key(self):
        """edit_task_title_in_file preserves jira_key."""
        fd, path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        try:
            with open(path, "w") as f:
                f.write("# 09/06/2025\n\n- Old title -- IN PROGRESS -- jira:XX-1\n")
            tasks = parse_journal(path)
            task_list = list(tasks.values())[0]
            task = task_list[0]
            result = edit_task_title_in_file(path, task, "New title")
            self.assertTrue(result)
            with open(path) as f:
                content = f.read()
            self.assertIn("New title", content)
            self.assertIn("jira:XX-1", content)
            self.assertNotIn("Old title", content)
        finally:
            os.unlink(path)


# ─── Error Classes ──────────────────────────────────────────────────────────


class TestJournalErrors(unittest.TestCase):
    """Test journal error hierarchy."""

    def test_journal_error_is_exception(self):
        self.assertTrue(issubclass(JournalError, Exception))

    def test_file_not_found_error(self):
        self.assertTrue(issubclass(JournalFileNotFoundError, JournalError))

    def test_read_error(self):
        self.assertTrue(issubclass(JournalReadError, JournalError))

    def test_file_not_found_message(self):
        err = JournalFileNotFoundError("test message")
        self.assertEqual(str(err), "test message")


# ─── Private Helpers: _parse_priority_value ────────────────────────────────


class TestParsePriorityValue(unittest.TestCase):
    def test_valid_priority(self):
        self.assertEqual(_parse_priority_value("HIGH"), "HIGH")
        self.assertEqual(_parse_priority_value("MEDIUM"), "MEDIUM")
        self.assertEqual(_parse_priority_value("LOW"), "LOW")
        self.assertEqual(_parse_priority_value("URGENT"), "URGENT")

    def test_case_insensitive(self):
        self.assertEqual(_parse_priority_value("high"), "HIGH")
        self.assertEqual(_parse_priority_value("Medium"), "MEDIUM")

    def test_alias_h(self):
        self.assertEqual(_parse_priority_value("H"), "HIGH")

    def test_alias_m(self):
        self.assertEqual(_parse_priority_value("M"), "MEDIUM")

    def test_alias_l(self):
        self.assertEqual(_parse_priority_value("L"), "LOW")

    def test_invalid_returns_none(self):
        self.assertIsNone(_parse_priority_value("INVALID"))
        self.assertIsNone(_parse_priority_value(""))
        self.assertIsNone(_parse_priority_value("  "))


# ─── Private Helpers: _parse_due_value ──────────────────────────────────────


class TestParseDueValue(unittest.TestCase):
    def test_valid_full_year(self):
        result = _parse_due_value("25/12/2024")
        self.assertEqual(result, datetime(2024, 12, 25))

    def test_valid_two_digit_year(self):
        result = _parse_due_value("01/01/26")
        self.assertEqual(result, datetime(2026, 1, 1))

    def test_invalid_format(self):
        self.assertIsNone(_parse_due_value("25-12-2024"))

    def test_invalid_date(self):
        self.assertIsNone(_parse_due_value("32/01/2024"))

    def test_empty_string(self):
        self.assertIsNone(_parse_due_value(""))


# ─── Private Helpers: _parse_recurrence_value ────────────────────────────────


class TestParseRecurrenceValue(unittest.TestCase):
    def test_valid_recurrence(self):
        self.assertEqual(_parse_recurrence_value("daily"), "daily")
        self.assertEqual(_parse_recurrence_value("weekly"), "weekly")
        self.assertEqual(_parse_recurrence_value("monthly"), "monthly")
        self.assertEqual(_parse_recurrence_value("yearly"), "yearly")

    def test_case_insensitive(self):
        self.assertEqual(_parse_recurrence_value("WEEKLY"), "weekly")
        self.assertEqual(_parse_recurrence_value("Monthly"), "monthly")

    def test_alias_d(self):
        self.assertEqual(_parse_recurrence_value("D"), "daily")

    def test_alias_w(self):
        self.assertEqual(_parse_recurrence_value("W"), "weekly")

    def test_alias_m(self):
        self.assertEqual(_parse_recurrence_value("M"), "monthly")

    def test_alias_y(self):
        self.assertEqual(_parse_recurrence_value("Y"), "yearly")

    def test_invalid_returns_none(self):
        self.assertIsNone(_parse_recurrence_value("INVALID"))
        self.assertIsNone(_parse_recurrence_value(""))


# ─── Private Helpers: _apply_task_metadata ──────────────────────────────────


class TestApplyTaskMetadata(unittest.TestCase):
    def _make_task(self):
        return Task(title="T", state="BACKLOG")

    def test_due_date(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "due:25/12/2024")
        self.assertTrue(result)
        self.assertEqual(task.due_date, datetime(2024, 12, 25))

    def test_due_short(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "d:01/01/2025")
        self.assertTrue(result)
        self.assertEqual(task.due_date, datetime(2025, 1, 1))

    def test_priority(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "priority:HIGH")
        self.assertTrue(result)
        self.assertEqual(task.priority, "HIGH")

    def test_priority_short(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "p:LOW")
        self.assertTrue(result)
        self.assertEqual(task.priority, "LOW")

    def test_recurrence(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "recur:weekly")
        self.assertTrue(result)
        self.assertEqual(task.recurrence, "weekly")

    def test_recurrence_short(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "r:daily")
        self.assertTrue(result)
        self.assertEqual(task.recurrence, "daily")

    def test_time_spent(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "spent:30m")
        self.assertTrue(result)
        self.assertEqual(task.time_spent, 30)

    def test_time_spent_accumulates(self):
        task = self._make_task()
        task.time_spent = 60
        result = _apply_task_metadata(task, "spent:30m")
        self.assertTrue(result)
        self.assertEqual(task.time_spent, 90)

    def test_blockedby(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "blockedby:Other Task")
        self.assertTrue(result)
        self.assertIn("Other Task", task.blocked_by)

    def test_blocks(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "blocks:Dependent Task")
        self.assertTrue(result)
        self.assertIn("Dependent Task", task.blocks)

    def test_jira_key(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "jira:PROJ-123")
        self.assertTrue(result)
        self.assertEqual(task.jira_key, "PROJ-123")

    def test_notes(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "notes:note1.md,note2.md")
        self.assertTrue(result)
        self.assertEqual(task.linked_notes, ["note1.md", "note2.md"])

    def test_unknown_chunk_returns_false(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "unknown:value")
        self.assertFalse(result)

    def test_invalid_due_returns_false(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "due:invalid")
        self.assertFalse(result)

    def test_invalid_priority_returns_false(self):
        task = self._make_task()
        result = _apply_task_metadata(task, "priority:INVALID")
        self.assertFalse(result)


# ─── Private Helpers: _apply_subtask_metadata ────────────────────────────────


class TestApplySubtaskMetadata(unittest.TestCase):
    def _make_subtask(self):
        return Subtask(title="S", state="BACKLOG")

    def test_due_date(self):
        sub = self._make_subtask()
        result = _apply_subtask_metadata(sub, "due:25/12/2024")
        self.assertTrue(result)
        self.assertEqual(sub.due_date, datetime(2024, 12, 25))

    def test_priority(self):
        sub = self._make_subtask()
        result = _apply_subtask_metadata(sub, "priority:HIGH")
        self.assertTrue(result)
        self.assertEqual(sub.priority, "HIGH")

    def test_notes(self):
        sub = self._make_subtask()
        result = _apply_subtask_metadata(sub, "notes:note1.md")
        self.assertTrue(result)
        self.assertEqual(sub.linked_notes, ["note1.md"])

    def test_unknown_returns_false(self):
        sub = self._make_subtask()
        result = _apply_subtask_metadata(sub, "unknown:x")
        self.assertFalse(result)


# ─── Private Helpers: _render_task_line ──────────────────────────────────────


class TestRenderTaskLine(unittest.TestCase):
    def test_basic(self):
        result = _render_task_line("Task", "BACKLOG", None, None)
        self.assertEqual(result.strip(), "- Task -- BACKLOG")

    def test_with_due_and_priority(self):
        result = _render_task_line("Task", "BACKLOG", datetime(2025, 1, 1), "HIGH")
        self.assertIn("due:01/01/2025", result)
        self.assertIn("priority:HIGH", result)

    def test_with_recurrence(self):
        result = _render_task_line("Task", "BACKLOG", None, None, recurrence="weekly")
        self.assertIn("recur:weekly", result)

    def test_with_jira_key(self):
        result = _render_task_line("Task", "DONE", None, None, jira_key="PROJ-1")
        self.assertIn("jira:PROJ-1", result)

    def test_with_linked_notes(self):
        result = _render_task_line("Task", "BACKLOG", None, None, linked_notes=["a.md", "b.md"])
        self.assertIn("notes:a.md,b.md", result)

    def test_with_indent(self):
        result = _render_task_line("Task", "BACKLOG", None, None, indent="    ")
        self.assertTrue(result.startswith("    -"))

    def test_ends_with_newline(self):
        result = _render_task_line("T", "DONE", None, None)
        self.assertTrue(result.endswith("\n"))


# ─── Private Helpers: _render_subtask_line ───────────────────────────────────


class TestRenderSubtaskLine(unittest.TestCase):
    def test_basic(self):
        result = _render_subtask_line("Sub", "BACKLOG")
        self.assertEqual(result.strip(), "+ Sub -- BACKLOG")

    def test_with_due(self):
        result = _render_subtask_line("Sub", "BACKLOG", due_date=datetime(2025, 6, 1))
        self.assertIn("due:01/06/2025", result)

    def test_with_priority(self):
        result = _render_subtask_line("Sub", "DONE", priority="HIGH")
        self.assertIn("priority:HIGH", result)

    def test_with_indent(self):
        result = _render_subtask_line("Sub", "BACKLOG", indent="        ")
        self.assertTrue(result.startswith("        +"))

    def test_ends_with_newline(self):
        result = _render_subtask_line("S", "DONE")
        self.assertTrue(result.endswith("\n"))


# ─── Private Helpers: _task_line_indent ──────────────────────────────────────


class TestTaskLineIndent(unittest.TestCase):
    def test_no_indent(self):
        self.assertEqual(_task_line_indent("- Task -- DONE", "-"), "")

    def test_four_spaces(self):
        self.assertEqual(_task_line_indent("    - Task -- DONE", "-"), "    ")

    def test_tab_indent(self):
        self.assertEqual(_task_line_indent("\t- Task", "-"), "\t")

    def test_subtask_indent(self):
        self.assertEqual(_task_line_indent("    + Sub -- DONE", "+"), "    ")


# ─── Private Helpers: _find_task_block_bounds ────────────────────────────────


class TestFindTaskBlockBounds(unittest.TestCase):
    def test_simple_task_block(self):
        lines = [
            "## 01/01/2025\n",
            "- Task A -- DONE\n",
            "- Task B -- BACKLOG\n",
        ]
        task = Task(title="Task A", state="DONE", source_line=2)
        result = _find_task_block_bounds(lines, task)
        self.assertEqual(result, (1, 2))

    def test_task_with_children(self):
        lines = [
            "## 01/01/2025\n",
            "- Parent -- IN PROGRESS\n",
            "+ Child -- DONE\n",
            ": A note\n",
            "- Next task -- BACKLOG\n",
        ]
        task = Task(title="Parent", state="IN PROGRESS", source_line=2)
        result = _find_task_block_bounds(lines, task)
        self.assertEqual(result, (1, 4))

    def test_no_source_line(self):
        task = Task(title="T", state="BACKLOG", source_line=None)
        self.assertIsNone(_find_task_block_bounds([], task))

    def test_out_of_bounds(self):
        task = Task(title="T", state="BACKLOG", source_line=10)
        self.assertIsNone(_find_task_block_bounds(["line\n"], task))

    def test_task_until_date_header(self):
        lines = [
            "- Task A -- DONE\n",
            "## 02/01/2025\n",
        ]
        task = Task(title="Task A", state="DONE", source_line=1)
        result = _find_task_block_bounds(lines, task)
        self.assertEqual(result, (0, 1))


# ─── Private Helpers: _find_parent_task_start ────────────────────────────────


class TestFindParentTaskStart(unittest.TestCase):
    def test_finds_parent_above_subtask(self):
        lines = [
            "- Parent -- BACKLOG\n",
            "    + Child -- DONE\n",
        ]
        sub = Subtask(title="Child", state="DONE", source_line=2)
        result = _find_parent_task_start(lines, sub)
        self.assertEqual(result, 0)

    def test_no_source_line(self):
        sub = Subtask(title="S", state="BACKLOG", source_line=None)
        self.assertIsNone(_find_parent_task_start([], sub))

    def test_no_parent_above(self):
        lines = [
            "## 01/01/2025\n",
            "    + Child -- DONE\n",
        ]
        sub = Subtask(title="Child", state="DONE", source_line=2)
        result = _find_parent_task_start(lines, sub)
        self.assertIsNone(result)


# ─── Private Helpers: _find_note_line_index ──────────────────────────────────


class TestFindNoteLineIndex(unittest.TestCase):
    def test_finds_first_note(self):
        lines = [
            "- Task -- BACKLOG\n",
            ": First note\n",
            ": Second note\n",
        ]
        task = Task(title="Task", state="BACKLOG", source_line=1)
        result = _find_note_line_index(lines, task, 0)
        self.assertEqual(result, 1)

    def test_finds_second_note(self):
        lines = [
            "- Task -- BACKLOG\n",
            ": First note\n",
            ": Second note\n",
        ]
        task = Task(title="Task", state="BACKLOG", source_line=1)
        result = _find_note_line_index(lines, task, 1)
        self.assertEqual(result, 2)

    def test_note_index_out_of_range(self):
        lines = [
            "- Task -- BACKLOG\n",
            ": First note\n",
        ]
        task = Task(title="Task", state="BACKLOG", source_line=1)
        self.assertIsNone(_find_note_line_index(lines, task, 5))


# ─── Private Helpers: _render_task_block ─────────────────────────────────────


class TestRenderTaskBlock(unittest.TestCase):
    def test_simple_task(self):
        task = Task(title="Task", state="BACKLOG", source_line=1)
        result = _render_task_block(task)
        self.assertEqual(len(result), 1)
        self.assertIn("- Task -- BACKLOG", result[0])

    def test_with_notes(self):
        task = Task(title="Task", state="DONE", source_line=1)
        task.comments = ["First note", "Second note"]
        result = _render_task_block(task)
        self.assertEqual(len(result), 3)
        self.assertIn(": First note", result[1])
        self.assertIn(": Second note", result[2])

    def test_with_subtasks(self):
        task = Task(title="Parent", state="BACKLOG", source_line=1)
        st = Subtask(title="Child", state="DONE")
        task.subtasks = [st]
        result = _render_task_block(task)
        self.assertEqual(len(result), 2)
        self.assertIn("+ Child -- DONE", result[1])

    def test_state_override(self):
        task = Task(title="Task", state="BACKLOG", source_line=1)
        result = _render_task_block(task, state_override="DONE")
        self.assertIn("DONE", result[0])
        self.assertNotIn("BACKLOG", result[0])


# ─── Private Helpers: _insert_task_block ─────────────────────────────────────


class TestInsertTaskBlock(unittest.TestCase):
    def test_insert_into_existing_section(self):
        lines = ["## 01/01/2025\n", "- Existing -- DONE\n"]
        block = ["- New -- BACKLOG\n"]
        result = _insert_task_block(lines, block, datetime(2025, 1, 1))
        self.assertIn("- New -- BACKLOG\n", result)
        self.assertEqual(result.index("- New -- BACKLOG\n"), 2)

    def test_creates_new_section(self):
        lines = ["## 01/01/2025\n"]
        block = ["- New -- BACKLOG\n"]
        result = _insert_task_block(lines, block, datetime(2025, 6, 15))
        self.assertTrue(any("## 15/06/2025" in l for l in result))
        self.assertTrue(any("New -- BACKLOG" in l for l in result))

    def test_insert_before_first_date(self):
        lines = ["## 01/01/2025\n"]
        block = ["- Orphan -- BACKLOG\n"]
        result = _insert_task_block(lines, block, None)
        self.assertEqual(result[0], "- Orphan -- BACKLOG\n")


# ─── CRUD: add_subtask_to_file ───────────────────────────────────────────────


class TestAddSubtaskToFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_add_subtask_after_parent(self):
        path = self._setup("## 01/01/2025\n- Parent -- BACKLOG\n")
        try:
            result = add_subtask_to_file(path, "Parent", "Child", "DONE")
            self.assertTrue(result)
            content = open(path).read()
            self.assertIn("+ Child -- DONE", content)
        finally:
            os.unlink(path)

    def test_parent_not_found(self):
        path = self._setup("## 01/01/2025\n- Other -- BACKLOG\n")
        try:
            result = add_subtask_to_file(path, "Nonexistent", "Child", "DONE")
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_add_subtask_after_existing_children(self):
        path = self._setup("## 01/01/2025\n- Parent -- BACKLOG\n+ Existing -- DONE\n")
        try:
            result = add_subtask_to_file(path, "Parent", "New child", "BACKLOG")
            self.assertTrue(result)
            lines = open(path).read().strip().split("\n")
            # New child should come after existing
            existing_idx = next(i for i, l in enumerate(lines) if "Existing" in l)
            new_idx = next(i for i, l in enumerate(lines) if "New child" in l)
            self.assertGreater(new_idx, existing_idx)
        finally:
            os.unlink(path)


# ─── CRUD: add_subtask_to_task ───────────────────────────────────────────────


class TestAddSubtaskToTask(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_add_subtask(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = add_subtask_to_task(path, task, "Child", "DONE")
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertIn("+ Child -- DONE", new_content)
        finally:
            os.unlink(path)

    def test_no_source_line_fails(self):
        task = Task(title="T", state="BACKLOG", source_line=None)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = add_subtask_to_task(path, task, "Child", "DONE")
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── CRUD: edit_subtask_title_in_file ────────────────────────────────────────


class TestEditSubtaskTitleInFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_renames_subtask(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ Old name -- DONE\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            sub = task.subtasks[0]
            result = edit_subtask_title_in_file(path, sub, "New name")
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertIn("New name", new_content)
            self.assertNotIn("Old name", new_content)
        finally:
            os.unlink(path)

    def test_empty_title_fails(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ Sub -- DONE\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            sub = parsed[datetime(2025, 1, 1)][0].subtasks[0]
            result = edit_subtask_title_in_file(path, sub, "  ")
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_no_source_line_fails(self):
        sub = Subtask(title="S", state="DONE", source_line=None)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = edit_subtask_title_in_file(path, sub, "X")
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── CRUD: delete_subtask_in_file ────────────────────────────────────────────


class TestDeleteSubtaskInFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_deletes_subtask(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ Child -- DONE\n+ Other -- BACKLOG\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            child = task.subtasks[0]
            result = delete_subtask_in_file(path, child)
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertNotIn("Child", new_content)
            self.assertIn("Other", new_content)
        finally:
            os.unlink(path)

    def test_no_source_line_fails(self):
        sub = Subtask(title="S", state="DONE", source_line=None)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = delete_subtask_in_file(path, sub)
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── CRUD: update_subtask_metadata_in_file ───────────────────────────────────


class TestUpdateSubtaskMetadataInFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_set_due_date(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ Sub -- BACKLOG\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            sub = parsed[datetime(2025, 1, 1)][0].subtasks[0]
            result = update_subtask_metadata_in_file(path, sub, due_date=datetime(2025, 3, 15))
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertIn("due:15/03/2025", new_content)
        finally:
            os.unlink(path)

    def test_set_priority(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ Sub -- BACKLOG\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            sub = parsed[datetime(2025, 1, 1)][0].subtasks[0]
            result = update_subtask_metadata_in_file(path, sub, priority="HIGH")
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertIn("priority:HIGH", new_content)
        finally:
            os.unlink(path)

    def test_clear_due(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ Sub -- BACKLOG -- due:01/06/2025\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            sub = parsed[datetime(2025, 1, 1)][0].subtasks[0]
            result = update_subtask_metadata_in_file(path, sub, clear_due=True)
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertNotIn("due:", new_content)
        finally:
            os.unlink(path)

    def test_no_source_line_fails(self):
        sub = Subtask(title="S", state="BACKLOG", source_line=None)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = update_subtask_metadata_in_file(path, sub)
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── CRUD: add_note_to_subtask_in_file ───────────────────────────────────────


class TestAddNoteToSubtaskInFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_add_note_to_subtask(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ Sub -- DONE\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            sub = parsed[datetime(2025, 1, 1)][0].subtasks[0]
            result = add_note_to_subtask_in_file(path, sub, "Note text")
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertIn(": Note text", new_content)
        finally:
            os.unlink(path)

    def test_empty_note_fails(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ Sub -- DONE\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            sub = parsed[datetime(2025, 1, 1)][0].subtasks[0]
            result = add_note_to_subtask_in_file(path, sub, "  ")
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── CRUD: delete_note_in_file ───────────────────────────────────────────────


class TestDeleteNoteInFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_deletes_note(self):
        content = "## 01/01/2025\n- Task -- DONE\n: Note 1\n: Note 2\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = delete_note_in_file(path, task, 0)
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertNotIn("Note 1", new_content)
            self.assertIn("Note 2", new_content)
        finally:
            os.unlink(path)

    def test_invalid_index_fails(self):
        content = "## 01/01/2025\n- Task -- DONE\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = delete_note_in_file(path, task, 0)
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── CRUD: edit_note_in_file ─────────────────────────────────────────────────


class TestEditNoteInFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_edits_note(self):
        content = "## 01/01/2025\n- Task -- DONE\n: Old text\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = edit_note_in_file(path, task, 0, "New text")
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertIn("New text", new_content)
            self.assertNotIn("Old text", new_content)
        finally:
            os.unlink(path)

    def test_empty_note_fails(self):
        content = "## 01/01/2025\n- Task -- DONE\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = edit_note_in_file(path, task, 0, "  ")
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── CRUD: move_task_to_date_in_file ─────────────────────────────────────────


class TestMoveTaskToDateInFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_moves_task_with_children(self):
        content = "## 01/01/2025\n- Task -- DONE\n+ Sub -- DONE\n## 02/01/2025\n- Other -- BACKLOG\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = move_task_to_date_in_file(path, task, datetime(2025, 6, 1))
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertIn("## 01/06/2025\n- Task -- DONE", new_content)
            self.assertIn("+ Sub -- DONE", new_content)
        finally:
            os.unlink(path)


# ─── CRUD: duplicate_task_in_file ────────────────────────────────────────────


class TestDuplicateTaskInFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_duplicates_task(self):
        content = "## 01/01/2025\n- Task -- DONE\n: Note\n+ Sub -- DONE\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = duplicate_task_in_file(path, task, datetime(2025, 6, 1))
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertIn("## 01/06/2025", new_content)
            self.assertIn("+ Sub -- DONE", new_content)
        finally:
            os.unlink(path)


# ─── CRUD: mark_all_subtasks_done_in_file ────────────────────────────────────


class TestMarkAllSubtasksDoneInFile(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_marks_all_subtasks_done(self):
        content = "## 01/01/2025\n- Parent -- IN PROGRESS\n+ Child 1 -- BACKLOG\n+ Child 2 -- WAITING\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = mark_all_subtasks_done_in_file(path, task)
            self.assertTrue(result)
            new_content = open(path).read()
            self.assertIn("+ Child 1 -- DONE", new_content)
            self.assertIn("+ Child 2 -- DONE", new_content)
        finally:
            os.unlink(path)

    def test_no_subtasks_returns_false(self):
        content = "## 01/01/2025\n- Task -- DONE\n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            task = parsed[datetime(2025, 1, 1)][0]
            result = mark_all_subtasks_done_in_file(path, task)
            self.assertFalse(result)
        finally:
            os.unlink(path)


# ─── Snapshot: read/restore ──────────────────────────────────────────────────


class TestJournalSnapshot(unittest.TestCase):
    def test_read_snapshot(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("hello")
            path = f.name
        try:
            result = read_journal_snapshot(path)
            self.assertEqual(result, "hello")
        finally:
            os.unlink(path)

    def test_read_nonexistent_returns_none(self):
        result = read_journal_snapshot("/nonexistent/file.md")
        self.assertIsNone(result)

    def test_restore_snapshot(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("old")
            path = f.name
        try:
            result = restore_journal_snapshot(path, "new content")
            self.assertTrue(result)
            self.assertEqual(open(path).read(), "new content")
        finally:
            os.unlink(path)


# ─── Lint ────────────────────────────────────────────────────────────────────


class TestLintJournal(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_clean_journal_no_findings(self):
        content = "## 01/01/2025\n- Task -- DONE\n"
        path = self._setup(content)
        try:
            result = lint_journal(path)
            self.assertEqual(result, [])
        finally:
            os.unlink(path)

    def test_invalid_date_header(self):
        content = "## invalid\n- Task -- DONE\n"
        path = self._setup(content)
        try:
            result = lint_journal(path)
            self.assertTrue(any("invalid date header" in f for f in result))
        finally:
            os.unlink(path)

    def test_note_without_parent(self):
        content = ": orphan note\n"
        path = self._setup(content)
        try:
            result = lint_journal(path)
            self.assertTrue(any("note without parent" in f for f in result))
        finally:
            os.unlink(path)

    def test_subtask_without_parent(self):
        content = "## 01/01/2025\n+ orphan subtask -- DONE\n"
        path = self._setup(content)
        try:
            result = lint_journal(path)
            self.assertTrue(any("subtask without parent" in f for f in result))
        finally:
            os.unlink(path)

    def test_invalid_task_format(self):
        content = "## 01/01/2025\n-  \n"
        path = self._setup(content)
        try:
            result = lint_journal(path)
            self.assertTrue(any("invalid task format" in f for f in result))
        finally:
            os.unlink(path)

    def test_invalid_subtask_format(self):
        content = "## 01/01/2025\n- Parent -- BACKLOG\n+ \n"
        path = self._setup(content)
        try:
            parsed = parse_journal(path)
            result = lint_journal(path)
            self.assertTrue(any("invalid subtask format" in f for f in result))
        finally:
            os.unlink(path)

    def test_valid_state_no_finding(self):
        content = "## 01/01/2025\n- Task -- DONE\n"
        path = self._setup(content)
        try:
            result = lint_journal(path)
            self.assertEqual(result, [])
        finally:
            os.unlink(path)

    def test_unreadable_file(self):
        result = lint_journal("/nonexistent/path.md")
        self.assertTrue(any("Could not read journal" in f for f in result))


# ─── update_dependency_references ────────────────────────────────────────────


class TestUpdateDependencyReferences(unittest.TestCase):
    def _setup(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_updates_blockedby_reference(self):
        content = "## 01/01/2025\n- Task -- BACKLOG -- blockedby:Old Name\n"
        path = self._setup(content)
        try:
            update_dependency_references(path, "Old Name", "New Name")
            new_content = open(path).read()
            self.assertIn("blockedby:New Name", new_content)
            self.assertNotIn("blockedby:Old Name", new_content)
        finally:
            os.unlink(path)

    def test_updates_blocks_reference(self):
        content = "## 01/01/2025\n- Task -- BACKLOG -- blocks:Old Name\n"
        path = self._setup(content)
        try:
            update_dependency_references(path, "Old Name", "New Name")
            new_content = open(path).read()
            self.assertIn("blocks:New Name", new_content)
        finally:
            os.unlink(path)

    def test_no_change_if_titles_equal(self):
        content = "## 01/01/2025\n- Task -- BACKLOG\n"
        path = self._setup(content)
        try:
            # Should not crash or change anything
            update_dependency_references(path, "Task", "Task")
            self.assertEqual(open(path).read(), content)
        finally:
            os.unlink(path)

    def test_empty_old_title_returns_early(self):
        content = "## 01/01/2025\n- Task -- BACKLOG\n"
        path = self._setup(content)
        try:
            update_dependency_references(path, "", "New")
            self.assertEqual(open(path).read(), content)
        finally:
            os.unlink(path)


# ─── archive_finished_tasks_in_file ──────────────────────────────────────────


class TestArchiveFinishedTasks(unittest.TestCase):
    def test_archives_finished_tasks(self):
        journal_content = "## 01/01/2025\n- Done task -- DONE\n- Active task -- BACKLOG\n"
        j = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        j.write(journal_content)
        j.close()
        archive = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        archive.close()
        try:
            result = archive_finished_tasks_in_file(j.name, archive.name)
            self.assertEqual(result, 1)
            journal_after = open(j.name).read()
            self.assertNotIn("Done task", journal_after)
            self.assertIn("Active task", journal_after)
            archive_content = open(archive.name).read()
            self.assertIn("Done task", archive_content)
        finally:
            os.unlink(j.name)
            os.unlink(archive.name)

    def test_no_finished_tasks_returns_zero(self):
        j = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        j.write("## 01/01/2025\n- Active -- IN PROGRESS\n")
        j.close()
        archive = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        archive.close()
        try:
            result = archive_finished_tasks_in_file(j.name, archive.name)
            self.assertEqual(result, 0)
        finally:
            os.unlink(j.name)
            os.unlink(archive.name)


if __name__ == "__main__":
    unittest.main()
