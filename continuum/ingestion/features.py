"""Layer 1 feature derivation: raw events -> §9 Borrower Feature Record.

**As-of correctness is the central constraint.** Every feature at time ``as_of`` is computed
using only records the pipeline could actually have seen by then — invoices filtered on
``ingested_at <= as_of``, settlements on ``settled_at <= as_of``, and so on. Filtering on
``issued_at`` instead would leak: an invoice issued before ``as_of`` but synced after it was
not available to the scorer, and its eventual settlement date certainly was not.

Getting this wrong produces a model that backtests beautifully and fails in production, which
is the specific failure mode §17 warns about when it asks where credible backtest data comes
from. The ``_visible`` helpers below are the enforcement point.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from continuum.clock import utc
from continuum.schemas import BorrowerFeatures

FEED_COLUMNS = ("accounting_feed", "bank_feed", "invoice_feed", "document_feed", "onchain_feed")

DEFAULT_OBSERVATION_DAYS = 30
"""How long a repayment must sit unpaid before it is *observably* a default.

A repayment carrying ``status == "defaulted"`` is a forward-looking judgement about how the
facility ended, and on the day it falls due nobody knows that yet — all that is observable is
"due, not paid". Surfacing the terminal status at ``due_at`` hands the scorer the outcome the
instant it becomes true, which is the same lookahead the ``_visible`` helpers exist to prevent,
and it flatters the demo: the score collapses at the exact hour of default instead of degrading
through the weeks of missed payment that precede it. 30 days matches the lateness threshold the
training label in ``synth.generate.gen_outcomes`` uses, so feature and label agree on what
"gone bad" means.
"""


class RawData:
    """Raw event tables for the whole cohort, loaded once and sliced per borrower."""

    def __init__(
        self,
        invoices: pd.DataFrame,
        bank: pd.DataFrame,
        repayments: pd.DataFrame,
        feed_events: pd.DataFrame,
        payer_health: dict[str, float] | None = None,
    ) -> None:
        self.invoices = invoices
        self.bank = bank
        self.repayments = repayments
        self.feed_events = feed_events
        self.payer_health = payer_health or {}

    def for_borrower(self, borrower_id: str) -> "RawData":
        return RawData(
            self.invoices[self.invoices.borrower_id == borrower_id],
            self.bank[self.bank.borrower_id == borrower_id],
            self.repayments[self.repayments.borrower_id == borrower_id],
            self.feed_events[self.feed_events.borrower_id == borrower_id],
            self.payer_health,
        )


# --------------------------------------------------------------------------------------
# Visibility helpers — the as-of enforcement point
# --------------------------------------------------------------------------------------


def _visible_invoices(inv: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """Invoices the pipeline had actually received by ``as_of``.

    Also masks settlement: an invoice visible at ``as_of`` may not have settled yet, so
    ``settled_at`` in the future must read as unsettled rather than as a known future fact.
    """
    out = inv[inv["ingested_at"] <= as_of].copy()
    future_settle = out["settled_at"].notna() & (out["settled_at"] > as_of)
    out.loc[future_settle, "settled_at"] = pd.NaT
    # A dispute opened after as_of is likewise not yet known.
    future_dispute = out["dispute_opened_at"].notna() & (out["dispute_opened_at"] > as_of)
    out.loc[future_dispute, "disputed"] = False
    out.loc[future_dispute, "dispute_opened_at"] = pd.NaT
    return out


def _visible_repayments(rep: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    out = rep[rep["due_at"] <= as_of].copy()
    future_paid = out["paid_at"].notna() & (out["paid_at"] > as_of)
    out.loc[future_paid, "paid_at"] = pd.NaT
    out.loc[future_paid, "days_late"] = np.nan
    out.loc[future_paid, "status"] = "outstanding"

    # A terminal "defaulted" status is only knowable once the repayment has actually sat unpaid
    # for long enough to say so. Until then it reads as what it looked like at the time: due,
    # unpaid, no lateness on record yet. See DEFAULT_OBSERVATION_DAYS.
    premature = (out["status"] == "defaulted") & (
        out["due_at"] > as_of - timedelta(days=DEFAULT_OBSERVATION_DAYS)
    )
    out.loc[premature, "status"] = "outstanding"
    return out


# --------------------------------------------------------------------------------------
# Feed freshness
# --------------------------------------------------------------------------------------


def source_freshness(
    feed_events: pd.DataFrame, as_of: datetime
) -> dict[str, datetime | None]:
    """Last successful sync per feed as of ``as_of`` — §9's ``source_freshness`` block."""
    visible = feed_events[feed_events["synced_at"] <= as_of]
    out: dict[str, datetime | None] = {}
    for feed in FEED_COLUMNS:
        rows = visible[visible["feed"] == feed]
        out[feed] = utc(rows["synced_at"].max().to_pydatetime()) if len(rows) else None
    return out


