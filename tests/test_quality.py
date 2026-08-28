"""§6's staleness requirement, which the brief singles out as the differentiating design decision.

    "Build a data-quality/staleness flag per borrower — if a data source goes silent (originator
    stops syncing), the score should visibly degrade in confidence, not silently freeze at the
    last-known value."

The property under test is therefore not "the formula computes what ASSUMPTIONS #5 says", it is
"silence costs something, monotonically, and it is attributable to a named feed".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from continuum import config
from continuum.ingestion import quality

T0 = datetime(2026, 8, 11, tzinfo=timezone.utc)


def _all_fresh(as_of=T0) -> dict:
    return {feed: as_of - timedelta(hours=1) for feed in config.FEED_SLA}


# --------------------------------------------------------------------------------------
# Per-feed decay
# --------------------------------------------------------------------------------------


def test_inside_the_grace_period_is_perfectly_fresh():
    sla = config.FEED_SLA["bank_feed"]
    at = T0 - timedelta(hours=sla["grace_hours"] - 1)
    assert quality.feed_freshness(at, T0, "bank_feed") == 1.0


def test_freshness_halves_every_halflife_after_grace():
    sla = config.FEED_SLA["bank_feed"]
    one = T0 - timedelta(hours=sla["grace_hours"] + sla["halflife_hours"])
    two = T0 - timedelta(hours=sla["grace_hours"] + 2 * sla["halflife_hours"])
    assert quality.feed_freshness(one, T0, "bank_feed") == pytest.approx(0.5)
    assert quality.feed_freshness(two, T0, "bank_feed") == pytest.approx(0.25)


def test_never_reported_is_zero_and_distinct_from_stale():
    """Never having data differs from having old data; both must differ from healthy."""
    assert quality.feed_freshness(None, T0, "bank_feed") == 0.0
    stale = quality.feed_freshness(T0 - timedelta(days=30), T0, "bank_feed")
    assert 0.0 < stale < 1.0


def test_unknown_feed_is_not_silently_trusted():
    assert quality.feed_freshness(T0, T0, "not_a_feed") == 0.0


def test_decay_is_monotone_in_silence():
    ages = [1, 24, 48, 96, 240, 720]
    values = [quality.feed_freshness(T0 - timedelta(hours=h), T0, "invoice_feed") for h in ages]
    assert values == sorted(values, reverse=True)


# --------------------------------------------------------------------------------------
# Aggregate
# --------------------------------------------------------------------------------------


def test_self_reported_sources_are_discounted_even_when_fresh():
    """§13: single-source unverifiable data is lower-confidence input, not equal input.

    Every feed reporting one hour ago still cannot reach 1.0, because the document feed carries
    ``corroboration = 0.6``.
    """
    score, detail = quality.data_quality_score(_all_fresh(), T0)
    assert all(v == 1.0 for v in detail.values())
    assert score < 1.0
    expected = sum(
        s["weight"] * s["corroboration"] for s in config.FEED_SLA.values()
    ) / sum(s["weight"] for s in config.FEED_SLA.values())
    assert score == pytest.approx(expected, abs=1e-4)


def test_a_dark_feed_lowers_the_aggregate_in_proportion_to_its_weight():
    fresh, _ = quality.data_quality_score(_all_fresh(), T0)

    freshness = _all_fresh()
    freshness["invoice_feed"] = T0 - timedelta(days=21)  # weight 0.35
    heavy, _ = quality.data_quality_score(freshness, T0)

    freshness = _all_fresh()
    freshness["onchain_feed"] = T0 - timedelta(days=21)  # weight 0.05
    light, _ = quality.data_quality_score(freshness, T0)

    assert heavy < light < fresh
    assert fresh - heavy > config.MATERIALITY["data_quality_drop"]


def test_a_fully_dark_borrower_floors_rather_than_zeroing():
    score, detail = quality.data_quality_score({f: None for f in config.FEED_SLA}, T0)
    assert score == config.DATA_QUALITY_FLOOR
    assert set(detail) == set(config.FEED_SLA)


def test_detail_names_every_feed_so_the_dashboard_can_attribute_the_drop():
    _, detail = quality.data_quality_score(_all_fresh(), T0)
    assert set(detail) == set(config.FEED_SLA)


# --------------------------------------------------------------------------------------
# Staleness summary
# --------------------------------------------------------------------------------------


def test_staleness_levels():
    assert quality.staleness_summary({f: 1.0 for f in config.FEED_SLA}) == ("fresh", [])

    level, degraded = quality.staleness_summary(
        {"invoice_feed": 0.55, "bank_feed": 1.0, "accounting_feed": 1.0}
    )
    assert level == "degraded" and degraded == ["invoice_feed"]

    level, degraded = quality.staleness_summary(
        {"invoice_feed": 0.02, "accounting_feed": 0.5, "bank_feed": 1.0}
    )
    assert level == "stale"
    # Worst first, so the dashboard leads with the feed that actually went dark.
    assert degraded == ["invoice_feed", "accounting_feed"]
