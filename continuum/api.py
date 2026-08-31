"""Read layer over the score log, plus §11's dispute path. ASSUMPTIONS #15 and #13.

The dashboard is a browser client; it cannot open Parquet files or replay the consumption layer,
so this module is the seam between ``data/`` and the frontend. It is deliberately thin: every
endpoint reads what the engine already wrote and, apart from the dispute path, computes nothing a
CLI in this repo does not already compute. Scoring happens in ``continuum.orchestrator``.

**Read-only except one endpoint.** ``POST /borrowers/{id}/disputes`` is the only write, and it is
here because §11 requires a borrower-facing appeal route:

    "Build a borrower-facing dispute/appeal flow: if a borrower believes a downgrade is wrong ...
    there needs to be a human-reviewable override path, logged on-chain."

Phase 0 has no chain, so the log is ``data/disputes/<borrower_id>.jsonl`` and the override is a
human's, not this endpoint's — see ``file_dispute``. ASSUMPTIONS #13 records that boundary.

**No authentication, and loopback-enforced because of it.** ASSUMPTIONS #15 says this API is a
localhost demo surface. That is a deployment note nobody reads, so the note is also a middleware:
a non-loopback peer gets 403 unless ``CONTINUUM_API_ALLOW_REMOTE=1`` is set deliberately. An
unauthenticated read layer over borrower financials — plus one endpoint that spends model calls —
should take more than a stray ``--host 0.0.0.0`` to expose.

Run:
    python -m uvicorn continuum.api:app --reload --port 8787
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from continuum import config, consumption
from continuum.clock import iso, now, utc
from continuum.ingestion import quality, store
from continuum.schemas import BorrowerFeatureRecord, ScorePublicationPayload
from continuum.scoring import calibration

log = logging.getLogger(__name__)

app = FastAPI(
    title="Continuum — Phase 0 scoring API",
    version="0.1.0",
    description=(
        "Read layer over the published score log, the explainability artifacts and the §11 "
        "consumption layer. Off-chain, single-operator, no attestation — see ASSUMPTIONS.md."
    ),
)

app.add_middleware(
    CORSMiddleware,
    # Enumerated rather than "*": the dashboard is the only intended browser client, and a
    # wildcard on an unauthenticated API lets any page the operator has open read this data
    # cross-origin.
    allow_origins=list(config.API_CORS_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@app.middleware("http")
async def loopback_only(request: Request, call_next):
    """ASSUMPTIONS #15, enforced rather than documented. See the module docstring."""
    if not config.API_ALLOW_REMOTE:
        peer = request.client.host if request.client else None
        if peer not in LOOPBACK:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        f"Continuum's Phase 0 API is unauthenticated and localhost-only "
                        f"(ASSUMPTIONS #15). Refused a request from {peer!r}. Set "
                        f"CONTINUUM_API_ALLOW_REMOTE=1 only if you have put authentication in "
                        f"front of it."
                    )
                },
            )
    return await call_next(request)


# --------------------------------------------------------------------------------------
# Shared loading helpers
# --------------------------------------------------------------------------------------


def _roster() -> dict[str, dict]:
    try:
        return {b["borrower_id"]: b for b in store.load_borrowers()}
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="No cohort on disk. Run: python -m continuum.synth.generate",
        )


def _borrower_or_404(borrower_id: str) -> dict:
    roster = _roster()
    if borrower_id not in roster:
        raise HTTPException(status_code=404, detail=f"unknown borrower {borrower_id!r}")
    return roster[borrower_id]


def _payload_json(p: ScorePublicationPayload) -> dict:
    return p.model_dump(mode="json")


def _record_json(r: BorrowerFeatureRecord) -> dict:
    """Feature record plus the derived staleness read the dashboard shows next to it."""
    out = r.model_dump(mode="json")
    level, degraded = quality.staleness_summary(r.feed_freshness_detail)
    out["staleness"] = {"level": level, "degraded_feeds": degraded}
    return out


