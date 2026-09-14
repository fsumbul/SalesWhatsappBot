"""Server-derived result scope, shared by SQL tools and conversation memory."""

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def day_window(user: Any) -> tuple[datetime, datetime]:
    tz = ZoneInfo(user.timezone or "Europe/Istanbul")
    start = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def scope(
    user: Any, kind: str, *, query: str = "", today: bool = False,
    direction: str | None = None, total: int = 0, page: int = 1,
    has_more: bool = False, subject: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    start, end = day_window(user)
    return {
        "record_type": kind, "audience": "selected" if subject else "matching" if query else "company",
        "subject": subject, "query": query, "timezone": str(start.tzinfo),
        "date_mode": "today" if today else "all_time",
        "local_today": start.date().isoformat(),
        "date_start": start.isoformat() if today else None,
        "date_end_exclusive": end.isoformat() if today else None,
        "date_field": "message_created_at" if kind in {"messages", "inbox"} else "created_at",
        "direction": (direction or "both") if kind == "messages" else None,
        "total": total, "page": page, "has_more": has_more,
        "complete": page == 1 and not has_more, **extra,
    }