# --------------------------------------------------------------------------------------
# Feature computation
# --------------------------------------------------------------------------------------


def compute_features(raw: RawData, as_of: datetime, base_dso: float = 40.0) -> BorrowerFeatures:
    """Derive the §9 ``features`` block (plus the EXT features) as of ``as_of``."""
    as_of = utc(as_of)
    inv = _visible_invoices(raw.invoices, as_of)
    rep = _visible_repayments(raw.repayments, as_of)
    bank = raw.bank[raw.bank["posted_at"] <= as_of]

    w30 = as_of - timedelta(days=30)
    w90 = as_of - timedelta(days=90)
    w180 = as_of - timedelta(days=180)
    w60 = as_of - timedelta(days=60)

    # ---- Revenue: settled cash receipts, the ground truth a bank feed gives you ---------
    credits = bank[bank["direction"] == "credit"]
    revenue_30d = float(credits.loc[credits["posted_at"] >= w30, "amount"].sum())

    # Trend: last 30d annualised against the prior 60d run-rate. Ratio-of-rates, so it is
    # comparable across borrowers of different sizes.
    prior_60 = credits[(credits["posted_at"] >= w90) & (credits["posted_at"] < w30)]
    prior_rate = float(prior_60["amount"].sum()) / 2.0 if len(prior_60) else 0.0
    revenue_trend_90d = float((revenue_30d - prior_rate) / prior_rate) if prior_rate > 0 else 0.0
    revenue_trend_90d = float(np.clip(revenue_trend_90d, -1.0, 2.0))

    # Volatility: CV of monthly receipts over a FIXED trailing 3 months, matching the feature
    # name. The window must be fixed: a window that grows as history accumulates makes early
    # observations look volatile and late ones smooth, which is a time trend, not a signal.
    history_start = bank["posted_at"].min() if len(bank) else as_of
    monthly: list[float] = []
    for m in range(3):
        lo, hi = as_of - timedelta(days=30 * (m + 1)), as_of - timedelta(days=30 * m)
        monthly.append(float(credits.loc[(credits["posted_at"] >= lo) & (credits["posted_at"] < hi), "amount"].sum()))
    if as_of - timedelta(days=90) >= history_start:
        mean_m = float(np.mean(monthly))
        revenue_volatility_90d = float(np.std(monthly) / mean_m) if mean_m > 0 else 0.0
    else:
        revenue_volatility_90d = 0.0  # insufficient history; neutral rather than misleading

    # ---- Cash runway from the reported bank balance -------------------------------------
    #
    # The denominator widens until it finds observable spend. Dividing by a floored 30-day burn
    # is the obvious implementation and it inverts the signal in exactly the case that matters:
    # a borrower who stops paying anyone — because they have defaulted, or because the bank feed
    # stopped reporting — has zero burn in the trailing month, so a floored denominator turns
    # "no outflows at all" into "runway is unbounded" and pins the feature to its most flattering
    # value. §6 requires silence to cost confidence, not manufacture it.
    #
    # Costs do not vanish when they stop being visible, so the fallback is the borrower's own
    # structural spend rate over a wider window. Only when there is no debit anywhere on record
    # does the runway read as zero — for a receivables borrower with a funded balance and no
    # observable spend, that is the conservative read and `novelty` widens the interval for it.
    debits = bank[bank["direction"] == "debit"]
    daily_burn = 0.0
    for days, since in ((30, w30), (90, w90), (180, w180)):
        spend = float(-debits.loc[debits["posted_at"] >= since, "amount"].sum())
        if spend > 0:
            daily_burn = spend / days
            break
    else:
        total_spend = float(-debits["amount"].sum())
        if total_spend > 0 and len(debits):
            span = max((as_of - debits["posted_at"].min()).total_seconds() / 86400.0, 1.0)
            daily_burn = total_spend / span

    if "balance_after" in bank.columns and len(bank):
        balance = float(bank.sort_values("posted_at")["balance_after"].iloc[-1])
    else:
        balance = float(bank["amount"].sum())
    cash_runway_days = (
        float(np.clip(balance / daily_burn, 0.0, 720.0)) if daily_burn > 0 else 0.0
    )

    # ---- DSO: value-weighted days-to-settle on invoices settled in the last 90d --------
    settled_90 = inv[inv["settled_at"].notna() & (inv["settled_at"] >= w90)]
    if len(settled_90) and settled_90["amount"].sum() > 0:
        dts = (settled_90["settled_at"] - settled_90["issued_at"]).dt.total_seconds() / 86400.0
        days_sales_outstanding = float(
            np.average(dts, weights=settled_90["amount"])
        )
    else:
        # No settlements visible in the window: fall back to the ageing of the open book,
        # which is the conservative read (an empty settlement window usually means nobody
        # is paying, not that DSO is fine).
        open_inv = inv[inv["settled_at"].isna()]
        if len(open_inv):
            age = (as_of - open_inv["issued_at"]).dt.total_seconds() / 86400.0
            days_sales_outstanding = float(max(base_dso, age.mean()))
        else:
            days_sales_outstanding = float(base_dso)

    # ---- Payment velocity: recent days-to-settle vs the borrower's own 180d baseline ----
    settled_180 = inv[inv["settled_at"].notna() & (inv["settled_at"] >= w180)]
    if len(settled_180) >= 5 and len(settled_90) >= 2:
        base = float(
            ((settled_180["settled_at"] - settled_180["issued_at"]).dt.total_seconds() / 86400.0).mean()
        )
        recent = float(
            ((settled_90["settled_at"] - settled_90["issued_at"]).dt.total_seconds() / 86400.0).mean()
        )
        payment_velocity_ratio = float(np.clip(recent / base, 0.2, 4.0)) if base > 0 else 1.0
    else:
        payment_velocity_ratio = 1.0

    # ---- Payer concentration (§9 top1; §13 wants payer risk first-class) ---------------
    open_or_recent = inv[
        inv["settled_at"].isna() | (inv["settled_at"] >= w90)
    ]
    if len(open_or_recent) and open_or_recent["amount"].sum() > 0:
        by_payer = open_or_recent.groupby("payer_id")["amount"].sum().sort_values(ascending=False)
        total = float(by_payer.sum())
        shares = by_payer / total
        payer_concentration_top1_pct = float(shares.iloc[0])
        payer_concentration_hhi = float((shares**2).sum())
        # Value-weighted payer health across the book.
        payer_risk_score = float(
            sum(raw.payer_health.get(pid, 0.5) * amt for pid, amt in by_payer.items()) / total
        )
    else:
        payer_concentration_top1_pct = 0.0
        payer_concentration_hhi = 0.0
        payer_risk_score = 0.5

    # Payer delay trend: are payers settling slower than they were a period ago?
    def _mean_dpd(frame: pd.DataFrame) -> float:
        if not len(frame):
            return 0.0
        return float(((frame["settled_at"] - frame["due_at"]).dt.total_seconds() / 86400.0).mean())

    recent_settled = inv[inv["settled_at"].notna() & (inv["settled_at"] >= w60)]
    older_settled = inv[
        inv["settled_at"].notna()
        & (inv["settled_at"] >= as_of - timedelta(days=180))
        & (inv["settled_at"] < w60)
    ]
    payer_payment_delay_trend_60d = float(
        np.clip(_mean_dpd(recent_settled) - _mean_dpd(older_settled), -60.0, 90.0)
    ) if len(recent_settled) and len(older_settled) else 0.0

    # ---- Repayment behaviour (§9) ------------------------------------------------------
    rep_180 = rep[rep["due_at"] >= w180]
    resolved = rep_180[rep_180["days_late"].notna()]
    if len(resolved):
        on_time_repayment_rate_180d = float((resolved["days_late"] <= 0).mean())
    else:
        on_time_repayment_rate_180d = 1.0

    late_all = rep[rep["days_late"].notna() & (rep["days_late"] > 0)]
    if len(late_all):
        last_late = late_all["due_at"].max()
        days_since_last_late_payment = float((as_of - last_late).total_seconds() / 86400.0)
    else:
        # Never late on record. Report observed clean history, but CAPPED: an uncapped value
        # grows linearly with observation age and becomes a calendar-time proxy. Past a year
        # clean, further cleanliness carries little extra information anyway.
        earliest = rep["due_at"].min() if len(rep) else as_of
        days_since_last_late_payment = float((as_of - earliest).total_seconds() / 86400.0)
    days_since_last_late_payment = float(min(days_since_last_late_payment, 365.0))

    rep_90 = rep[(rep["due_at"] >= w90) & rep["days_late"].notna()]
    late_payment_count_90d = float((rep_90["days_late"] > 0).sum())

    # Defaulted repayments are a hard signal and must not be lost by the days_late dropna.
    defaulted = rep[rep["status"] == "defaulted"]
    if len(defaulted):
        on_time_repayment_rate_180d = min(on_time_repayment_rate_180d, 0.0)
        days_since_last_late_payment = 0.0
        late_payment_count_90d = max(late_payment_count_90d, float(len(defaulted)))

    # ---- Invoice book activity ---------------------------------------------------------
    inv_30 = inv[inv["issued_at"] >= w30]
    invoice_volume_30d = float(inv_30["amount"].sum())

    open_book = inv[inv["settled_at"].isna()]
    avg_invoice_age_days = float(
        ((as_of - open_book["issued_at"]).dt.total_seconds() / 86400.0).mean()
    ) if len(open_book) else 0.0

    inv_90 = inv[inv["issued_at"] >= w90]
    if len(inv_90) and inv_90["amount"].sum() > 0:
        dispute_rate_90d = float(
            inv_90.loc[inv_90["disputed"], "amount"].sum() / inv_90["amount"].sum()
        )
    else:
        dispute_rate_90d = 0.0

    # ---- On-chain (§6). Synthetic proxy: settled facility repayments as on-chain record.
    # Expressed as a success RATE, not a count: a cumulative count grows with observation age
    # and would let the model key on "how late in the window are we" rather than on credit risk.
    onchain_repayment_success_rate = (
        float(rep["paid_at"].notna().mean()) if len(rep) else 1.0
    )
    outstanding = float(open_book["amount"].sum())
    onchain_existing_leverage_ratio = float(
        np.clip(outstanding / revenue_30d, 0.0, 12.0)
    ) if revenue_30d > 0 else 0.0

    return BorrowerFeatures(
        revenue_30d=round(revenue_30d, 2),
        revenue_trend_90d=round(revenue_trend_90d, 4),
        days_sales_outstanding=round(days_sales_outstanding, 2),
        payer_concentration_top1_pct=round(payer_concentration_top1_pct, 4),
        on_time_repayment_rate_180d=round(on_time_repayment_rate_180d, 4),
        days_since_last_late_payment=round(days_since_last_late_payment, 1),
        revenue_volatility_90d=round(revenue_volatility_90d, 4),
        cash_runway_days=round(cash_runway_days, 1),
        invoice_volume_30d=round(invoice_volume_30d, 2),
        avg_invoice_age_days=round(avg_invoice_age_days, 1),
        dispute_rate_90d=round(dispute_rate_90d, 4),
        late_payment_count_90d=late_payment_count_90d,
        payment_velocity_ratio=round(payment_velocity_ratio, 4),
        payer_concentration_hhi=round(payer_concentration_hhi, 4),
        payer_payment_delay_trend_60d=round(payer_payment_delay_trend_60d, 2),
        payer_risk_score=round(payer_risk_score, 4),
        onchain_repayment_success_rate=round(onchain_repayment_success_rate, 4),
        onchain_existing_leverage_ratio=round(onchain_existing_leverage_ratio, 4),
    )


MODEL_FEATURES: tuple[str, ...] = tuple(BorrowerFeatures.model_fields.keys())
"""Column order for the model matrix. Fixed so a trained booster always sees the same layout."""


def features_to_row(f: BorrowerFeatures) -> dict[str, float]:
    return {k: float(getattr(f, k)) for k in MODEL_FEATURES}
