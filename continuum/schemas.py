"""Pydantic models for the two schemas in ``claude.md`` §9.

These are the contract. Field names and nesting match the brief's JSON examples exactly —
if a field here diverges from §9, this file is wrong, not the brief.

Fields **added** beyond §9 are marked ``# EXT`` with a reason. §9 calls its schemas "starter
versions ... an LLM can extend these", so extension is sanctioned, but each one is called out
so you can see precisely where this implementation exceeds the spec.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Grade = Literal[
    "AAA", "AA", "A+", "A", "A-", "BBB", "BBB-", "BB", "BB-", "B", "B-", "CCC", "CC", "C", "D"
]

TriggerReason = Literal[
    "scheduled_daily",
    "event_anomaly",
    "event_new_invoice",
    "event_repayment",
    "event_dispute",
    "event_document",
    "event_data_quality_drop",
    "manual_rescore",
    "dispute_resolution",
]

FeedName = Literal[
    "accounting_feed", "bank_feed", "invoice_feed", "document_feed", "onchain_feed"
]


# --------------------------------------------------------------------------------------
# llm_flags — the Claude reasoning agent's structured output (§7 part 2)
# --------------------------------------------------------------------------------------


class LLMFlags(BaseModel):
    """§9's ``llm_flags`` block.

    §7: "Outputs a structured risk-flag object (not a free-text opinion)". This is that
    object, and it is what the agent's structured-output schema is generated from.
    """

    model_config = ConfigDict(extra="forbid")

    covenant_breach: bool = Field(
        description="True only if document text shows a specific, identifiable breach of a "
        "stated covenant. Suspicion or elevated risk is not a breach."
    )
    adverse_news_detected: bool = Field(
        description="True if there is materially negative news about the borrower or a major "
        "counterparty (especially an invoice payer) that bears on repayment capacity."
    )
    confidence: float = Field(
        description="0.0-1.0 confidence in these flags given evidence quality and completeness. "
        "Report low confidence when documents are thin, ambiguous, or contradictory."
    )
    evidence_refs: list[str] = Field(
        description="Document IDs supporting each flag raised. Empty only when no flag is raised."
    )

    # ---- EXT: fields beyond §9 -------------------------------------------------------
    payer_deterioration: bool = Field(
        default=False,
        description="True if an invoice PAYER (not the borrower) shows signs of distress.",
    )
    """EXT: §13 requires "payer-level risk a first-class feature, not an afterthought".
    §9's llm_flags has no slot for it, so it is added here rather than buried in prose."""

    rationale: str = Field(
        default="",
        description="Two sentences maximum, citing document IDs. Not a risk opinion — an "
        "explanation of why the booleans above were set as they were.",
    )
    """EXT: §7 requires explainability; a flag with no traceable reason cannot be disputed
    under §11. Deliberately length-capped so it stays an audit note, not an opinion."""

    source: Literal["claude", "offline_fixture"] = "claude"
    """EXT: ASSUMPTIONS #8 — marks stub output so it is never read as a model judgement."""

    model_used: str = ""
    """EXT: which Claude model produced this, for the §11 audit trail."""

    escalated: bool = False
    """EXT: whether the escalation model was invoked (§14 edge-case path)."""

    output_mode: Literal["schema_enforced", "text_json", "none"] = "none"
    """EXT: how these flags were obtained.

    ``schema_enforced`` means the API validated the response against the schema server-side
    (``messages.parse``). ``text_json`` means the schema was enforced only by our own Pydantic
    validation after parsing free text — used when the configured endpoint rejects structured
    output. Both are validated before use, but they are not equally trustworthy, and §7's "not a
    free-text opinion" requirement deserves to be visibly met or visibly degraded rather than
    assumed. ``none`` accompanies ``source="offline_fixture"``."""


# --------------------------------------------------------------------------------------
# Borrower Feature Record (§9)
# --------------------------------------------------------------------------------------


