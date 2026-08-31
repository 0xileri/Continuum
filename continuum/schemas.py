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

    source: Literal["claude", "0g-compute", "offline_fixture"] = "claude"
    """EXT: how these flags were obtained, and by whom.

    ``0g-compute`` is the Wave 3 path (§5.2): the reasoning call ran on a 0G Compute provider and
    the response carries a TEE signature. ``claude`` is a direct Anthropic API call — same prompt,
    same schema, no attestation. ``offline_fixture`` is neither, and marks stub output so it is
    never read as a model judgement (ASSUMPTIONS #8)."""

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

    document_ids_seen: list[str] = Field(default_factory=list)
    """EXT: every document the reasoning agent was shown when this score was computed.

    Recorded rather than inferred, because "has this document been read?" cannot be answered from
    its own date. A covenant certificate dated the 1st that arrives on the 15th is new information
    on the 15th, but a date comparison against the last scoring time buries it — permanently,
    since it only gets older. Real document feeds backdate constantly.

    It is also the provenance answer to "which documents produced this score?", which
    ``llm_flags.evidence_refs`` only partly gives: that lists the documents supporting a *raised*
    flag, not the ones read and dismissed."""

    compute_attestation: "Attestation | None" = None
    """EXT: the 0G Compute attestation returned with this record's ``llm_flags``.

    Carried on the record rather than inside ``llm_flags`` so §6's ``llm_flags`` block keeps
    exactly the four fields the brief prints. The aggregator merges this into the published
    payload's ``attestation``, which is where §6 wants it."""

    borrower_name: str = ""
    sector: str = ""


# --------------------------------------------------------------------------------------
# Score Publication Payload (§9)
# --------------------------------------------------------------------------------------


class Attestation(BaseModel):
    """§6's attestation block, filled by 0G Compute.

    Wave 3 §4: *"the original Phase 0 POC stubbed this with an explicit placeholder type rather
    than omitting it or faking TEE fields. That placeholder is now filled by the 0G Compute
    attestation object."* This model is that fill.

    ``type="none"`` survives as the honest fallback for a run that could not reach the Compute
    marketplace. It is not a degraded ``0g-compute`` — a payload either carries a verified TEE
    signature or says plainly that it does not, because a field that means "attested" sometimes and
    "we tried" other times is worse than no field. ``config.OG_REQUIRE_ATTESTATION`` makes the
    fallback fatal for runs whose output is going to be shown as Integration Proof.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["0g-compute", "none"] = "none"
    provider: str = "not_attested"

    # ---- §6's 0G Compute fields ------------------------------------------------------
    job_id: str = ""
    """The chat/request id the response was settled and verified under (``ZG-Res-Key``). This is
    the handle ``broker.inference.processResponse`` checks the provider's TEE signature against."""

    proof_ref: str = ""
    """The verification artifact, 0x-prefixed. See ``og/compute.mjs`` for exactly what is captured
    — it is whatever the broker returns, stored verbatim per §5.2 rather than reshaped."""

    compute_node: str = ""
    """Provider address on the 0G Compute marketplace. Part of what is attested: a TEE signature
    proves *a* genuine enclave answered, and the provider identity is what makes it reproducible."""

    verified: bool = False
    """``broker.inference.processResponse`` returned true for this response.

    Separate from ``type`` on purpose. A response can come back from the marketplace and fail
    verification, and collapsing that into the type would let an unverified answer render
    identically to a verified one."""

    model: str = ""
    """The model the provider served, as reported by ``getServiceMetadata``."""

    # ---- EXT: carried forward from the off-chain phase --------------------------------
    measurement_hash: str = ""
    """sha256 over (scorer identity + input feature record), computed locally.

    Kept alongside the 0G attestation rather than replaced by it, because the two prove different
    things. The 0G signature proves a genuine TEE produced the *reasoning output*; this digest
    binds the *published score* to the exact feature record and scorer that produced it. Neither
    says the underlying invoice data was real."""

    signature: str | None = None


class StorageRef(BaseModel):
    """§6's ``storage_ref`` — where the Borrower Feature Record actually lives.

    §5.3: the on-chain payload "carries only the resulting content hash/URI, not the raw record".
    That is a privacy property as much as a gas one: borrower financials do not belong in a public
    registry, and a merkle root is a commitment to them without being a disclosure.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["0g-storage", "local"] = "local"
    root_hash: str = ""
    """Merkle root from ``ZgFile.merkleTree()``. The permanent identifier a record is fetched by."""
    uri: str = ""
    tx_hash: str = ""
    """The 0G Storage upload transaction, so a reader can confirm the write happened."""
    uploaded_at: datetime | None = None
    size_bytes: int = 0


class ChainRef(BaseModel):
    """§6's ``chain_ref`` — the registry transaction that made the score an on-chain fact."""

    model_config = ConfigDict(extra="forbid")

    network: str = ""
    chain_id: int = 0
    tx_hash: str = ""
    contract: str = "ContinuumScoreRegistry"
    contract_address: str = ""
    block_number: int = 0
    explorer_url: str = ""
    """Direct 0G Explorer link to the transaction. Denormalised into the payload deliberately —
    §10 makes an Explorer link a submission artifact, and a reader should not have to know how to
    assemble one from a chain id and a hash."""


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
    storage_ref: StorageRef = Field(default_factory=StorageRef)
    chain_ref: ChainRef | None = None
    """§6. ``chain_ref`` is None until the score is published to the registry — every re-score is
    recorded off-chain (a score held back by the §4 cooldown is still evidence that the gate ran),
    and only the ones that clear the gate acquire a transaction."""
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

    # ---- EXT: §4's staleness rule, surfaced on the payload ---------------------------
    staleness_silent: bool = False
    """Whether any weighted feed was past its grace period when this score was computed.

    On the payload rather than only in the explanation artifact because ``staleness`` needs it:
    the ratchet ceiling is "the score at the last observation where every feed was fresh", and
    finding that observation means scanning the published series for this flag. Keeping it in the
    artifact would make the rule depend on a file the log does not own."""

    staleness_penalty_points: float = 0.0
    """Points removed by §4's staleness penalty. Non-negative."""

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


# ``BorrowerFeatureRecord`` forward-references ``Attestation``, which is defined below it because
# the file is ordered to match §6's presentation (feature record, then publication payload).
# Rebuilding here resolves the reference explicitly rather than relying on Pydantic's deferred
# rebuild happening to fire before the first validation.
BorrowerFeatureRecord.model_rebuild()
