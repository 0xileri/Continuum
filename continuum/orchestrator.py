"""Scoring cadence — the two ways a score comes to exist (§7).

§7 makes the cadence part of the product rather than an implementation detail:

    "Scheduled baseline: e.g. daily re-score for active borrowers... Event-triggered re-score: any
    new data event from Layer 1 that crosses a materiality threshold triggers an immediate re-score,
    independent of the daily cycle."

Both paths converge on ``aggregate.score`` and differ only in what wakes them and what
``trigger_reason`` they stamp. §14 nominates GitHub Actions cron for the scheduled path in Phase 0;
this module is what that cron calls.

**The daily path jitters ``as_of``, not just the publication timestamp.** §13's gaming concern is "a
borrower timing cash movements around known scoring windows", and jittering only the moment of
publication leaves the data cut-off at a predictable midnight — a borrower can still park cash at
23:00 and move it at 00:30. Randomising the cut-off is what actually closes that. Deterministic per
(borrower, date) so a run is reproducible and auditable (ASSUMPTIONS #17).

**``published_at`` tracks ``as_of``, not wall time.** Phase 0 scores a historical window, so stamping
wall time would put twelve borrowers' publications inside the same second and make §10's cooldown
arithmetic — which measures gaps between publications — meaningless. In a live deployment the two
converge anyway.

Run:
    python -m continuum.orchestrator daily
    python -m continuum.orchestrator event --borrower brw_01hx8k2m4n
    python -m continuum.orchestrator backfill --weeks 10      # a score history to look at
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timedelta

import pandas as pd

from continuum import config
from continuum.clock import iso, utc
from continuum.ingestion import features as feat
from continuum.ingestion import quality, store
from continuum.ingestion.features import RawData
from continuum.schemas import LLMFlags, ScorePublicationPayload, TriggerReason
from continuum.scoring import aggregate, llm_agent
from continuum.scoring.anomaly import AnomalyReport, detect
from continuum.scoring.structured import StructuredModel

HISTORY_OBSERVATIONS = 20
"""Prior observations handed to the anomaly layer. Must clear ``ANOMALY_MIN_HISTORY`` (14) or the
layer abstains, and cover ``anomaly.BASELINE_WINDOW`` (12) twice over so the "was this already out
of band last week?" check has a baseline of its own to work with."""

HISTORY_STEP_DAYS = 7
"""Spacing of that grid. Weekly because the anomaly layer's concentration check reads
``history.tail(2).head(1)`` as "a week ago" — a different step here would silently change what that
signal means."""


# --------------------------------------------------------------------------------------
# Time — where "now" comes from in a phase that replays a historical window
# --------------------------------------------------------------------------------------


def data_horizon() -> datetime:
    """The synthetic cohort's own present. Default ``as_of`` for every path here.

    Wall-clock ``now()`` would sit outside the generated window, so every feed would read as months
    stale and all twelve borrowers would degrade to their grade ceiling — a demo that looks like a
    broken engine rather than a working one. Phase 0 is explicitly synthetic (ASSUMPTIONS #10), so
    the data's own horizon is the honest "now". ``--as-of`` overrides it.
    """
    from continuum.synth.generate import HISTORY_START

    return utc(HISTORY_START + timedelta(days=config.HISTORY_DAYS))


def _warmup_floor() -> datetime:
    """Earliest instant with enough history behind it for features to mean anything."""
    from continuum.scoring.anomaly import REPLAY_WARMUP_DAYS
    from continuum.synth.generate import HISTORY_START

    return utc(HISTORY_START + timedelta(days=REPLAY_WARMUP_DAYS))


def jitter_minutes(borrower_id: str, as_of: datetime) -> int:
    """§13 randomised scoring time, deterministic per (borrower, date). ASSUMPTIONS #17.

    Hashed rather than drawn from a RNG so the same (borrower, date) always yields the same cut-off:
    a borrower disputing a score under §11 must be able to have the run reproduced exactly, and an
    unseeded random offset would make that impossible.
    """
    seed = f"{borrower_id}|{utc(as_of).date().isoformat()}"
    draw = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:4], "big")
    span = config.DAILY_JITTER_MINUTES
    return draw % (2 * span + 1) - span


