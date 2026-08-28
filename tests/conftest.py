"""Shared fixtures.

Most of the suite is deliberately data-free: the scale, the publish gate, the interval and the
rate curve are pure functions of numbers, and testing them against a generated cohort would make
a calibration test fail because a borrower changed. The handful of tests that genuinely need the
cohort are gated on ``requires_cohort`` and skip cleanly on a fresh clone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from continuum import config
from continuum.schemas import (
    Attestation,
    BorrowerFeatureRecord,
    BorrowerFeatures,
    LLMFlags,
    ScorePublicationPayload,
)

T0 = datetime(2026, 8, 11, tzinfo=timezone.utc)


@pytest.fixture
def features() -> BorrowerFeatures:
    """A mid-range borrower. Every field explicit so a schema change surfaces here first."""
    return BorrowerFeatures(
        revenue_30d=184_200.50,
        revenue_trend_90d=0.06,
        days_sales_outstanding=41.0,
        payer_concentration_top1_pct=0.34,
        on_time_repayment_rate_180d=0.97,
        days_since_last_late_payment=63.0,
        revenue_volatility_90d=0.11,
        cash_runway_days=95.0,
        invoice_volume_30d=190_000.0,
        avg_invoice_age_days=28.0,
        dispute_rate_90d=0.0,
        late_payment_count_90d=0.0,
        payment_velocity_ratio=1.02,
        payer_concentration_hhi=0.36,
        payer_payment_delay_trend_60d=1.4,
        payer_risk_score=0.81,
        onchain_repayment_success_rate=1.0,
        onchain_existing_leverage_ratio=1.2,
    )


@pytest.fixture
def clean_flags() -> LLMFlags:
    return LLMFlags(
        covenant_breach=False,
        adverse_news_detected=False,
        payer_deterioration=False,
        confidence=0.8,
        evidence_refs=[],
        rationale="Documents address covenants and show no breach.",
        source="claude",
        model_used=config.LLM_MODEL,
        escalated=False,
        output_mode="schema_enforced",
    )


@pytest.fixture
def record(features, clean_flags) -> BorrowerFeatureRecord:
    return BorrowerFeatureRecord(
        borrower_id="brw_test0001",
        as_of=T0,
        source_freshness={
            "invoice_feed": T0 - timedelta(hours=3),
            "bank_feed": T0 - timedelta(hours=1),
            "accounting_feed": T0 - timedelta(hours=8),
            "document_feed": T0 - timedelta(days=6),
            "onchain_feed": T0 - timedelta(hours=2),
        },
        features=features,
        llm_flags=clean_flags,
        data_quality_score=0.96,
        feed_freshness_detail={
            "invoice_feed": 1.0,
            "bank_feed": 1.0,
            "accounting_feed": 1.0,
            "document_feed": 1.0,
            "onchain_feed": 1.0,
        },
        borrower_name="Testwick Components Ltd",
        sector="manufacturing",
    )


def make_payload(**overrides) -> ScorePublicationPayload:
    """A §9 payload with sane defaults, for gate and consumption tests."""
    base = dict(
        borrower_id="brw_test0001",
        score="BBB",
        score_numeric=700,
        confidence_interval=(682, 718),
        prior_score=None,
        trigger_reason="scheduled_daily",
        model_version=config.MODEL_VERSION,
        attestation=Attestation(),
        published_at=T0,
        explainability_ref="explain_test",
        data_quality_score=0.96,
        published_onchain=True,
    )
    base.update(overrides)
    return ScorePublicationPayload(**base)


@pytest.fixture
def payload_factory():
    return make_payload


@pytest.fixture
def requires_cohort():
    """Skip a test unless the synthetic cohort has been generated."""
    if not (config.RAW_DIR / "borrowers.json").exists():
        pytest.skip("cohort not generated; run python -m continuum.synth.generate")
