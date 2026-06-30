"""Shared state for the web server."""

import threading
from pathlib import Path
from typing import Optional, Any

from src.tm_journal import parse_journal
from src.tm_logic import assign_task_ids


class WebState:
    """Shared state for the web server."""

    def __init__(self, journal_path: str, script_dir: Path):
        self._script_dir = script_dir
        self._journal_name = Path(journal_path).name
        self.lock = threading.Lock()
        self._tasks_by_date: dict = {}
        self.refresh()

    @property
    def journal_path(self) -> str:
        return str(self._script_dir / "journals" / self._journal_name)

    @journal_path.setter
    def journal_path(self, value: str) -> None:
        self._journal_name = Path(value).name

    def refresh(self) -> dict:
        with self.lock:
            self._tasks_by_date = parse_journal(self.journal_path)
            assign_task_ids(self._tasks_by_date)
        return self._tasks_by_date

    @property
    def tasks_by_date(self) -> dict:
        return self._tasks_by_date


_state: Optional[WebState] = None


def set_web_state(state: WebState) -> None:
    """Set the global web state (called by server.py on startup)."""
    global _state
    _state = state


def get_web_state() -> WebState:
    """Get the global web state. Must be called after set_web_state()."""
    global _state
    if _state is None:
        raise RuntimeError("WebState not initialized")
    return _state


class _StateProxy:
    """Proxy that delegates all attribute access to the current WebState instance.
    
    Handler modules import this proxy as `_state` so that every attribute
    access reaches the live WebState object, even after it is replaced via
    set_web_state().
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_web_state(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(get_web_state(), name, value)


_state_proxy = _StateProxy()