# --------------------------------------------------------------------------------------
# Inputs for one borrower at one instant
# --------------------------------------------------------------------------------------


def _base_dso(borrower_id: str) -> float:
    from continuum.synth.profiles import COHORT_BY_ID

    profile = COHORT_BY_ID.get(borrower_id)
    return profile.base_dso if profile else 40.0


def feature_history(
    b_raw: RawData,
    as_of: datetime,
    *,
    base_dso: float,
    observations: int = HISTORY_OBSERVATIONS,
    step_days: int = HISTORY_STEP_DAYS,
) -> pd.DataFrame:
    """Recompute the borrower's recent feature trajectory so the anomaly layer has a baseline.

    Strictly prior to ``as_of`` — the newest row is one step back. Passing the current observation in
    as its own history would let today's value set the median it is measured against, and nothing
    would ever look unusual.

    **Recomputed rather than read from the training matrix, deliberately.** That matrix stops 90 days
    short of the end of history because its label needs a forward opportunity window, so reading it
    here would blind the live path to the most recent quarter — exactly the window in which the
    ``feed_goes_dark`` borrower's feeds actually go quiet. ``anomaly.replay``'s docstring documents
    the same trap.

    This is the honest cost of Phase 0 having no time-series store: ~20 feature recomputations per
    score. ASSUMPTIONS #2 already flags Postgres/TimescaleDB as the Phase 1 swap, at which point this
    function becomes a single indexed range query.
    """
    as_of = utc(as_of)
    floor = _warmup_floor()
    rows: list[dict] = []

    for step in range(observations, 0, -1):
        point = as_of - timedelta(days=step * step_days)
        if point < floor:
            continue
        rows.append(
            {"as_of": point, **feat.features_to_row(feat.compute_features(b_raw, point, base_dso=base_dso))}
        )

    return pd.DataFrame(rows)


def _prior_state(
    borrower_id: str,
) -> tuple[ScorePublicationPayload | None, ScorePublicationPayload | None]:
    """``(last recorded, last published)`` — the two different priors §10 needs.

    They diverge as soon as one re-score is recorded without clearing the publish threshold, which is
    the common case. Conflating them is how a slow drift escapes the gate; see
    ``aggregate.publish_decision``.
    """
    history = store.load_scores(borrower_id)
    if not history:
        return None, None
    published = [s for s in history if s.published_onchain]
    return history[-1], (published[-1] if published else None)


def _reusable_flags(borrower_id: str, documents: list[dict], as_of: datetime) -> LLMFlags | None:
    """Reuse the previous document assessment when the visible document set has not changed.

    ``FEED_SLA["document_feed"]`` allows 30 days between syncs, so daily re-scoring against an
    unchanged file would spend a Claude call per borrower per day to reproduce a known answer. The
    comparison is on the *newest visible document*, evaluated at both timestamps, so a document that
    becomes visible between runs correctly forces a fresh read.

    Never reuses an offline stub: a run with credentials attached must actually call the agent rather
    than inherit a zero-confidence placeholder (ASSUMPTIONS #8).
    """
    prior = store.load_feature_record(borrower_id)
    if prior is None or prior.llm_flags.source == "offline_fixture":
        return None

    def newest(at: datetime) -> str | None:
        visible = llm_agent.visible_documents(documents, at)
        return visible[0].get("doc_id") if visible else None

    return prior.llm_flags if newest(as_of) == newest(prior.as_of) else None


# --------------------------------------------------------------------------------------
# The shared scoring step
# --------------------------------------------------------------------------------------


