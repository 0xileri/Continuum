"""§10's publish gating and its two deliberate asymmetries. ASSUMPTIONS #19.

Two behaviours here are decisions rather than transcription, and both are the kind that quietly
regress into their "obvious" version during a refactor:

  1. Drift is measured against the last *published* score, not the last computed one. Hop-to-hop
     a slow slide never crosses the threshold, and the registry goes stale with every individual
     decision defensible.
  2. The cooldown does not suppress a downgrade that crosses a grade boundary. A fixed cooldown
     also delays a collapse, and suppressing bad news to damp noise is the worse failure.

Both are tested as behaviour, so a change to either has to be a deliberate edit to this file.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from continuum import config
from continuum.scoring.aggregate import publish_decision

from conftest import T0, make_payload


def _decide(score_numeric, grade, *, trigger="scheduled_daily", hours_since=48, last=None):
    last_published = last if last is not None else make_payload()
    return publish_decision(
        score_numeric=score_numeric,
        grade=grade,
        trigger_reason=trigger,
        published_at=T0 + timedelta(hours=hours_since),
        last_published=last_published,
    )


def test_first_publication_always_goes_out():
    ok, reason = publish_decision(
        score_numeric=700,
        grade="BBB",
        trigger_reason="scheduled_daily",
        published_at=T0,
        last_published=None,
    )
    assert ok and "first publication" in reason


def test_small_drift_is_recorded_but_not_republished():
    ok, reason = _decide(705, "BBB")
    assert not ok
    assert "under the" in reason and "threshold" in reason


def test_drift_at_the_threshold_publishes():
    ok, _ = _decide(700 + int(config.PUBLISH_THRESHOLD_POINTS), "BBB")
    assert ok


def test_a_grade_move_always_publishes_however_small():
    """A letter change is what a consuming pool and a borrower both read."""
    ok, reason = _decide(690, "BBB-")  # 10 points, but across a band edge
    assert ok and "grade moved" in reason


def test_drift_is_cumulative_against_the_last_published_score():
    """ASSUMPTIONS #19's first decision, stated as behaviour.

    A borrower sliding under the per-hop threshold every day must still publish once the
    *accumulated* gap crosses it. Measuring hop-to-hop is the bug this guards.
    """
    published = make_payload(score_numeric=700, score="BBB")
    # Four points a day, staying inside the BBB band (690-729) the whole way. Each individual hop
    # is well under the 10-point threshold, which is exactly the shape the rule has to catch.
    assert _decide(696, "BBB", last=published)[0] is False
    assert _decide(692, "BBB", last=published)[0] is False
    # By the third day the gap from the published value is 12 points, and it publishes.
    ok, reason = _decide(688, "BBB", last=published)
    assert ok and "crosses the" in reason


def test_cooldown_holds_an_event_triggered_republication():
    ok, reason = _decide(
        740, "A-", trigger="event_anomaly", hours_since=config.RESCORE_COOLDOWN_HOURS - 1
    )
    assert not ok
    assert "cooldown" in reason


def test_the_daily_checkpoint_is_not_rate_limited():
    """§10 asks for a cooldown between updates; the scheduled cadence is the baseline, not an
    update storm, so it is exempt."""
    ok, _ = _decide("A-" and 740, "A-", trigger="scheduled_daily", hours_since=1)
    assert ok


def test_a_boundary_crossing_downgrade_overrides_the_cooldown():
    """ASSUMPTIONS #19's second decision. A borrower falling off a cliff at 03:00 must not hold
    their old grade until 09:00 while a pool lends against it."""
    ok, reason = _decide(
        520, "B", trigger="event_repayment", hours_since=config.RESCORE_COOLDOWN_HOURS - 2
    )
    assert ok
    assert "overrides cooldown" in reason


def test_an_upgrade_inside_the_cooldown_still_waits():
    """The asymmetry is deliberate and one-directional: only bad news jumps the queue."""
    ok, reason = _decide(
        800, "A+", trigger="event_anomaly", hours_since=config.RESCORE_COOLDOWN_HOURS - 2
    )
    assert not ok and "cooldown" in reason


def test_cooldown_expiry_releases_the_held_move():
    ok, _ = _decide(
        740, "A-", trigger="event_anomaly", hours_since=config.RESCORE_COOLDOWN_HOURS + 1
    )
    assert ok


def test_reason_is_always_human_readable():
    """§7 wants the trigger and the decision visible, not a bare boolean."""
    for args in ((705, "BBB"), (740, "A-"), (520, "B")):
        _, reason = _decide(*args)
        assert reason and reason[0] != " " and len(reason) >= 20


def test_og_require_attestation_env_is_respected(monkeypatch):
    import importlib

    import continuum.config as config

    monkeypatch.setenv("CONTINUUM_OG_REQUIRE_ATTESTATION", "0")
    importlib.reload(config)
    assert config.OG_REQUIRE_ATTESTATION is False

    monkeypatch.setenv("CONTINUUM_OG_REQUIRE_ATTESTATION", "1")
    importlib.reload(config)
    assert config.OG_REQUIRE_ATTESTATION is True


def test_supported_llm_backends_are_enforced(monkeypatch):
    import importlib

    import continuum.config as config

    monkeypatch.setenv("CONTINUUM_LLM_BACKEND", "anthropic")
    importlib.reload(config)
    assert config.LLM_BACKEND == "anthropic"

    monkeypatch.setenv("CONTINUUM_LLM_BACKEND", "bogus")
    with pytest.raises(RuntimeError, match="CONTINUUM_LLM_BACKEND"):
        importlib.reload(config)


def test_publish_wave3_preflight_requires_publish_flag(monkeypatch):
    import types

    import scripts.publish_wave3 as publish_wave3

    monkeypatch.setenv("CONTINUUM_OG_NETWORK", "mainnet")
    monkeypatch.setenv("CONTINUUM_OG_PUBLISH", "0")

    args = types.SimpleNamespace(limit=1, yes=True)
    assert publish_wave3.preflight(args) is False
