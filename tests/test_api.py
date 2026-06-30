"""Tests for web API handlers — state, serializers, and handler logic."""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from threading import Lock
from unittest.mock import MagicMock, patch

from src.tm_models import Task, Subtask
from src.tm_web.serializers import json_response, error_response, read_body, serialize_task
from src.tm_web.state import WebState, set_web_state, get_web_state


def _make_handler():
    """Create a mock HTTP handler for testing API endpoints."""
    handler = MagicMock()
    handler.wfile = MagicMock()
    return handler


def _setup_state(journal_name="test.txt", script_dir=None, tasks_by_date=None):
    """Set up a clean WebState for testing."""
    ws = WebState.__new__(WebState)
    ws._journal_name = journal_name
    ws._script_dir = Path(script_dir) if script_dir else Path("/tmp")
    ws._tasks_by_date = tasks_by_date or {}
    ws.lock = Lock()
    ws._config = {}
    set_web_state(ws)
    return ws


class TestStateSetup(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()
        set_web_state(None)

    def test_set_and_get_state(self):
        ws = _setup_state(script_dir=self.tmpdir.name)
        self.assertIsNotNone(get_web_state())
        self.assertEqual(get_web_state(), ws)

    def test_state_journal_path(self):
        _setup_state("journal.txt", script_dir=self.tmpdir.name)
        from src.tm_web.state import _state_proxy
        self.assertIn("journal.txt", _state_proxy.journal_path)


class TestSerializers(unittest.TestCase):
    def test_json_response_sends_headers_and_body(self):
        handler = _make_handler()
        json_response(handler, {"ok": True, "key": "value"})

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/json; charset=utf-8")
        handler.send_header.assert_any_call("Access-Control-Allow-Origin", "*")
        handler.end_headers.assert_called_once()

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        parsed = json.loads(written)
        self.assertEqual(parsed, {"ok": True, "key": "value"})

    def test_error_response(self):
        handler = _make_handler()
        error_response(handler, "not found", status=404)

        handler.send_response.assert_called_once_with(404)
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        parsed = json.loads(written)
        self.assertEqual(parsed, {"error": "not found"})

    def test_serialize_task_basic(self):
        tvi = MagicMock()
        tvi.task_id = "1"
        tvi.title = "Test"
        tvi.state = "TODO"
        tvi.priority = None
        tvi.due_date = None
        tvi.tags = []
        tvi.subtasks = []
        tvi.notes = []
        tvi.recurrence = None
        tvi.time_spent = None
        tvi.jira_key = None
        tvi.linked_notes = []

        data = serialize_task(tvi)
        self.assertEqual(data["id"], "1")
        self.assertEqual(data["title"], "Test")
        self.assertEqual(data["state"], "TODO")

    def test_serialize_task_with_dates(self):
        tvi = MagicMock()
        tvi.task_id = "2"
        tvi.title = "With Date"
        tvi.state = "IN PROGRESS"
        tvi.priority = "HIGH"
        tvi.due_date = datetime(2026, 6, 15)
        tvi.tags = ["#work"]
        tvi.subtasks = []
        tvi.notes = ["a note"]
        tvi.recurrence = "weekly"
        tvi.time_spent = 90
        tvi.jira_key = "PROJ-123"
        tvi.linked_notes = ["note.md"]

        data = serialize_task(tvi)
        self.assertEqual(data["due_date"], "15/06/2026")
        self.assertEqual(data["priority"], "HIGH")
        self.assertEqual(data["recurrence"], "weekly")
        self.assertEqual(data["jira_key"], "PROJ-123")


class TestHandlerJournals(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        jdir = Path(self.tmpdir.name) / "journals"
        jdir.mkdir()
        (jdir / "current.txt").write_text("## 01/06/2026\n- test -- TODO\n")
        (jdir / "other.txt").write_text("## 01/06/2026\n- other -- DONE\n")
        _setup_state("current.txt", script_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()
        set_web_state(None)

    def test_api_get_journals(self):
        from src.tm_web.handlers.system import api_get_journals
        handler = _make_handler()
        api_get_journals(handler, {})

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("journals", data)
        self.assertIn("current", data)
        names = [j["name"] for j in data["journals"]]
        self.assertIn("current.txt", names)
        self.assertIn("other.txt", names)

    def test_api_get_status(self):
        from src.tm_web.handlers.system import api_get_status
        handler = _make_handler()
        api_get_status(handler, {})

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("sync", data)
        self.assertIn("jira", data)


class TestHandlerTasks(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        jdir = Path(self.tmpdir.name) / "journals"
        jdir.mkdir()
        self.journal_path = str(jdir / "journal.txt")
        Path(self.journal_path).write_text("## 01/06/2026\n- Existing -- TODO\n")
        _setup_state("journal.txt", script_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()
        set_web_state(None)

    def test_api_get_tasks(self):
        from src.tm_web.handlers.views import api_get_tasks
        handler = _make_handler()
        api_get_tasks(handler, {"view": ["all"]})

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("tasks", data)
        self.assertIn("states", data)
        self.assertIsInstance(data["tasks"], list)

    def test_api_create_task(self):
        from src.tm_web.handlers.tasks import api_create_task
        handler = _make_handler()
        body = json.dumps({
            "title": "New Task",
            "state": "TODO",
            "due_date": "15/06/2026",
            "priority": "HIGH",
        })
        handler.rfile.read.return_value = body.encode()

        api_create_task(handler, {})

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertTrue(data.get("ok", False))
        # Verify task was added by re-parsing
        from src.tm_journal import parse_journal
        tasks = parse_journal(self.journal_path)
        all_tasks = [t for ts in tasks.values() for t in ts]
        titles = [t.title for t in all_tasks]
        self.assertIn("New Task", titles)

    def test_api_create_task_empty_title(self):
        from src.tm_web.handlers.tasks import api_create_task
        handler = _make_handler()
        body = json.dumps({"title": ""})
        handler.rfile.read.return_value = body.encode()

        api_create_task(handler, {})

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("error", data)


class TestHandlerNotes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        jdir = Path(self.tmpdir.name) / "journals"
        jdir.mkdir()
        self.journal_path = str(jdir / "journal.txt")
        Path(self.journal_path).write_text("## 01/06/2026\n- Task -- TODO\n")
        _setup_state("journal.txt", script_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()
        set_web_state(None)

    def test_api_get_notes_empty(self):
        from src.tm_web.handlers.notes import api_get_notes
        handler = _make_handler()
        api_get_notes(handler, {})

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("notes", data)
        self.assertEqual(data["notes"], [])

    def test_api_create_note(self):
        from src.tm_web.handlers.notes import api_create_note
        handler = _make_handler()
        body = json.dumps({"name": "test-note"})
        handler.rfile.read.return_value = body.encode()

        api_create_note(handler, {})

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertTrue(data.get("ok", False))

    def test_api_get_note_folders_empty(self):
        from src.tm_web.handlers.notes import api_get_note_folders
        handler = _make_handler()
        api_get_note_folders(handler, {})

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("folders", data)


class TestHandlerConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        jdir = Path(self.tmpdir.name) / "journals"
        jdir.mkdir()
        self.journal_path = str(jdir / "journal.txt")
        Path(self.journal_path).write_text("## 01/06/2026\n- Task -- TODO\n")
        _setup_state("journal.txt", script_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()
        set_web_state(None)

    def test_api_get_config(self):
        from src.tm_web.handlers.system import api_get_config
        handler = _make_handler()
        api_get_config(handler, {})

        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("settings", data)
        self.assertIn("secrets", data)

    def test_api_save_config(self):
        from src.tm_web.handlers.system import api_save_config
        handler = _make_handler()
        body = json.dumps({"kanban_columns": ["A", "B", "C"]})
        handler.rfile.read.return_value = body.encode()

        api_save_config(handler, {})


class TestHandlerSystem(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        jdir = Path(self.tmpdir.name) / "journals"
        jdir.mkdir()
        self.journal_path = str(jdir / "journal.txt")
        Path(self.journal_path).write_text("## 01/06/2026\n- Task -- TODO\n")
        _setup_state("journal.txt", script_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()
        set_web_state(None)

    def test_api_log_time(self):
        from src.tm_web.handlers.system import api_log_time
        handler = _make_handler()
        body = json.dumps({"task_id": "1", "time": "1h30m"})
        handler.rfile.read.return_value = body.encode()

        api_log_time(handler, {})
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        # Task 1 not found since we didn't parse
        self.assertIsInstance(data, dict)

    def test_api_add_blocker(self):
        from src.tm_web.handlers.system import api_add_blocker
        handler = _make_handler()
        body = json.dumps({"task_id": "1", "blocker_title": "Blocker"})
        handler.rfile.read.return_value = body.encode()

        api_add_blocker(handler, {})
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIsInstance(data, dict)

    def test_api_get_sync_status(self):
        from src.tm_web.handlers.system import api_get_sync_status
        handler = _make_handler()
        api_get_sync_status(handler, {})
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("configured", data)
        self.assertIn("status", data)


class TestHandlerViews(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        jdir = Path(self.tmpdir.name) / "journals"
        jdir.mkdir()
        self.journal_path = str(jdir / "journal.txt")
        Path(self.journal_path).write_text(
            "## 01/06/2026\n"
            "- Task 1 -- TODO -- due:10/06/2026\n"
            "- Task 2 -- DONE -- due:05/06/2026\n"
            "## 05/06/2026\n"
            "- Future -- TODO\n"
        )
        _setup_state("journal.txt", script_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()
        set_web_state(None)

    def test_api_get_agenda(self):
        from src.tm_web.handlers.views import api_get_agenda
        handler = _make_handler()
        api_get_agenda(handler, {"days": ["7"]})
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("overdue", data)
        self.assertIn("due_today", data)
        self.assertIn("due_soon", data)

    def test_api_get_calendar(self):
        from src.tm_web.handlers.views import api_get_calendar
        handler = _make_handler()
        api_get_calendar(handler, {"view": ["month"], "year": ["2026"], "month": ["6"]})
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("days", data)
        self.assertIn("view", data)

    def test_api_get_stats(self):
        from src.tm_web.handlers.views import api_get_stats
        handler = _make_handler()
        api_get_stats(handler, {})
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("total", data)
        self.assertIn("by_state", data)

    def test_api_search(self):
        from src.tm_web.handlers.views import api_search_tasks
        handler = _make_handler()
        api_search_tasks(handler, {"q": ["Task"]})
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("tasks", data)
        self.assertIn("query", data)


class TestHandlerTaskCRUD(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        jdir = Path(self.tmpdir.name) / "journals"
        jdir.mkdir()
        self.journal_path = str(jdir / "journal.txt")
        Path(self.journal_path).write_text("## 01/06/2026\n- EditMe -- TODO -- priority:HIGH\n- DelMe -- BACKLOG\n")
        _setup_state("journal.txt", script_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()
        set_web_state(None)

    def test_api_edit_task_missing_id(self):
        from src.tm_web.handlers.tasks import api_edit_task
        handler = _make_handler()
        body = json.dumps({"title": "Edited"})
        handler.rfile.read.return_value = body.encode()

        api_edit_task(handler, {})
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("error", data)

    def test_api_change_state_missing_id(self):
        from src.tm_web.handlers.tasks import api_change_state
        handler = _make_handler()
        body = json.dumps({"state": "DONE"})
        handler.rfile.read.return_value = body.encode()

        api_change_state(handler, {})

    def test_api_delete_task(self):
        from src.tm_web.handlers.tasks import api_delete_task
        handler = _make_handler()
        api_delete_task(handler, {"task_id": ["2"]})
        written = b"".join(c[0][0] for c in handler.wfile.write.call_args_list)
        data = json.loads(written)
        self.assertIn("error", data)

    def test_api_add_subtask(self):
        from src.tm_web.handlers.tasks import api_add_subtask
        handler = _make_handler()
        body = json.dumps({"task_id": "1", "title": "Subtask 1"})
        handler.rfile.read.return_value = body.encode()

        api_add_subtask(handler, {})

    def test_api_delete_subtask(self):
        from src.tm_web.handlers.tasks import api_delete_subtask
        handler = _make_handler()
        body = json.dumps({"task_id": "1", "subtask_id": "1"})
        handler.rfile.read.return_value = body.encode()

        api_delete_subtask(handler, {})


if __name__ == "__main__":
    unittest.main()
