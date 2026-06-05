"""Journal sync module — push/pull journals to a private git remote.

Architecture:
    tm_settings → tm_sync (this module)
                    ↓
              subprocess (git CLI)
              urllib (GitHub/GitLab API for repo creation)

This module is entirely optional. If sync is not configured, all functions
are no-ops. The rest of the application is unaffected.
"""

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple


# ─── Module state ──────────────────────────────────────────────────────────────

_sync_config: Optional[dict] = None
_journals_dir: Optional[Path] = None
_secrets: Optional[dict] = None
_last_push_time: float = 0.0
_push_timer: Optional[threading.Timer] = None
_push_lock = threading.Lock()
_last_git_error: str = ""

DEBOUNCE_SECONDS = 5
SECRETS_FILE = ".ttm_secrets"


# ─── Public API ────────────────────────────────────────────────────────────────

def init_sync(journals_dir: Path, settings: dict, project_dir: Path) -> bool:
    """Initialize sync subsystem. Returns True if sync is active."""
    global _sync_config, _journals_dir, _secrets

    sync_cfg = settings.get("sync")
    if not sync_cfg or not sync_cfg.get("enabled", False):
        return False

    remote = sync_cfg.get("remote", "").strip()
    if not remote:
        return False

    _sync_config = sync_cfg
    _journals_dir = journals_dir
    _secrets = _load_secrets(project_dir)

    # Ensure journals dir is a git repo with the configured remote
    if not _is_git_repo(journals_dir):
        _git_init(journals_dir, remote, sync_cfg.get("branch", "main"))

    return True


def sync_pull(interactive: bool = True) -> bool:
    """Pull remote changes. Returns True if successful.

    If interactive=True, prompts user on conflict.
    """
    if not _is_active():
        return False

    branch = _sync_config.get("branch", "main")
    remote_url = _resolve_remote_url()

    # Ensure remote URL is up to date (token may have changed)
    _run_git(["remote", "set-url", "origin", remote_url])

    # Fetch first
    result = _run_git(["fetch", "origin", branch])
    if result is None:
        detail = _last_git_error.split("\n")[0] if _last_git_error else ""
        msg = "No connection — working offline"
        if detail:
            msg += f" ({detail})"
        _print_sync(msg)
        return False

    # Check if local branch has any commits yet
    has_head = _run_git(["rev-parse", "HEAD"]) is not None
    if not has_head:
        # Fresh repo — commit local files first so they're not lost
        _run_git(["add", "-A"])
        status = _run_git(["status", "--porcelain"])
        if status and status.strip():
            # Ensure git identity exists for commit (use fallback if not configured)
            _ensure_git_identity()
            _run_git(["commit", "-m", "local: preserve existing journals"])
        # Now try to rebase onto remote (merges both histories)
        pull_result = _run_git(["pull", "--rebase", "origin", branch])
        if pull_result is None:
            # Rebase failed — try merge instead
            _run_git(["rebase", "--abort"])
            _run_git(["pull", "--no-rebase", "origin", branch, "--allow-unrelated-histories"])
        return True

    # Check if there are remote changes
    diff_result = _run_git(["diff", f"origin/{branch}", "--stat"])
    if diff_result is not None and diff_result.strip() == "":
        return True  # Nothing to pull

    # Check for local uncommitted changes
    status = _run_git(["status", "--porcelain"])
    has_local_changes = status is not None and status.strip() != ""

    if has_local_changes and interactive:
        return _resolve_pull_conflict(branch)

    # Simple fast-forward pull
    pull_result = _run_git(["pull", "--rebase", "origin", branch])
    if pull_result is None:
        _print_sync("Pull failed — working with local version")
        return False

    return True


def sync_push_async() -> None:
    """Schedule a debounced async push (called after each journal write)."""
    if not _is_active():
        return

    global _push_timer
    with _push_lock:
        if _push_timer is not None:
            _push_timer.cancel()
        _push_timer = threading.Timer(DEBOUNCE_SECONDS, _do_push_background)
        _push_timer.daemon = True
        _push_timer.start()


