"""Email transport helpers for cross-platform task report delivery."""

import json
import os
import smtplib
import subprocess
import tempfile
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote


@dataclass
class EmailConfig:
    """Runtime configuration for email sending."""

    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_sender: Optional[str] = None
    smtp_use_ssl: bool = False
    smtp_use_starttls: bool = True
    default_recipient: Optional[str] = None
    subject_prefix: str = "[TaskManager]"


@dataclass
class EmailResult:
    """Result object returned by email sending operations."""

    success: bool
    mode: str
    message: str


def _to_bool(value: object, default: bool) -> bool:
    """Convert common string/number values to bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "y"}:
        return True
    if normalized in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _first_existing_config(config_paths: List[Path]) -> Dict[str, object]:
    """Load the first existing JSON config file from candidate paths."""
    for path in config_paths:
        try:
            if path.exists() and path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def load_email_config(config_paths: Optional[List[Path]] = None) -> EmailConfig:
    """Load email settings from config file and environment variables.

    Environment variables override file values:
    - TM_EMAIL_SMTP_HOST
    - TM_EMAIL_SMTP_PORT
    - TM_EMAIL_SMTP_USER
    - TM_EMAIL_SMTP_PASSWORD
    - TM_EMAIL_SMTP_SENDER
    - TM_EMAIL_SMTP_SSL
    - TM_EMAIL_SMTP_STARTTLS
    - TM_EMAIL_DEFAULT_RECIPIENT
    - TM_EMAIL_SUBJECT_PREFIX
    """
    paths = config_paths or [Path.home() / ".task_manager_email.json"]
    file_data = _first_existing_config(paths)

    smtp_port_raw = os.getenv("TM_EMAIL_SMTP_PORT", str(file_data.get("smtp_port", 587)))
    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        smtp_port = 587

    return EmailConfig(
        smtp_host=os.getenv("TM_EMAIL_SMTP_HOST", str(file_data.get("smtp_host", "")).strip() or None),
        smtp_port=smtp_port,
        smtp_user=os.getenv("TM_EMAIL_SMTP_USER", str(file_data.get("smtp_user", "")).strip() or None),
        smtp_password=os.getenv("TM_EMAIL_SMTP_PASSWORD", str(file_data.get("smtp_password", "")).strip() or None),
        smtp_sender=os.getenv("TM_EMAIL_SMTP_SENDER", str(file_data.get("smtp_sender", "")).strip() or None),
        smtp_use_ssl=_to_bool(os.getenv("TM_EMAIL_SMTP_SSL", file_data.get("smtp_use_ssl")), False),
        smtp_use_starttls=_to_bool(os.getenv("TM_EMAIL_SMTP_STARTTLS", file_data.get("smtp_use_starttls")), True),
        default_recipient=os.getenv(
            "TM_EMAIL_DEFAULT_RECIPIENT", str(file_data.get("default_recipient", "")).strip() or None
        ),
        subject_prefix=os.getenv("TM_EMAIL_SUBJECT_PREFIX", str(file_data.get("subject_prefix", "[TaskManager]")).strip()),
    )


def _send_via_smtp(config: EmailConfig, recipient: str, subject: str, body: str) -> None:
    """Send an email using SMTP settings."""
    sender = config.smtp_sender or config.smtp_user
    if not config.smtp_host or not sender:
        raise ValueError("SMTP host/sender are not configured")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    if config.smtp_use_ssl:
        with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=20) as smtp:
            if config.smtp_user and config.smtp_password:
                smtp.login(config.smtp_user, config.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=20) as smtp:
            smtp.ehlo()
            if config.smtp_use_starttls:
                smtp.starttls()
                smtp.ehlo()
            if config.smtp_user and config.smtp_password:
                smtp.login(config.smtp_user, config.smtp_password)
            smtp.send_message(msg)


def _open_mailto_draft(recipient: str, subject: str, body: str) -> bool:
    """Open default mail client with prefilled draft via mailto URI."""
    uri = f"mailto:{quote(recipient)}?subject={quote(subject)}&body={quote(body)}"
    return _open_uri(uri)


def _open_uri(uri: str) -> bool:
    """Open a URI using platform launchers first, then webbrowser fallback."""
    try:
        if os.name == "nt":
            os.startfile(uri)  # type: ignore[attr-defined]
            return True

        if os.name == "posix":
            if os.uname().sysname.lower() == "darwin":
                result = subprocess.run(["open", uri], check=False, capture_output=True)
            else:
                result = subprocess.run(["xdg-open", uri], check=False, capture_output=True)
            if result.returncode == 0:
                return True
    except Exception:
        pass

    try:
        return webbrowser.open(uri)
    except Exception:
        return False


def _write_report_file(body: str) -> Optional[Path]:
    """Persist email body to a temp text file and return its path."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = Path(tempfile.gettempdir()) / f"taskmanager_pending_{ts}.txt"
        report_path.write_text(body, encoding="utf-8")
        return report_path
    except Exception:
        return None


def send_email_report(recipient: str, subject: str, body: str, config: EmailConfig) -> EmailResult:
    """Send pending task report using SMTP, or fallback to mailto draft."""
    smtp_error = None

    if config.smtp_host and (config.smtp_sender or config.smtp_user):
        try:
            _send_via_smtp(config, recipient, subject, body)
            return EmailResult(True, "smtp", "Email sent via SMTP.")
        except Exception as exc:
            smtp_error = str(exc)

    mailto_limit = 1700
    draft_opened = False
    used_body = True

    # Some mail clients (including Outlook in some setups) fail silently with long mailto bodies.
    if len(body) <= mailto_limit:
        draft_opened = _open_mailto_draft(recipient, subject, body)
    if not draft_opened:
        draft_opened = _open_uri(f"mailto:{quote(recipient)}?subject={quote(subject)}")
        used_body = False

    if draft_opened:
        if smtp_error:
            if not used_body:
                report_path = _write_report_file(body)
                if report_path:
                    return EmailResult(
                        True,
                        "mailto",
                        f"SMTP failed ({smtp_error}). Opened draft without body. Report saved at {report_path}.",
                    )
                return EmailResult(
                    True,
                    "mailto",
                    f"SMTP failed ({smtp_error}). Opened draft without body (client body-size limit).",
                )
            return EmailResult(
                True,
                "mailto",
                f"SMTP failed ({smtp_error}). Opened draft in default mail app instead.",
            )
        if not used_body:
            report_path = _write_report_file(body)
            if report_path:
                return EmailResult(
                    True,
                    "mailto",
                    f"Opened draft without body (client body-size limit). Report saved at {report_path}.",
                )
            return EmailResult(True, "mailto", "Opened draft without body (client body-size limit).")
        return EmailResult(True, "mailto", "Opened draft in default mail app.")

    if smtp_error:
        return EmailResult(False, "error", f"Could not send email (SMTP error: {smtp_error}).")

    return EmailResult(
        False,
        "error",
        "No email transport available. Configure SMTP or set a default mail app for mailto links.",
    )
