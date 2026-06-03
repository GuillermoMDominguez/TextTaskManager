"""Application configuration — reads from .ttm_config via tm_settings."""

from tm_settings import get_setting

VALID_STATES: list[str] = get_setting("states", ["BACKLOG", "IN PROGRESS", "WAITING", "TESTING", "DONE", "CANCELLED"])
STATE_ALIASES: dict[str, str] = get_setting("state_aliases", {"IN TESTING": "TESTING"})
FINISHED_STATES: list[str] = get_setting("finished_states", ["DONE", "CANCELLED"])
VALID_PRIORITIES: list[str] = get_setting("priorities", ["LOW", "MEDIUM", "HIGH", "URGENT"])
PRIORITY_ALIASES: dict[str, str] = get_setting("priority_aliases", {"L": "LOW", "M": "MEDIUM", "H": "HIGH", "U": "URGENT"})
DEFAULT_STATE: str = get_setting("default_state", "BACKLOG")
VALID_RECURRENCES = ("daily", "weekly", "biweekly", "monthly", "yearly")
RECURRENCE_ALIASES = {
    "D": "daily",
    "W": "weekly",
    "BW": "biweekly",
    "M": "monthly",
    "Y": "yearly",
}
APP_VERSION = "1.2"
BANNER_INNER_WIDTH = 46