def sync_push_blocking() -> bool:
    """Force an immediate full sync (pull + push). Returns True if successful."""
    if not _is_active():
        _print_sync("Sync not configured")
        return False

    global _push_timer
    with _push_lock:
        if _push_timer is not None:
            _push_timer.cancel()
            _push_timer = None

    # Pull first to get remote changes
    sync_pull(interactive=True)
    # Then push local changes
    return _do_push(verbose=True)


def sync_status() -> str:
    """Return a human-readable sync status string."""
    if not _is_active():
        return "Sync: not configured"

    remote = _sync_config.get("remote", "")
    branch = _sync_config.get("branch", "main")

    # Check if there are unpushed commits
    result = _run_git(["status", "--porcelain"])
    if result is None:
        return f"Sync: {remote} ({branch}) — git error"

    dirty = result.strip() != ""
    return f"Sync: {remote} ({branch})" + (" — pending changes" if dirty else " — up to date")


def is_configured() -> bool:
    """Check if sync is configured and active."""
    return _is_active()


def shutdown() -> None:
    """Flush any pending push before exit."""
    global _push_timer
    with _push_lock:
        if _push_timer is not None:
            _push_timer.cancel()
            _push_timer = None
    if _is_active():
        _do_push(verbose=False)


# ─── Setup / Config wizard ─────────────────────────────────────────────────────

