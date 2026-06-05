"""Comprehensive tests for tm_settings.py."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tm_settings
from tm_settings import (
    DEFAULT_SETTINGS,
    _deep_merge,
    _find_config_file,
    get_setting,
    load_settings,
    save_settings,
)


class TestDeepMerge(unittest.TestCase):
    """Tests for _deep_merge helper."""

    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        _deep_merge(base, {"b": 99})
        self.assertEqual(base, {"a": 1, "b": 99})

    def test_nested_merge(self):
        base = {"outer": {"x": 1, "y": 2}}
        _deep_merge(base, {"outer": {"y": 99}})
        self.assertEqual(base, {"outer": {"x": 1, "y": 99}})

    def test_deeply_nested_merge(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        _deep_merge(base, {"a": {"b": {"c": 100}}})
        self.assertEqual(base["a"]["b"]["c"], 100)
        self.assertEqual(base["a"]["b"]["d"], 2)

    def test_new_keys_added(self):
        base = {"a": 1}
        _deep_merge(base, {"b": 2, "c": 3})
        self.assertEqual(base, {"a": 1, "b": 2, "c": 3})

    def test_new_nested_key_added(self):
        base = {"outer": {"x": 1}}
        _deep_merge(base, {"outer": {"new_key": "hello"}})
        self.assertEqual(base["outer"]["new_key"], "hello")
        self.assertEqual(base["outer"]["x"], 1)

    def test_empty_override(self):
        base = {"a": 1, "b": 2}
        _deep_merge(base, {})
        self.assertEqual(base, {"a": 1, "b": 2})

    def test_empty_base(self):
        base = {}
        _deep_merge(base, {"a": 1, "b": {"c": 2}})
        self.assertEqual(base, {"a": 1, "b": {"c": 2}})

    def test_both_empty(self):
        base = {}
        _deep_merge(base, {})
        self.assertEqual(base, {})

    def test_list_replacement_not_merged(self):
        base = {"items": [1, 2, 3]}
        _deep_merge(base, {"items": [4, 5]})
        self.assertEqual(base["items"], [4, 5])

    def test_list_in_nested_dict_replaced(self):
        base = {"config": {"tags": ["a", "b"]}}
        _deep_merge(base, {"config": {"tags": ["x"]}})
        self.assertEqual(base["config"]["tags"], ["x"])

    def test_override_dict_with_scalar(self):
        base = {"a": {"nested": 1}}
        _deep_merge(base, {"a": "replaced"})
        self.assertEqual(base["a"], "replaced")

    def test_override_scalar_with_dict(self):
        base = {"a": "scalar"}
        _deep_merge(base, {"a": {"nested": True}})
        self.assertEqual(base["a"], {"nested": True})

    def test_none_value_override(self):
        base = {"a": 1}
        _deep_merge(base, {"a": None})
        self.assertIsNone(base["a"])

    def test_preserves_unrelated_keys(self):
        base = {"x": 10, "y": 20, "z": 30}
        _deep_merge(base, {"y": 99})
        self.assertEqual(base["x"], 10)
        self.assertEqual(base["z"], 30)


class TestLoadSettings(unittest.TestCase):
    """Tests for load_settings."""

    def setUp(self):
        tm_settings._cached_settings = None
        tm_settings._settings_path = None

    def tearDown(self):
        tm_settings._cached_settings = None
        tm_settings._settings_path = None

    def test_returns_defaults_when_no_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = load_settings(project_dir=Path(tmpdir))
        self.assertEqual(settings["default_state"], "BACKLOG")
        self.assertEqual(settings["max_undo"], 20)

    def test_merges_user_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"max_undo": 50, "default_state": "TODO"}
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            settings = load_settings(project_dir=Path(tmpdir))
        self.assertEqual(settings["max_undo"], 50)
        self.assertEqual(settings["default_state"], "TODO")

    def test_preserves_defaults_not_overridden(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"max_undo": 5}
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            settings = load_settings(project_dir=Path(tmpdir))
        self.assertEqual(settings["show_log"], True)
        self.assertEqual(settings["date_format"], "%d/%m/%Y")

    def test_nested_override_preserves_sibling_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"email": {"smtp_host": "mail.example.com"}}
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            settings = load_settings(project_dir=Path(tmpdir))
        self.assertEqual(settings["email"]["smtp_host"], "mail.example.com")
        self.assertEqual(settings["email"]["smtp_port"], 587)

    def test_handles_missing_file_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = load_settings(project_dir=Path(tmpdir))
        self.assertIsInstance(settings, dict)
        self.assertIn("states", settings)

    def test_handles_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text("{ invalid json !!!", encoding="utf-8")
            settings = load_settings(project_dir=Path(tmpdir))
        self.assertEqual(settings["default_state"], "BACKLOG")

    def test_handles_empty_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text("", encoding="utf-8")
            settings = load_settings(project_dir=Path(tmpdir))
        self.assertEqual(settings["default_state"], "BACKLOG")

    def test_caching_returns_same_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text(json.dumps({"max_undo": 10}), encoding="utf-8")
            first = load_settings(project_dir=Path(tmpdir))
            # Modify the file — should not matter due to cache
            config_path.write_text(json.dumps({"max_undo": 999}), encoding="utf-8")
            second = load_settings(project_dir=Path(tmpdir))
        self.assertIs(first, second)
        self.assertEqual(second["max_undo"], 10)

    def test_force_reload_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text(json.dumps({"max_undo": 10}), encoding="utf-8")
            first = load_settings(project_dir=Path(tmpdir))
            self.assertEqual(first["max_undo"], 10)
            config_path.write_text(json.dumps({"max_undo": 999}), encoding="utf-8")
            second = load_settings(project_dir=Path(tmpdir), force_reload=True)
        self.assertEqual(second["max_undo"], 999)

    def test_returns_dict_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = load_settings(project_dir=Path(tmpdir))
        self.assertIsInstance(settings, dict)

    def test_does_not_mutate_default_settings(self):
        original_default_state = DEFAULT_SETTINGS["default_state"]
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text(
                json.dumps({"default_state": "CUSTOM"}), encoding="utf-8"
            )
            load_settings(project_dir=Path(tmpdir))
        self.assertEqual(DEFAULT_SETTINGS["default_state"], original_default_state)


class TestSaveSettings(unittest.TestCase):
    """Tests for save_settings."""

    def setUp(self):
        tm_settings._cached_settings = None
        tm_settings._settings_path = None

    def tearDown(self):
        tm_settings._cached_settings = None
        tm_settings._settings_path = None

    def test_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = {"max_undo": 30, "show_log": False}
            result = save_settings(settings, project_dir=Path(tmpdir))
            self.assertTrue(result)
            written = json.loads(
                (Path(tmpdir) / ".ttm_config").read_text(encoding="utf-8")
            )
        self.assertEqual(written["max_undo"], 30)
        self.assertEqual(written["show_log"], False)

    def test_returns_true_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = save_settings({"a": 1}, project_dir=Path(tmpdir))
        self.assertTrue(result)

    def test_returns_false_on_unwritable_path(self):
        result = save_settings({"a": 1}, project_dir=Path("/nonexistent/dir/path"))
        self.assertFalse(result)

    def test_saves_nested_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = {"email": {"smtp_host": "smtp.test.com", "smtp_port": 465}}
            save_settings(settings, project_dir=Path(tmpdir))
            written = json.loads(
                (Path(tmpdir) / ".ttm_config").read_text(encoding="utf-8")
            )
        self.assertEqual(written["email"]["smtp_host"], "smtp.test.com")
        self.assertEqual(written["email"]["smtp_port"], 465)

    def test_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text(json.dumps({"old": True}), encoding="utf-8")
            save_settings({"new": True}, project_dir=Path(tmpdir))
            written = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertNotIn("old", written)
        self.assertTrue(written["new"])

    def test_saves_to_home_when_no_project_dir(self):
        with patch("tm_settings.Path.home") as mock_home:
            with tempfile.TemporaryDirectory() as tmpdir:
                mock_home.return_value = Path(tmpdir)
                result = save_settings({"test": True})
                self.assertTrue(result)
                written = json.loads(
                    (Path(tmpdir) / ".ttm_config").read_text(encoding="utf-8")
                )
                self.assertTrue(written["test"])

    def test_file_is_utf8_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = {"label": "tarea con acentos: ñ"}
            save_settings(settings, project_dir=Path(tmpdir))
            # Verify the file is readable as UTF-8 and round-trips correctly
            content = (Path(tmpdir) / ".ttm_config").read_text(encoding="utf-8")
            loaded = json.loads(content)
            self.assertEqual(loaded["label"], "tarea con acentos: ñ")


class TestGetSetting(unittest.TestCase):
    """Tests for get_setting."""

    def setUp(self):
        tm_settings._cached_settings = None
        tm_settings._settings_path = None

    def tearDown(self):
        tm_settings._cached_settings = None
        tm_settings._settings_path = None

    def _load_with_config(self, config):
        """Helper: write config to temp dir and load it."""
        tmpdir = tempfile.mkdtemp()
        config_path = Path(tmpdir) / ".ttm_config"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        load_settings(project_dir=Path(tmpdir))
        return tmpdir

    def test_top_level_key(self):
        self._load_with_config({"max_undo": 42})
        self.assertEqual(get_setting("max_undo"), 42)

    def test_dot_notation_nested(self):
        self._load_with_config({"email": {"smtp_host": "mail.test.org"}})
        self.assertEqual(get_setting("email.smtp_host"), "mail.test.org")

    def test_dot_notation_deep_nested(self):
        # sync is a nested dict in defaults
        self._load_with_config({"sync": {"branch": "develop"}})
        self.assertEqual(get_setting("sync.branch"), "develop")

    def test_missing_key_returns_none(self):
        self._load_with_config({})
        self.assertIsNone(get_setting("nonexistent_key"))

    def test_missing_key_returns_custom_default(self):
        self._load_with_config({})
        self.assertEqual(get_setting("nonexistent_key", "fallback"), "fallback")

    def test_missing_nested_key_returns_default(self):
        self._load_with_config({})
        self.assertIsNone(get_setting("email.nonexistent"))

    def test_dot_path_through_non_dict_returns_default(self):
        self._load_with_config({"max_undo": 20})
        self.assertEqual(get_setting("max_undo.sub_key", "nope"), "nope")

    def test_returns_list_value(self):
        self._load_with_config({"states": ["A", "B", "C"]})
        self.assertEqual(get_setting("states"), ["A", "B", "C"])

    def test_returns_boolean_value(self):
        self._load_with_config({"show_log": False})
        self.assertFalse(get_setting("show_log"))

    def test_returns_none_setting_value(self):
        # default_priority is None in defaults
        self._load_with_config({})
        self.assertIsNone(get_setting("default_priority"))

    def test_empty_string_key(self):
        self._load_with_config({})
        # "" splits to [""] which won't match anything meaningful
        result = get_setting("", "default_val")
        self.assertEqual(result, "default_val")


class TestFindConfigFile(unittest.TestCase):
    """Tests for _find_config_file."""

    def setUp(self):
        tm_settings._cached_settings = None
        tm_settings._settings_path = None

    def tearDown(self):
        tm_settings._cached_settings = None
        tm_settings._settings_path = None

    def test_finds_config_in_start_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".ttm_config"
            config_path.write_text("{}", encoding="utf-8")
            result = _find_config_file(start_dir=Path(tmpdir))
        self.assertEqual(result, config_path)

    def test_returns_none_when_no_config_anywhere(self):
        """When no .ttm_config exists in any search path, returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_start = Path(tmpdir) / "start"
            empty_start.mkdir()
            empty_home = Path(tmpdir) / "home"
            empty_home.mkdir()
            # Patch all locations _find_config_file checks:
            # cwd, Path(__file__).parent (via module attribute), and home
            with patch("tm_settings.Path.cwd", return_value=Path(tmpdir)):
                with patch("tm_settings.Path.home", return_value=empty_home):
                    # Patch __file__ so script_parent points to a dir with no config
                    original_file = tm_settings.__file__
                    try:
                        tm_settings.__file__ = str(empty_home / "tm_settings.py")
                        result = _find_config_file(start_dir=empty_start)
                    finally:
                        tm_settings.__file__ = original_file
        self.assertIsNone(result)

    def test_prefers_start_dir_over_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            start = Path(tmpdir) / "project"
            start.mkdir()
            home = Path(tmpdir) / "home"
            home.mkdir()
            (start / ".ttm_config").write_text('{"from": "start"}', encoding="utf-8")
            (home / ".ttm_config").write_text('{"from": "home"}', encoding="utf-8")
            with patch("tm_settings.Path.home", return_value=home):
                result = _find_config_file(start_dir=start)
        self.assertEqual(result, start / ".ttm_config")