def public_document(d: dict) -> dict:
    """One document, stripped to the fields a dashboard may show.

    An allowlist for the same reason ``llm_agent._prompt_documents`` uses one: ``_truth`` is the
    generator's ground truth and ``scenario_tag`` names the template a document was built from.
    Either one, rendered next to the agent's flags, turns the explainability trail into an answer
    key — and a demo where the agent is graded against something the page already displays proves
    nothing. Building by allowlist means a new field added to the generator cannot leak by default.
    """
    return {
        "doc_id": d["doc_id"],
        "doc_type": d["doc_type"],
        "title": d["title"],
        "created_at": d["created_at"],
        "provenance": d.get("provenance", "self_reported"),
        "body": d["body"],
    }


def _eligible_receivables(borrower_id: str) -> float | None:
    record = store.load_feature_record(borrower_id)
    return consumption.eligible_receivables_from(record) if record else None


# --------------------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Liveness plus what the engine has actually produced, so the dashboard can say why it is
    empty rather than rendering blank panels."""
    try:
        roster = store.load_borrowers()
    except FileNotFoundError:
        roster = []
    scored = [b["borrower_id"] for b in roster if store.latest_score(b["borrower_id"])]
    return {
        "status": "ok",
        "borrowers": len(roster),
        "borrowers_scored": len(scored),
        "model_version": config.MODEL_VERSION,
        "build_commit": config.BUILD_COMMIT,
        "server_time": iso(now()),
        "next_step": (
            "python -m continuum.synth.generate"
            if not roster
            else "python -m continuum.orchestrator backfill --weeks 12"
            if not scored
            else None
        ),
    }


@app.get("/meta", tags=["meta"])
def meta() -> dict:
    """Constants the dashboard needs to render bands, curves and disclosures without hardcoding.

    Every one of these is a decision recorded in ASSUMPTIONS.md. Serving them rather than
    duplicating them in TypeScript means the dashboard cannot drift out of step with the engine
    when a threshold is re-tuned — which it will be, per §17.
    """
    return {
        "model_version": config.MODEL_VERSION,
        "grade_bands": [
            {"grade": g, "lower": lo, "upper": hi}
            for g, lo, hi in (
                (grade, *calibration.grade_band(grade)) for grade, _ in config.GRADE_BANDS
            )
        ],
        "score_anchor_points": config.SCORE_ANCHOR_POINTS,
        "points_to_double_odds": config.SCORE_POINTS_TO_DOUBLE_ODDS,
        "publish_threshold_points": config.PUBLISH_THRESHOLD_POINTS,
        "rescore_cooldown_hours": config.RESCORE_COOLDOWN_HOURS,
        "grade_ceiling_halfwidth_multiple": config.GRADE_CEILING_HALFWIDTH_MULTIPLE,
        "ci_weights": config.CI_WEIGHTS,
        "ci_base_half_width": config.CI_BASE_HALF_WIDTH,
        "ci_max_half_width": config.CI_MAX_HALF_WIDTH,
        "llm_flag_penalties": config.LLM_FLAG_PENALTIES,
        "materiality": config.MATERIALITY,
        "feed_sla": config.FEED_SLA,
        "consumption": {
            "pool_base_rate_bps": config.POOL_BASE_RATE_BPS,
            "risk_premium_at_anchor_bps": config.RISK_PREMIUM_AT_ANCHOR_BPS,
            "max_risk_premium_bps": config.MAX_RISK_PREMIUM_BPS,
            "uncertainty_load_share": config.UNCERTAINTY_LOAD_SHARE,
            "max_rate_change_bps_per_update": config.MAX_RATE_CHANGE_BPS_PER_UPDATE,
            "rate_cooldown_hours": config.RATE_COOLDOWN_HOURS,
        },
        "dispute": {
            "narrative_max_chars": config.DISPUTE_NARRATIVE_MAX_CHARS,
            "reassess_cooldown_minutes": config.DISPUTE_REASSESS_COOLDOWN_MINUTES,
        },
        # §5.1 — the scorer, and the fact that it is a placeholder, served rather than asserted
        # in a README the demo audience will not read.
        "scorer": {
            "kind": config.SCORER,
            "weights": config.QUANT_WEIGHTS,
            "pivots": {k: list(v) for k, v in config.QUANT_PIVOTS.items()},
            "model_variance_floor": config.QUANT_MODEL_VARIANCE,
            "statement": (
                "Wave 3 scores with a transparent weighted formula over four features, not a "
                "trained model. §3 puts XGBoost/LightGBM out of scope because no real default "
                "data exists yet to fit or backtest against. Every interval is widened by a fixed "
                "model-variance floor to represent that."
            ),
        },
        # §4's staleness rule, so the dashboard can explain a falling score without hardcoding
        # constants that §17 says will be re-tuned.
        "staleness": {
            "grace_hours_floor": config.STALENESS_GRACE_HOURS,
            "points_per_weighted_day": config.STALENESS_POINTS_PER_DAY,
            "ratchet": config.STALENESS_RATCHET,
            "statement": (
                "Silence is worsening information. The penalty is unbounded in duration, so a "
                "score keeps degrading for as long as a feed stays quiet, and a ratchet forbids "
                "the published score rising while any feed is silent."
            ),
        },
        # §2/§5.2/§5.4 — the 0G integration, and precisely what it does and does not cover.
        "og": _og_meta(),
    }


def _og_meta() -> dict:
    """The 0G network profile, the deployed registry, and the §5.2 scope note.

    Served rather than written into the frontend so the disclosure cannot be edited out of the UI
    without editing the engine that makes the claim — and so a network promotion from testnet to
    mainnet moves the dashboard's Explorer links with it automatically.
    """
    from continuum.og import bridge as og_bridge
    from continuum.og import chain as og_chain

    profile = config.og()
    ready, reason = og_bridge.available()
    address = og_chain.registry_address()

    return {
        "network": profile["name"],
        "chain_id": profile["chain_id"],
        "explorer": profile["explorer"],
        "storage_explorer": profile["storage_explorer"],
        "faucet": profile["faucet"],
        "registry_address": address,
        "registry_explorer_url": og_chain.explorer_contract_url(),
        "publish_enabled": config.OG_PUBLISH_ON_SCORE,
        "llm_backend": config.LLM_BACKEND,
        "bridge_ready": ready,
        "bridge_reason": reason,
        "require_attestation": config.OG_REQUIRE_ATTESTATION,
        # §5.2's fallback, stated where a viewer of the dashboard will actually see it.
        "compute_scope": (
            "0G Compute runs the document-reasoning call and signs the response inside a TEE; the "
            "broker verifies that signature before the flags are used. The aggregation arithmetic "
            "runs off-chain on the operator's machine — 0G Compute serves inference against "
            "registered providers and does not execute arbitrary jobs, so §5.2's stated fallback "
            "applies. This is a scope reduction, not a mock."
        ),
        "trust_statement": (
            "Single-operator system (§3, §11). Multi-operator consensus scoring is a later-wave "
            "roadmap item and is not claimed here. The 0G Compute attestation covers the reasoning "
            "call; measurement_hash binds the published score to one feature record and one "
            "scoring rule. Neither is evidence that the underlying invoice data was real."
        ),
    }


@app.get("/og", tags=["0g"])
def og_status() -> dict:
    """0G integration status — §10's Integration Proof, as data the dashboard can render."""
    from continuum.og import chain as og_chain

    meta = _og_meta()
    published = []
    for borrower in _roster().values():
        for payload in store.load_scores(borrower["borrower_id"]):
            if payload.chain_ref and payload.chain_ref.tx_hash:
                published.append(
                    {
                        "borrower_id": payload.borrower_id,
                        "borrower_name": borrower.get("name", ""),
                        "score": payload.score,
                        "score_numeric": payload.score_numeric,
                        "published_at": iso(payload.published_at),
                        "tx_hash": payload.chain_ref.tx_hash,
                        "block_number": payload.chain_ref.block_number,
                        "explorer_url": payload.chain_ref.explorer_url,
                        "storage_root_hash": payload.storage_ref.root_hash,
                        "attested": payload.attestation.verified,
                    }
                )
    published.sort(key=lambda r: r["published_at"], reverse=True)

    return {
        **meta,
        "onchain_publications": published,
        "onchain_count": len(published),
        "contract_url": og_chain.explorer_contract_url(),
    }


