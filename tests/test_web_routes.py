"""Tests for the web API route table and handlers."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch


class TestRouteTable(unittest.TestCase):
    """Verify the route table is valid and all routes point to callable handlers."""

    def setUp(self):
        from src.tm_web.routes import API_ROUTES
        self.routes = API_ROUTES

    def test_all_routes_point_to_callables(self):
        """Every route entry must map to a callable handler."""
        for (method, path), handler in self.routes.items():
            with self.subTest(route=f"{method} {path}"):
                self.assertTrue(
                    callable(handler),
                    f"Handler for {method} {path} is not callable: {handler}"
                )

    def test_route_count(self):
        """We expect exactly 56 routes."""
        self.assertEqual(len(self.routes), 56)

    def test_get_routes_exist(self):
        """All expected GET routes must be present."""
        expected_get = [
            "/api/tasks", "/api/agenda", "/api/calendar", "/api/kanban",
            "/api/stats", "/api/weekly", "/api/burndown", "/api/tags",
            "/api/tags/tasks", "/api/blockers", "/api/time", "/api/jira",
            "/api/jira/transitions", "/api/search", "/api/config", "/api/log",
            "/api/status", "/api/journals", "/api/sync/status",
            "/api/gantt",
            "/api/parse-date",
            "/api/templates",
            "/api/notes", "/api/notes/folders", "/api/notes/read",
        ]
        for path in expected_get:
            with self.subTest(route=f"GET {path}"):
                self.assertIn(("GET", path), self.routes, f"Missing GET {path}")

    def test_post_routes_exist(self):
        """All expected POST routes must be present."""
        expected_post = [
            "/api/tasks", "/api/tasks/state", "/api/tasks/edit",
            "/api/tasks/delete", "/api/tasks/notes", "/api/tasks/notes/delete",
            "/api/tasks/notes/edit", "/api/tasks/subtasks",
            "/api/subtasks/edit", "/api/subtasks/delete",
            "/api/subtasks/notes", "/api/subtasks/notes/delete",
            "/api/subtasks/notes/edit",
            "/api/jira/transition", "/api/jira/mark-all", "/api/jira/mark",
            "/api/config", "/api/journals/switch", "/api/tasks/time",
            "/api/blockers/add", "/api/blockers/delete",
            "/api/sync/pull", "/api/sync/push", "/api/sync/settings",
            "/api/notes", "/api/notes/delete", "/api/notes/move",
            "/api/tasks/notes/link", "/api/tasks/notes/unlink",
            "/api/templates/save", "/api/templates/apply",
        ]
        for path in expected_post:
            with self.subTest(route=f"POST {path}"):
                self.assertIn(("POST", path), self.routes, f"Missing POST {path}")

    def test_no_duplicate_routes(self):
        """No duplicate route keys."""
        self.assertEqual(len(self.routes), len(set(self.routes.keys())))

    def test_handler_names_match_routes(self):
        """Verify handlers have the expected names (basic sanity)."""
        checks = {
            ("GET", "/api/tasks"): "api_get_tasks",
            ("POST", "/api/tasks"): "api_create_task",
            ("GET", "/api/notes"): "api_get_notes",
            ("POST", "/api/notes"): "api_create_note",
            ("POST", "/api/tasks/edit"): "api_edit_task",
            ("POST", "/api/tasks/notes/link"): "api_link_task_note",
        }
        for route, expected_name in checks.items():
            with self.subTest(route=route):
                handler = self.routes.get(route)
                self.assertIsNotNone(handler, f"Route {route} not found")
                self.assertEqual(
                    handler.__name__, expected_name,
                    f"Route {route} has handler {handler.__name__}, expected {expected_name}"
                )


class TestSerializers(unittest.TestCase):
    """Test the serializer functions."""

    def setUp(self):
        from src.tm_web.serializers import serialize_task
        self.serialize_task = serialize_task

    def test_serialize_task_basic(self):
        """Test basic task serialization."""
        from src.tm_views_data import TaskViewItem

        task = TaskViewItem(
            task_id="42",
            title="Test task",
            state="BACKLOG",
            priority="HIGH",
            tags=["frontend", "urgent"],
            notes=["A note"],
            recurrence="weekly",
        )

        result = self.serialize_task(task)
        self.assertEqual(result["id"], "42")
        self.assertEqual(result["title"], "Test task")
        self.assertEqual(result["state"], "BACKLOG")
        self.assertEqual(result["priority"], "HIGH")
        self.assertEqual(result["tags"], ["frontend", "urgent"])
        self.assertEqual(result["notes"], ["A note"])
        self.assertEqual(result["recurrence"], "weekly")
        self.assertIsNone(result["due_date"])
        self.assertEqual(result["subtasks"], [])

    def test_serialize_task_with_due_date(self):
        """Test due_date serialization."""
        from src.tm_views_data import TaskViewItem

        due = datetime(2026, 6, 30)
        task = TaskViewItem(task_id="1", title="Due task", state="BACKLOG", due_date=due)

        result = self.serialize_task(task)
        self.assertEqual(result["due_date"], "30/06/2026")

    def test_serialize_task_with_subtasks(self):
        """Test subtask serialization."""
        from src.tm_views_data import TaskViewItem, SubtaskViewItem

        subtask = SubtaskViewItem(
            task_id="1.1", title="Subtask 1", state="DONE",
            tags=["test"], notes=["Note"],
        )
        task = TaskViewItem(
            task_id="1", title="Parent", state="BACKLOG",
            subtasks=[subtask],
        )

        result = self.serialize_task(task)
        self.assertEqual(len(result["subtasks"]), 1)
        st = result["subtasks"][0]
        self.assertEqual(st["id"], "1.1")
        self.assertEqual(st["title"], "Subtask 1")
        self.assertEqual(st["state"], "DONE")


class TestJsonResponse(unittest.TestCase):
    """Test the json_response helper."""

    def setUp(self):
        from src.tm_web.serializers import json_response, error_response, read_body
        self.json_response = json_response
        self.error_response = error_response
        self.read_body = read_body

    def test_json_response_sends_headers_and_body(self):
        """json_response sends correct Content-Type and body."""
        handler = MagicMock()
        self.json_response(handler, {"ok": True, "value": 42})

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/json; charset=utf-8")
        handler.wfile.write.assert_called_once()

        written = handler.wfile.write.call_args[0][0]
        self.assertIn(b'"ok"', written)
        self.assertIn(b'true', written)

    def test_error_response(self):
        """error_response sends error JSON with correct status."""
        handler = MagicMock()
        self.error_response(handler, "Not found", 404)

        handler.send_response.assert_called_once_with(404)
        written = handler.wfile.write.call_args[0][0]
        self.assertIn(b'"error"', written)
        self.assertIn(b'Not found', written)


class TestStateModule(unittest.TestCase):
    """Test the state module."""

    def test_import_state(self):
        """Verify state module can be imported."""
        from src.tm_web.state import WebState, _state, _state_proxy, get_web_state, set_web_state
        self.assertIsNotNone(WebState)
        self.assertIsNone(_state)
        self.assertIsNotNone(_state_proxy)
        self.assertTrue(callable(set_web_state))
        self.assertTrue(callable(get_web_state))

    def test_state_proxy_delegates(self):
        """_state_proxy delegates to the current WebState."""
        from pathlib import Path
        from src.tm_web.state import WebState, set_web_state, _state_proxy

        ws = WebState.__new__(WebState)
        ws._journal_name = "test.txt"
        ws._script_dir = Path("/tmp")
        ws._tasks_by_date = {}
        from threading import Lock
        ws.lock = Lock()
        set_web_state(ws)

        self.assertEqual(_state_proxy.journal_path, "/tmp/journals/test.txt")


class TestHandlersImports(unittest.TestCase):
    """Verify all handler modules can be imported."""

    def test_tasks_handlers(self):
        from src.tm_web.handlers.tasks import (
            api_create_task, api_edit_task, api_delete_task,
            api_change_state, api_add_note, api_delete_note, api_edit_note,
            api_add_subtask, api_edit_subtask, api_delete_subtask,
            api_add_subtask_note, api_delete_subtask_note, api_edit_subtask_note,
        )
        self.assertTrue(callable(api_create_task))

    def test_views_handlers(self):
        from src.tm_web.handlers.views import (
            api_get_tasks, api_get_agenda, api_get_calendar,
            api_get_kanban, api_get_stats, api_get_weekly_report,
            api_get_burndown, api_get_tags, api_get_tag_tasks,
            api_get_blockers, api_get_time_tracking, api_search_tasks,
        )
        self.assertTrue(callable(api_get_tasks))

    def test_notes_handlers(self):
        from src.tm_web.handlers.notes import (
            api_get_notes, api_get_note_folders, api_get_note,
            api_create_note, api_delete_note_route, api_move_note_route,
            api_link_task_note, api_unlink_task_note,
        )
        self.assertTrue(callable(api_get_notes))

    def test_system_handlers(self):
        from src.tm_web.handlers.system import (
            api_get_journals, api_switch_journal, api_get_status,
            api_get_log, api_get_config, api_save_config,
            api_get_sync_status, api_post_sync_pull, api_post_sync_push,
            api_post_sync_settings, api_log_time,
            api_add_blocker, api_delete_blocker,
        )
        self.assertTrue(callable(api_get_config))

    def test_jira_handlers(self):
        from src.tm_web.handlers.jira import (
            api_get_jira, api_get_jira_transitions,
            api_post_jira_transition, api_post_jira_mark_all, api_post_jira_mark,
        )
        self.assertTrue(callable(api_get_jira))


class TestServerModule(unittest.TestCase):
    """Test the refactored server module imports."""

    def test_server_imports(self):
        """Verify all expected names are exported from server.py."""
        from src.tm_web.server import (
            TTMRequestHandler, start_server, start_server_background,
            stop_server, is_running, get_url,
            _bind_available_server, _open_browser_app_mode, _wait_for_server,
            _server_instance, _server_thread, _server_port, _PROJECT_ROOT,
        )
        self.assertTrue(callable(start_server))
        self.assertTrue(callable(stop_server))
        self.assertTrue(callable(is_running))
        self.assertTrue(callable(get_url))

    def test_init_re_exports(self):
        """Verify __init__.py re-exports work."""
        from src.tm_web import (
            start_server, start_server_background,
            stop_server, is_running, get_url,
        )
        self.assertTrue(callable(start_server))
        self.assertTrue(callable(is_running))


if __name__ == "__main__":
    unittest.main()
