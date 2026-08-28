"""Data quality and staleness. ASSUMPTIONS #5.

This module implements the single design decision §6 calls out as most differentiating:

    "Build a data-quality/staleness flag per borrower — if a data source goes silent
    (originator stops syncing), the score should visibly degrade in confidence, not silently
    freeze at the last-known value."

Freshness decays exponentially per feed after a grace period, weighted by that feed's
importance and discounted by whether the source is directly integrated or self-reported
(§13: single-source unverifiable data must carry lower confidence).
"""

from __future__ import annotations

import math
from datetime import datetime

from continuum import config
from continuum.clock import utc


def feed_freshness(last_sync: datetime | None, as_of: datetime, feed: str) -> float:
    """Decayed freshness of one feed in [0, 1].

    1.0 inside the feed's grace period, then halving every ``halflife_hours``. A feed that has
    never reported returns 0.0 — never having data differs from having stale data, and both
    must be distinguishable from healthy.
    """
    sla = config.FEED_SLA.get(feed)
    if sla is None:
        return 0.0
    if last_sync is None:
        return 0.0

    age_hours = (utc(as_of) - utc(last_sync)).total_seconds() / 3600.0
    if age_hours <= sla["grace_hours"]:
        return 1.0

    overdue = age_hours - sla["grace_hours"]
    return float(0.5 ** (overdue / sla["halflife_hours"]))


def data_quality_score(
    source_freshness: dict[str, datetime | None], as_of: datetime
) -> tuple[float, dict[str, float]]:
    """§9's ``data_quality_score`` plus the per-feed detail behind it.

    Returns ``(aggregate, {feed: freshness})``. The detail matters for the dashboard: an
    aggregate of 0.71 tells a lender the data is degraded but not *which* feed went dark,
    and §6 requires that to be visible.
    """
    detail: dict[str, float] = {}
    weighted_sum = 0.0
    weight_total = 0.0

    for feed, sla in config.FEED_SLA.items():
        fresh = feed_freshness(source_freshness.get(feed), as_of, feed)
        detail[feed] = round(fresh, 4)
        # §13: self-reported sources are discounted even when perfectly fresh.
        weighted_sum += sla["weight"] * fresh * sla["corroboration"]
        weight_total += sla["weight"]

    aggregate = weighted_sum / weight_total if weight_total else 0.0
    return max(config.DATA_QUALITY_FLOOR, round(aggregate, 4)), detail


def staleness_summary(detail: dict[str, float]) -> tuple[str, list[str]]:
    """Human-readable staleness state for the dashboard and the score's trigger detail.

    Returns ``(level, [degraded_feed, ...])`` where level is ``fresh`` | ``degraded`` | ``stale``.
    """
    degraded = sorted(
        [f for f, v in detail.items() if v < 0.75], key=lambda f: detail[f]
    )
    if not degraded:
        return "fresh", []
    worst = min(detail[f] for f in degraded)
    return ("stale" if worst < 0.35 else "degraded"), degraded