def rescore(
    borrower: dict,
    raw: RawData,
    as_of: datetime,
    *,
    model: StructuredModel,
    documents: list[dict],
    trigger_reason: TriggerReason | None = None,
    fresh_llm: bool = False,
    force_escalation: bool = False,
    persist: bool = True,
) -> tuple[aggregate.ScoreResult, AnomalyReport]:
    """Score one borrower at one instant. Both cadence paths funnel through here.

    ``trigger_reason=None`` means "let the anomaly layer name it" — the event path. Passing one
    explicitly says the run was caused by something else, and the anomaly verdict then rides along in
    ``anomaly_pressure`` and the trigger detail instead of overwriting the reason: a daily run that
    happens to notice a deviation was still caused by the clock, and a log that claims otherwise
    makes the two cadences indistinguishable after the fact.

    ``force_escalation`` sends the document read straight to the stronger model and implies
    ``fresh_llm``. It exists for §11's dispute path (``api.file_dispute``): a borrower contesting a
    downgrade should not have their appeal answered by the same tier that produced the judgement
    they are contesting, and a cached assessment answers it with nothing at all.
    """
    borrower_id = borrower["borrower_id"]
    as_of = utc(as_of)
    b_raw = raw.for_borrower(borrower_id)
    base_dso = _base_dso(borrower_id)

    if force_escalation:
        flags = llm_agent.agent().assess(
            borrower.get("name", borrower_id),
            borrower.get("sector", ""),
            as_of,
            documents,
            force_escalation=True,
        )
    else:
        flags = None if fresh_llm else _reusable_flags(borrower_id, documents, as_of)
    record = aggregate.build_feature_record(
        borrower, b_raw, as_of, documents=documents, base_dso=base_dso, llm_flags=flags
    )

    prior, last_published = _prior_state(borrower_id)
    report = detect(
        record.features,
        feature_history(b_raw, as_of, base_dso=base_dso),
        as_of=as_of,
        invoices=b_raw.invoices,
        repayments=b_raw.repayments,
        current_dq=record.data_quality_score,
        # The last data-quality reading we actually acted on, which is the one a drop should be
        # measured against — not the value at an arbitrary point on the history grid.
        prior_dq=prior.data_quality_score if prior else None,
    )

    result = aggregate.score(
        record,
        model=model,
        anomaly_report=report,
        trigger_reason=trigger_reason or report.trigger_reason or "scheduled_daily",
        prior=prior,
        last_published=last_published,
        published_at=as_of,
    )
    if persist:
        aggregate.publish(result)
    return result, report


# --------------------------------------------------------------------------------------
# Path 1 — scheduled daily baseline
# --------------------------------------------------------------------------------------


def daily(
    as_of: datetime | None = None,
    *,
    borrower_id: str | None = None,
    fresh_llm: bool = False,
    persist: bool = True,
    verbose: bool = True,
) -> list[aggregate.ScoreResult]:
    """Re-score every active borrower once, at a per-borrower jittered cut-off."""
    as_of = utc(as_of) if as_of else data_horizon()
    model = StructuredModel.load()
    raw = store.load_raw()
    borrowers = [b for b in store.load_borrowers() if borrower_id in (None, b["borrower_id"])]

    if verbose:
        print(f"Daily re-score — nominal as-of {iso(as_of)}  ({len(borrowers)} borrowers)")
        print(f"cut-off jittered +/-{config.DAILY_JITTER_MINUTES}min per borrower (§13)\n")
        print(f"{'borrower':<16}{'grade':>6}{'score':>7}{'interval':>13}{'delta':>7}{'dq':>6}"
              f"{'pub':>5}  trigger")
        print("-" * 118)

    results: list[aggregate.ScoreResult] = []
    for b in borrowers:
        bid = b["borrower_id"]
        cutoff = as_of + timedelta(minutes=jitter_minutes(bid, as_of))
        documents = store.load_documents(bid)
        result, _ = rescore(
            b,
            raw,
            cutoff,
            model=model,
            documents=documents,
            trigger_reason="scheduled_daily",
            fresh_llm=fresh_llm,
            persist=persist,
        )
        results.append(result)

        if verbose:
            p = result.payload
            lo, hi = p.confidence_interval
            delta = f"{p.score_delta:+d}" if p.prior_score_numeric is not None else "-"
            print(
                f"{bid:<16}{p.score:>6}{p.score_numeric:>7}{f'[{lo}-{hi}]':>13}{delta:>7}"
                f"{p.data_quality_score:>6.2f}{'yes' if p.published_onchain else 'no':>5}  "
                f"{p.triggered_by_detail[:52]}"
            )

    if verbose:
        published = sum(1 for r in results if r.payload.published_onchain)
        print(
            f"\n{len(results)} re-scored, {published} cleared §10's publish gate"
            f"{'' if persist else '  (dry run — nothing written)'}"
        )
    return results


