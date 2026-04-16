"""
Centralized timestamp utilities.
All event pipelines use now_iso() to ensure consistent ISO-format strings.
"""
from datetime import datetime, timezone


def now_iso() -> str:
    """Return current UTC time as an ISO 8601 string. Always timezone-aware."""
    return datetime.now(timezone.utc).isoformat()


def to_iso(dt: datetime) -> str:
    """Convert any datetime object to an ISO 8601 string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
