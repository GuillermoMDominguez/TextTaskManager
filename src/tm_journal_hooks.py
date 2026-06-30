"""Post-write hooks system for journal operations.

Shared with tm_sync to prevent concurrent access.
"""

import threading
from typing import Callable, List


# ─── File lock (shared with tm_sync to prevent concurrent access) ──────────────
# Acquire this lock before writing to any journal file.
file_lock = threading.RLock()


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