@app.get("/rate-curve", tags=["meta"])
def rate_curve() -> dict:
    """§11's ``f`` and ``g`` sampled across the scale, for the dashboard's terms panel.

    Sampled from the same functions ``consumption`` prices with, so the curve a borrower is shown
    and the curve they are charged on cannot diverge.
    """
    return {
        "points": [
            {
                "score": s,
                "grade": calibration.points_to_grade(float(s)),
                "risk_premium_bps": round(consumption.risk_premium_bps(float(s)), 1),
                "all_in_rate_bps": round(
                    config.POOL_BASE_RATE_BPS + consumption.risk_premium_bps(float(s)), 1
                ),
                "max_ltv": consumption.max_ltv(float(s)),
            }
            for s in range(200, 1001, 10)
        ],
        "base_rate_bps": config.POOL_BASE_RATE_BPS,
        "max_risk_premium_bps": config.MAX_RISK_PREMIUM_BPS,
    }


# --------------------------------------------------------------------------------------
# Borrowers
# --------------------------------------------------------------------------------------


@app.get("/borrowers", tags=["borrowers"])
def list_borrowers() -> list[dict]:
    """Cohort roster with each borrower's latest score. The dashboard's landing view."""
    out: list[dict] = []
    for borrower in _roster().values():
        bid = borrower["borrower_id"]
        history = store.load_scores(bid)
        latest = history[-1] if history else None
        published = [p for p in history if p.published_onchain]
        out.append(
            {
                **borrower,
                "score_count": len(history),
                "published_count": len(published),
                "latest": _payload_json(latest) if latest else None,
                "latest_published": (
                    _payload_json(published[-1]) if published else None
                ),
                # Enough of a series for a sparkline without fetching every borrower's history.
                "spark": [
                    {"published_at": iso(p.published_at), "score_numeric": p.score_numeric}
                    for p in history[-40:]
                ],
            }
        )
    out.sort(key=lambda b: (b["latest"]["score_numeric"] if b["latest"] else 1001))
    return out


