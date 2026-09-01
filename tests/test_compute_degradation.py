"""``og.compute`` degrades; it does not refuse.

The distinction these tests pin is between **scoring** and **publishing**. They are separate
decisions and only the second needs a guarantee:

  - A score computed without the agent is a legitimate score that is *less confident*. The interval
    widens and the grade ceiling falls, which is the engine's standard response to a missing signal
    everywhere else — see ``quality.data_quality_score`` and ``staleness``.
  - A score published without a verified attestation is a claim the system cannot back, and that is
    blocked by ``config.OG_REQUIRE_ATTESTATION`` in ``aggregate.score`` and by
    ``publish_wave3.py``'s ``--allow-unattested`` gate.

Raising inside the scoring path conflated the two. It stopped the daily cron mid-cohort on one
borrower's missing documents, left that borrower permanently unscoreable on mainnet, and protected
nothing — the publication was already blocked downstream. Both failures were found on a real
mainnet run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from continuum import config
from continuum.og import compute as og_compute
from continuum.og.bridge import BridgeError, BridgeUnavailable

T0 = datetime(2026, 8, 11, tzinfo=timezone.utc)


def _doc(doc_id="doc_1", days_ago=3):
    return {
        "doc_id": doc_id,
        "borrower_id": "brw_test0001",
        "doc_type": "covenant_certificate",
        "title": "Quarterly covenant certificate",
        "body": "All covenants in compliance.",
        "created_at": (T0 - timedelta(days=days_ago)).isoformat(),
    }


def _mainnet(monkeypatch):
    """The network these failures used to be fatal on."""
    monkeypatch.setattr(config, "OG_NETWORK", "mainnet")
    monkeypatch.setattr(config, "OG_ALLOW_OFFLINE_DEMO", False)


# --------------------------------------------------------------------------------------
# No documents
# --------------------------------------------------------------------------------------


def test_no_documents_scores_at_low_confidence_rather_than_raising(monkeypatch):
    """A borrower with no documents is a legitimate state, not an error.

    It means no document-based flag can be raised either way. Raising made such a borrower
    permanently unscoreable on mainnet — a real cohort member sat unscored for exactly this reason.
    """
    _mainnet(monkeypatch)
    result = og_compute.reason_over_documents("Ardent Print", "print", T0, [])

    assert result.flags.covenant_breach is False
    assert result.flags.adverse_news_detected is False
    assert 0.0 < result.flags.confidence < 0.5, "should be low confidence, not zero and not certain"
    assert result.attestation.type == "none"
    assert not result.attested


# --------------------------------------------------------------------------------------
# Compute unreachable or failing
# --------------------------------------------------------------------------------------


def test_bridge_unavailable_degrades_on_mainnet(monkeypatch):
    """One provider outage must not stop the scheduled run for the whole cohort."""
    _mainnet(monkeypatch)
    monkeypatch.setattr(
        og_compute, "call_bridge", lambda *a, **k: (_ for _ in ()).throw(BridgeUnavailable("no key"))
    )

    result = og_compute.reason_over_documents("Test Co", "retail", T0, [_doc()])

    assert result.flags.confidence == 0.0, "an absent agent raises no flags at zero confidence"
    assert result.flags.covenant_breach is False
    assert result.attestation.type == "none"
    assert not result.attested


def test_bridge_error_degrades_on_mainnet(monkeypatch):
    _mainnet(monkeypatch)
    monkeypatch.setattr(
        og_compute, "call_bridge", lambda *a, **k: (_ for _ in ()).throw(BridgeError("boom"))
    )

    result = og_compute.reason_over_documents("Test Co", "retail", T0, [_doc()])
    assert result.flags.confidence == 0.0
    assert not result.attested


def test_an_unusable_response_keeps_the_attestation(monkeypatch):
    """A verified signature over a malformed answer is still a true fact about the provider.

    Discarding it would hide a provider that is reliably returning garbage.
    """
    _mainnet(monkeypatch)
    monkeypatch.setattr(
        og_compute,
        "call_bridge",
        lambda *a, **k: {
            "ok": True,
            "content": "this is not json",
            "attestation": {
                "type": "0g-compute",
                "provider": "0g-compute-network",
                "job_id": "j1",
                "proof_ref": "0x" + "ab" * 32,
                "compute_node": "0xProvider",
                "verified": True,
                "model": "some-model",
            },
        },
    )

    result = og_compute.reason_over_documents("Test Co", "retail", T0, [_doc()])
    assert result.flags.confidence == 0.0, "unparseable content must not become flags"
    assert result.attestation.type == "0g-compute"
    assert result.attestation.verified is True


# --------------------------------------------------------------------------------------
# Unverified signature
# --------------------------------------------------------------------------------------


def test_an_unverified_signature_still_scores_but_is_not_attested(monkeypatch):
    """The flags are real; the provenance is not proven. Both facts are recorded.

    This is precisely why ``verified`` is a separate field from ``type`` — collapsing them would
    let an unverified response render identically to a verified one.
    """
    _mainnet(monkeypatch)
    monkeypatch.setattr(
        og_compute,
        "call_bridge",
        lambda *a, **k: {
            "ok": True,
            "content": '{"covenant_breach": true, "adverse_news_detected": false, '
            '"payer_deterioration": false, "confidence": 0.9, '
            '"evidence_refs": ["doc_1"], "rationale": "breach found"}',
            "attestation": {
                "type": "0g-compute",
                "provider": "0g-compute-network",
                "job_id": "j2",
                "proof_ref": "0x" + "cd" * 32,
                "compute_node": "0xProvider",
                "verified": False,
                "model": "some-model",
            },
            "verify_error": "signature mismatch",
        },
    )

    result = og_compute.reason_over_documents("Test Co", "retail", T0, [_doc()])

    assert result.flags.covenant_breach is True, "the response is used"
    assert result.attestation.verified is False, "but its provenance is not claimed"
    assert result.attested is False, "so it cannot be published as attested"


# --------------------------------------------------------------------------------------
# The guarantee still exists — one layer up
# --------------------------------------------------------------------------------------


def test_publication_is_what_requires_the_attestation(record, monkeypatch):
    """Moving the check did not remove it.

    ``aggregate.score`` refuses an unattested score when OG_REQUIRE_ATTESTATION is set, which is
    where a guarantee about published claims belongs.
    """
    from continuum.scoring import aggregate
    from continuum.scoring.anomaly import AnomalyReport
    from continuum.scoring.quant import QuantScorer

    monkeypatch.setattr(config, "OG_REQUIRE_ATTESTATION", True)
    record.compute_attestation = None

    with pytest.raises(RuntimeError, match="attestation"):
        aggregate.score(
            record,
            model=QuantScorer(),
            anomaly_report=AnomalyReport(triggered=False, trigger_reason=None),
            trigger_reason="scheduled_daily",
        )


# --------------------------------------------------------------------------------------
# Reused assessments keep their attestation
# --------------------------------------------------------------------------------------


def test_reused_flags_carry_their_attestation(monkeypatch):
    """A reused assessment must not become unattested.

    ``_reusable_flags`` skips the Compute call when the visible document set has not changed —
    correct, since re-reading an unchanged file spends a call to reproduce a known answer. But
    returning the flags without their attestation made the resulting score report ``type="none"``
    for reasoning that was demonstrably attested, forcing a false choice between cheap daily
    scoring and attested scoring.

    The attestation is a true statement about where those flags came from, and the flags have not
    changed. ``job_id`` and the record's ``as_of`` still say when it was produced.
    """
    from continuum import orchestrator
    from continuum.ingestion import store
    from continuum.schemas import Attestation, BorrowerFeatureRecord, BorrowerFeatures, LLMFlags

    att = Attestation(
        type="0g-compute",
        provider="0g-compute-network",
        job_id="job-abc",
        proof_ref="0x" + "ef" * 32,
        compute_node="0xProvider",
        verified=True,
        model="some-model",
    )
    doc = _doc("doc_only", days_ago=5)

    prior = BorrowerFeatureRecord(
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
            covenant_breach=False,
            adverse_news_detected=False,
            confidence=0.8,
            evidence_refs=[],
            source="0g-compute",
        ),
        data_quality_score=0.9,
        compute_attestation=att,
    )
    monkeypatch.setattr(store, "load_feature_record", lambda _bid: prior)

    reused = orchestrator._reusable_flags("brw_test0001", [doc], T0)
    assert reused is not None, "an unchanged document set should be reusable"

    flags, attestation = reused
    assert flags.confidence == 0.8
    assert attestation is not None and attestation.verified is True
    assert attestation.job_id == "job-abc", "the original call is still identified"


def test_an_offline_stub_is_never_reused(monkeypatch):
    """ASSUMPTIONS #8 — a run with credentials must call the agent rather than inherit a
    zero-confidence placeholder, which would look like a real assessment that found nothing."""
    from continuum import orchestrator
    from continuum.ingestion import store
    from continuum.schemas import BorrowerFeatureRecord, BorrowerFeatures, LLMFlags

    prior = BorrowerFeatureRecord(
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
            covenant_breach=False,
            adverse_news_detected=False,
            confidence=0.0,
            evidence_refs=[],
            source="offline_fixture",
        ),
        data_quality_score=0.9,
    )
    monkeypatch.setattr(store, "load_feature_record", lambda _bid: prior)
    assert orchestrator._reusable_flags("brw_test0001", [_doc()], T0) is None