class BorrowerFeatures(BaseModel):
    """The ``features`` block. The six fields §9 names are required; the rest are EXT.

    EXT features exist because §7 names risk drivers §9's starter block has no slot for
    ("volatility of cash flow", "payment velocity", dispute activity) and §13 requires
    payer-side risk to be first-class. Every one is derivable from the Layer 1 sources in §6.
    """

    model_config = ConfigDict(extra="forbid")

    # ---- §9 verbatim ----------------------------------------------------------------
    revenue_30d: float
    revenue_trend_90d: float
    days_sales_outstanding: float
    payer_concentration_top1_pct: float
    on_time_repayment_rate_180d: float
    days_since_last_late_payment: float

    # ---- EXT: §7 "volatility of cash flow", "payment velocity" ----------------------
    revenue_volatility_90d: float = 0.0
    cash_runway_days: float = 0.0
    invoice_volume_30d: float = 0.0
    avg_invoice_age_days: float = 0.0
    dispute_rate_90d: float = 0.0
    late_payment_count_90d: float = 0.0
    payment_velocity_ratio: float = 1.0
    """Recent days-to-pay vs. the borrower's own 180d baseline. >1 means slowing down."""

    # ---- EXT: §13 payer-side risk as a first-class feature --------------------------
    payer_concentration_hhi: float = 0.0
    """Herfindahl-Hirschman index over payer shares of the receivable book. Used instead of a
    top-3 share because these borrowers have 2-3 payers, which makes top-3 a constant 1.0.
    HHI is scale-free and discriminates regardless of payer count."""
    payer_payment_delay_trend_60d: float = 0.0
    payer_risk_score: float = 0.5
    """0=distressed, 1=healthy. Weighted across the payer book by outstanding value."""

    # ---- EXT: §6 on-chain source ----------------------------------------------------
    onchain_repayment_success_rate: float = 1.0
    """Settled share of facility repayments. A ratio, not a raw count: a cumulative count
    rises with observation age and would act as a calendar-time proxy."""
    onchain_existing_leverage_ratio: float = 0.0


class BorrowerFeatureRecord(BaseModel):
    """§9's Borrower Feature Record, verbatim in shape."""

    model_config = ConfigDict(extra="forbid")

    borrower_id: str
    as_of: datetime
    source_freshness: dict[str, datetime | None]
    """§9 shows accounting/bank/invoice. Keys are FeedName; a None value means the feed has
    never reported, which is distinct from having reported long ago."""

    features: BorrowerFeatures
    llm_flags: LLMFlags
    data_quality_score: float

    # ---- EXT ------------------------------------------------------------------------
    feed_freshness_detail: dict[str, float] = Field(default_factory=dict)
    """EXT: per-feed decayed freshness in [0,1]. §6 requires a visible per-borrower staleness
    flag; the aggregate data_quality_score alone can't show WHICH feed went dark."""

    borrower_name: str = ""
    sector: str = ""


# --------------------------------------------------------------------------------------
# Score Publication Payload (§9)
# --------------------------------------------------------------------------------------


class Attestation(BaseModel):
    """§9's attestation block. Phase 0 is honest about having none — ASSUMPTIONS #11."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tee", "zk", "none"] = "none"
    provider: str = "phase_0_offchain_no_attestation"
    measurement_hash: str = ""
    """Real sha256 over (model artifact digest + input feature record). Tamper-evident audit
    value, NOT a proof of honest execution. §8's distinction, kept explicit."""
    signature: str | None = None


class ScorePublicationPayload(BaseModel):
    """§9's Score Publication Payload, verbatim in shape."""

    model_config = ConfigDict(extra="forbid")

    borrower_id: str
    score: Grade
    score_numeric: int
    confidence_interval: tuple[int, int]
    prior_score: Grade | None
    trigger_reason: TriggerReason
    model_version: str
    attestation: Attestation
    published_at: datetime
    explainability_ref: str

    # ---- EXT ------------------------------------------------------------------------
    data_quality_score: float = 1.0
    """EXT: §6 requires confidence to degrade visibly on stale data. A consumer reading only
    the payload must be able to see data quality without fetching the feature record."""

    prior_score_numeric: int | None = None
    score_delta: int = 0
    as_of: datetime | None = None
    """EXT: §7 requires publishing "last updated" — as_of is the data timestamp, which is
    distinct from published_at (the scoring timestamp). Staleness needs both."""

    llm_flags: LLMFlags | None = None
    anomaly_pressure: float = 0.0
    triggered_by_detail: str = ""
    """EXT: §7 — "Publish a ... 'trigger reason' alongside every score ... don't bury it."
    trigger_reason is the enum; this is the human-readable specifics."""

    published_onchain: bool = False
    """EXT: §10 threshold-crossing discipline. Phase 0 has no chain, so this records whether the
    re-score *would* have been pushed to the registry — the decision `aggregate.publish_decision`
    made, and its reason, are in the explanation artifact. Every re-score is logged either way
    (ASSUMPTIONS #12); dropping the gated ones would erase the evidence that a gate exists.
    ``consumption.terms_history`` filters on this flag, which is what makes it mean something
    downstream rather than being a field nobody reads."""