def run_config_wizard(project_dir: Path, journals_dir: Path) -> Optional[dict]:
    """Interactive guided setup for sync configuration.

    Returns the sync config dict if successful, None if cancelled.
    """
    from .tm_ui import Colors

    print(f"\n{Colors.HEADER}{'═' * 50}{Colors.RESET}")
    print(f"{Colors.BOLD}  Journal Sync Configuration{Colors.RESET}")
    print(f"{Colors.HEADER}{'═' * 50}{Colors.RESET}\n")
    print(f"  This will create a private git repository to sync")
    print(f"  your journal files across machines.\n")

    # Step 1: Choose provider
    print(f"{Colors.BOLD}  Step 1: Git provider{Colors.RESET}")
    print(f"    1. GitHub")
    print(f"    2. GitLab")
    print(f"    3. Custom remote URL (any git host)")

    provider_choice = _prompt("  Choose [1/2/3]: ", default="1")
    if provider_choice not in ("1", "2", "3"):
        print(f"{Colors.ERROR}  Invalid choice. Cancelled.{Colors.RESET}")
        return None

    if provider_choice == "3":
        # Custom URL — just ask for it
        remote_url = _prompt("  Remote URL (SSH or HTTPS): ")
        if not remote_url:
            print(f"{Colors.ERROR}  Cancelled.{Colors.RESET}")
            return None
        token = None
        provider = "custom"
    else:
        provider = "github" if provider_choice == "1" else "gitlab"
        api_host = "github.com" if provider == "github" else "gitlab.com"

        # Step 2: Credentials
        print(f"\n{Colors.BOLD}  Step 2: Authentication{Colors.RESET}")
        print(f"  A personal access token is needed to create the repo.")
        if provider == "github":
            print(f"  Create one at: https://github.com/settings/tokens")
            print(f"  Required scope: 'repo'")
        else:
            print(f"  Create one at: https://gitlab.com/-/user_settings/personal_access_tokens")
            print(f"  Required scope: 'api'")

        token = _prompt("  Personal access token: ")
        if not token:
            print(f"{Colors.ERROR}  Cancelled.{Colors.RESET}")
            return None

        username = _prompt("  Username: ")
        if not username:
            print(f"{Colors.ERROR}  Cancelled.{Colors.RESET}")
            return None

        # Step 3: Repo name
        print(f"\n{Colors.BOLD}  Step 3: Repository{Colors.RESET}")
        repo_name = _prompt("  Repo name [ttm-journal]: ", default="ttm-journal")

        # Try to create the repo
        print(f"\n  Creating private repo '{repo_name}'...")
        success, message = _create_remote_repo(provider, username, token, repo_name)
        if not success:
            if "already exists" in message.lower():
                print(f"  {Colors.DIM}Repo already exists — will use it.{Colors.RESET}")
            else:
                print(f"  {Colors.ERROR}Error: {message}{Colors.RESET}")
                retry = _prompt("  Continue anyway with manual URL? [y/N]: ", default="n")
                if retry.lower() != "y":
                    return None
                remote_url = _prompt("  Remote URL: ")
                if not remote_url:
                    return None
                token = None
                provider = "custom"

        if provider != "custom":
            if provider == "github":
                remote_url = f"https://github.com/{username}/{repo_name}.git"
            else:
                remote_url = f"https://gitlab.com/{username}/{repo_name}.git"

    # Step 4: Branch
    branch = _prompt("\n  Branch name [main]: ", default="main")

    # Save config
    sync_config = {
        "enabled": True,
        "remote": remote_url,
        "branch": branch,
    }

    # Save secrets if token was provided
    if token:
        secrets = {"sync_token": token}
        _save_secrets(project_dir, secrets)
        print(f"\n  {Colors.DIM}Token saved to .ttm_secrets (git-ignored){Colors.RESET}")

    # Initialize the local git repo and do first push
    print(f"\n  Initializing sync...")
    global _sync_config, _journals_dir, _secrets
    _sync_config = sync_config
    _journals_dir = journals_dir
    _secrets = {"sync_token": token} if token else {}

    remote_with_auth = _resolve_remote_url()

    if not _is_git_repo(journals_dir):
        _git_init(journals_dir, remote_with_auth, branch)
    else:
        _run_git(["remote", "set-url", "origin", remote_with_auth])

    # Check if remote already has content (existing backup from another machine)
    fetch_result = _run_git(["fetch", "origin", branch])
    remote_has_content = False
    if fetch_result is not None:
        # Check if the remote branch exists and has commits
        log_result = _run_git(["log", f"origin/{branch}", "--oneline", "-1"])
        remote_has_content = log_result is not None and log_result.strip() != ""

    if remote_has_content:
        # Remote has an existing journal backup
        print(f"\n  {Colors.BOLD}Existing journal backup found on remote!{Colors.RESET}")
        print(f"    D = Download remote journal (replace local)")
        print(f"    M = Merge (keep both, remote + local)")
        print(f"    P = Push local (overwrite remote)")
        action = _prompt("  Choose [D/M/P]: ", default="D").upper()

        if action == "D":
            # Download: reset local to remote content
            _run_git(["checkout", f"origin/{branch}", "--", "."])
            _run_git(["add", "-A"])
            _run_git(["commit", "-m", "Downloaded journal from remote", "--allow-empty"])
            # Set tracking
            _run_git(["branch", f"--set-upstream-to=origin/{branch}", branch])
            print(f"\n  {Colors.BOLD}Journal downloaded from remote.{Colors.RESET}")
        elif action == "M":
            # Merge: commit local, then pull with rebase
            _run_git(["add", "-A"])
            _run_git(["commit", "-m", "Local journal before merge", "--allow-empty"])
            merge_result = _run_git(["pull", "--rebase", "origin", branch])
            if merge_result is None:
                _run_git(["rebase", "--abort"])
                print(f"  {Colors.ERROR}Merge conflict — keeping local version.{Colors.RESET}")
            _run_git(["push", "-u", "origin", branch])
            print(f"\n  {Colors.BOLD}Journals merged.{Colors.RESET}")
        else:
            # Push: force-push local over remote
            _run_git(["add", "-A"])
            _run_git(["commit", "-m", "Initial journal sync", "--allow-empty"])
            _run_git(["push", "--force", "-u", "origin", branch])
            print(f"\n  {Colors.BOLD}Local journal pushed (remote overwritten).{Colors.RESET}")
    else:
        # Fresh remote — initial commit and push
        _run_git(["add", "-A"])
        _run_git(["commit", "-m", "Initial journal sync", "--allow-empty"])
        push_result = _run_git(["push", "-u", "origin", branch])
        if push_result is not None:
            print(f"\n  {Colors.BOLD}Initial sync completed.{Colors.RESET}")
        else:
            print(f"\n  {Colors.ERROR}Push failed. Check credentials and try 'sync' later.{Colors.RESET}")
            print(f"  Config saved — sync will retry on next change.")

    print(f"  Remote: {remote_url}")
    print(f"  Branch: {branch}")
    print(f"\n{Colors.HEADER}{'═' * 50}{Colors.RESET}\n")
    return sync_config


