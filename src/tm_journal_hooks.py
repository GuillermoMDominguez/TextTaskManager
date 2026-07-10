"""Post-write hooks system for journal operations.

Shared with tm_sync to prevent concurrent access.
"""

import os
import threading
from typing import Callable, List


# ─── File lock (shared with tm_sync to prevent concurrent access) ──────────────
# Acquire this lock before writing to any journal file.
# Uses threading.RLock + fcntl.flock for cross-process safety (CLI + web).

_HAS_FLOCK = False
_flock_fd = None
_LOCKFILE_PATH = "/tmp/ttm_journal.lock"

try:
    import fcntl
    _HAS_FLOCK = True
except ImportError:
    pass


class _CrossProcessLock:
    """Combined thread-safe + cross-process lock using fcntl.flock.

    Supports reentrancy (same thread can acquire multiple times) and
    cross-process exclusion via fcntl.flock(LOCK_EX) on a shared lock file.
    flock(LOCK_EX) is NOT reentrant within the same process, so we track
    recursion depth to only call flock once per outermost acquire.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._owner = None
        self._depth = 0

    def acquire(self) -> None:
        current = threading.current_thread().ident
        if self._owner == current:
            self._depth += 1
            return
        self._lock.acquire()
        self._owner = current
        self._depth = 1
        if _HAS_FLOCK and _flock_fd is not None:
            fcntl.flock(_flock_fd, fcntl.LOCK_EX)

    def release(self) -> None:
        if self._depth > 1:
            self._depth -= 1
            return
        self._depth = 0
        self._owner = None
        if _HAS_FLOCK and _flock_fd is not None:
            fcntl.flock(_flock_fd, fcntl.LOCK_UN)
        self._lock.release()

    def __enter__(self) -> "_CrossProcessLock":
        self.acquire()
        return self

    def __exit__(self, *args) -> None:
        self.release()


def _init_lockfile() -> None:
    """Open the global lock file once at import time."""
    global _flock_fd
    if not _HAS_FLOCK:
        return
    if _flock_fd is not None:
        return
    try:
        _flock_fd = os.open(_LOCKFILE_PATH, os.O_CREAT | os.O_RDONLY, 0o644)
    except OSError:
        _flock_fd = None


file_lock = _CrossProcessLock()
_init_lockfile()


# ─── Post-write hooks ──────────────────────────────────────────────────────────
# Registered callbacks are called after any journal write operation.
# Signature: callback() -> None

_post_write_hooks: List[Callable[[], None]] = []


def register_post_write_hook(callback: Callable[[], None]) -> None:
    """Register a callback to be invoked after any journal file write."""
    _post_write_hooks.append(callback)


def _notify_post_write() -> None:
    """Invoke all registered post-write hooks."""
    for hook in _post_write_hooks:
        try:
            hook()
        except Exception:
            pass  # Hooks must never crash the main app
