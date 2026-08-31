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

# --------------------------------------------------------------------------------------
# Attestation upgrades
# --------------------------------------------------------------------------------------


def _attested(payload):
    payload.attestation.type = "0g-compute"
    payload.attestation.verified = True
    return payload


def test_an_attestation_upgrade_publishes_even_when_the_score_is_unchanged():
    """§2 is the reason this project is on 0G, and the gate has to reflect that.

    Going from an unattested score to one carrying a verified TEE signature does not move the
    number — it changes what the number is worth. Without this rule an attested score can never
    reach the chain unless the score independently drifts, so the registry would keep saying
    `attested: false` while a verified signature sat off-chain. Caught on mainnet, where four
    freshly-attested scores were all held by the gate.
    """
    published = make_payload(score_numeric=700, score="BBB")
    ok, reason = publish_decision(
        score_numeric=700,
        grade="BBB",
        trigger_reason="scheduled_daily",
        published_at=T0 + timedelta(hours=1),
        last_published=published,
        attested=True,
    )
    assert ok and "attestation upgraded" in reason


def test_an_already_attested_score_is_still_gated_on_drift():
    """The upgrade rule fires on the transition, not on being attested."""
    published = _attested(make_payload(score_numeric=700, score="BBB"))
    ok, _ = publish_decision(
        score_numeric=703,
        grade="BBB",
        trigger_reason="scheduled_daily",
        published_at=T0 + timedelta(hours=1),
        last_published=published,
        attested=True,
    )
    assert not ok


def test_losing_attestation_does_not_itself_publish():
    """One-directional, like the cooldown override.

    An attested score becoming unattested — a Compute outage, say — is not news worth gas. Leave
    the better record standing and let the next real score movement carry it.
    """
    published = _attested(make_payload(score_numeric=700, score="BBB"))
    ok, _ = publish_decision(
        score_numeric=700,
        grade="BBB",
        trigger_reason="scheduled_daily",
        published_at=T0 + timedelta(hours=1),
        last_published=published,
        attested=False,
    )
    assert not ok


def test_an_attestation_upgrade_publishes_during_a_cooldown():
    """The upgrade rule is checked before the cooldown, and that is safe rather than sloppy.

    The cooldown exists to damp update *storms*, and this rule cannot produce one: it fires only on
    the false -> true transition, which happens at most once per borrower per attestation outage.
    A borrower re-scored every hour with a stable attestation hits the ordinary drift rules, not
    this one — ``test_an_already_attested_score_is_still_gated_on_drift`` pins that.

    Making it wait would mean up to six hours of the registry reporting `attested: false` for a
    score whose verified signature already exists, for no reduction in write volume.
    """
    published = _attested(make_payload(score_numeric=700, score="BBB"))
    published.attestation.verified = False
    ok, reason = publish_decision(
        score_numeric=700,
        grade="BBB",
        trigger_reason="event_anomaly",
        published_at=T0 + timedelta(hours=config.RESCORE_COOLDOWN_HOURS - 1),
        last_published=published,
        attested=True,
    )
    assert ok and "attestation upgraded" in reason


# --------------------------------------------------------------------------------------
# Document-arrival trigger (§7 part 2)
# --------------------------------------------------------------------------------------


def _doc(doc_id, created):
    # created_at is an ISO string on the wire, as the generator writes it and llm_agent parses it.
    return {
        "doc_id": doc_id,
        "borrower_id": "brw_test0001",
        "doc_type": "covenant_certificate",
        "title": "t",
        "body": "b",
        "created_at": created.isoformat(),
    }