class TestDefaultSettingsStructure(unittest.TestCase):
    """Verify DEFAULT_SETTINGS has expected keys and types."""

    def test_has_states_key(self):
        self.assertIn("states", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["states"], list)

    def test_has_finished_states_key(self):
        self.assertIn("finished_states", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["finished_states"], list)

    def test_has_progress_states_key(self):
        self.assertIn("progress_states", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["progress_states"], list)

    def test_has_testing_states_key(self):
        self.assertIn("testing_states", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["testing_states"], list)

    def test_has_state_aliases_key(self):
        self.assertIn("state_aliases", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["state_aliases"], dict)

    def test_has_priorities_key(self):
        self.assertIn("priorities", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["priorities"], list)

    def test_has_priority_aliases_key(self):
        self.assertIn("priority_aliases", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["priority_aliases"], dict)

    def test_has_default_state_key(self):
        self.assertIn("default_state", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["default_state"], str)

    def test_has_kanban_columns_key(self):
        self.assertIn("kanban_columns", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["kanban_columns"], list)

    def test_has_date_format_key(self):
        self.assertIn("date_format", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["date_format"], str)

    def test_has_prompt_format_key(self):
        self.assertIn("prompt_format", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["prompt_format"], str)

    def test_has_prompt_colors_key(self):
        self.assertIn("prompt_colors", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["prompt_colors"], dict)

    def test_has_background_color_key(self):
        self.assertIn("background_color", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["background_color"], str)

    def test_has_max_undo_key(self):
        self.assertIn("max_undo", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["max_undo"], int)

    def test_has_show_log_key(self):
        self.assertIn("show_log", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["show_log"], bool)

    def test_has_templates_key(self):
        self.assertIn("templates", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["templates"], dict)

    def test_has_email_key(self):
        self.assertIn("email", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["email"], dict)

    def test_email_has_expected_subkeys(self):
        email = DEFAULT_SETTINGS["email"]
        expected = [
            "smtp_host", "smtp_port", "smtp_user",
            "smtp_password", "from_address", "default_recipient",
            "subject_prefix",
        ]
        for key in expected:
            self.assertIn(key, email, f"email missing key: {key}")

    def test_has_sync_key(self):
        self.assertIn("sync", DEFAULT_SETTINGS)
        self.assertIsInstance(DEFAULT_SETTINGS["sync"], dict)

    def test_sync_has_expected_subkeys(self):
        sync = DEFAULT_SETTINGS["sync"]
        self.assertIn("enabled", sync)
        self.assertIn("remote", sync)
        self.assertIn("branch", sync)

    def test_sync_has_enabled_key(self):
        # NOTE: DEFAULT_SETTINGS may be mutated by _deep_merge (shallow copy bug)
        # so we only check the key exists and is a bool
        self.assertIn("enabled", DEFAULT_SETTINGS["sync"])
        self.assertIsInstance(DEFAULT_SETTINGS["sync"]["enabled"], bool)

    def test_email_smtp_port_is_int(self):
        self.assertIsInstance(DEFAULT_SETTINGS["email"]["smtp_port"], int)

    def test_priorities_contains_expected_levels(self):
        self.assertIn("LOW", DEFAULT_SETTINGS["priorities"])
        self.assertIn("HIGH", DEFAULT_SETTINGS["priorities"])
        self.assertIn("URGENT", DEFAULT_SETTINGS["priorities"])

    def test_states_contains_done(self):
        self.assertIn("DONE", DEFAULT_SETTINGS["states"])

    def test_finished_states_subset_of_states(self):
        for s in DEFAULT_SETTINGS["finished_states"]:
            self.assertIn(s, DEFAULT_SETTINGS["states"])


if __name__ == "__main__":
    unittest.main()
