"""Injectable clock. ASSUMPTIONS #16.

Scoring must be reproducible: a backtest or demo scenario replays historical timestamps, so
nothing in the engine may read wall-clock time directly. Everything calls ``now()`` here.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

_frozen: datetime | None = None


def now() -> datetime:
    """Current UTC time, or the frozen time if a scenario has pinned one."""
    return _frozen or datetime.now(timezone.utc)


def utc(dt: datetime) -> datetime:
    """Coerce to timezone-aware UTC. Naive input is assumed to already be UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """ISO-8601 with a trailing ``Z``, matching §9's examples."""
    if dt is None:
        return None
    return utc(dt).isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def frozen(at: datetime):
    """Pin ``now()`` for deterministic scoring runs."""
    global _frozen
    prior, _frozen = _frozen, utc(at)
    try:
        yield _frozen
    finally:
        _frozen = prior
