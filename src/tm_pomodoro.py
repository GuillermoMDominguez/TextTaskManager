"""Pomodoro timer — countdown in the terminal with early-stop support."""

import sys
import time
import threading


def run_pomodoro(minutes: int = 25, task_title: str = "") -> int:
    """Run a pomodoro countdown in the terminal. Returns elapsed minutes.

    Can be interrupted with Enter or Ctrl+C (counts partial time).
    Cross-platform (works on Windows, Linux, macOS).
    """
    total_seconds = minutes * 60
    label = f" [{task_title[:30]}]" if task_title else ""
    print(f"\n  Pomodoro started: {minutes}min{label}")
    print(f"  Press Enter to stop early.\n")

    stop_event = threading.Event()

    def _wait_for_enter():
        """Background thread that waits for Enter key."""
        try:
            sys.stdin.readline()
            stop_event.set()
        except Exception:
            pass

    listener = threading.Thread(target=_wait_for_enter, daemon=True)
    listener.start()

    start = time.time()
    try:
        while not stop_event.is_set():
            elapsed = time.time() - start
            remaining = total_seconds - int(elapsed)
            if remaining <= 0:
                break
            mins, secs = divmod(remaining, 60)
            sys.stdout.write(f"\r  ⏱  {mins:02d}:{secs:02d} remaining  ")
            sys.stdout.flush()
            stop_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        pass

    elapsed_minutes = max(1, int((time.time() - start) / 60 + 0.5))
    sys.stdout.write(f"\r  ✓  Pomodoro done! ({elapsed_minutes}min logged)        \n")
    sys.stdout.flush()
    return elapsed_minutes
