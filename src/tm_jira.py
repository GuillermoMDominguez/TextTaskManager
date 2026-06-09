"""Jira integration module — query and manage Jira tasks from TTM.

Architecture:
    .ttm_secrets → credentials (jira_url, jira_email, jira_api_token)
    requests     → Jira REST API v3

This module is entirely optional. If Jira is not configured, all public
functions return gracefully. Configure with 'config jira' command.
"""

import json
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

from .tm_settings import load_secrets, save_secrets
from .tm_ui import Colors


# ─── Module state ──────────────────────────────────────────────────────────────

_jira_url: str = ""
_jira_email: str = ""
_jira_token: str = ""
_jira_account_id: str = ""
_auth: Optional[tuple] = None
_headers = {"Accept": "application/json", "Content-Type": "application/json"}
_project_dir: Optional[Path] = None

LAST_SEEN_FILE = ".jira_last_seen"

# Store last notify results for mark command
_last_unread: list = []


# ─── Status filter mapping ─────────────────────────────────────────────────────

STATUS_FILTERS = {
    "todo": 'status = "To Do"',
    "progress": 'status = "In Progress"',
    "done": "statusCategory = Done",
    "cancelled": 'status = "Cancelled" OR status = "Canceled"',
    "review": 'status = "In Review"',
    "blocked": 'status = "Blocked"',
}

ALIASES = {
    "a": "active",
    "t": "todo",
    "p": "progress",
    "rv": "review",
    "bk": "blocked",
    "d": "done",
    "cn": "cancelled",
    "od": "overdue",
    "n": "notify",
    "m": "mark",
    "mv": "move",
    "f": "find",
    "o": "open",
}


# ─── Public API ────────────────────────────────────────────────────────────────

def init_jira(project_dir: Path) -> bool:
    """Initialize Jira subsystem from secrets. Returns True if configured."""
    global _jira_url, _jira_email, _jira_token, _jira_account_id, _auth, _project_dir

    if requests is None:
        return False

    _project_dir = project_dir
    secrets = load_secrets(project_dir)

    _jira_url = secrets.get("jira_url", "").rstrip("/")
    _jira_email = secrets.get("jira_email", "")
    _jira_token = secrets.get("jira_api_token", "")

    if not all([_jira_url, _jira_email, _jira_token]):
        return False

    _auth = (_jira_email, _jira_token)

    # Fetch accountId for mention detection (cached in secrets)
    _jira_account_id = secrets.get("jira_account_id", "")
    if not _jira_account_id:
        _jira_account_id = _fetch_my_account_id() or ""
        if _jira_account_id:
            secrets["jira_account_id"] = _jira_account_id
            save_secrets(project_dir, secrets)

    return True


def is_configured() -> bool:
    """Check if Jira credentials are available."""
    return _auth is not None


