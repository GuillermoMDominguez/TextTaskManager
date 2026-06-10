"""Web UI module for TextTaskManager.

Provides an optional browser-based interface that runs alongside the CLI.
Zero external dependencies — uses Python stdlib http.server.

Usage:
    From CLI: type 'web' to start the server
    Standalone: python3 -m src.tm_web --journal path/to/journal.txt
"""

from .server import start_server

__all__ = ["start_server"]