@app.get("/borrowers/{borrower_id}", tags=["borrowers"])
def get_borrower(borrower_id: str = Path(...)) -> dict:
    """Everything one borrower page needs in a single round trip.

    Bundled rather than split across five calls because every panel on that page describes the
    same score, and fetching them separately lets the header render one re-score while the
    waterfall below it explains another.
    """
    borrower = _borrower_or_404(borrower_id)
    history = store.load_scores(borrower_id)
    latest = history[-1] if history else None
    record = store.load_feature_record(borrower_id)
    explanation = store.load_explanation(latest.explainability_ref) if latest else None
    receivables = consumption.eligible_receivables_from(record) if record else None
    terms = consumption.terms_history(history, eligible_receivables=receivables)

    return {
        "borrower": borrower,
        "latest": _payload_json(latest) if latest else None,
        "feature_record": _record_json(record) if record else None,
        "explanation": explanation,
        "scores": [_payload_json(p) for p in history],
        "terms": [t.to_dict() for t in terms],
        "eligible_receivables": receivables,
        "disputes": store.load_disputes(borrower_id),
        "documents": [public_document(d) for d in store.load_documents(borrower_id)],
    }


@app.get("/borrowers/{borrower_id}/scores", tags=["borrowers"])
def get_scores(
    borrower_id: str = Path(...),
    published_only: bool = Query(
        False,
        description=(
            "Only re-scores that cleared §10's publish gate. False shows every re-score, "
            "including the ones the threshold held back — which is what makes the gate visible."
        ),
    ),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict]:
    _borrower_or_404(borrower_id)
    history = store.load_scores(borrower_id)
    if published_only:
        history = [p for p in history if p.published_onchain]
    return [_payload_json(p) for p in history[-limit:]]