def run_config_wizard(project_dir: Path) -> bool:
    """Interactive wizard to configure Jira credentials. Returns True on success."""
    if requests is None:
        print(f"  {Colors.ERROR}The 'requests' library is required for Jira integration.{Colors.RESET}")
        print(f"  {Colors.DIM}Install with: pip install requests{Colors.RESET}")
        return False

    print(f"\n  {Colors.HEADER}{'─' * 50}{Colors.RESET}")
    print(f"  {Colors.HEADER}Jira Configuration{Colors.RESET}")
    print(f"  {Colors.HEADER}{'─' * 50}{Colors.RESET}\n")

    # Load existing secrets to preserve other keys
    secrets = load_secrets(project_dir)
    existing_url = secrets.get("jira_url", "")
    existing_email = secrets.get("jira_email", "")

    # Step 1: Jira URL
    print(f"  {Colors.DIM}Step 1: Jira instance URL{Colors.RESET}")
    print(f"  {Colors.DIM}Example: https://yourcompany.atlassian.net{Colors.RESET}")
    default_hint = f" [{existing_url}]" if existing_url else ""
    jira_url = _prompt(f"  Jira URL{default_hint}: ") or existing_url
    if not jira_url:
        print(f"  {Colors.ERROR}URL is required. Aborting.{Colors.RESET}")
        return False
    jira_url = jira_url.rstrip("/")
    print()

    # Step 2: Email
    print(f"  {Colors.DIM}Step 2: Your Jira account email{Colors.RESET}")
    default_hint = f" [{existing_email}]" if existing_email else ""
    jira_email = _prompt(f"  Email{default_hint}: ") or existing_email
    if not jira_email:
        print(f"  {Colors.ERROR}Email is required. Aborting.{Colors.RESET}")
        return False
    print()

    # Step 3: API Token
    print(f"  {Colors.DIM}Step 3: Personal API Token{Colors.RESET}")
    print(f"  {Colors.DIM}Create at: https://id.atlassian.com/manage-profile/security/api-tokens{Colors.RESET}")
    jira_token = _prompt("  API Token: ")
    if not jira_token:
        print(f"  {Colors.ERROR}Token is required. Aborting.{Colors.RESET}")
        return False
    print()

    # Step 4: Test connection
    print(f"  {Colors.DIM}Testing connection...{Colors.RESET}")
    auth = (jira_email, jira_token)
    try:
        resp = requests.get(
            f"{jira_url}/rest/api/3/myself",
            auth=auth,
            headers=_headers,
            timeout=10,
        )
        if resp.status_code == 200:
            user_data = resp.json()
            display_name = user_data.get("displayName", jira_email)
            print(f"  {Colors.DIM}Connected as: {display_name}{Colors.RESET}")
        elif resp.status_code == 401:
            print(f"  {Colors.ERROR}Authentication failed. Check email/token.{Colors.RESET}")
            return False
        else:
            print(f"  {Colors.ERROR}HTTP {resp.status_code}. Check the URL.{Colors.RESET}")
            return False
    except requests.exceptions.ConnectionError:
        print(f"  {Colors.ERROR}Cannot connect to {jira_url}{Colors.RESET}")
        return False
    except requests.exceptions.Timeout:
        print(f"  {Colors.ERROR}Connection timed out.{Colors.RESET}")
        return False

    # Save to .ttm_secrets
    secrets["jira_url"] = jira_url
    secrets["jira_email"] = jira_email
    secrets["jira_api_token"] = jira_token
    save_secrets(project_dir, secrets)

    # Activate module
    init_jira(project_dir)

    print(f"\n  {Colors.DIM}Credentials saved to .ttm_secrets{Colors.RESET}")
    print(f"  {Colors.HEADER}Jira configured successfully.{Colors.RESET}\n")
    return True