# --------------------------------------------------------------------------------------
# Path 2 — event-triggered, out of cadence
# --------------------------------------------------------------------------------------


def event(
    borrower_id: str,
    as_of: datetime | None = None,
    *,
    force: bool = False,
    fresh_llm: bool = False,
    persist: bool = True,
    verbose: bool = True,
) -> aggregate.ScoreResult | None:
    """Check the anomaly layer and re-score only if something material happened.

    Returns ``None`` when nothing crossed a threshold — and writes nothing. That silence is the
    feature: a path that re-scores on every poll is the daily cron with extra steps, and §7's
    event-driven claim would be marketing. No jitter here; an event-driven re-score happens when the
    event arrives, so there is no predictable window to hide from.
    """
    as_of = utc(as_of) if as_of else data_horizon()
    model = StructuredModel.load()
    raw = store.load_raw()
    borrower = next((b for b in store.load_borrowers() if b["borrower_id"] == borrower_id), None)
    if borrower is None:
        raise KeyError(f"unknown borrower {borrower_id!r}")

    b_raw = raw.for_borrower(borrower_id)
    base_dso = _base_dso(borrower_id)
    documents = store.load_documents(borrower_id)

    # Evaluate materiality on the features alone, before spending a model or agent call.
    features = feat.compute_features(b_raw, as_of, base_dso=base_dso)
    freshness = feat.source_freshness(b_raw.feed_events, as_of)
    dq, _ = quality.data_quality_score(freshness, as_of)
    prior, _ = _prior_state(borrower_id)

    report = detect(
        features,
        feature_history(b_raw, as_of, base_dso=base_dso),
        as_of=as_of,
        invoices=b_raw.invoices,
        repayments=b_raw.repayments,
        current_dq=dq,
        prior_dq=prior.data_quality_score if prior else None,
    )

    if verbose:
        print(f"Event check — {borrower.get('name', borrower_id)} ({borrower_id})")
        print(f"as-of {iso(as_of)}\n  {report.summary()}\n")

    if not report.triggered and not force:
        if verbose:
            print("Nothing material. No re-score, nothing written.")
            print("(Pass --force to score anyway; it is stamped manual_rescore, not an event.)")
        return None

    result, _ = rescore(
        borrower,
        raw,
        as_of,
        model=model,
        documents=documents,
        trigger_reason=report.trigger_reason if report.triggered else "manual_rescore",
        fresh_llm=fresh_llm,
        persist=persist,
    )

    if verbose:
        p = result.payload
        lo, hi = p.confidence_interval
        was = (
            f"  (was {p.prior_score}/{p.prior_score_numeric}, {p.score_delta:+d})"
            if p.prior_score
            else ""
        )
        print(f"Re-scored on {p.trigger_reason}: {p.score} {p.score_numeric} [{lo}-{hi}]{was}")
        print(f"  {p.triggered_by_detail}")
        print(f"  §10: {result.explanation['publish_decision']['reason']}")
        print(f"  explainability_ref: {p.explainability_ref}")
    return result


# --------------------------------------------------------------------------------------
# Backfill — run the daily path across a window so there is a history to look at
# --------------------------------------------------------------------------------------


def backfill(
    *,
    weeks: int = 10,
    step_days: int = 7,
    borrower_id: str | None = None,
    end: datetime | None = None,
    fresh_llm: bool = False,
) -> list[aggregate.ScoreResult]:
    """Replay the scheduled path over a trailing window, oldest first.

    §15's Phase 0 exit criterion is a dashboard "where a borrower's score visibly moves in response
    to a real data event". One run cannot show movement, so this produces the series that makes the
    publish gate, the cooldown and the widening interval observable at all.
    """
    end = utc(end) if end else data_horizon()
    points = [end - timedelta(days=i * step_days) for i in range(weeks - 1, -1, -1)]

    print(f"Backfill — {len(points)} weekly checkpoints, {iso(points[0])} -> {iso(points[-1])}\n")
    out: list[aggregate.ScoreResult] = []
    for i, point in enumerate(points, 1):
        print(f"[{i}/{len(points)}] {point.date()}")
        out.extend(
            daily(
                point,
                borrower_id=borrower_id,
                fresh_llm=fresh_llm,
                verbose=False,
            )
        )
    print(f"\n{len(out)} scores written across {len(points)} checkpoints.")
    print("Inspect with: python -m continuum.orchestrator show --borrower <id>")
    return out