@app.get("/borrowers/{borrower_id}/features", tags=["borrowers"])
def get_features(borrower_id: str = Path(...)) -> dict:
    _borrower_or_404(borrower_id)
    record = store.load_feature_record(borrower_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"no feature record for {borrower_id!r}; run the orchestrator first",
        )
    return _record_json(record)


@app.get("/borrowers/{borrower_id}/documents", tags=["borrowers"])
def get_documents(borrower_id: str = Path(...)) -> list[dict]:
    """Documents the reasoning agent read, ground truth stripped. See ``public_document``."""
    _borrower_or_404(borrower_id)
    docs = [public_document(d) for d in store.load_documents(borrower_id)]
    docs.sort(key=lambda d: d["created_at"], reverse=True)
    return docs


@app.get("/borrowers/{borrower_id}/terms", tags=["consumption"])
def get_terms(
    borrower_id: str = Path(...),
    published_only: bool = Query(
        True,
        description=(
            "Default True: a re-score the registry never received is not something a pool "
            "could have priced against. False shows what the terms would have done had every "
            "re-score published — useful for arguing about the threshold, misleading as history."
        ),
    ),
) -> dict:
    _borrower_or_404(borrower_id)
    history = store.load_scores(borrower_id)
    receivables = _eligible_receivables(borrower_id)
    terms = consumption.terms_history(
        history, eligible_receivables=receivables, published_only=published_only
    )
    return {
        "borrower_id": borrower_id,
        "eligible_receivables": receivables,
        "published_only": published_only,
        "terms": [t.to_dict() for t in terms],
        "disclaimer": (
            "No pool exists. These are the terms a pool would set from the published score "
            "under §11's formula. Constants are placeholders — ASSUMPTIONS #14."
        ),
    }


# --------------------------------------------------------------------------------------
# Explainability (§7)
# --------------------------------------------------------------------------------------


@app.get("/explanations/{ref}", tags=["explainability"])
def get_explanation(ref: str = Path(...)) -> dict:
    """§7: "Every score needs a feature-attribution breakdown ... available on request."

    This is that request. Every published payload carries the ``explainability_ref`` that
    addresses it, and ``aggregate.publish`` writes the explanation before the score so the
    pointer is never dangling.
    """
    explanation = store.load_explanation(ref)
    if explanation is None:
        raise HTTPException(status_code=404, detail=f"unknown explainability_ref {ref!r}")
    return explanation


@app.get("/borrowers/{borrower_id}/explanations/{ref}", tags=["explainability"])
def get_borrower_explanation(borrower_id: str, ref: str) -> dict:
    """Same artifact, addressed through the borrower it belongs to.

    Checked rather than trusted: an explanation fetched under the wrong borrower would render a
    different borrower's features under this one's grade, which is the one mistake an
    explainability surface must not make.
    """
    _borrower_or_404(borrower_id)
    explanation = get_explanation(ref)
    if explanation.get("borrower_id") != borrower_id:
        raise HTTPException(
            status_code=404,
            detail=f"{ref!r} does not belong to {borrower_id!r}",
        )
    return explanation


# --------------------------------------------------------------------------------------
# §11 dispute path — ASSUMPTIONS #13
# --------------------------------------------------------------------------------------


