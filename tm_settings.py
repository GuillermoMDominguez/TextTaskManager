"""User settings loaded from .ttm_config JSON file."""

import json
from pathlib import Path
from typing import Any, Optional


DEFAULT_SETTINGS = {
    "states": ["BACKLOG", "IN PROGRESS", "WAITING", "TESTING", "DONE", "CANCELLED"],
    "finished_states": ["DONE", "CANCELLED"],
    "state_aliases": {"IN TESTING": "TESTING"},
    "priorities": ["LOW", "MEDIUM", "HIGH", "URGENT"],
    "priority_aliases": {"L": "LOW", "M": "MEDIUM", "H": "HIGH", "U": "URGENT"},
    "default_state": "BACKLOG",
    "kanban_columns": ["BACKLOG", "IN PROGRESS", "WAITING", "TESTING", "DONE", "CANCELLED"],
    "date_format": "%d/%m/%Y",
    "default_priority": None,
    "agenda_days": 7,
    "sort_by": "none",
    "sort_direction": "asc",
    "show_done_default": False,
    "weekly_report_days": 7,
    "email": {
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "from_address": "",
        "default_recipient": "",
        "subject_prefix": "[TaskManager]",
    },
    "colors_enabled": True,
    "background_color": "0,0,0",
    "max_undo": 20,
    "templates": {},
}


_cached_settings: Optional[dict] = None
_settings_path: Optional[Path] = None


def _find_config_file(start_dir: Optional[Path] = None) -> Optional[Path]:
    """Find .ttm_config in project dir, cwd, or home dir."""
    candidates = []
    if start_dir:
        candidates.append(start_dir / ".ttm_config")
    # Also check cwd and script directory
    cwd = Path.cwd()
    if cwd not in (start_dir, None):
        candidates.append(cwd / ".ttm_config")
    # Check directory where tm_settings.py lives (project root)
    script_parent = Path(__file__).parent
    if script_parent not in (start_dir, cwd):
        candidates.append(script_parent / ".ttm_config")
    candidates.append(Path.home() / ".ttm_config")
    for path in candidates:
        if path.exists():
            return path
    return None


def load_settings(project_dir: Optional[Path] = None, force_reload: bool = False) -> dict:
    """Load settings from .ttm_config, merging with defaults."""
    global _cached_settings, _settings_path

    if _cached_settings is not None and not force_reload:
        return _cached_settings

    config_path = _find_config_file(project_dir)
    _settings_path = config_path

    settings = dict(DEFAULT_SETTINGS)

    if config_path and config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_settings = json.load(f)
            _deep_merge(settings, user_settings)
        except (json.JSONDecodeError, OSError):
            pass

    _cached_settings = settings
    return settings


def save_settings(settings: dict, project_dir: Optional[Path] = None) -> bool:
    """Save settings to .ttm_config in the project directory."""
    target = project_dir / ".ttm_config" if project_dir else Path.home() / ".ttm_config"
    try:
        with open(target, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        return True
    except OSError:
        return False


def get_setting(key: str, default: Any = None) -> Any:
    """Get a specific setting value using dot notation (e.g. 'email.smtp_host')."""
    settings = load_settings()
    keys = key.split(".")
    value = settings
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return default
    return value


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base dict."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
