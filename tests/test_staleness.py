"""§4's staleness rule — the one the brief says to follow exactly.

    *"when a data source goes silent, the score keeps degrading under continued silence rather than
    plateauing or reversing upward. Silence is treated as worsening information, not neutral
    information."*

Both named failure modes get their own test, because both were reachable before this module
existed and neither is caught by testing the freshness decay:

  - ``test_penalty_never_plateaus`` — an exponential freshness decay bounded by
    ``CI_MAX_HALF_WIDTH`` stops costing after a few weeks. This asserts it never stops.
  - ``test_score_cannot_rise_while_a_feed_is_silent`` — ``days_since_last_late_payment`` counts
    upward with no new data, so silence reads as improvement. This asserts it cannot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from continuum import config
from continuum.scoring import staleness

T0 = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _fresh(as_of=T0) -> dict:
    return {feed: as_of - timedelta(hours=1) for feed in config.FEED_SLA}


def _dark(feeds: tuple[str, ...], days: float, as_of=T0) -> dict:
    out = _fresh(as_of)
    for feed in feeds:
        out[feed] = as_of - timedelta(days=days)
    return out


class _Payload:
    """Minimal stand-in for a recorded score — the ratchet reads two attributes."""

    def __init__(self, score_numeric: int, silent: bool) -> None:
        self.score_numeric = score_numeric
        self.staleness_silent = silent


# --------------------------------------------------------------------------------------
# Measuring silence
# --------------------------------------------------------------------------------------


def test_all_feeds_reporting_costs_nothing():
    a = staleness.assess(_fresh(), T0)
    assert not a.silent
    assert a.penalty_points == 0.0
    assert a.ratchet_ceiling is None


def test_grace_is_per_feed_not_flat():
    """The document feed syncs monthly by design; a flat grace reads it as permanently silent.

    That bug made every borrower stale, which meant the ratchet never found a fully-fresh
    observation to anchor to and therefore never engaged at all.
    """
    freshness = _fresh()
    freshness["document_feed"] = T0 - timedelta(days=6)
    assert not staleness.assess(freshness, T0).silent

    # Past the document feed's own 30-day grace, it does count.
    freshness["document_feed"] = T0 - timedelta(days=45)
    assert staleness.assess(freshness, T0).silent


def test_silence_inside_a_feeds_own_grace_is_free():
    a = staleness.assess(_dark(("bank_feed",), 1.5), T0)
    assert not a.silent


def test_penalty_is_weighted_by_feed_importance():
    """A dark invoice feed (0.35) must cost more than a dark on-chain feed (0.05)."""
    heavy = staleness.assess(_dark(("invoice_feed",), 30), T0)
    light = staleness.assess(_dark(("onchain_feed",), 30), T0)
    assert heavy.penalty_points > light.penalty_points
    assert heavy.penalty_points / light.penalty_points == pytest.approx(0.35 / 0.05, rel=0.05)


def test_two_half_dark_feeds_are_worse_than_one():
    one = staleness.assess(_dark(("invoice_feed",), 30), T0)
    two = staleness.assess(_dark(("invoice_feed", "accounting_feed"), 30), T0)
    assert two.penalty_points > one.penalty_points


def test_a_feed_that_never_reported_is_silent_but_carries_no_duration():
    """No start date means no measurable duration; inventing one would fabricate evidence.

    It still engages the ratchet, because "this feed has never reported" is knowledge that the
    data is absent, not neutrality about it.
    """
    freshness = _fresh()
    freshness["invoice_feed"] = None
    a = staleness.assess(freshness, T0, ratchet_ceiling=700)
    assert a.silent
    assert a.never_reported == ["invoice_feed"]
    assert a.penalty_points == 0.0
    assert a.ratchet_ceiling == 700


# --------------------------------------------------------------------------------------
# "keeps degrading ... rather than plateauing"
# --------------------------------------------------------------------------------------


def test_penalty_never_plateaus():
    """§4's first named failure mode.

    An exponential freshness decay asymptotes and the interval caps, so a decay-only design stops
    costing after a few weeks — a borrower dark for a year lands on the same letter as one dark for
    a month. The penalty must be unbounded in duration.
    """
    penalties = [
        staleness.assess(_dark(("invoice_feed", "accounting_feed"), d), T0).penalty_points
        for d in (7, 30, 90, 180, 365, 730)
    ]
    assert penalties == sorted(penalties)
    assert all(b > a for a, b in zip(penalties, penalties[1:])), "penalty plateaued"
    # And it is linear in duration, so the cost of the second year equals the cost of the first.
    assert penalties[-1] == pytest.approx(2 * penalties[-2], rel=0.02)


def test_continued_silence_keeps_moving_the_letter():
    from continuum.scoring import calibration

    grades = []
    for days in (7, 30, 90, 180, 365):
        a = staleness.assess(_dark(("invoice_feed", "accounting_feed"), days), T0)
        points, _ = staleness.apply(742.0, a)
        grades.append(calibration.points_to_grade(points))

    assert grades[0] != grades[-1]
    assert grades[-1] == "D", "a year of silence should not still read as investment grade"
    assert len(set(grades)) >= 4, "the letter barely moved across a year of silence"


def test_penalty_cannot_push_the_score_below_zero():
    a = staleness.assess(_dark(tuple(config.FEED_SLA), 3650), T0)
    points, _ = staleness.apply(742.0, a)
    assert points == 0.0


# --------------------------------------------------------------------------------------
# "... or reversing upward"
# --------------------------------------------------------------------------------------


def test_score_cannot_rise_while_a_feed_is_silent():
    """§4's second named failure mode, and the reason the ratchet exists.

    ``days_since_last_late_payment`` counts upward with no new data, so a dark borrower's raw
    formula output drifts up. Publishing that would render silence as good news.
    """
    a = staleness.assess(_dark(("invoice_feed",), 20), T0, ratchet_ceiling=700)
    points, notes = staleness.apply(760.0, a)
    assert points == 700
    assert any("ratchet" in n for n in notes)


def test_the_score_may_still_fall_while_silent():
    """The ratchet is a ceiling, not a freeze. Deterioration must still get through."""
    a = staleness.assess(_dark(("invoice_feed",), 20), T0, ratchet_ceiling=700)
    points, _ = staleness.apply(640.0, a)
    assert points < 640  # the penalty applied, and the ceiling did not lift it back up


def test_ratchet_chains_to_the_previous_score_not_the_pre_silence_one():
    """A partial recovery below the pre-silence level is still a reversal.

    Anchoring only on "the score before the feeds went quiet" lets a borrower fall to 675 and climb
    back to 684 while still dark — rewarded for silence, just less than they might have been. The
    published series has to be monotone non-increasing under continued silence.
    """
    history = [
        _Payload(737, silent=False),  # last fully fresh
        _Payload(711, silent=True),
        _Payload(675, silent=True),  # most recent
    ]
    ceiling = staleness.ratchet_ceiling_from_history(history, _dark(("invoice_feed",), 30), T0)
    assert ceiling == 675


def test_ratchet_releases_when_the_feed_reports_again():
    """Recovery is allowed on evidence, never on the passage of time."""
    history = [_Payload(737, silent=False), _Payload(675, silent=True)]
    assert staleness.ratchet_ceiling_from_history(history, _fresh(), T0) is None


def test_no_ratchet_without_history():
    assert staleness.ratchet_ceiling_from_history([], _dark(("invoice_feed",), 30), T0) is None


def test_ratchet_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "STALENESS_RATCHET", False)
    history = [_Payload(737, silent=False)]
    assert staleness.ratchet_ceiling_from_history(history, _dark(("invoice_feed",), 30), T0) is None


# --------------------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------------------


def test_penalty_applies_before_the_ratchet():
    """A borrower who is both silent and deteriorating must pay for both.

    If the ceiling bound first, the penalty would land harmlessly beneath it and two independent
    problems would be priced as one — the same ordering argument as the LLM penalty preceding the
    grade ceiling in ``aggregate.score``.
    """
    a = staleness.assess(_dark(("invoice_feed", "accounting_feed"), 60), T0, ratchet_ceiling=700)
    points, notes = staleness.apply(720.0, a)
    assert points < 700, "the ceiling absorbed the penalty instead of stacking with it"
    assert len(notes) == 1 and "staleness" in notes[0]


def test_assessment_serialises_its_attribution():
    a = staleness.assess(_dark(("invoice_feed",), 30), T0)
    d = a.as_dict()
    assert d["per_feed_days"]["invoice_feed"] > 0
    assert d["worst_feed"] == "invoice_feed"
    assert "§4" in d["rule"]