class DisputeRequest(BaseModel):
    """What a borrower submits when contesting a published score.

    ``narrative`` is capped and never reaches a model (see ``file_dispute``). The structured
    fields are what a reviewer triages on; the narrative is what they read.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        description="Short machine-readable reason code, e.g. stale_data, resolved_dispute, "
        "incorrect_document, payer_error.",
        max_length=64,
    )
    narrative: str = Field(
        default="",
        description="Borrower's account of why the score is wrong. Stored for a human reviewer.",
    )
    disputed_score_ref: str | None = Field(
        default=None,
        description="explainability_ref of the score being contested. Defaults to the latest.",
    )
    contact: str = Field(default="", max_length=200)


@app.get("/borrowers/{borrower_id}/disputes", tags=["disputes"])
def get_disputes(borrower_id: str = Path(...)) -> list[dict]:
    _borrower_or_404(borrower_id)
    return store.load_disputes(borrower_id)


@app.post("/borrowers/{borrower_id}/disputes", status_code=201, tags=["disputes"])
def file_dispute(
    borrower_id: str = Path(...),
    dispute: DisputeRequest = Body(...),
) -> dict:
    """Record a borrower's appeal and re-read their documents on the escalation model.

    §11 requires "a human-reviewable override path, logged" — and Phase 0 implements the log and
    the re-read, **not** the override. Nothing here changes a published score: the appended record
    is what a reviewer acts on, and their decision would be a fresh re-score stamped
    ``dispute_resolution``. ASSUMPTIONS #13 states that boundary; conflating the two would let an
    unauthenticated endpoint move a live rate, which is a considerably worse failure than a slow
    appeal.

    Three properties are load-bearing:

    **The narrative never reaches a model.** It is borrower-authored free text arriving at an
    endpoint with no auth, and the re-read it triggers is the same agent §13 treats as under
    injection attack. Feeding the appeal into the prompt would hand a borrower a direct channel
    into the assessment they are contesting. The re-read sees exactly what the scheduled path
    sees: the documents.

    **The re-read is rate limited; the record is not.** ``DISPUTE_REASSESS_COOLDOWN_MINUTES``
    gates the Opus-tier call, because an unauthenticated endpoint that spends model calls is a
    denial-of-wallet against the operator. Disputes filed inside the window are still written in
    full, with the skip reason in the record — refusing to *log* an appeal would defeat the point
    of §11 having one.

    **The re-score is speculative.** ``persist=False``: the appeal produces a *reassessment* the
    reviewer can compare against the contested score, not a new published value. A borrower who
    could publish by complaining would have found the cheapest downgrade-reversal in the system.
    """
    if config.API_READ_ONLY:
        raise HTTPException(
            status_code=403,
            detail=(
                "This instance is read-only. Filing a dispute triggers a re-read on the "
                "escalation model, which spends from the operator's 0G Compute ledger — not "
                "something an unauthenticated public endpoint should be able to do. Run the "
                "engine locally to exercise the §11 appeal path."
            ),
        )

    borrower = _borrower_or_404(borrower_id)
    latest = store.latest_score(borrower_id)
    filed_at = utc(now())

    if len(dispute.narrative) > config.DISPUTE_NARRATIVE_MAX_CHARS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"narrative exceeds {config.DISPUTE_NARRATIVE_MAX_CHARS} characters "
                f"(got {len(dispute.narrative)})"
            ),
        )

    contested_ref = dispute.disputed_score_ref or (latest.explainability_ref if latest else None)
    if latest is None:
        raise HTTPException(
            status_code=409,
            detail="no score has been published for this borrower; there is nothing to dispute",
        )

    record: dict[str, Any] = {
        "dispute_id": f"dsp_{borrower_id[-6:]}_{int(filed_at.timestamp())}",
        "borrower_id": borrower_id,
        "filed_at": iso(filed_at),
        "reason": dispute.reason,
        "narrative": dispute.narrative,
        "contact": dispute.contact,
        "disputed_score_ref": contested_ref,
        "disputed_score": latest.score,
        "disputed_score_numeric": latest.score_numeric,
        "disputed_published_at": iso(latest.published_at),
        "status": "open",
        "resolution": None,
        "reassessment": None,
    }

    skip_reason = _reassess_cooldown_reason(borrower_id, filed_at)
    if skip_reason:
        record["reassessment"] = {"performed": False, "reason": skip_reason}
        store.append_dispute(borrower_id, record)
        return record

    try:
        record["reassessment"] = _reassess(borrower, latest)
    except FileNotFoundError as exc:
        record["reassessment"] = {"performed": False, "reason": f"engine not ready: {exc}"}
    except Exception as exc:  # a failed re-read must not lose the appeal
        log.exception("dispute reassessment failed for %s", borrower_id)
        record["reassessment"] = {
            "performed": False,
            "reason": f"reassessment failed ({type(exc).__name__}); the dispute is still logged",
        }

    store.append_dispute(borrower_id, record)
    return record


def _reassess_cooldown_reason(borrower_id: str, filed_at: datetime) -> str | None:
    """``None`` if a re-read is allowed, otherwise why it was skipped. See ``file_dispute``."""
    prior = [
        d
        for d in store.load_disputes(borrower_id)
        if (d.get("reassessment") or {}).get("performed")
    ]
    if not prior:
        return None
    last = utc(datetime.fromisoformat(prior[-1]["filed_at"]))
    window = timedelta(minutes=config.DISPUTE_REASSESS_COOLDOWN_MINUTES)
    if filed_at - last >= window:
        return None
    minutes = (filed_at - last).total_seconds() / 60.0
    return (
        f"escalated re-read skipped: {minutes:.0f}min since the last one, minimum is "
        f"{config.DISPUTE_REASSESS_COOLDOWN_MINUTES}min. The dispute is recorded in full."
    )


def _reassess(borrower: dict, contested: ScorePublicationPayload) -> dict:
    """Re-run the score with a forced escalation-model document read, without publishing.

    Imported lazily because the scoring stack pulls in LightGBM and pandas: the read endpoints
    must serve on a machine where the model has not been trained yet, and paying that import at
    module load would make ``/health`` — the endpoint whose job is to say the engine is not ready
    — the thing that fails.
    """
    from continuum.ingestion import store as ingest_store
    from continuum.orchestrator import rescore
    from continuum.scoring.structured import StructuredModel

    borrower_id = borrower["borrower_id"]
    model = StructuredModel.load()
    raw = ingest_store.load_raw()
    documents = ingest_store.load_documents(borrower_id)

    result, _ = rescore(
        borrower,
        raw,
        contested.as_of or contested.published_at,
        model=model,
        documents=documents,
        trigger_reason="dispute_resolution",
        force_escalation=True,
        persist=False,
    )

    p = result.payload
    return {
        "performed": True,
        "reason": "escalated document re-read at the contested as_of (§11, ASSUMPTIONS #13)",
        "as_of": iso(p.as_of),
        "score": p.score,
        "score_numeric": p.score_numeric,
        "confidence_interval": list(p.confidence_interval),
        "delta_vs_disputed": p.score_numeric - contested.score_numeric,
        "llm_flags": p.llm_flags.model_dump(mode="json") if p.llm_flags else None,
        "explanation": result.explanation,
        "published": False,
        "note": (
            "Speculative re-score. Nothing was written to the score log; a reviewer decides "
            "whether to publish an override."
        ),
    }


# --------------------------------------------------------------------------------------
# Static dashboard — single-service deployment
# --------------------------------------------------------------------------------------
#
# In development the Vite dev server runs separately and proxies /api here. In a deployed build
# there is one process: this one serves the compiled dashboard and the API together, which removes
# the CORS surface entirely and means one URL to share.
#
# Registered last, deliberately. FastAPI matches routes in registration order, so every API path
# above wins; the catch-all below only ever sees what nothing else claimed. Mounting it earlier
# would shadow the whole API.

_DIST = config.PROJECT_ROOT / "dashboard" / "dist"

if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        """Serve a built file if it exists, otherwise the SPA shell.

        The dashboard holds its state client-side, so a deep link or a refresh has to return
        index.html and let the app resolve the route rather than 404.
        """
        candidate = (_DIST / full_path).resolve()
        # Containment check: full_path is attacker-controlled, and without this a request for
        # ../../.env would escape the build directory and serve whatever it found.
        if full_path and _DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