def execute(command: str) -> None:
    """Execute a Jira subcommand (e.g. 'active', 'notify', 'move BD-123')."""
    parts = command.strip().split()
    if not parts:
        _cmd_help()
        return

    cmd = parts[0].lower()
    cmd = ALIASES.get(cmd, cmd)
    args = parts[1:]

    # Commands that work without credentials
    if cmd in ("h", "help", "?"):
        _cmd_help()
        return
    if cmd == "status":
        if is_configured():
            print(f"  Jira: {_jira_url}")
            print(f"  User: {_jira_email}")
        else:
            print(f"  {Colors.DIM}Jira: not configured. Run 'config jira'{Colors.RESET}")
        return

    # Everything else requires credentials
    if not is_configured():
        print(f"  {Colors.ERROR}Jira not configured. Run 'config jira' first.{Colors.RESET}")
        return

    if cmd in ("active", "all"):
        data = _get_active_issues()
        _display_issues(data, "Active Tasks (status != Done/Cancelled)")
    elif cmd in STATUS_FILTERS:
        data = _get_filtered_issues(cmd)
        _display_issues(data, f"Tasks - {cmd.upper()}")
    elif cmd == "overdue":
        data = _get_overdue()
        _display_issues(data, "Overdue Tasks")
    elif cmd == "find":
        query = " ".join(args)
        if not query:
            print(f"  {Colors.DIM}Usage: jira find <text>{Colors.RESET}")
        else:
            data = _search_issues(query)
            _display_issues(data, f'Search: "{query}"')
    elif cmd == "notify":
        messages = _get_unread_comments()
        _display_unread(messages)
    elif cmd == "mark":
        _cmd_mark(args)
    elif cmd == "move":
        if not args:
            print(f"  {Colors.DIM}Usage: jira move <KEY>  (e.g. jira move BD-123){Colors.RESET}")
        else:
            _cmd_move(args[0].upper())
    elif cmd == "open":
        if not args:
            print(f"  {Colors.DIM}Usage: jira open <KEY>  (e.g. jira open BD-123){Colors.RESET}")
        else:
            _cmd_open(args[0].upper())
    elif cmd == "full":
        data = _get_active_issues()
        _display_issues(data, "Active Tasks (status != Done/Cancelled)")
        messages = _get_unread_comments()
        _display_unread(messages)
    else:
        print(f"  {Colors.ERROR}Unknown jira command: {cmd}{Colors.RESET}")
        print(f"  Type 'jira help' for available commands.")


# ─── Commands ──────────────────────────────────────────────────────────────────

def _cmd_help():
    print(f"""
  {Colors.HEADER}Jira Commands{Colors.RESET} (prefix with 'jira')
  {Colors.DIM}{'─' * 50}{Colors.RESET}
  {Colors.BOLD}active{Colors.RESET}    {Colors.DIM}a{Colors.RESET}    Tasks with status != Done/Cancelled
  {Colors.BOLD}todo{Colors.RESET}      {Colors.DIM}t{Colors.RESET}    Tasks in "To Do"
  {Colors.BOLD}progress{Colors.RESET}  {Colors.DIM}p{Colors.RESET}    Tasks "In Progress"
  {Colors.BOLD}review{Colors.RESET}    {Colors.DIM}rv{Colors.RESET}   Tasks "In Review"
  {Colors.BOLD}blocked{Colors.RESET}   {Colors.DIM}bk{Colors.RESET}   Blocked tasks
  {Colors.BOLD}done{Colors.RESET}      {Colors.DIM}d{Colors.RESET}    Completed tasks
  {Colors.BOLD}cancelled{Colors.RESET} {Colors.DIM}cn{Colors.RESET}   Cancelled tasks
  {Colors.BOLD}overdue{Colors.RESET}   {Colors.DIM}od{Colors.RESET}   Overdue tasks (past due date)
  {Colors.BOLD}find{Colors.RESET}      {Colors.DIM}f{Colors.RESET}    Search by text (e.g. jira find login)
  {Colors.BOLD}notify{Colors.RESET}    {Colors.DIM}n{Colors.RESET}    Unread messages
  {Colors.BOLD}mark{Colors.RESET}      {Colors.DIM}m{Colors.RESET}    Mark read (jira mark all | jira mark 1 3)
  {Colors.BOLD}move{Colors.RESET}      {Colors.DIM}mv{Colors.RESET}   Change status (e.g. jira move BD-123)
  {Colors.BOLD}open{Colors.RESET}      {Colors.DIM}o{Colors.RESET}    Open in browser (e.g. jira open BD-123)
  {Colors.BOLD}full{Colors.RESET}           Active tasks + unread messages
  {Colors.BOLD}status{Colors.RESET}         Show connection info
""")


def _cmd_open(key: str):
    """Open issue in browser."""
    url = f"{_jira_url}/browse/{key}"
    webbrowser.open(url)
    print(f"  {Colors.DIM}Opened {url}{Colors.RESET}")