# ─── Private helpers ───────────────────────────────────────────────────────────

def _is_active() -> bool:
    return _sync_config is not None and _journals_dir is not None


def _load_secrets(project_dir: Path) -> dict:
    secrets_path = project_dir / SECRETS_FILE
    if not secrets_path.exists():
        return {}
    try:
        with open(secrets_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_secrets(project_dir: Path, secrets: dict) -> None:
    secrets_path = project_dir / SECRETS_FILE
    try:
        with open(secrets_path, "w", encoding="utf-8") as f:
            json.dump(secrets, f, indent=2)
    except OSError:
        pass


def _resolve_remote_url() -> str:
    """Build the remote URL, injecting token for HTTPS if available."""
    remote = _sync_config.get("remote", "")
    token = (_secrets or {}).get("sync_token", "")

    if not token:
        return remote

    # Only inject token for HTTPS URLs
    if remote.startswith("https://"):
        # https://github.com/user/repo.git → https://<token>@github.com/user/repo.git
        return remote.replace("https://", f"https://oauth2:{token}@", 1)

    return remote


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def _ensure_git_identity() -> None:
    """Set git user.name/email locally if not configured (prevents commit failures)."""
    name = _run_git(["config", "user.name"])
    if not name or not name.strip():
        _run_git(["config", "user.name", "TTM Sync"])
    email = _run_git(["config", "user.email"])
    if not email or not email.strip():
        _run_git(["config", "user.email", "ttm@local"])


def _git_init(journals_dir: Path, remote_url: str, branch: str) -> None:
    """Initialize a new git repo in journals directory, preserving local files."""
    # Backup existing local journal files BEFORE any git operation
    local_files = {p.name: p.read_bytes() for p in journals_dir.glob("*.txt") if p.is_file()}

    _run_git(["init"])
    _run_git(["remote", "add", "origin", remote_url])

    # Create .gitignore for the journals repo (ignore nothing by default)
    gitignore_path = journals_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text("# Journal sync repo\n", encoding="utf-8")

    # Try to fetch and checkout existing remote content
    fetch_ok = _run_git(["fetch", "origin", branch])
    if fetch_ok is not None:
        # Remote has content — set local branch to track it
        _run_git(["checkout", "-b", branch, f"origin/{branch}"])
        # Restore local files that were overwritten (local takes priority)
        for name, content in local_files.items():
            (journals_dir / name).write_bytes(content)
    else:
        # No remote content or no connection — start fresh local branch
        _run_git(["checkout", "-b", branch])


def _run_git(args: list, timeout: int = 30) -> Optional[str]:
    """Run a git command in the journals directory. Returns stdout or None on failure."""
    if _journals_dir is None:
        return None

    global _last_git_error
    cmd = ["git"] + args
    env = dict(__import__("os").environ)
    # Prevent git from opening interactive credential prompts
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_journals_dir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode == 0:
            _last_git_error = ""
            return result.stdout
        _last_git_error = (result.stderr or result.stdout or "").strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _last_git_error = str(exc)
        return None


def _do_push(verbose: bool = False) -> bool:
    """Commit all changes and push to remote."""
    if not _is_active():
        return False

    _print_sync("Syncing...")

    branch = _sync_config.get("branch", "main")

    # Ensure remote URL is current
    remote_url = _resolve_remote_url()
    _run_git(["remote", "set-url", "origin", remote_url])

    # Acquire journal file lock during add+commit to prevent capturing
    # a partially-written file from the main thread
    from .tm_journal import file_lock as _journal_lock
    with _journal_lock:
        # Stage all changes
        _run_git(["add", "-A"])

        # Check if there's anything to commit
        status = _run_git(["status", "--porcelain"])
        if status is None or status.strip() == "":
            if verbose:
                _print_sync("Nothing to push")
            return True

        # Commit
        _ensure_git_identity()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        commit_result = _run_git(["commit", "-m", f"sync: {timestamp}"])
        if commit_result is None:
            detail = _last_git_error.split("\n")[0] if _last_git_error else "unknown error"
            _print_sync(f"Commit failed: {detail}")
            return False

    # Pull before push to handle diverged histories (no lock needed — network op)
    _run_git(["pull", "--rebase", "origin", branch])

    # Push
    push_result = _run_git(["push", "origin", branch])
    if push_result is None:
        detail = _last_git_error.split("\n")[0] if _last_git_error else "unknown error"
        _print_sync(f"Push failed: {detail}")
        return False

    if verbose:
        _print_sync("Pushed successfully")
    else:
        _print_sync("ok")
    return True


def _do_push_background() -> None:
    """Background push (called by debounce timer)."""
    try:
        _do_push(verbose=False)
    except Exception as exc:
        _print_sync(f"Background sync error: {exc}")


def _resolve_pull_conflict(branch: str) -> bool:
    """Handle pull when there are local uncommitted changes."""
    from .tm_ui import Colors

    print(f"\n{Colors.HEADER}  Remote has changes and you have local modifications.{Colors.RESET}")
    print(f"    L = Keep local (stash, pull, re-apply local on top)")
    print(f"    R = Use remote (discard local uncommitted changes)")
    print(f"    S = Skip pull (keep working offline)")

    choice = _prompt("  Choose [L/R/S]: ", default="L").upper()

    if choice == "L":
        # Commit local, then rebase
        _run_git(["add", "-A"])
        _run_git(["commit", "-m", "local changes before pull"])
        result = _run_git(["pull", "--rebase", "origin", branch])
        if result is None:
            # Rebase conflict — abort and inform user
            _run_git(["rebase", "--abort"])
            _print_sync("Rebase conflict — keeping local version")
            return False
        _print_sync("Pulled and rebased local changes on top")
        return True
    elif choice == "R":
        _run_git(["checkout", "--", "."])
        _run_git(["pull", "origin", branch])
        _print_sync("Updated to remote version")
        return True
    else:
        _print_sync("Skipped pull — working offline")
        return False


def _create_remote_repo(provider: str, username: str, token: str, repo_name: str) -> Tuple[bool, str]:
    """Create a private repo via API. Returns (success, message)."""
    if provider == "github":
        return _create_github_repo(token, repo_name)
    elif provider == "gitlab":
        return _create_gitlab_repo(token, repo_name)
    return False, "Unknown provider"


def _create_github_repo(token: str, repo_name: str) -> Tuple[bool, str]:
    """Create a private repo on GitHub."""
    url = "https://api.github.com/user/repos"
    data = json.dumps({"name": repo_name, "private": True, "auto_init": False}).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                return True, "Created"
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 422 and "already_exists" in body:
            return False, "Repository already exists"
        return False, f"HTTP {e.code}: {body[:200]}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"Network error: {e}"

    return False, "Unknown error"


def _create_gitlab_repo(token: str, repo_name: str) -> Tuple[bool, str]:
    """Create a private repo on GitLab."""
    url = "https://gitlab.com/api/v4/projects"
    data = json.dumps({"name": repo_name, "visibility": "private", "initialize_with_readme": False}).encode()
    headers = {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                return True, "Created"
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 400 and "has already been taken" in body:
            return False, "Repository already exists"
        return False, f"HTTP {e.code}: {body[:200]}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"Network error: {e}"

    return False, "Unknown error"


def _print_sync(message: str) -> None:
    """Route sync messages through the system log."""
    try:
        from .tm_log import log
        log("sync", message)
    except ImportError:
        pass


def _prompt(text: str, default: str = "") -> str:
    """Prompt user for input with optional default."""
    try:
        value = input(text).strip()
        return value if value else default
    except (EOFError, KeyboardInterrupt):
        return default


def get_sync_user() -> str:
    """Return the username from the sync remote URL, or empty string."""
    if not _sync_config:
        return ""
    remote = _sync_config.get("remote", "")
    # Extract user from URL patterns:
    #   https://github.com/User/repo.git -> User
    #   git@github.com:User/repo.git -> User
    import re
    m = re.search(r"[/:]([^/:]+)/[^/]+(?:\.git)?$", remote)
    return m.group(1) if m else ""
