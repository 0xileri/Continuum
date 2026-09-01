"""§7 part 4b — aggregation: the fan-in that turns three model outputs into one published score.

§7 asks for a single score combining parts 1–3 of the ensemble, "plus a **confidence interval**, not
just a point estimate. Publish both." This is where that happens, and it is the only place in the
engine that does:

    structured PD  +  llm_flags  +  anomaly report   ->   §9 Score Publication Payload

The arithmetic lives next door in ``calibration.py`` as pure functions of numbers. What is here is
the *wiring*: which input feeds which term, in what order, and what gets written down about it.

**Order of operations is load-bearing, not stylistic.** The LLM penalty comes off the model's points
*before* the interval-derived grade ceiling is applied. Reversed, a borrower with a breached covenant
and stale feeds would have the ceiling bind first and the penalty land harmlessly below it — two
independent problems priced as one. The ceiling is one-sided (see
``config.GRADE_CEILING_HALFWIDTH_MULTIPLE``): uncertainty can only lower the attainable score.

**What is published is not all of what was computed.** §7 requires a feature-attribution breakdown
"available on request" and §11 requires a borrower to be able to dispute a downgrade. The payload
carries the score; the explanation artifact carries every intermediate that produced it, keyed by
``explainability_ref``. A downgrade nobody can trace back to a number is not disputable, and under
§11 that makes it unusable — so the explanation is written *before* the score, never after.

Run:
    python -m continuum.scoring.aggregate        # dry-run one borrower, print the full fan-in
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from continuum import config
from continuum.clock import iso, now, utc
from continuum.ingestion import quality, store
from continuum.ingestion.features import RawData, compute_features, source_freshness
from continuum.schemas import (
    Attestation,
    BorrowerFeatureRecord,
    LLMFlags,
    ScorePublicationPayload,
    TriggerReason,
)
from continuum.scoring import attestation, calibration, llm_agent, staleness
from continuum.scoring.anomaly import AnomalyReport
from continuum.scoring.quant import QuantScorer


def load_scorer():
    """The §5.1 scorer the engine runs, per ``config.SCORER``.

    Wave 3 §3 puts a trained model out of scope, so the default is the weighted quant formula. The
    LightGBM path stays reachable — it is working code and Phase 2 material — but it is imported
    lazily so that a Wave 3 run never pays for LightGBM, and a machine without it still scores.
    """
    if config.SCORER == "structured":
        from continuum.scoring.structured import StructuredModel

        return StructuredModel.load()
    return QuantScorer.load()


@dataclass
class ScoreResult:
    """One re-score, complete: what gets published and what backs it up.

    Held together in one object because the payload's ``explainability_ref`` is a dangling pointer
    until the explanation is persisted. Returning them separately invites a caller to publish one
    without the other.
    """

    payload: ScorePublicationPayload
    record: BorrowerFeatureRecord
    explanation: dict

    @property
    def borrower_id(self) -> str:
        return self.payload.borrower_id

    def summary(self) -> str:
        p = self.payload
        lo, hi = p.confidence_interval
        move = "" if p.prior_score is None else f"  was {p.prior_score}/{p.prior_score_numeric}"
        flag = "" if p.published_onchain else "  [recorded, not republished]"
        return (
            f"{p.borrower_id}  {p.score:>4} {p.score_numeric:>4} "
            f"[{lo}-{hi}]  dq={p.data_quality_score:.2f}{move}{flag}"
        )


# --------------------------------------------------------------------------------------
# Layer 1 -> §9 Borrower Feature Record
# --------------------------------------------------------------------------------------


def build_feature_record(
    borrower: dict,
    raw: RawData,
    as_of: datetime,
    *,
    documents: list[dict],
    base_dso: float = 40.0,
    llm_flags: LLMFlags | None = None,
    compute_attestation: "Attestation | None" = None,
) -> BorrowerFeatureRecord:
    """Assemble §9's Borrower Feature Record for one borrower at one instant.

    ``raw`` must already be narrowed to this borrower (``RawData.for_borrower``) — passing the whole
    cohort would compute cohort-wide revenue and nobody would notice until the numbers looked odd.

    ``llm_flags`` may be supplied to reuse an earlier assessment. That is not a micro-optimisation:
    ``FEED_SLA["document_feed"]`` allows a 30-day reporting interval, so a daily re-score that
    re-reads an unchanged document file spends a Claude call reproducing a known answer. The caller
    owns the staleness decision (see ``orchestrator._reusable_flags``); when omitted, the agent runs.
    """
    as_of = utc(as_of)
    features = compute_features(raw, as_of, base_dso=base_dso)
    freshness = source_freshness(raw.feed_events, as_of)
    dq, dq_detail = quality.data_quality_score(freshness, as_of)

    # A caller reusing a previous assessment passes its attestation too — the flags being reused
    # were produced by that call, so the attestation is still a true statement about them.
    if llm_flags is None:
        llm_flags, compute_attestation = assess_documents(
            borrower.get("name", borrower["borrower_id"]),
            borrower.get("sector", ""),
            as_of,
            documents,
        )

    return BorrowerFeatureRecord(
        borrower_id=borrower["borrower_id"],
        as_of=as_of,
        source_freshness=freshness,
        features=features,
        llm_flags=llm_flags,
        data_quality_score=dq,
        feed_freshness_detail=dq_detail,
        # Every document the agent was shown, not only the ones it cited. See the field's
        # docstring: this is what makes "has this been read?" answerable without guessing from
        # a date the publisher does not control.
        document_ids_seen=[d["doc_id"] for d in llm_agent.visible_documents(documents, as_of)],
        compute_attestation=compute_attestation,
        borrower_name=borrower.get("name", ""),
        sector=borrower.get("sector", ""),
    )


def assess_documents(
    borrower_name: str, sector: str, as_of: datetime, documents: list[dict]
) -> tuple[LLMFlags, "Attestation | None"]:
    """Route §5.1's reasoning call to the backend ``config.LLM_BACKEND`` names.

    Returns ``(flags, attestation)``. Only the 0G Compute path produces an attestation; the direct
    Anthropic path returns ``None``, and the payload then carries ``type="none"`` rather than an
    attestation-shaped object with nothing behind it.

    ``og.compute`` is imported lazily so that a run configured for the direct API never loads the
    bridge, and a machine with no Node installed can still score.
    """
    if config.LLM_BACKEND == "0g-compute":
        from continuum.og import compute as og_compute

        result = og_compute.reason_over_documents(borrower_name, sector, as_of, documents)
        return result.flags, (result.attestation if result.attestation.type == "0g-compute" else None)

    if config.LLM_BACKEND == "offline":
        return llm_agent.offline_flags("CONTINUUM_LLM_BACKEND=offline"), None

    return llm_agent.agent().assess(borrower_name, sector, as_of, documents), None


# --------------------------------------------------------------------------------------
# §10 publish gating
# --------------------------------------------------------------------------------------


def publish_decision(
    *,
    score_numeric: int,
    grade: str,
    trigger_reason: TriggerReason,
    published_at: datetime,
    last_published: ScorePublicationPayload | None,
    attested: bool = False,
) -> tuple[bool, str]:
    """§10's threshold-crossing and cooldown discipline. Returns ``(publish, reason)``.

    Phase 0 has no chain, so this decides a boolean on a payload rather than a transaction. Modelled
    now because the behaviour is what Phase 1 inherits, and because a threshold rule tested only
    after it is gating real gas is a threshold rule tested in production.

    **Drift is measured against the last *published* score, not the last computed one.** That
    distinction is the entire point of the rule. Hop-to-hop, a borrower sliding nine points a day
    never crosses a ten-point threshold, and after a fortnight the registry is 120 points stale with
    every individual decision defensible. §10 wants publishing to track the on-chain value's error,
    which is cumulative.

    **The cooldown is deliberately one-sided.** §10 asks for "a cooldown between updates for the same
    borrower", and a fixed one would also delay a collapse — a borrower falling off a cliff at 03:00
    would sit at their old grade until 09:00 while a pool lent against it. Suppressing bad news to
    damp noise is the worse failure, so a downgrade that crosses a grade boundary overrides the
    cooldown; everything else waits. Same asymmetry as ``GRADE_CEILING_HALFWIDTH_MULTIPLE``, same
    reason. ASSUMPTIONS #19.
    """
    if last_published is None:
        return True, "first publication for this borrower"

    # An attestation upgrade publishes even when the number has not moved.
    #
    # The gate's other rules all ask "has the score changed enough to be worth gas?", which is the
    # right question for score drift and the wrong one here. Going from an unattested score to one
    # carrying a verified 0G Compute TEE signature does not change the number — it changes what the
    # number is *worth*, which is the entire premise of §2. A consuming pool filtering on `attested`
    # sees a different record; leaving the chain saying `attested: false` while a verified signature
    # sits off-chain would understate the system precisely where it is strongest.
    #
    # One-directional, like the cooldown override. An attested score becoming unattested (a Compute
    # outage, say) is not news worth gas — the honest thing there is to leave the better record
    # standing and let the next real score movement carry the downgrade.
    if attested and not last_published.attestation.verified:
        return True, (
            "attestation upgraded: this score carries a verified 0G Compute TEE signature and the "
            "published one does not"
        )

    drift = abs(score_numeric - last_published.score_numeric)
    grade_moved = grade != last_published.score
    downgraded = grade_moved and score_numeric < last_published.score_numeric
    gap_h = (utc(published_at) - utc(last_published.published_at)).total_seconds() / 3600.0
    in_cooldown = (
        trigger_reason.startswith("event_") and gap_h < config.RESCORE_COOLDOWN_HOURS
    )

    if in_cooldown and not downgraded:
        return False, (
            f"§10 cooldown: {gap_h:.1f}h since last publication, minimum is "
            f"{config.RESCORE_COOLDOWN_HOURS}h ({drift}pt drift held back)"
        )
    if in_cooldown and downgraded:
        return True, (
            f"downgrade {last_published.score} -> {grade} overrides cooldown "
            f"({gap_h:.1f}h < {config.RESCORE_COOLDOWN_HOURS}h)"
        )
    if grade_moved:
        return True, f"grade moved {last_published.score} -> {grade}"
    if drift >= config.PUBLISH_THRESHOLD_POINTS:
        return True, (
            f"{drift}pt drift from published {last_published.score_numeric} crosses the "
            f"{config.PUBLISH_THRESHOLD_POINTS:.0f}pt threshold"
        )
    return False, (
        f"{drift}pt drift from published {last_published.score_numeric} is under the "
        f"{config.PUBLISH_THRESHOLD_POINTS:.0f}pt threshold; recorded, not republished"
    )


def _trigger_detail(
    record: BorrowerFeatureRecord,
    report: AnomalyReport,
    stale: "staleness.StalenessAssessment | None" = None,
) -> str:
    """Human-readable one-liner for §7's "trigger reason" requirement.

    §7: "Publish a 'last updated' and 'trigger reason' alongside every score — this is your entire
    value proposition made visible; don't bury it." A bare enum value buries it. This says what the
    engine actually noticed.
    """
    parts: list[str] = [report.summary()]

    level, degraded = quality.staleness_summary(record.feed_freshness_detail)
    if level != "fresh":
        parts.append(f"data {level} ({', '.join(degraded)})")
    if stale is not None and stale.silent:
        parts.append(stale.summary())

    raised = [f for f in config.LLM_FLAG_PENALTIES if getattr(record.llm_flags, f, False)]
    if raised:
        parts.append("document flags: " + ", ".join(raised))
    if record.llm_flags.source == "offline_fixture":
        parts.append("no document assessment available (offline stub, zero confidence)")

    return "; ".join(p for p in parts if p)


def _explain_ref(borrower_id: str, published_at: datetime, measurement_hash: str) -> str:
    """Deterministic explanation key.

    Derived from the same three things the attestation binds, so re-running an identical score
    overwrites its own explanation instead of littering ``data/explain`` with duplicates — while any
    genuine change of input, model or timestamp lands on a new key and leaves the old one intact for
    audit.
    """
    seed = f"{borrower_id}|{iso(utc(published_at))}|{measurement_hash}"
    return f"explain_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


# --------------------------------------------------------------------------------------
# The fan-in
# --------------------------------------------------------------------------------------


def score(
    record: BorrowerFeatureRecord,
    *,
    model,
    anomaly_report: AnomalyReport,
    trigger_reason: TriggerReason,
    prior: ScorePublicationPayload | None = None,
    last_published: ScorePublicationPayload | None = None,
    triggered_by_detail: str = "",
    published_at: datetime | None = None,
    history: list[ScorePublicationPayload] | None = None,
) -> ScoreResult:
    """Combine the ensemble into one §9 payload plus its explanation artifact.

    ``prior`` is the previous *recorded* score and supplies ``prior_score`` / ``score_delta`` — what a
    human reads as "moved since last time we looked". ``last_published`` is the previous score that
    cleared §10's gate, and is what drift is measured against. They are usually the same object and
    must not be assumed to be; see ``publish_decision``.

    Writes nothing. ``publish()`` does the persisting, so a caller can score speculatively — a §11
    dispute re-run, a what-if — without appending to an immutable log.
    """
    published_at = utc(published_at or now())
    features = record.features

    # --- part 1: structured cash-flow model -------------------------------------------------
    pd_hat = model.predict_pd(features)
    novelty_share, novel_features = model.novelty(features)
    fold_std = float(model.cv_metrics.get("auc_fold_std", 0.0))
    points_from_model = calibration.pd_to_points(pd_hat, model.base_rate)

    # --- part 2: document reasoning agent ---------------------------------------------------
    penalty, flags_raised = calibration.llm_penalty(record.llm_flags)
    points_after_llm = points_from_model - penalty

    # --- §4 staleness rule: silence is worsening information --------------------------------
    #
    # Applied here, before the interval-derived ceiling, for the same reason the LLM penalty is:
    # a borrower who is both silent and deteriorating must pay for both. Applying the ceiling
    # first would let the staleness penalty land harmlessly beneath it and price two independent
    # problems as one.
    stale = staleness.assess(
        record.source_freshness,
        record.as_of,
        ratchet_ceiling=staleness.ratchet_ceiling_from_history(
            history or [], record.source_freshness, record.as_of
        ),
    )
    points_after_staleness, staleness_notes = staleness.apply(points_after_llm, stale)

    # --- parts 1-3 -> interval, then ceiling ------------------------------------------------
    terms = calibration.uncertainty_terms(
        data_quality=record.data_quality_score,
        novelty_share=novelty_share,
        fold_std=fold_std,
        anomaly_pressure=anomaly_report.pressure,
        llm_confidence=record.llm_flags.confidence,
        model_variance_floor=float(model.cv_metrics.get("quant_model_variance", 0.0)),
    )
    hw = calibration.half_width(terms)
    ceiling = calibration.grade_ceiling(hw)
    ceiling_binding = ceiling < points_after_staleness

    final = max(0.0, min(1000.0, min(points_after_staleness, ceiling)))
    grade = calibration.points_to_grade(final)
    score_numeric = int(round(final))
    ci = (
        int(round(max(0.0, final - hw))),
        int(round(min(1000.0, final + hw))),
    )

    # --- §2/§6: the 0G Compute attestation, plus the local input binding --------------------
    att = attestation.build(
        record,
        scorer_sha256=model.artifact_sha256,
        model_version=model.model_version,
        scorer_kind=getattr(model, "scorer_kind", "structured_lightgbm"),
    )
    if config.OG_REQUIRE_ATTESTATION and att.type != "0g-compute":
        raise RuntimeError(
            f"{record.borrower_id}'s score has no 0G Compute attestation. Refusing to publish an "
            f"unattested score."
        )
    if config.OG_REQUIRE_ATTESTATION and not att.verified:
        raise RuntimeError(
            f"0G Compute attestation missing or unverified for {record.borrower_id} — refusing to "
            "publish"
        )
    explain_ref = _explain_ref(record.borrower_id, published_at, att.measurement_hash)

    publish_now, publish_reason = publish_decision(
        score_numeric=score_numeric,
        grade=grade,
        trigger_reason=trigger_reason,
        published_at=published_at,
        last_published=last_published,
        attested=att.verified,
    )

    payload = ScorePublicationPayload(
        borrower_id=record.borrower_id,
        score=grade,
        score_numeric=score_numeric,
        confidence_interval=ci,
        prior_score=prior.score if prior else None,
        trigger_reason=trigger_reason,
        model_version=model.model_version,
        attestation=att,
        published_at=published_at,
        explainability_ref=explain_ref,
        # ---- EXT ----
        data_quality_score=round(record.data_quality_score, 4),
        prior_score_numeric=prior.score_numeric if prior else None,
        score_delta=score_numeric - prior.score_numeric if prior else 0,
        as_of=record.as_of,
        llm_flags=record.llm_flags,
        anomaly_pressure=round(anomaly_report.pressure, 4),
        staleness_silent=stale.silent,
        staleness_penalty_points=round(stale.penalty_points, 2),
        triggered_by_detail=triggered_by_detail
        or _trigger_detail(record, anomaly_report, stale),
        published_onchain=publish_now,
    )

    dq_level, dq_degraded = quality.staleness_summary(record.feed_freshness_detail)
    explanation = {
        "explainability_ref": explain_ref,
        "borrower_id": record.borrower_id,
        "borrower_name": record.borrower_name,
        "as_of": iso(record.as_of),
        "published_at": iso(published_at),
        "model_version": model.model_version,
        "model_artifact_sha256": model.artifact_sha256,
        "trigger_reason": trigger_reason,
        # The arithmetic, in the order it happened, so a dispute can be checked by hand.
        "scorer": getattr(model, "scorer_kind", "structured_lightgbm"),
        "score_build_up": {
            "pd": round(pd_hat, 6),
            "base_rate": round(model.base_rate, 6),
            "points_from_model": round(points_from_model, 2),
            "llm_penalty": round(-penalty, 2),
            "points_after_llm": round(points_after_llm, 2),
            "staleness_penalty": round(-stale.penalty_points, 2),
            "points_after_staleness": round(points_after_staleness, 2),
            "grade_ceiling": round(ceiling, 2),
            "ceiling_binding": ceiling_binding,
            "score_numeric": score_numeric,
            "grade": grade,
            "confidence_interval": list(ci),
            "grade_band": list(calibration.grade_band(grade)),
        },
        # §4's rule, with its attribution, so a borrower can see which feed cost them what.
        "staleness": {**stale.as_dict(), "notes": staleness_notes},
        # Feature attribution. Units differ by scorer — log-odds for the trained booster's
        # TreeSHAP, composite-health-index units for §5.1's weighted formula — and the header row
        # carries a "_units" key so the dashboard never mislabels one as the other.
        "feature_attribution": model.attribute(features),
        "features": features.model_dump(),
        # Why the interval is this wide, term by term — see uncertainty_terms' docstring.
        "interval": {
            "half_width_points": round(hw, 2),
            "base_half_width": config.CI_BASE_HALF_WIDTH,
            "max_half_width": config.CI_MAX_HALF_WIDTH,
            "capped": hw >= config.CI_MAX_HALF_WIDTH,
            "terms": {k: round(v, 4) for k, v in terms.items()},
            "term_contributions": {
                k: round(config.CI_WEIGHTS[k] * terms.get(k, 0.0), 4) for k in config.CI_WEIGHTS
            },
            "ceiling_multiple": config.GRADE_CEILING_HALFWIDTH_MULTIPLE,
        },
        "model_uncertainty": {
            "novelty_share": round(novelty_share, 4),
            "out_of_range_features": novel_features,
            "cv_auc_fold_std": round(fold_std, 4),
            "fold_std_saturation_ref": config.CI_MODEL_FOLD_STD_REF,
            "n_train_rows": model.n_train_rows,
            "n_train_borrowers": model.n_train_borrowers,
        },
        "llm": {
            "flags_raised": flags_raised,
            "penalty_points": round(-penalty, 2),
            "penalty_cap": config.LLM_PENALTY_CAP,
            "confidence": record.llm_flags.confidence,
            "source": record.llm_flags.source,
            "model_used": record.llm_flags.model_used,
            "escalated": record.llm_flags.escalated,
            "output_mode": record.llm_flags.output_mode,
            "rationale": record.llm_flags.rationale,
            "evidence_refs": record.llm_flags.evidence_refs,
        },
        "anomaly": anomaly_report.as_dict(),
        "data_quality": {
            "score": round(record.data_quality_score, 4),
            "level": dq_level,
            "degraded_feeds": dq_degraded,
            "feed_freshness": {k: round(v, 4) for k, v in record.feed_freshness_detail.items()},
            "source_freshness": {
                k: iso(v) if v else None for k, v in record.source_freshness.items()
            },
        },
        "publish_decision": {
            "published_onchain": publish_now,
            "reason": publish_reason,
            "threshold_points": config.PUBLISH_THRESHOLD_POINTS,
            "cooldown_hours": config.RESCORE_COOLDOWN_HOURS,
            "measured_against": (
                {
                    "score_numeric": last_published.score_numeric,
                    "score": last_published.score,
                    "published_at": iso(last_published.published_at),
                }
                if last_published
                else None
            ),
        },
        "attestation": att.model_dump(mode="json"),
        "attestation_summary": attestation.describe(att),
        "input_digest": attestation.input_digest(record),
        # §11, restated where an auditor will actually be looking rather than only in a README.
        "trust_disclaimer": (
            "Wave 3 is a single-operator system. The 0G Compute attestation covers the document "
            "reasoning call only: a TEE-held key signed that response and the broker verified the "
            "signature. The aggregation arithmetic ran off-chain on the operator's machine — the "
            "§5.2 fallback, taken because 0G Compute serves inference against registered providers "
            "and does not execute arbitrary jobs. measurement_hash binds this score to one feature "
            "record and one scoring rule; it is tamper evidence, not a proof of honest execution, "
            "and nothing here is evidence that the underlying invoice data was real."
        ),
    }

    return ScoreResult(payload=payload, record=record, explanation=explanation)


def publish(result: ScoreResult) -> ScorePublicationPayload:
    """Persist the explanation, the feature record, then append the score.

    Order is deliberate. ``append_score`` writes to an append-only log (ASSUMPTIONS #12) that is
    never rewritten, so it goes last: a crash between calls leaves an orphaned explanation, which is
    harmless, rather than a published score whose ``explainability_ref`` points at nothing — which
    under §11 is a downgrade a borrower cannot contest.

    Note that every re-score is appended, whether or not it cleared the publish gate.
    ``published_onchain`` records that decision; dropping the row would erase the evidence that a
    threshold was applied.

    When ``config.OG_PUBLISH_ON_SCORE`` is set the 0G writes happen here too, in the order §5.3
    and §5.4 imply and no other: **local, then 0G Storage, then the chain.** The registry stores a
    storage root hash, so publishing on-chain before the record is actually stored would put a
    dangling reference in a permanent, public place. A storage write that succeeds while the chain
    write fails leaves an orphaned record, which costs a few kilobytes and nothing else.
    """
    store.save_explanation(result.payload.explainability_ref, result.explanation)
    store.save_feature_record(result.record)

    if config.OG_PUBLISH_ON_SCORE:
        publish_to_0g(result)

    store.append_score(result.payload)
    return result.payload


def publish_to_0g(result: ScoreResult) -> ScoreResult:
    """Write the feature record to 0G Storage and, if the gate allowed it, the score to the chain.

    Mutates ``result.payload`` in place so the persisted row carries the refs. Failures are
    recorded rather than raised: a 0G outage must not lose a score, and a payload whose
    ``storage_ref.provider`` is still ``"local"`` is self-describing — it says the record exists
    only on this machine, which is exactly what happened.

    The chain write is attempted only for scores that cleared the off-chain publish gate. The
    registry enforces §4's cooldown independently (§7), so the two gates can disagree; when they do,
    the on-chain answer wins and is recorded in the explanation. That is the correct precedence —
    the contract is the thing a consuming pool reads.
    """
    from continuum.og import chain as og_chain
    from continuum.og import storage as og_storage

    payload = result.payload
    og_log: dict = {}

    storage = og_storage.put_feature_record(result.record)
    payload.storage_ref = storage.ref
    og_log["storage"] = {
        "ok": storage.ok,
        "summary": storage.summary(),
        "root_hash": storage.ref.root_hash,
        "uri": storage.ref.uri,
        "explorer_url": storage.explorer_url or og_storage.explorer_url(storage.ref),
        "error": storage.error,
    }

    if payload.published_onchain:
        published = og_chain.publish_score(payload)
        payload.chain_ref = published.ref
        og_log["chain"] = {
            "published": published.published,
            "summary": published.summary(),
            "rejected_by": published.rejected_by,
            "seconds_remaining": published.seconds_remaining,
            "error": published.error,
            "tx_hash": published.ref.tx_hash if published.ref else "",
            "explorer_url": published.ref.explorer_url if published.ref else "",
        }
        if not published.published:
            # The off-chain gate said publish and the registry disagreed. Record the truth: this
            # score is not on-chain, whatever the local decision was.
            payload.published_onchain = False
    else:
        og_log["chain"] = {
            "published": False,
            "summary": "not attempted — held by the off-chain publish gate",
        }

    result.explanation["og"] = og_log
    return result


# --------------------------------------------------------------------------------------
# CLI — dry-run the fan-in for one borrower and show the whole build-up
# --------------------------------------------------------------------------------------


def main() -> None:
    """Score one borrower without persisting anything, and print how the number was reached.

    Deliberately not the cadence runner — that is ``continuum.orchestrator``. This exists so the
    fan-in can be inspected in isolation when a number looks wrong.
    """
    from continuum.scoring.anomaly import detect

    try:
        model = StructuredModel.load()
    except FileNotFoundError:
        print("No trained model found. Run: python -m continuum.scoring.train")
        return

    borrowers = store.load_borrowers()
    if not borrowers:
        print("No borrowers found. Run: python -m continuum.synth.generate")
        return

    borrower = borrowers[0]
    raw = store.load_raw().for_borrower(borrower["borrower_id"])
    as_of = utc(raw.feed_events["synced_at"].max().to_pydatetime())
    documents = store.load_documents(borrower["borrower_id"])

    print(f"Dry-run fan-in — {borrower.get('name')} ({borrower['borrower_id']})")
    print(f"as-of {iso(as_of)}  archetype={borrower.get('archetype', '?')}\n")

    record = build_feature_record(borrower, raw, as_of, documents=documents)
    # No history grid here — this is a single-point inspection, so the anomaly layer abstains and
    # contributes its abstention pressure. The orchestrator is what builds real history.
    import pandas as pd

    report = detect(record.features, pd.DataFrame(), as_of=as_of)

    result = score(
        record,
        model=model,
        anomaly_report=report,
        trigger_reason="manual_rescore",
        triggered_by_detail="aggregate.main dry run (no history, anomaly layer abstains)",
    )
    b = result.explanation["score_build_up"]
    i = result.explanation["interval"]

    print(f"{'PD (structured model)':<34}{b['pd']:>12.4f}   (base rate {b['base_rate']:.4f})")
    print(f"{'points from model':<34}{b['points_from_model']:>12.1f}")
    print(f"{'LLM flag penalty':<34}{b['llm_penalty']:>12.1f}   "
          f"{result.explanation['llm']['flags_raised'] or 'no flags raised'}")
    print(f"{'= points after documents':<34}{b['points_after_llm']:>12.1f}")
    print(f"{'grade ceiling at this interval':<34}{b['grade_ceiling']:>12.1f}   "
          f"{'BINDING' if b['ceiling_binding'] else 'not binding'}")
    print(f"{'-> published score':<34}{b['score_numeric']:>12d}   "
          f"{b['grade']}  CI {b['confidence_interval']}\n")

    print(f"interval half-width {i['half_width_points']:.1f}p — widening by term:")
    for k, v in sorted(i["term_contributions"].items(), key=lambda kv: -kv[1]):
        print(f"    {k:<18}{i['terms'][k]:>7.3f} x {config.CI_WEIGHTS[k]:.2f} = {v:>6.3f}")

    # attribute() prefixes a header row carrying the base log-odds; the rest are per-feature.
    attribution = result.explanation["feature_attribution"]
    header = attribution[0]
    rows = [r for r in attribution if "feature" in r]
    print(f"\ntop feature attributions (log-odds, + = raises PD)   base "
          f"{header['_base_log_odds']:+.3f}, features {header['_feature_sum_log_odds']:+.3f}:")
    for row in rows[:6]:
        print(f"    {row['feature']:<32}{row['value']:>12.3f}{row['contribution']:>+10.3f}")

    print(f"\ntrigger detail: {result.payload.triggered_by_detail}")
    print(f"publish: {result.explanation['publish_decision']['reason']}")
    print("\nNothing was written. To produce real scores:")
    print("    python -m continuum.orchestrator daily")


if __name__ == "__main__":
    main()