def show(borrower_id: str) -> None:
    """Print one borrower's score history from the append-only log."""
    history = store.load_scores(borrower_id)
    if not history:
        print(f"No scores recorded for {borrower_id}.")
        return

    print(f"Score history — {borrower_id}  ({len(history)} re-scores)\n")
    print(f"{'published_at':<22}{'grade':>6}{'score':>7}{'interval':>13}{'width':>7}{'dq':>6}"
          f"{'pub':>5}  trigger")
    print("-" * 120)
    for p in history:
        lo, hi = p.confidence_interval
        print(
            f"{iso(p.published_at)[:19]:<22}{p.score:>6}{p.score_numeric:>7}"
            f"{f'[{lo}-{hi}]':>13}{hi - lo:>7}{p.data_quality_score:>6.2f}"
            f"{'yes' if p.published_onchain else 'no':>5}  {p.trigger_reason}"
        )

    first, last = history[0], history[-1]
    w0 = first.confidence_interval[1] - first.confidence_interval[0]
    w1 = last.confidence_interval[1] - last.confidence_interval[0]
    print(
        f"\n{first.score}/{first.score_numeric} -> {last.score}/{last.score_numeric} "
        f"({last.score_numeric - first.score_numeric:+d} points), "
        f"interval width {w0} -> {w1} points, "
        f"{sum(1 for p in history if p.published_onchain)}/{len(history)} republished"
    )


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _as_of(raw: str | None) -> datetime | None:
    return utc(datetime.fromisoformat(raw)) if raw else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daily", help="Scheduled baseline re-score of the cohort")
    d.add_argument("--as-of", default=None, help="ISO timestamp; defaults to the data horizon")
    d.add_argument("--borrower", default=None)
    d.add_argument("--fresh-llm", action="store_true", help="Re-run the document agent even if the visible document set is unchanged")
    d.add_argument("--dry-run", action="store_true", help="Score but write nothing")
    d.add_argument("--reset", action="store_true", help="Clear the score log first")

    e = sub.add_parser("event", help="Event-triggered re-score, if anything is material")
    e.add_argument("--borrower", required=True)
    e.add_argument("--as-of", default=None)
    e.add_argument("--force", action="store_true", help="Score even if nothing triggered")
    e.add_argument("--fresh-llm", action="store_true")
    e.add_argument("--dry-run", action="store_true")

    b = sub.add_parser("backfill", help="Replay the daily path over a trailing window")
    b.add_argument("--weeks", type=int, default=10)
    b.add_argument("--step-days", type=int, default=7)
    b.add_argument("--borrower", default=None)
    b.add_argument("--as-of", default=None, help="End of the window")
    b.add_argument("--fresh-llm", action="store_true")
    b.add_argument("--reset", action="store_true")

    s = sub.add_parser("show", help="Print a borrower's recorded score history")
    s.add_argument("--borrower", required=True)

    args = parser.parse_args()

    try:
        if args.cmd in ("daily", "backfill") and args.reset:
            store.clear_scores(args.borrower)
            print(f"Cleared score log for {args.borrower or 'all borrowers'}.\n")

        if args.cmd == "daily":
            daily(
                _as_of(args.as_of),
                borrower_id=args.borrower,
                fresh_llm=args.fresh_llm,
                persist=not args.dry_run,
            )
        elif args.cmd == "event":
            event(
                args.borrower,
                _as_of(args.as_of),
                force=args.force,
                fresh_llm=args.fresh_llm,
                persist=not args.dry_run,
            )
        elif args.cmd == "backfill":
            backfill(
                weeks=args.weeks,
                step_days=args.step_days,
                borrower_id=args.borrower,
                end=_as_of(args.as_of),
                fresh_llm=args.fresh_llm,
            )
        elif args.cmd == "show":
            show(args.borrower)
    except FileNotFoundError as exc:
        print(f"Missing prerequisite: {exc}")
        print("\nBuild order:")
        print("    python -m continuum.synth.generate")
        print("    python -m continuum.ingestion.build_features")
        print("    python -m continuum.scoring.train")


if __name__ == "__main__":
    main()
