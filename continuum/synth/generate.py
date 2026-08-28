"""Synthetic invoice-financing data generator. ASSUMPTIONS #10.

Emits raw, event-level records — the shape a real Layer 1 integration would deliver (§6):

  bank_transactions.parquet  — Plaid-style debits/credits
  invoices.parquet           — invoice-level payer, amount, due date, dispute status (§6)
  repayments.parquet         — borrower repayments against the facility
  documents.jsonl            — unstructured text for the Claude agent
  outcomes.parquet           — ground-truth default labels for model training only
  feed_events.parquet        — per-feed sync heartbeats, so staleness is derived from real
                               feed silence rather than being asserted

Nothing here is aggregated. Feature derivation belongs to the ingestion layer, so the
generator can be swapped for a real originator API without touching the scoring path.

Run:  python -m continuum.synth.generate
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from continuum import config
from continuum.synth import documents as docs
from continuum.synth.profiles import COHORT, PAYERS_BY_ID, BorrowerProfile, health_at

# History ends today (the brief's "current date") so the dashboard shows a live-looking system.
HISTORY_END = datetime(2026, 8, 11, tzinfo=timezone.utc)
HISTORY_START = HISTORY_END - timedelta(days=config.HISTORY_DAYS)


def _rng(borrower_id: str, salt: str = "") -> np.random.Generator:
    """Per-borrower deterministic RNG so one profile's changes don't reshuffle the others.

    Uses SHA-256 rather than the builtin ``hash()``: Python randomizes string hashing per
    process (PYTHONHASHSEED), so a ``hash()``-derived seed silently produces different data on
    every run. That breaks the reproducibility RANDOM_SEED is supposed to guarantee, and makes
    any backtest result impossible to reproduce.
    """
    key = f"{config.RANDOM_SEED}|{borrower_id}|{salt}".encode()
    seed = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % (2**32)
    return np.random.default_rng(seed)


# --------------------------------------------------------------------------------------
# Invoices — the core asset in this vertical
# --------------------------------------------------------------------------------------


def gen_invoices(p: BorrowerProfile) -> pd.DataFrame:
    """Invoice-level records with payer, amount, due date and dispute status (§6)."""
    rng = _rng(p.borrower_id, "inv")
    rows: list[dict] = []
    n_days = config.HISTORY_DAYS
    seq = 0

    # Invoices arrive in clusters, as real trading does — not one per day.
    day = 0
    while day < n_days:
        t = day / n_days
        health = health_at(p, t)
        issue_date = HISTORY_START + timedelta(days=int(day))

        # Days until the next batch, decided up front so per-invoice amounts can be scaled to
        # hit the monthly revenue target exactly.
        step = int(rng.integers(2, 6))

        # Deteriorating borrowers issue fewer, lumpier invoices as trading contracts.
        n_this_batch = max(1, int(rng.integers(1, 4) * (0.55 + 0.75 * health)))
        monthly_target = p.base_monthly_revenue * (0.45 + 0.85 * health)

        # Amount per invoice = the share of monthly revenue this batch's window represents,
        # split across the batch. Without this the cadence and batch size multiply out to an
        # arbitrary multiple of the target (measured at ~2.2x), which silently breaks every
        # downstream calibration that assumes revenue ~= base_monthly_revenue.
        batch_value = monthly_target * (step / 30.0)
        per_invoice = batch_value / n_this_batch

        for _ in range(n_this_batch):
            seq += 1
            payer_id = str(rng.choice(p.payer_ids, p=np.array(p.payer_weights) / sum(p.payer_weights)))
            payer = PAYERS_BY_ID[payer_id]
            payer_health = max(0.05, min(1.0, payer.base_health + payer.health_drift * (t * n_days / 365)))

            # Lognormal noise, mean-corrected so it does not inflate the target.
            sigma = 0.42
            amount = float(per_invoice * rng.lognormal(0, sigma) / np.exp(sigma**2 / 2))
            terms_days = int(rng.choice([30, 45, 60], p=[0.55, 0.30, 0.15]))
            due_date = issue_date + timedelta(days=terms_days)

            # Settlement lag is driven by three independent channels. Keeping them separate
            # matters: §13 requires payer risk to be first-class, so payer distress must be
            # able to push a borrower into delinquency on its own.
            #
            # The borrower's structural slowness is DAMPED (x0.45) rather than added raw.
            # Added raw, a slow-paying sector like construction sits permanently past the
            # delinquency threshold and its label saturates at 1.0 across the whole history —
            # the model then learns "sector == construction" instead of "this borrower is
            # deteriorating", which is exactly the wrong lesson.
            slowness = (p.base_dso - 40.0) * 0.45
            health_drag = (1.0 - health) ** 1.5 * 55.0
            payer_drag = (1.0 - payer_health) ** 1.5 * 40.0
            noise = rng.normal(0, 7.0)

            days_past_due = slowness + health_drag + payer_drag + noise
            settled_date = due_date + timedelta(days=float(max(-9.0, days_past_due)))

            # Disputes rise with payer distress and borrower deterioration (quality issues).
            dispute_p = 0.012 + 0.10 * (1 - payer_health) + 0.055 * (1 - health)
            disputed = bool(rng.random() < dispute_p)

            # Unsettled if it would settle past the end of history, or the borrower defaulted.
            past_default = p.defaults and t >= p.default_at_pct
            if settled_date > HISTORY_END or past_default:
                settled_date = None

            rows.append(
                {
                    "invoice_id": f"inv_{p.borrower_id[-6:]}_{seq:05d}",
                    "borrower_id": p.borrower_id,
                    "payer_id": payer_id,
                    "amount": round(amount, 2),
                    "issued_at": issue_date,
                    "due_at": due_date,
                    "settled_at": settled_date,
                    "terms_days": terms_days,
                    "disputed": disputed,
                    "dispute_opened_at": (issue_date + timedelta(days=int(terms_days * 0.7)))
                    if disputed
                    else None,
                    "source": "invoice_feed",
                    "ingested_at": issue_date + timedelta(hours=float(rng.uniform(1, 9))),
                }
            )

        day += step

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------
# Bank transactions
# --------------------------------------------------------------------------------------


def gen_bank(p: BorrowerProfile, invoices: pd.DataFrame) -> pd.DataFrame:
    """Bank feed: invoice settlements as credits, operating costs as debits (§6)."""
    rng = _rng(p.borrower_id, "bank")
    rows: list[dict] = []

    settled = invoices.dropna(subset=["settled_at"])
    for r in settled.itertuples():
        rows.append(
            {
                "txn_id": f"txn_{r.invoice_id[4:]}_c",
                "borrower_id": p.borrower_id,
                "posted_at": r.settled_at,
                "amount": float(r.amount),
                "direction": "credit",
                "category": "customer_receipt",
                "counterparty": PAYERS_BY_ID[r.payer_id].name,
                "source": "bank_feed",
            }
        )

    # Operating outflows = sticky fixed costs + a receipts-linked variable component.
    #
    # Two properties matter:
    #
    #   1. Costs must be STICKY-DOWNWARD. A real cost base is set at the scale the business was
    #      operating at when it sized itself, and does not ratchet down the moment receipts fall.
    #      That lag is what turns a revenue decline into a cash squeeze, and it is the only
    #      reason a runway feature carries signal at all.
    #   2. A borrower whose receipts are FLAT must have a flat balance. Any systematic surplus or
    #      deficit compounds into a monotone balance trend, which makes cash_runway a proxy for
    #      elapsed time rather than for credit risk — the leakage class build_features.py guards.
    #
    # Both fall out of one choice: the sticky component is anchored to a LAGGING estimate of the
    # borrower's own receipt rate — an EWMA of its trailing-30d receipts with a ~90-day half-life.
    # A business whose receipts drift up or down slowly has its cost base follow, so its balance
    # stays flat and no calendar trend appears. A business whose receipts drop sharply carries the
    # old cost base for months, and that gap is the cash squeeze. A fixed anchor (whether from
    # base_monthly_revenue or measured over an early window) cannot do both: it leaves any
    # underlying growth as an unpaid-for surplus that compounds into exactly the monotone balance
    # trend build_features.py flags.
    STICKY_SHARE = 0.58
    VARIABLE_SHARE = 0.42  # sums to 1.0 with STICKY_SHARE, so receipts == anchor is cash-neutral
    ANCHOR_HALFLIFE_DAYS = 90.0
    """How long a cost base takes to close half the gap to a new receipt level. Long enough that
    a sharp decline produces a real multi-month squeeze, short enough that ordinary growth or
    contraction is absorbed rather than compounding into a balance trend."""
    RAMP_DAY = 45
    """No invoices have matured before ~day 40, so the receipts series is near-zero before then.
    Seeding the EWMA from that stretch would anchor costs at almost nothing."""
    SEED_FROM_DAY, SEED_TO_DAY = 90, 270
    """Window the EWMA and the opening balance are seeded from. Starts at day 90 so every
    trailing-30d window in it is fully formed — seeding off days 45-100 measures a book that is
    still filling up, which sets the cost base and the opening balance below steady state and
    leaves an early-history surplus that shows up as a rising balance trend."""

    credit_df = pd.DataFrame(rows)
    step_days = list(range(0, config.HISTORY_DAYS, 3))
    step_size = 3.0

    # Trailing-30d receipts at each step.
    receipts_series: list[float] = []
    for day in step_days:
        posted = HISTORY_START + timedelta(days=day)
        lo = posted - timedelta(days=30)
        recent = credit_df[(credit_df["posted_at"] >= lo) & (credit_df["posted_at"] < posted)]
        receipts_series.append(float(recent["amount"].sum()) if len(recent) else 0.0)

    seed_window = [
        r for d, r in zip(step_days, receipts_series) if SEED_FROM_DAY <= d <= SEED_TO_DAY
    ]
    seed = float(np.mean(seed_window)) if seed_window else p.base_monthly_revenue

    alpha = 1.0 - 0.5 ** (step_size / ANCHOR_HALFLIFE_DAYS)
    anchor_series: list[float] = []
    anchor = seed
    for day, r in zip(step_days, receipts_series):
        if day >= RAMP_DAY:  # before this the feed is still empty; hold the seed
            anchor += alpha * (r - anchor)
        anchor_series.append(anchor)

    noise_sigma = 0.18
    for i, day in enumerate(step_days):
        t = day / config.HISTORY_DAYS
        if p.defaults and t >= p.default_at_pct:
            continue
        posted = HISTORY_START + timedelta(days=day, hours=float(rng.uniform(6, 20)))
        monthly = STICKY_SHARE * anchor_series[i] + VARIABLE_SHARE * receipts_series[i]
        # Mean-corrected lognormal, so the noise term adds no hidden drift.
        noise = rng.lognormal(0, noise_sigma) / np.exp(noise_sigma**2 / 2)
        outflow = monthly * (3.0 / 30.0) * noise

        rows.append(
            {
                "txn_id": f"txn_{p.borrower_id[-6:]}_{day:04d}_d",
                "borrower_id": p.borrower_id,
                "posted_at": posted,
                "amount": -round(float(outflow), 2),
                "direction": "debit",
                "category": str(rng.choice(["payroll", "supplier", "rent", "tax", "utilities"])),
                "counterparty": "",
                "source": "bank_feed",
            }
        )

    df = pd.DataFrame(rows).sort_values("posted_at").reset_index(drop=True)

    # Running balance. A real bank feed supplies this, and it matters for feature quality:
    # deriving cash position from a cumulative sum of net flow makes it grow monotonically with
    # time, which turns any runway feature into a calendar-time proxy. A true balance stays flat
    # for a healthy business and declines for a deteriorating one.
    #
    # 2.6 months of receipts opening: enough to absorb the pre-settlement ramp-up at the start of
    # history without going negative, and to leave a buffer a decline can visibly eat into.
    opening = seed * 2.6
    df["balance_after"] = (opening + df["amount"].cumsum()).round(2)

    return df


# --------------------------------------------------------------------------------------
# Repayments against the facility
# --------------------------------------------------------------------------------------


def gen_repayments(p: BorrowerProfile) -> pd.DataFrame:
    """Fortnightly facility repayments. Lateness is the strongest single credit signal here."""
    rng = _rng(p.borrower_id, "repay")
    rows: list[dict] = []
    seq = 0

    for day in range(14, config.HISTORY_DAYS, 14):
        t = day / config.HISTORY_DAYS
        health = health_at(p, t)
        seq += 1
        due = HISTORY_START + timedelta(days=day)

        if p.defaults and t >= p.default_at_pct:
            rows.append(
                {
                    "repayment_id": f"rep_{p.borrower_id[-6:]}_{seq:03d}",
                    "borrower_id": p.borrower_id,
                    "due_at": due,
                    "paid_at": None,
                    "amount_due": round(p.base_monthly_revenue * 0.075, 2),
                    "amount_paid": 0.0,
                    "days_late": None,
                    "status": "defaulted",
                    "source": "bank_feed",
                }
            )
            continue

        # Both frequency AND severity of lateness scale with deterioration. Severity matters:
        # the training label keys off 30+ day lateness, which a mildly-late borrower never
        # reaches, so the two archetypes have to separate on magnitude, not just count.
        late_p = 0.02 + 0.60 * (1 - health) ** 1.9
        if rng.random() < late_p:
            severity = 1.0 + 5.0 * (1 - health) ** 2
            days_late = float(max(1.0, rng.gamma(2.4, 4.0) * severity))
        else:
            days_late = float(-rng.uniform(0, 3))

        paid_at = due + timedelta(days=days_late)
        if paid_at > HISTORY_END:
            paid_at, days_late, status = None, None, "outstanding"
        else:
            status = "paid_late" if days_late > 0 else "paid_on_time"

        rows.append(
            {
                "repayment_id": f"rep_{p.borrower_id[-6:]}_{seq:03d}",
                "borrower_id": p.borrower_id,
                "due_at": due,
                "paid_at": paid_at,
                "amount_due": round(p.base_monthly_revenue * 0.075, 2),
                "amount_paid": round(p.base_monthly_revenue * 0.075, 2) if paid_at else 0.0,
                "days_late": days_late,
                "status": status,
                "source": "bank_feed",
            }
        )

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------
# Feed heartbeats — makes §6 staleness real rather than asserted
# --------------------------------------------------------------------------------------


def gen_feed_events(p: BorrowerProfile) -> pd.DataFrame:
    """Per-feed sync heartbeats.

    §6: "if a data source goes silent (originator stops syncing), the score should visibly
    degrade in confidence, not silently freeze at the last-known value." The ingestion layer
    derives freshness from the last heartbeat per feed, so a dark feed is discovered from the
    data rather than hard-coded.
    """
    rng = _rng(p.borrower_id, "feed")
    rows: list[dict] = []
    dark_from_day = int(config.HISTORY_DAYS * p.dark_from_pct)

    cadence = {
        "accounting_feed": 2,
        "bank_feed": 1,
        "invoice_feed": 1,
        "document_feed": 30,
        "onchain_feed": 1,
    }

    for feed, every in cadence.items():
        for day in range(0, config.HISTORY_DAYS + 1, every):
            if feed in p.dark_feeds and day >= dark_from_day:
                continue  # feed has gone silent
            ts = HISTORY_START + timedelta(days=day, hours=float(rng.uniform(0, 23)))
            if ts > HISTORY_END:
                continue
            rows.append(
                {
                    "borrower_id": p.borrower_id,
                    "feed": feed,
                    "synced_at": ts,
                    "records": int(rng.integers(1, 40)),
                }
            )

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------------------


def gen_documents(p: BorrowerProfile, invoices: pd.DataFrame) -> list[dict]:
    """Unstructured text for the Claude agent, timed to when the risk actually emerges."""
    rng = _rng(p.borrower_id, "doc")
    out: list[dict] = []

    # Adverse/breach documents land late, when deterioration is underway; clean ones are
    # spread across the history as routine reporting.
    placement = {
        "clean_covenant": (0.30, 0.95),
        "routine_statement": (0.40, 0.92),
        "soft_covenant_warning": (0.55, 0.80),
        "margin_pressure_statement": (0.62, 0.88),
        "covenant_breach_notice": (0.82, 0.95),
        "dispute_correspondence": (0.70, 0.93),
        "adverse_news": (0.85, 0.97),
        "recovery_statement": (0.72, 0.95),
    }

    for i, tag in enumerate(p.documents):
        tmpl = docs.TEMPLATES[tag]
        lo, hi = placement.get(tag, (0.5, 0.9))
        t = float(rng.uniform(lo, hi))
        created = HISTORY_START + timedelta(days=t * config.HISTORY_DAYS)

        window = invoices[
            (invoices["issued_at"] <= created)
            & (invoices["issued_at"] >= created - timedelta(days=90))
        ]
        revenue = float(window["amount"].sum() / 3) if len(window) else p.base_monthly_revenue
        receivables = float(window["amount"].sum() * 0.42) if len(window) else revenue * 1.3
        health = health_at(p, t)

        top_payer = PAYERS_BY_ID[p.payer_ids[0]]
        disputed_rows = window[window["disputed"]] if len(window) else window
        inv_ids = list(disputed_rows["invoice_id"].head(3)) if len(disputed_rows) else []
        while len(inv_ids) < 3:
            inv_ids.append(f"inv_{p.borrower_id[-6:]}_{rng.integers(1, 999):05d}")

        body = tmpl.body.format(
            borrower=p.name,
            period=created.strftime("%d %B %Y"),
            revenue=revenue,
            receivables=receivables,
            dso=p.base_dso + (1 - health) * 22,
            dso_prior=p.base_dso,
            cash=max(4_000.0, revenue * (0.05 + 0.30 * health)),
            payer_name=top_payer.name,
            sector_title=docs.SECTOR_TITLES.get(p.sector, "Business"),
            inv_a=inv_ids[0],
            inv_b=inv_ids[1],
            inv_c=inv_ids[2],
            disputed_amount=float(disputed_rows["amount"].sum()) if len(disputed_rows) else revenue * 0.18,
        )

        out.append(
            {
                "doc_id": f"doc_{p.borrower_id[-6:]}_{i:02d}",
                "borrower_id": p.borrower_id,
                "doc_type": tmpl.doc_type,
                "title": tmpl.title,
                "body": body,
                "created_at": created.isoformat(),
                "source": "document_feed",
                "provenance": docs.PROVENANCE.get(tmpl.doc_type, "self_reported"),
                "scenario_tag": tag,
                # Ground truth for agent evaluation only. ingestion never reads these keys,
                # and they are never included in a prompt.
                "_truth": {
                    "covenant_breach": tmpl.truth_covenant_breach,
                    "adverse_news_detected": tmpl.truth_adverse_news,
                    "payer_deterioration": tmpl.truth_payer_deterioration,
                },
            }
        )

    return out


# --------------------------------------------------------------------------------------
# Outcome labels — for training only
# --------------------------------------------------------------------------------------


def gen_outcomes(
    p: BorrowerProfile, repayments: pd.DataFrame, invoices: pd.DataFrame
) -> pd.DataFrame:
    """Forward-looking bad-outcome labels for the structured model (§7 part 1).

    The label is what actually hurts a lender in this vertical: **receivables that stop
    settling**. A facility-repayment-only label is far too sparse (it fires a handful of times
    across an 18-month history) and misses the invoice delinquency that precedes it.

    Label at ``as_of`` = 1 if, in the FOLLOWING 90 days, any of:
      - a facility repayment falls 30+ days late,
      - the borrower defaults,
      - more than ``DELINQ_VALUE_THRESHOLD`` of receivables by value goes 45+ days past due.

    Forward-looking by construction, so the model learns to *anticipate* deterioration rather
    than describe it after the fact. Observations are every 7 days and are therefore heavily
    autocorrelated within a borrower — training MUST split by borrower group, not at random.

    **Right-censoring is handled explicitly.** An invoice still unsettled at the end of the
    history has not yet had the chance to become delinquent, so counting it as clean (or as
    late, measured against end-of-history) biases the label. Undetermined invoices are dropped
    from both numerator and denominator. Without this the label inflates precisely at the
    recent end of the window — the region the live dashboard displays.
    """
    DELINQ_DAYS = 45  # ~p90 of the observed days-past-due distribution
    DELINQ_VALUE_THRESHOLD = 0.15

    rows: list[dict] = []
    reps = repayments.dropna(subset=["days_late"])
    default_day = HISTORY_START + timedelta(days=config.HISTORY_DAYS * p.default_at_pct)

    inv = invoices.copy()
    settled = inv["settled_at"].notna()
    age_at_end = (HISTORY_END - inv["due_at"]).dt.total_seconds() / 86400.0
    days_past_due = (inv["settled_at"] - inv["due_at"]).dt.total_seconds() / 86400.0

    # Determined = we know the answer: it settled, or it is already past the threshold unpaid.
    inv["determined"] = settled | (age_at_end > DELINQ_DAYS)
    inv["is_delinquent"] = np.where(
        settled, days_past_due > DELINQ_DAYS, age_at_end > DELINQ_DAYS
    )

    for day in range(90, config.HISTORY_DAYS - 90, 7):
        as_of = HISTORY_START + timedelta(days=day)
        horizon = as_of + timedelta(days=90)

        fwd_reps = reps[(reps["due_at"] > as_of) & (reps["due_at"] <= horizon)]
        repayment_bad = bool((fwd_reps["days_late"] > 30).any())

        default_bad = bool(p.defaults and as_of <= default_day <= horizon)

        # Invoices falling due in the forward window whose outcome is determined, by value.
        fwd_inv = inv[
            (inv["due_at"] > as_of) & (inv["due_at"] <= horizon) & inv["determined"]
        ]
        if len(fwd_inv) and fwd_inv["amount"].sum() > 0:
            delinq_value_pct = float(
                fwd_inv.loc[fwd_inv["is_delinquent"], "amount"].sum() / fwd_inv["amount"].sum()
            )
        else:
            delinq_value_pct = 0.0
        delinquency_bad = delinq_value_pct > DELINQ_VALUE_THRESHOLD

        rows.append(
            {
                "borrower_id": p.borrower_id,
                "as_of": as_of,
                "label_bad_90d": int(repayment_bad or default_bad or delinquency_bad),
                "fwd_delinq_value_pct": round(delinq_value_pct, 4),
                "fwd_repayment_late": int(repayment_bad),
                "fwd_default": int(default_bad),
                "archetype": p.archetype,
            }
        )

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------


def main() -> None:
    all_inv, all_bank, all_rep, all_feed, all_out = [], [], [], [], []
    all_docs: list[dict] = []

    print(f"Generating {len(COHORT)} synthetic borrowers over {config.HISTORY_DAYS} days")
    print(f"  window: {HISTORY_START.date()} -> {HISTORY_END.date()}  seed={config.RANDOM_SEED}\n")

    for p in COHORT:
        inv = gen_invoices(p)
        bank = gen_bank(p, inv)
        rep = gen_repayments(p)
        feed = gen_feed_events(p)
        dcs = gen_documents(p, inv)
        out = gen_outcomes(p, rep, inv)

        all_inv.append(inv)
        all_bank.append(bank)
        all_rep.append(rep)
        all_feed.append(feed)
        all_out.append(out)
        all_docs.extend(dcs)

        late = int((rep["days_late"] > 0).sum())
        dark = ",".join(p.dark_feeds) or "-"
        print(
            f"  {p.borrower_id}  {p.name[:28]:<28} {p.archetype:<15} "
            f"inv={len(inv):>4} txn={len(bank):>4} rep={len(rep):>3} late={late:>2} "
            f"docs={len(dcs)} dark={dark}"
        )

    pd.concat(all_inv).to_parquet(config.RAW_DIR / "invoices.parquet", index=False)
    pd.concat(all_bank).to_parquet(config.RAW_DIR / "bank_transactions.parquet", index=False)
    pd.concat(all_rep).to_parquet(config.RAW_DIR / "repayments.parquet", index=False)
    pd.concat(all_feed).to_parquet(config.RAW_DIR / "feed_events.parquet", index=False)
    pd.concat(all_out).to_parquet(config.RAW_DIR / "outcomes.parquet", index=False)

    with open(config.RAW_DIR / "documents.jsonl", "w", encoding="utf-8") as fh:
        for d in all_docs:
            fh.write(json.dumps(d) + "\n")

    roster = [
        {
            "borrower_id": p.borrower_id,
            "name": p.name,
            "sector": p.sector,
            "archetype": p.archetype,
            "payer_ids": list(p.payer_ids),
            "defaults": p.defaults,
            "dark_feeds": list(p.dark_feeds),
        }
        for p in COHORT
    ]
    with open(config.RAW_DIR / "borrowers.json", "w", encoding="utf-8") as fh:
        json.dump(roster, fh, indent=2)

    labels = pd.concat(all_out)
    print(
        f"\nWrote raw event data to {config.RAW_DIR}"
        f"\n  invoices={sum(len(d) for d in all_inv)}  transactions={sum(len(d) for d in all_bank)}"
        f"  documents={len(all_docs)}"
        f"\n  training observations={len(labels)}  positive label rate={labels['label_bad_90d'].mean():.1%}"
    )
    print("\n  label rate by archetype:")
    by_arch = labels.groupby("archetype")["label_bad_90d"].agg(["mean", "count"])
    for arch, row in by_arch.sort_values("mean").iterrows():
        print(f"    {arch:<16} {row['mean']:>6.1%}  (n={int(row['count'])})")
    print("\nNext: python -m continuum.ingestion.build_features")


if __name__ == "__main__":
    main()