def _cmd_move(issue_key: str):
    """Change status of an issue interactively."""
    data = _api_get(f"issue/{issue_key}/transitions")
    if not data:
        print(f"  {Colors.ERROR}Could not fetch transitions for {issue_key}{Colors.RESET}")
        return

    transitions = data.get("transitions", [])
    if not transitions:
        print(f"  {Colors.DIM}No transitions available for {issue_key}{Colors.RESET}")
        return

    print(f"\n  {Colors.HEADER}Move {issue_key} to:{Colors.RESET}")
    print(f"  {Colors.DIM}{'─' * 40}{Colors.RESET}")
    for i, tr in enumerate(transitions, 1):
        name = tr.get("name", "?")
        to_status = tr.get("to", {}).get("name", "")
        print(f"  {Colors.BOLD}{i:>2}.{Colors.RESET} {name:<20} {Colors.DIM}-> {to_status}{Colors.RESET}")
    print(f"  {Colors.DIM} 0. Cancel{Colors.RESET}")
    print()

    try:
        choice = input(f"  Select [0-{len(transitions)}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    if not choice or choice == "0":
        print(f"  {Colors.DIM}Cancelled.{Colors.RESET}")
        return

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(transitions):
            tr = transitions[idx]
            payload = {"transition": {"id": str(tr["id"])}}
            result = _api_post(f"issue/{issue_key}/transitions", payload)
            if result is not None:
                print(f"  {Colors.DIM}{issue_key} moved to '{tr['name']}'{Colors.RESET}")
            else:
                print(f"  {Colors.ERROR}Failed to transition {issue_key}{Colors.RESET}")
        else:
            print(f"  {Colors.ERROR}Invalid choice.{Colors.RESET}")
    except ValueError:
        print(f"  {Colors.ERROR}Invalid input.{Colors.RESET}")


def _cmd_mark(args: list):
    """Mark messages as read."""
    global _last_unread
    if not args or args == ["all"]:
        _mark_all_read()
        print(f"  {Colors.DIM}All messages marked as read.{Colors.RESET}")
    else:
        if not _last_unread:
            print(f"  {Colors.DIM}Run 'jira notify' first to load messages.{Colors.RESET}")
        else:
            ids_to_mark = []
            marked = []
            for arg in args:
                try:
                    idx = int(arg) - 1
                    if 0 <= idx < len(_last_unread):
                        ids_to_mark.append(_last_unread[idx]["id"])
                        marked.append(arg)
                except ValueError:
                    pass
            if ids_to_mark:
                _mark_read_by_ids(ids_to_mark)
                print(f"  {Colors.DIM}Marked #{', #'.join(marked)} as read.{Colors.RESET}")
            else:
                print(f"  {Colors.ERROR}Invalid numbers. Use: jira mark 1 2 3{Colors.RESET}")


# ─── Data fetching ─────────────────────────────────────────────────────────────

def _get_active_issues(max_results: int = 30):
    jql = (
        "assignee = currentUser() "
        "AND statusCategory != Done "
        "ORDER BY status ASC, priority DESC, updated DESC"
    )
    fields = ["summary", "status", "priority", "updated", "project", "issuetype", "duedate"]
    return _api_search(jql, fields, max_results)


def _get_filtered_issues(status_filter: str, max_results: int = 30):
    filter_jql = STATUS_FILTERS[status_filter]
    jql = f"assignee = currentUser() AND ({filter_jql})"
    jql += " ORDER BY priority DESC, updated DESC"
    fields = ["summary", "status", "priority", "updated", "project", "issuetype", "duedate"]
    return _api_search(jql, fields, max_results)


def _get_overdue(max_results: int = 20):
    today = datetime.now().strftime("%Y-%m-%d")
    jql = (
        f"assignee = currentUser() AND duedate < '{today}' "
        f"AND statusCategory != Done "
        f"ORDER BY duedate ASC"
    )
    fields = ["summary", "status", "priority", "duedate", "project", "issuetype", "updated"]
    return _api_search(jql, fields, max_results)


def _search_issues(text: str, max_results: int = 20):
    safe_text = text.replace('"', '\\"')
    jql = (
        f'assignee = currentUser() AND text ~ "{safe_text}" '
        f"AND statusCategory != Done "
        f"ORDER BY updated DESC"
    )
    fields = ["summary", "status", "priority", "updated", "project", "issuetype", "duedate"]
    return _api_search(jql, fields, max_results)


def _fetch_my_account_id() -> Optional[str]:
    """Fetch current user's accountId from Jira /myself endpoint."""
    try:
        resp = requests.get(
            f"{_jira_url}/rest/api/3/myself",
            auth=_auth, headers=_headers, timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("accountId")
    except Exception:
        return None


def _adf_mentions_user(body: dict, account_id: str) -> bool:
    """Check if an ADF body contains a @mention of the given accountId."""
    if not body or not account_id:
        return False
    return _walk_adf_for_mention(body, account_id)


def _walk_adf_for_mention(node, account_id: str) -> bool:
    """Recursively walk ADF nodes looking for a mention of account_id."""
    if isinstance(node, dict):
        if node.get("type") == "mention":
            attrs = node.get("attrs", {})
            if attrs.get("id") == account_id:
                return True
        for child in node.get("content", []):
            if _walk_adf_for_mention(child, account_id):
                return True
    elif isinstance(node, list):
        for child in node:
            if _walk_adf_for_mention(child, account_id):
                return True
    return False


def _get_unread_comments():
    """Fetch unread notifications: @mentions + comments on issues you reported.

    Matches Jira web notification behavior more closely by only surfacing:
    - Comments that @mention the current user (in ADF body)
    - Comments on issues where the current user is the reporter
    Skips all other comments on merely assigned/watched issues.
    """
    data = _get_read_data()
    last_seen = data.get("last_seen")
    read_ids = set(data.get("read_ids", []))

    if last_seen:
        jql = (
            "(assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser()) "
            f"AND updated >= '{last_seen[:10]}' ORDER BY updated DESC"
        )
    else:
        jql = (
            "(assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser()) "
            "AND updated >= -3d ORDER BY updated DESC"
        )

    search_data = _api_search(jql, ["summary", "status", "updated", "comment", "reporter"], 20)
    if not search_data:
        return []

    unread = []
    for issue in search_data.get("issues", []):
        fields = issue.get("fields", {})
        comments = fields.get("comment", {}).get("comments", [])
        # Check if current user is the reporter of this issue
        reporter_email = fields.get("reporter", {}).get("emailAddress", "")
        is_reporter = (_jira_email and reporter_email == _jira_email)

        for comment in comments:
            comment_id = comment.get("id", "")
            author = comment.get("author", {}).get("displayName", "")
            email = comment.get("author", {}).get("emailAddress", "")
            created = comment.get("created", "")

            # Skip my own comments
            if _jira_email and _jira_email in (email or ""):
                continue
            # Skip already read
            if comment_id in read_ids:
                continue
            # Only newer than last_seen
            if last_seen and created and created < last_seen:
                continue

            # Only surface if: user is @mentioned OR user is reporter
            adf_body = comment.get("body", {})
            is_mentioned = _adf_mentions_user(adf_body, _jira_account_id)

            if not is_mentioned and not is_reporter:
                continue

            unread.append({
                "id": comment_id,
                "key": issue["key"],
                "summary": fields["summary"],
                "author": author,
                "date": created[:16].replace("T", " ") if created else "",
                "body": _extract_text(adf_body),
                "reason": "mention" if is_mentioned else "reporter",
            })

    unread.sort(key=lambda x: x["date"], reverse=True)
    return unread[:20]


# ─── Display helpers ───────────────────────────────────────────────────────────

def _display_issues(data, label: str):
    if not data:
        return
    issues = data.get("issues", [])
    total = data.get("total", 0)

    print(f"\n  {Colors.HEADER}{'─' * 60}{Colors.RESET}")
    print(f"  {Colors.HEADER}{label} ({total}){Colors.RESET}")
    print(f"  {Colors.HEADER}{'─' * 60}{Colors.RESET}")

    if not issues:
        print(f"  {Colors.DIM}No issues found.{Colors.RESET}\n")
        return

    for i, issue in enumerate(issues, 1):
        f = issue["fields"]
        key = issue["key"]
        summary = f.get("summary", "No title")[:52]
        status = _fmt_status(f.get("status", {}))
        priority = _fmt_priority(f.get("priority"))
        project = f.get("project", {}).get("key", "")
        itype = f.get("issuetype", {}).get("name", "")
        due = f.get("duedate") or ""

        due_str = ""
        if due:
            try:
                due_date = datetime.strptime(due, "%Y-%m-%d")
                if due_date.date() < datetime.now().date():
                    due_str = f" \033[91m[OVERDUE {due}]\033[0m"
                else:
                    due_str = f" {Colors.DIM}[due {due}]{Colors.RESET}"
            except ValueError:
                pass

        print(f"  {Colors.DIM}{i:>2}.{Colors.RESET} {Colors.BOLD}{key:<11}{Colors.RESET} {status:<28} {priority:<18} {Colors.DIM}{itype}{Colors.RESET}")
        print(f"      {summary}{due_str}")
        print(f"      {Colors.DIM}{project} | updated {(f.get('updated') or '')[:10]}{Colors.RESET}")
    print()


def _display_unread(messages: list):
    """Display unread messages."""
    global _last_unread
    _last_unread = messages

    print(f"\n  {Colors.HEADER}{'─' * 60}{Colors.RESET}")
    print(f"  {Colors.HEADER}Unread Messages{Colors.RESET}")
    print(f"  {Colors.HEADER}{'─' * 60}{Colors.RESET}")

    if not messages:
        print(f"  {Colors.DIM}No unread messages. You're up to date!{Colors.RESET}\n")
        return

    for i, m in enumerate(messages, 1):
        reason_tag = " \033[95m@\033[0m" if m.get("reason") == "mention" else ""
        print(f"  \033[93m{i:>2}.\033[0m {Colors.BOLD}{m['key']:<11}{Colors.RESET} \033[96m{m['author']}\033[0m{reason_tag}  {Colors.DIM}{m['date']}{Colors.RESET}")
        print(f"      {m['summary']}")
        if m["body"]:
            body = m["body"]
            print(f"      {Colors.DIM}───{Colors.RESET}")
            while body:
                print(f"      {body[:100]}")
                body = body[100:]
            print(f"      {Colors.DIM}───{Colors.RESET}")
        print()
    print(f"  \033[93m{len(messages)} unread message(s)\033[0m")
    print(f"  {Colors.DIM}jira mark all = mark as read | jira mark 1 3 5 = mark specific{Colors.RESET}\n")


def _fmt_priority(priority):
    if not priority:
        return f"{Colors.DIM}--{Colors.RESET}"
    name = priority.get("name", "")
    if name in ("Highest", "Critical"):
        return f"\033[91m{name}\033[0m"
    elif name == "High":
        return f"\033[93m{name}\033[0m"
    elif name == "Medium":
        return f"\033[96m{name}\033[0m"
    return f"{Colors.DIM}{name}{Colors.RESET}"


def _fmt_status(status):
    name = status.get("name", "?")
    cat = status.get("statusCategory", {}).get("key", "")
    if cat == "done":
        return f"\033[92m{name}\033[0m"
    elif cat == "indeterminate":
        return f"\033[94m{name}\033[0m"
    elif cat == "new":
        return f"\033[93m{name}\033[0m"
    return f"{Colors.DIM}{name}{Colors.RESET}"


# ─── Last seen tracking ────────────────────────────────────────────────────────

def _get_read_data() -> dict:
    if _project_dir is None:
        return {"last_seen": None, "read_ids": []}
    path = _project_dir / LAST_SEEN_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_seen": None, "read_ids": []}


def _save_read_data(data: dict):
    if _project_dir is None:
        return
    path = _project_dir / LAST_SEEN_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _mark_all_read():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    _save_read_data({"last_seen": now, "read_ids": []})


def _mark_read_by_ids(ids_to_mark: list):
    data = _get_read_data()
    read_ids = set(data.get("read_ids", []))
    read_ids.update(ids_to_mark)
    data["read_ids"] = list(read_ids)[-500:]
    _save_read_data(data)


# ─── API layer ─────────────────────────────────────────────────────────────────

def _api_search(jql: str, fields: list, max_results: int = 50):
    """Search using POST /rest/api/3/search/jql."""
    url = f"{_jira_url}/rest/api/3/search/jql"
    payload = {"jql": jql, "maxResults": max_results, "fields": fields}
    try:
        resp = requests.post(url, auth=_auth, headers=_headers, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError:
        if resp.status_code == 401:
            print(f"  {Colors.ERROR}Jira: Invalid credentials. Run 'config jira'{Colors.RESET}")
        elif resp.status_code == 400:
            print(f"  {Colors.ERROR}Jira: Invalid JQL query{Colors.RESET}")
        else:
            print(f"  {Colors.ERROR}Jira: HTTP {resp.status_code}{Colors.RESET}")
        return None
    except requests.exceptions.ConnectionError:
        print(f"  {Colors.ERROR}Jira: Cannot connect to {_jira_url}{Colors.RESET}")
        return None
    except requests.exceptions.Timeout:
        print(f"  {Colors.ERROR}Jira: Connection timed out{Colors.RESET}")
        return None


def _api_get(endpoint: str):
    """GET request to Jira API."""
    url = f"{_jira_url}/rest/api/3/{endpoint}"
    try:
        resp = requests.get(url, auth=_auth, headers=_headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError:
        print(f"  {Colors.ERROR}Jira: HTTP {resp.status_code}{Colors.RESET}")
        return None
    except requests.exceptions.ConnectionError:
        print(f"  {Colors.ERROR}Jira: Cannot connect to {_jira_url}{Colors.RESET}")
        return None
    except requests.exceptions.Timeout:
        print(f"  {Colors.ERROR}Jira: Connection timed out{Colors.RESET}")
        return None


def _api_post(endpoint: str, payload: dict = None):
    """POST request to Jira API."""
    url = f"{_jira_url}/rest/api/3/{endpoint}"
    try:
        resp = requests.post(url, auth=_auth, headers=_headers, json=payload or {}, timeout=15)
        if resp.status_code == 204:
            return True
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError:
        print(f"  {Colors.ERROR}Jira: HTTP {resp.status_code}{Colors.RESET}")
        return None
    except requests.exceptions.ConnectionError:
        print(f"  {Colors.ERROR}Jira: Cannot connect to {_jira_url}{Colors.RESET}")
        return None
    except requests.exceptions.Timeout:
        print(f"  {Colors.ERROR}Jira: Connection timed out{Colors.RESET}")
        return None


# ─── ADF text extraction ──────────────────────────────────────────────────────

def _extract_text(body) -> str:
    """Extract plain text from Atlassian Document Format (ADF)."""
    if not body or not isinstance(body, dict):
        return ""
    texts: list = []
    _walk_adf(body, texts)
    return " ".join(texts)


def _walk_adf(node, texts: list):
    """Recursively walk ADF nodes to extract all text."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            texts.append(node.get("text", ""))
        for child in node.get("content", []):
            _walk_adf(child, texts)
    elif isinstance(node, list):
        for child in node:
            _walk_adf(child, texts)


# ─── Private helpers ───────────────────────────────────────────────────────────

def _prompt(text: str, default: str = "") -> str:
    try:
        value = input(text).strip()
        return value if value else default
    except (EOFError, KeyboardInterrupt):
        return default
