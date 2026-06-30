"""Jira API handlers."""

from pathlib import Path

from ..serializers import error_response, json_response
from ..state import _state_proxy as _state


def api_get_jira(handler, params):
    """GET /api/jira — Jira integration data."""
    try:
        from src import tm_jira
        from src.tm_jira import is_configured, init_jira, _get_active_issues
    except ImportError:
        json_response(handler, {"configured": False, "issues": [], "error": "Jira module not available"})
        return

    if not is_configured():
        journal_dir = Path(_state.journal_path).resolve().parent
        if not init_jira(journal_dir):
            init_jira(journal_dir.parent)

    if not is_configured():
        json_response(handler, {"configured": False, "issues": []})
        return

    try:
        filter_type = params.get("filter", ["active"])[0]
        search_query = params.get("q", [""])[0]
        base_url = tm_jira._jira_url or ""

        if filter_type == "active":
            result = _get_active_issues()
        elif filter_type in ("todo", "progress", "done", "review", "blocked", "cancelled"):
            from src.tm_jira import _get_filtered_issues
            result = _get_filtered_issues(filter_type)
        elif filter_type == "overdue":
            from src.tm_jira import _get_overdue
            result = _get_overdue()
        elif filter_type == "find" and search_query:
            from src.tm_jira import _search_issues
            result = _search_issues(search_query)
        elif filter_type == "notify":
            from src.tm_jira import _get_unread_comments
            messages = _get_unread_comments()
            json_response(handler, {"configured": True, "notifications": messages, "base_url": base_url})
            return
        else:
            result = _get_active_issues()

        issues = []
        if result and isinstance(result, dict):
            issues = result.get("issues", [])
        elif result and isinstance(result, list):
            issues = result

        serialized = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            fields = issue.get("fields", {})
            serialized.append({
                "key": issue.get("key"),
                "summary": fields.get("summary"),
                "status": fields.get("status", {}).get("name") if isinstance(fields.get("status"), dict) else None,
                "priority": fields.get("priority", {}).get("name") if isinstance(fields.get("priority"), dict) else None,
                "project": fields.get("project", {}).get("key") if isinstance(fields.get("project"), dict) else None,
                "due_date": fields.get("duedate"),
                "type": fields.get("issuetype", {}).get("name") if isinstance(fields.get("issuetype"), dict) else None,
            })

        json_response(handler, {"configured": True, "issues": serialized, "base_url": base_url})
    except Exception as e:
        json_response(handler, {"configured": True, "issues": [], "error": str(e)})


def api_get_jira_transitions(handler, params):
    """GET /api/jira/transitions?key=ISSUE-123 — get available transitions."""
    try:
        from src import tm_jira
        from src.tm_jira import is_configured, init_jira, get_issue_transitions
    except ImportError:
        json_response(handler, {"error": "Jira module not available"}, 500)
        return

    issue_key = params.get("key", [None])[0]
    if not issue_key:
        error_response(handler, "key parameter is required")
        return

    if not is_configured():
        journal_dir = Path(_state.journal_path).resolve().parent
        if not init_jira(journal_dir):
            init_jira(journal_dir.parent)

    if not is_configured():
        error_response(handler, "Jira not configured", 400)
        return

    try:
        transitions = get_issue_transitions(issue_key)
        json_response(handler, {"key": issue_key, "transitions": transitions or []})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_post_jira_transition(handler, params):
    """POST /api/jira/transition — transition a Jira issue."""
    from ..serializers import read_body

    body = read_body(handler)
    issue_key = body.get("key", "").strip()
    transition_name = body.get("transition", "").strip()

    if not issue_key or not transition_name:
        error_response(handler, "key and transition are required")
        return

    try:
        from src import tm_jira
        from src.tm_jira import is_configured, init_jira, transition_issue

        if not is_configured():
            journal_dir = Path(_state.journal_path).resolve().parent
            if not init_jira(journal_dir):
                init_jira(journal_dir.parent)

        if not is_configured():
            error_response(handler, "Jira not configured", 400)
            return

        success = transition_issue(issue_key, transition_name)
        if success:
            json_response(handler, {"ok": True, "key": issue_key, "transition": transition_name})
        else:
            error_response(handler, f"Failed to transition {issue_key} to '{transition_name}'", 500)
    except Exception as e:
        error_response(handler, str(e), 500)


def api_post_jira_mark_all(handler, params):
    """POST /api/jira/mark-all — mark all Jira notifications as read."""
    try:
        from src import tm_jira
        from src.tm_jira import mark_all_notifications_read

        mark_all_notifications_read()
        json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)


def api_post_jira_mark(handler, params):
    """POST /api/jira/mark — mark a single Jira notification as read."""
    from ..serializers import read_body

    body = read_body(handler)
    comment_id = body.get("comment_id", "").strip()
    if not comment_id:
        error_response(handler, "comment_id is required")
        return
    try:
        from src import tm_jira
        from src.tm_jira import mark_notification_read

        mark_notification_read(comment_id)
        json_response(handler, {"ok": True})
    except Exception as e:
        error_response(handler, str(e), 500)
