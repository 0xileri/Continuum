"""The §15 Phase 0 exit criterion, driven end to end.

    *"You can show a live dashboard where a borrower's score visibly moves in response to a real
    data event, with an explainability trail."* — claude.md §15

A score history alone does not demonstrate that. Backfilled scores move because the synthetic
borrower was generated to deteriorate; nothing in that proves the engine is *reactive* rather than
replaying a script. This tool closes that gap by injecting an event into the Layer 1 store after
the fact — the way a webhook would — and then running the ordinary event path against it. Nothing
here calls the scorer directly or hands it a verdict: the injected rows go into
``data/raw/*.parquet``, and the anomaly layer decides on its own whether they are material.

That distinction is the whole point. If a scenario fails to trigger, this script says so and
writes nothing, because a demo that forces the re-score it is meant to be evidence for is not
evidence.

Scenarios, and which requirement each exercises:

    payer_default    §13 payer-side risk — the concentrated payer stops settling, and a facility
                     repayment lands far outside the borrower's own pattern (§7's worked example).
    dispute_spike    §7 part 3 — a share of the open book goes into dispute inside a week.
    covenant_breach  §6/§7 part 2 — a breach notice arrives on the document feed and the reasoning
                     agent, not a keyword matcher, is what turns it into a score movement.
    feed_goes_dark   §6, the important one — a feed stops reporting and the score must "visibly
                     degrade in confidence, not silently freeze at the last-known value".

Every injection is snapshotted first and is fully reversible:

    python scripts/demo_event.py --borrower brw_01hx8k2m4n --scenario payer_default
    python scripts/demo_event.py --revert
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from continuum import config, consumption, orchestrator  # noqa: E402
from continuum.clock import iso, utc  # noqa: E402
from continuum.ingestion import store  # noqa: E402
from continuum.schemas import ScorePublicationPayload  # noqa: E402
from continuum.synth import documents as doc_templates  # noqa: E402
from continuum.synth.profiles import COHORT_BY_ID, PAYERS_BY_ID  # noqa: E402

SNAPSHOT_DIR = config.RAW_DIR / "_demo_snapshot"
RAW_FILES = (
    "invoices.parquet",
    "bank_transactions.parquet",
    "repayments.parquet",
    "feed_events.parquet",
    "documents.jsonl",
)


# --------------------------------------------------------------------------------------
# Snapshot / revert — an injection must never be a one-way door
# --------------------------------------------------------------------------------------


def snapshot() -> bool:
    """Copy the raw tables aside once, before the first injection. Returns True if it wrote one.

    Taken once and not overwritten: a second scenario run must still be able to get back to the
    *generated* data, not to the state the first injection left behind.
    """
    if SNAPSHOT_DIR.exists():
        return False
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for name in RAW_FILES:
        src = config.RAW_DIR / name
        if src.exists():
            shutil.copy2(src, SNAPSHOT_DIR / name)
    return True


def revert() -> None:
    if not SNAPSHOT_DIR.exists():
        print("No snapshot on disk — nothing to revert.")
        print("(If the raw data looks wrong, regenerate it: python -m continuum.synth.generate)")
        return
    for name in RAW_FILES:
        src = SNAPSHOT_DIR / name
        if src.exists():
            shutil.copy2(src, config.RAW_DIR / name)
    shutil.rmtree(SNAPSHOT_DIR)
    print(f"Reverted {len(RAW_FILES)} raw tables to their pre-injection state.")
    print(
        "\nScores written after the injection are still in data/scores/ — the log is append-only\n"
        "by design (ASSUMPTIONS #12). Clear them with:\n"
        "    python -m continuum.orchestrator daily --reset --dry-run"
    )


# --------------------------------------------------------------------------------------
# Raw table I/O
# --------------------------------------------------------------------------------------


class RawTables:
    """The raw store, loaded for mutation rather than for scoring.

    ``store.load_raw`` returns a scoring view; this reads the same files as plain frames so a
    scenario can write rows back. Kept out of ``continuum.ingestion.store`` deliberately: nothing
    in the engine may write to Layer 1, and a mutation helper living next to the readers is an
    invitation to use it from somewhere that should not.
    """

    def __init__(self) -> None:
        self.invoices = pd.read_parquet(config.RAW_DIR / "invoices.parquet")
        self.bank = pd.read_parquet(config.RAW_DIR / "bank_transactions.parquet")
        self.repayments = pd.read_parquet(config.RAW_DIR / "repayments.parquet")
        self.feeds = pd.read_parquet(config.RAW_DIR / "feed_events.parquet")
        self.documents = store.load_documents()

    def write(self) -> None:
        self.invoices.to_parquet(config.RAW_DIR / "invoices.parquet", index=False)
        self.bank.to_parquet(config.RAW_DIR / "bank_transactions.parquet", index=False)
        self.repayments.to_parquet(config.RAW_DIR / "repayments.parquet", index=False)
        self.feeds.to_parquet(config.RAW_DIR / "feed_events.parquet", index=False)
        with open(config.RAW_DIR / "documents.jsonl", "w", encoding="utf-8") as fh:
            for d in self.documents:
                fh.write(json.dumps(d) + "\n")


def _open_book(inv: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    visible = inv[(inv["issued_at"] <= as_of) & (inv["ingested_at"] <= as_of)]
    return visible[visible["settled_at"].isna() | (visible["settled_at"] > as_of)]


def _top_payer(inv: pd.DataFrame, as_of: datetime) -> str:
    book = _open_book(inv, as_of)
    if book.empty:
        book = inv[inv["issued_at"] <= as_of]
    return str(book.groupby("payer_id")["amount"].sum().idxmax())


# --------------------------------------------------------------------------------------
# Scenarios — each mutates the raw tables and returns what it did
# --------------------------------------------------------------------------------------


def scenario_payer_default(tables: RawTables, borrower_id: str, as_of: datetime) -> list[str]:
    """The concentrated payer stops settling, and the facility repayment slips with it.

    §13 calls this "payer-side risk laundering": the borrower's own trading is unchanged, and the
    deterioration arrives entirely through a customer. Two things are injected because that is how
    it actually surfaces — receipts stop arriving, and the borrower funds the gap by paying the
    facility late.
    """
    notes: list[str] = []
    inv = tables.invoices
    mine = inv["borrower_id"] == borrower_id
    payer_id = _top_payer(inv[mine], as_of)
    payer_name = PAYERS_BY_ID[payer_id].name

    # 1. Receipts from that payer stop arriving: invoices settled in the last 30 days revert to
    #    unsettled, and the bank credits that recorded them disappear with them.
    window = as_of - timedelta(days=30)
    target = mine & (inv["payer_id"] == payer_id) & inv["settled_at"].notna() & (
        inv["settled_at"] >= window
    )
    unsettled_ids = list(inv.loc[target, "invoice_id"])
    unsettled_value = float(inv.loc[target, "amount"].sum())
    inv.loc[target, "settled_at"] = pd.NaT

    credit_ids = {f"txn_{i[4:]}_c" for i in unsettled_ids}
    before = len(tables.bank)
    tables.bank = tables.bank[~tables.bank["txn_id"].isin(credit_ids)].reset_index(drop=True)
    notes.append(
        f"{len(unsettled_ids)} invoices from {payer_name} worth {unsettled_value:,.0f} reverted to "
        f"unsettled; {before - len(tables.bank)} matching bank credits withdrawn"
    )

    # 2. The facility repayment that fell due about six weeks ago settles now, far outside the
    #    borrower's own pattern. §7's worked example, and what _repayment_signals watches for.
    rep = tables.repayments
    rmine = rep["borrower_id"] == borrower_id
    candidates = rep[rmine & (rep["due_at"] <= as_of - timedelta(days=40))]
    if candidates.empty:
        notes.append("no repayment old enough to make late; receipts-only injection")
        return notes

    idx = candidates["due_at"].idxmax()
    paid_at = as_of - timedelta(days=2)
    days_late = float((paid_at - rep.at[idx, "due_at"]).total_seconds() / 86400.0)
    rep.at[idx, "paid_at"] = paid_at
    rep.at[idx, "days_late"] = days_late
    rep.at[idx, "status"] = "paid_late"
    rep.at[idx, "amount_paid"] = rep.at[idx, "amount_due"]
    notes.append(
        f"facility repayment {rep.at[idx, 'repayment_id']} settled {days_late:.0f} days late "
        f"(due {rep.at[idx, 'due_at'].date()}, paid {paid_at.date()})"
    )
    return notes


def scenario_dispute_spike(tables: RawTables, borrower_id: str, as_of: datetime) -> list[str]:
    """A material share of the open book goes into dispute inside a week.

    Sized as a share, not a count: ``MATERIALITY["dispute_value_pct_of_receivables"]`` is 5% of
    receivables by value, so the injection climbs the open book largest-first until it clears
    12% — comfortably material without being a single implausible mega-invoice.
    """
    inv = tables.invoices
    book = _open_book(inv[inv["borrower_id"] == borrower_id], as_of).sort_values(
        "amount", ascending=False
    )
    if book.empty:
        return ["no open receivables to dispute"]

    receivables = float(book["amount"].sum())
    target_value = receivables * 0.12
    opened_at = as_of - timedelta(days=2)

    disputed, running = [], 0.0
    for row in book.itertuples():
        if running >= target_value:
            break
        disputed.append(row.invoice_id)
        running += float(row.amount)

    mask = inv["invoice_id"].isin(disputed)
    inv.loc[mask, "disputed"] = True
    inv.loc[mask, "dispute_opened_at"] = opened_at

    return [
        f"{len(disputed)} invoices worth {running:,.0f} disputed on {opened_at.date()} — "
        f"{running / receivables:.1%} of the {receivables:,.0f} open book "
        f"(materiality threshold {config.MATERIALITY['dispute_value_pct_of_receivables']:.0%})"
    ]


def scenario_covenant_breach(tables: RawTables, borrower_id: str, as_of: datetime) -> list[str]:
    """A breach notice lands on the document feed.

    The one scenario the anomaly layer structurally cannot catch — a breach is not a number until
    something has read the document — so it exercises the document-arrival trigger
    (``orchestrator.new_document_since_last_score``) and §7 part 2 end to end.

    Rendered from the same template the generator uses, with the same ``_truth`` block, so the
    agent is graded on the same basis as every other document. The body is deliberately a real
    covenant certificate with a failing ratio rather than a sentence saying "breach": a keyword
    matcher passes the latter and fails the former, and that gap is why this layer costs a model
    call at all.
    """
    profile = COHORT_BY_ID[borrower_id]
    tmpl = doc_templates.TEMPLATES["covenant_breach_notice"]
    created = as_of - timedelta(days=1)

    inv = tables.invoices
    window = inv[
        (inv["borrower_id"] == borrower_id)
        & (inv["issued_at"] <= created)
        & (inv["issued_at"] >= created - timedelta(days=90))
    ]
    revenue = float(window["amount"].sum() / 3) if len(window) else profile.base_monthly_revenue
    receivables = float(window["amount"].sum() * 0.42) if len(window) else revenue * 1.3
    disputed_rows = window[window["disputed"]] if len(window) else window
    inv_ids = list(disputed_rows["invoice_id"].head(3))
    while len(inv_ids) < 3:
        inv_ids.append(f"inv_{borrower_id[-6:]}_{len(inv_ids):05d}")

    body = tmpl.body.format(
        borrower=profile.name,
        period=created.strftime("%d %B %Y"),
        revenue=revenue,
        receivables=receivables,
        dso=profile.base_dso + 18,
        dso_prior=profile.base_dso,
        cash=max(4_000.0, revenue * 0.08),
        payer_name=PAYERS_BY_ID[profile.payer_ids[0]].name,
        sector_title=doc_templates.SECTOR_TITLES.get(profile.sector, "Business"),
        inv_a=inv_ids[0],
        inv_b=inv_ids[1],
        inv_c=inv_ids[2],
        disputed_amount=(
            float(disputed_rows["amount"].sum()) if len(disputed_rows) else revenue * 0.18
        ),
    )

    doc_id = f"doc_{borrower_id[-6:]}_demo_breach"
    tables.documents = [d for d in tables.documents if d["doc_id"] != doc_id]
    tables.documents.append(
        {
            "doc_id": doc_id,
            "borrower_id": borrower_id,
            "doc_type": tmpl.doc_type,
            "title": tmpl.title,
            "body": body,
            "created_at": created.isoformat(),
            "source": "document_feed",
            "provenance": doc_templates.PROVENANCE.get(tmpl.doc_type, "self_reported"),
            "scenario_tag": "covenant_breach_notice",
            "_truth": {
                "covenant_breach": tmpl.truth_covenant_breach,
                "adverse_news_detected": tmpl.truth_adverse_news,
                "payer_deterioration": tmpl.truth_payer_deterioration,
            },
        }
    )

    return [
        f"breach notice {doc_id} filed {created.date()} — interest cover 1.42x against a "
        f"2.00x covenant, concentration 61% against a 45% limit"
    ]


def scenario_feed_goes_dark(
    tables: RawTables, borrower_id: str, as_of: datetime, *, days: int = 21
) -> list[str]:
    """The originator stops syncing. §6's requirement, isolated.

    Nothing about the borrower changes — no invoice, no repayment, no document. Only the evidence
    stops arriving. §6 is explicit that the score must then "visibly degrade in confidence, not
    silently freeze at the last-known value", and this is the scenario that shows whether it does:
    the interval widens, ``GRADE_CEILING_HALFWIDTH_MULTIPLE`` pulls the attainable grade down with
    it, and §11 turns both into a higher rate.
    """
    feeds = tables.feeds
    dark = ("invoice_feed", "accounting_feed")
    cutoff = as_of - timedelta(days=days)

    mask = (
        (feeds["borrower_id"] == borrower_id)
        & feeds["feed"].isin(dark)
        & (feeds["synced_at"] >= cutoff)
    )
    removed = int(mask.sum())
    tables.feeds = feeds[~mask].reset_index(drop=True)

    return [
        f"{removed} heartbeats removed: {', '.join(dark)} have not reported since "
        f"{cutoff.date()} ({days} days of silence)"
    ]


SCENARIOS = {
    "payer_default": scenario_payer_default,
    "dispute_spike": scenario_dispute_spike,
    "covenant_breach": scenario_covenant_breach,
    "feed_goes_dark": scenario_feed_goes_dark,
}


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------


def _print_score_line(label: str, p: ScorePublicationPayload) -> None:
    lo, hi = p.confidence_interval
    print(
        f"  {label:<8}{p.score:>5}{p.score_numeric:>6}   [{lo}-{hi}] width {hi - lo:>3}   "
        f"dq {p.data_quality_score:.2f}   {p.trigger_reason}"
    )


def _print_attribution(explanation: dict, limit: int = 6) -> None:
    rows = [r for r in explanation.get("feature_attribution", []) if "feature" in r]
    if not rows:
        return
    print("\n  explainability trail — TreeSHAP, log-odds, + raises modelled risk (§7):")
    for row in rows[:limit]:
        arrow = "^" if row["contribution"] > 0 else "v"
        print(
            f"    {arrow} {row['feature']:<32}{row['value']:>14,.3f}"
            f"{row['contribution']:>+9.3f}"
        )
    print(f"    full artifact: data/explain/{explanation['explainability_ref']}.json")


def _print_interval(explanation: dict) -> None:
    interval = explanation.get("interval", {})
    terms = interval.get("term_contributions", {})
    if not terms:
        return
    print(
        f"\n  why the interval is +/-{interval['half_width_points']:.0f} points "
        f"(ASSUMPTIONS #6){' — CAPPED' if interval.get('capped') else ''}:"
    )
    for name, contribution in sorted(terms.items(), key=lambda kv: -kv[1]):
        raw = interval["terms"].get(name, 0.0)
        print(f"    {name:<18}{raw:>7.3f} x {config.CI_WEIGHTS[name]:.2f} = {contribution:>6.3f}")


def _print_terms(before: ScorePublicationPayload, after: ScorePublicationPayload) -> None:
    """§11 in money: what the pool would charge before and after the event."""
    receivables = consumption.eligible_receivables_from(
        store.load_feature_record(after.borrower_id)
    )
    t0 = consumption.terms_for(before, eligible_receivables=receivables)
    t1 = consumption.terms_for(after, prior_terms=t0, eligible_receivables=receivables)

    print("\n  §11 consumption layer — the terms a pool would apply:")
    print(f"    before   {t0.summary()}")
    print(f"    after    {t1.summary()}")
    if t1.rate_change_bps:
        print(f"    change   {t1.rate_change_bps:+d}bps")
    for note in t1.notes:
        print(f"      - {note}")


# --------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------


def run(borrower_id: str, scenario: str, as_of: datetime | None, *, dry_run: bool) -> int:
    borrower = next(
        (b for b in store.load_borrowers() if b["borrower_id"] == borrower_id), None
    )
    if borrower is None:
        print(f"Unknown borrower {borrower_id!r}. Run with --list to see the cohort.")
        return 2

    as_of = utc(as_of) if as_of else orchestrator.data_horizon()
    before = store.latest_score(borrower_id)
    if before is None:
        print(f"No score on file for {borrower_id}. The demo needs a prior to move away from:")
        print("    python -m continuum.orchestrator backfill --weeks 12")
        return 2

    print(f"Scenario '{scenario}' — {borrower.get('name', borrower_id)} ({borrower_id})")
    print(f"as-of {iso(as_of)}\n")
    print("Before the event:")
    _print_score_line("score", before)

    if snapshot():
        print(f"\nSnapshot written to {SNAPSHOT_DIR.name}/ — revert with --revert")
    else:
        print(f"\nSnapshot already present in {SNAPSHOT_DIR.name}/ (an injection is live)")

    tables = RawTables()
    print(f"\nInjecting into Layer 1:")
    for note in SCENARIOS[scenario](tables, borrower_id, as_of):
        print(f"  - {note}")
    tables.write()

    print("\nRunning the ordinary event path — the anomaly layer decides, not this script.\n")
    result = orchestrator.event(borrower_id, as_of, persist=not dry_run, verbose=True)

    if result is None:
        print(
            "\nThe injection did not cross a materiality threshold, so no re-score fired and\n"
            "nothing was written. That is the honest outcome, not a bug: forcing the re-score\n"
            "this demo exists to evidence would make the demo worthless. Revert with --revert."
        )
        return 1

    after = result.payload
    print("\nAfter the event:")
    _print_score_line("score", before)
    _print_score_line("->", after)
    delta = after.score_numeric - before.score_numeric
    w0 = before.confidence_interval[1] - before.confidence_interval[0]
    w1 = after.confidence_interval[1] - after.confidence_interval[0]
    print(
        f"\n  {before.score} -> {after.score}   {delta:+d} points   "
        f"interval {w0} -> {w1} points   "
        f"published: {'yes' if after.published_onchain else 'no (held by §10 gate)'}"
    )

    _print_interval(result.explanation)
    _print_attribution(result.explanation)

    llm = result.explanation.get("llm", {})
    if llm.get("source") == "offline_fixture":
        print(
            "\n  NOTE: the document agent was offline (no ANTHROPIC_API_KEY), so it raised no\n"
            "  flags at zero confidence — which widened the interval rather than clearing the\n"
            f"  borrower. {llm.get('rationale', '')}"
        )
    elif llm.get("flags_raised"):
        print(
            f"\n  document agent ({llm.get('model_used')}, confidence {llm.get('confidence'):.2f}): "
            f"{', '.join(llm['flags_raised'])} — {llm.get('penalty_points', 0):+.0f} points"
        )
        if llm.get("rationale"):
            print(f"    {llm['rationale']}")

    _print_terms(before, after)

    if dry_run:
        print("\n(dry run — nothing was written to the score log)")
    print("\nSee it in the dashboard:")
    print("    python -m uvicorn continuum.api:app --port 8787")
    print("    cd dashboard && npm run dev")
    print("\nUndo the injection when you are done:  python scripts/demo_event.py --revert")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("borrower", nargs="?", help="borrower id (positional or --borrower)")
    parser.add_argument("--borrower", dest="borrower_flag", default=None)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="payer_default")
    parser.add_argument("--as-of", default=None, help="ISO timestamp; defaults to the data horizon")
    parser.add_argument("--dry-run", action="store_true", help="Score but write no publication")
    parser.add_argument("--revert", action="store_true", help="Undo every injection")
    parser.add_argument("--list", action="store_true", help="List the cohort and the scenarios")
    args = parser.parse_args()

    if args.revert:
        revert()
        return 0

    if args.list:
        print("Scenarios:")
        for name, fn in sorted(SCENARIOS.items()):
            print(f"  {name:<18}{(fn.__doc__ or '').strip().splitlines()[0]}")
        print("\nCohort:")
        try:
            for b in store.load_borrowers():
                print(f"  {b['borrower_id']}  {b['name']:<30}{b['archetype']}")
        except FileNotFoundError:
            print("  (none — run python -m continuum.synth.generate)")
        return 0

    borrower_id = args.borrower_flag or args.borrower
    if not borrower_id:
        parser.error("pass a borrower id (positionally or with --borrower), or use --list")

    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    try:
        return run(borrower_id, args.scenario, as_of, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(f"Missing prerequisite: {exc}\n")
        print("Build order:")
        print("    python -m continuum.synth.generate")
        print("    python -m continuum.ingestion.build_features")
        print("    python -m continuum.scoring.train")
        print("    python -m continuum.orchestrator backfill --weeks 12")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