def test_a_backdated_document_still_counts_as_new(monkeypatch, tmp_path):
    """The bug this guards is silent and permanent.

    Document feeds backdate: a covenant certificate dated the 1st routinely arrives on the 15th.
    The old test — "created_at after the last scoring time" — calls that document old on the day it
    lands, and keeps calling it old forever, because the comparison only ever moves against it. A
    breach notice could sit in the feed unread indefinitely.
    """
    from continuum import orchestrator
    from continuum.ingestion import store
    from continuum.schemas import BorrowerFeatureRecord, BorrowerFeatures, LLMFlags

    arrived = _doc("doc_late", T0 - timedelta(days=9))
    already = _doc("doc_old", T0 - timedelta(days=30))

    record = BorrowerFeatureRecord(
        borrower_id="brw_test0001",
        as_of=T0 - timedelta(days=1),
        source_freshness={},
        features=BorrowerFeatures(
            revenue_30d=1,
            revenue_trend_90d=0,
            days_sales_outstanding=45,
            payer_concentration_top1_pct=0.4,
            on_time_repayment_rate_180d=0.9,
            days_since_last_late_payment=90,
        ),
        llm_flags=LLMFlags(
            covenant_breach=False, adverse_news_detected=False, confidence=0.5, evidence_refs=[]
        ),
        data_quality_score=0.9,
        # The last score read only the old document, even though the late one is dated earlier
        # than the score itself.
        document_ids_seen=["doc_old"],
    )
    monkeypatch.setattr(store, "load_feature_record", lambda _bid: record)

    fresh = orchestrator.new_document_since_last_score(
        "brw_test0001", [already, arrived], T0
    )
    assert fresh is not None and fresh["doc_id"] == "doc_late"


def test_an_already_read_document_does_not_retrigger(monkeypatch):
    """Re-reading an unchanged file would spend a model call to reproduce a known answer."""
    from continuum import orchestrator
    from continuum.ingestion import store
    from continuum.schemas import BorrowerFeatureRecord, BorrowerFeatures, LLMFlags

    doc = _doc("doc_seen", T0 - timedelta(days=2))
    record = BorrowerFeatureRecord(
        borrower_id="brw_test0001",
        as_of=T0 - timedelta(days=1),
        source_freshness={},
        features=BorrowerFeatures(
            revenue_30d=1,
            revenue_trend_90d=0,
            days_sales_outstanding=45,
            payer_concentration_top1_pct=0.4,
            on_time_repayment_rate_180d=0.9,
            days_since_last_late_payment=90,
        ),
        llm_flags=LLMFlags(
            covenant_breach=False, adverse_news_detected=False, confidence=0.5, evidence_refs=[]
        ),
        data_quality_score=0.9,
        document_ids_seen=["doc_seen"],
    )
    monkeypatch.setattr(store, "load_feature_record", lambda _bid: record)
    assert orchestrator.new_document_since_last_score("brw_test0001", [doc], T0) is None


def test_records_without_the_field_fall_back_to_the_date_heuristic(monkeypatch):
    """Old records predate document_ids_seen. The fallback must fail toward 'nothing new' rather
    than toward a false trigger, since this path spends a model call."""
    from continuum import orchestrator
    from continuum.ingestion import store
    from continuum.schemas import BorrowerFeatureRecord, BorrowerFeatures, LLMFlags

    doc = _doc("doc_old", T0 - timedelta(days=30))
    record = BorrowerFeatureRecord(
        borrower_id="brw_test0001",
        as_of=T0 - timedelta(days=1),
        source_freshness={},
        features=BorrowerFeatures(
            revenue_30d=1,
            revenue_trend_90d=0,
            days_sales_outstanding=45,
            payer_concentration_top1_pct=0.4,
            on_time_repayment_rate_180d=0.9,
            days_since_last_late_payment=90,
        ),
        llm_flags=LLMFlags(
            covenant_breach=False, adverse_news_detected=False, confidence=0.5, evidence_refs=[]
        ),
        data_quality_score=0.9,
        document_ids_seen=[],
    )
    monkeypatch.setattr(store, "load_feature_record", lambda _bid: record)
    assert orchestrator.new_document_since_last_score("brw_test0001", [doc], T0) is None
