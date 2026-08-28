"""§7 part 3 — the anomaly / early-warning layer.

    "Lightweight statistical or time-series model watching for sudden deviations (a payment 20
    days later than the borrower's historical pattern, a spike in invoice disputes) that should
    trigger an **immediate re-score outside the normal cadence**, rather than waiting for the
    next scheduled run." — §7

This module decides *whether to score now*. It does not score. Keeping that boundary sharp matters:
the trigger decision is cheap and runs on every inbound data event, while a full re-score costs a
model call and a publication. If the two were fused, "continuous" would mean either scoring on
every webhook or scoring on a timer — the two things §7 is explicitly trying to improve on.

**Every threshold is borrower-relative.** §13's "gaming the re-score cadence" attack assumes the
borrower knows the rules; against a fixed cutoff — "a dispute over £50k triggers a re-read" — the
counter is to keep disputes at £49k. Against a robust z-score over the borrower's own history there
is no fixed number to sit under, and driving the median up to create headroom means genuinely
behaving better for months first.

Robust statistics rather than mean and standard deviation throughout: a single 5-sigma event inflates
its own standard deviation enough to hide the next one, which for early warning is the wrong failure
direction. Median and MAD do not move on one outlier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from continuum import config
from continuum.clock import iso, utc
from continuum.schemas import BorrowerFeatures, TriggerReason

MAD_TO_SIGMA = 1.4826
"""Scale factor making MAD a consistent estimator of the standard deviation for normal data."""

# Direction that counts as bad, per monitored feature. A revenue *rise* is a deviation but not a
# warning; DSO rising is. Features whose deviation matters in both directions are marked "both" —
# an unexplained collapse in invoice volume and an unexplained surge both merit a look, because
# either can mean the originator's feed is misreporting.
ADVERSE_DIRECTION: dict[str, str] = {
    "revenue_30d": "down",
    "days_sales_outstanding": "up",
    "payer_concentration_top1_pct": "up",
    "on_time_repayment_rate_180d": "down",
    "dispute_rate_90d": "up",
    "invoice_volume_30d": "both",
}

PRESSURE_CAP_Z = 6.0
"""Robust z at which anomaly pressure saturates at 1.0. Beyond this the number stops carrying
information — a z of 40 usually means a broken feed, not forty times the risk."""

BASELINE_WINDOW = 12
"""Observations of recent history the z-scores are measured against, not the whole record.

This is the difference between an early-warning layer and a stuck alarm. Measured against an
expanding window, a borrower whose repayment rate steps from 1.00 to 0.83 and stays there is a
4-sigma deviation from its own median every week thereafter — the layer re-fires the same event
indefinitely, and in the cohort replay that made the *improving* borrower the noisiest of the
twelve. §7 asks for "sudden deviations"; something that has been true for three months is not
sudden, it is the borrower's current state, and its proper home is the score itself, not the
trigger. A trailing window means a level shift fires once or twice and then becomes the new
baseline, which is also what a human credit analyst would do with it.

Twelve weekly observations is roughly a quarter: long enough for the median and MAD to be stable,
short enough that a borrower's condition a year ago is not evidence about this week."""

SCALE_FLOOR_REL = 0.05
"""Floor on the z denominator, as a fraction of the borrower's own median.

Without it, robust z is unusable on features that sit at or near zero most of the time. Meridian's
``dispute_rate_90d`` is 0.000 for eleven of twelve weeks, so its MAD is 0 and the std fallback is
near 0; a single routine 6% dispute then scores z = +20.6 — indistinguishable from a genuine
collapse. Dividing by "how much this normally varies" only means something when it normally varies.

A dispersion floor is the standard answer, and it is also the honest one: it caps how loud an
observation can be relative to the borrower's own scale, rather than letting an unusually quiet
history manufacture significance."""

SCALE_FLOOR_ABS: dict[str, float] = {
    "days_sales_outstanding": 2.0,
    "payer_concentration_top1_pct": 0.03,
    "on_time_repayment_rate_180d": 0.03,
    "dispute_rate_90d": 0.02,
}
"""Absolute dispersion floors, in each feature's own units, for the bounded features.

A relative floor is no help when the median is zero — 5% of nothing is nothing. These are set at
roughly "the smallest move a credit analyst would bother mentioning": two days of DSO, three points
of concentration or repayment rate, two percent of the invoice book in dispute. Features not listed
here are scale-free enough that the relative floor covers them."""


@dataclass
class Signal:
    """One detected deviation."""

    kind: str
    feature: str
    detail: str
    severity: float  # 0-1, normalised so signals of different kinds are comparable
    value: float | None = None
    baseline: float | None = None
    robust_z: float | None = None

    def as_dict(self) -> dict:
        out = {
            "kind": self.kind,
            "feature": self.feature,
            "detail": self.detail,
            "severity": round(self.severity, 4),
        }
        for k in ("value", "baseline", "robust_z"):
            v = getattr(self, k)
            if v is not None:
                out[k] = round(float(v), 4)
        return out


@dataclass
class AnomalyReport:
    """The layer's verdict: score now or wait, and how unsettled things look either way."""

    triggered: bool
    trigger_reason: TriggerReason | None
    signals: list[Signal] = field(default_factory=list)
    pressure: float = 0.0
    """0-1 summary of how far outside its own pattern this borrower is sitting.

    Feeds ``CI_WEIGHTS["anomaly_pressure"]`` in the aggregator: a borrower behaving strangely gets
    a wider published interval even when the point score barely moves, because the evidence that
    the score is *right* has weakened. §11 turns interval width into risk premium, so instability
    costs the borrower something without needing to be called a downgrade.
    """
    abstained: bool = False
    """True when history is too short for the z-scores to mean anything. Distinct from "no anomaly":
    the layer has no opinion rather than a clean one, and the aggregator treats that as uncertainty
    rather than as reassurance."""

    def as_dict(self) -> dict:
        return {
            "triggered": self.triggered,
            "trigger_reason": self.trigger_reason,
            "pressure": round(self.pressure, 4),
            "abstained": self.abstained,
            "signals": [s.as_dict() for s in self.signals],
        }

    def summary(self) -> str:
        if self.abstained:
            return "insufficient history for anomaly detection"
        if not self.signals:
            return "no deviation from borrower's own pattern"
        return "; ".join(s.detail for s in self.signals[:3])


# --------------------------------------------------------------------------------------
# Robust statistics
# --------------------------------------------------------------------------------------


def robust_z(value: float, history: np.ndarray, *, feature: str | None = None) -> tuple[float, float]:
    """Median/MAD z-score of ``value`` against ``history``. Returns ``(z, median)``.

    The denominator is floored (see ``SCALE_FLOOR_REL`` / ``SCALE_FLOOR_ABS``) so a feature that
    happens to have been flat cannot turn an ordinary move into a 20-sigma event. When MAD is zero
    a scaled standard deviation is tried first; the floor is the last line, and if every candidate
    is zero the z is 0.0 — a constant feature that suddenly changes is caught by the event-specific
    checks below, not here.
    """
    history = history[np.isfinite(history)]
    if len(history) < 3:
        return 0.0, float(value)

    median = float(np.median(history))
    mad = float(np.median(np.abs(history - median)))
    scale = mad * MAD_TO_SIGMA

    if scale <= 1e-9:
        scale = float(np.std(history))

    floor = max(SCALE_FLOOR_REL * abs(median), SCALE_FLOOR_ABS.get(feature or "", 0.0))
    scale = max(scale, floor)

    if scale <= 1e-9:
        return 0.0, median

    return float((value - median) / scale), median


def _adverse(feature: str, z: float) -> bool:
    """Whether a deviation of this sign is the bad direction for this feature."""
    direction = ADVERSE_DIRECTION.get(feature, "both")
    if direction == "up":
        return z > 0
    if direction == "down":
        return z < 0
    return True


# --------------------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------------------


def _feature_signals(
    current: BorrowerFeatures, history: pd.DataFrame
) -> list[Signal]:
    """Robust z-scores on the monitored features against the borrower's own recent past.

    **Edge-triggered, not level-triggered.** A feature that breached its band last week and is still
    breaching it this week does not fire again. This is the difference between "something changed"
    and "something is wrong": the second is the score's job, and it is already being republished on
    the daily cadence with the deterioration priced in. Firing on the level instead re-reports one
    event for as long as it persists — and because ``dispute_rate_90d`` is a 90-day window by
    construction, one dispute would trigger thirteen consecutive out-of-cadence re-scores.

    The previous observation's z is recomputed against the baseline *it* would have seen (history
    minus its own row), so the gate is a property of the data, not of retained state. That matters
    for replay and for §11 disputes: the same inputs always produce the same trigger decision.
    """
    signals: list[Signal] = []
    threshold = config.MATERIALITY["robust_z_abs"]
    baseline = history.tail(BASELINE_WINDOW)
    prior_baseline = history.iloc[:-1].tail(BASELINE_WINDOW)

    for feature in config.MONITORED_FEATURES:
        if feature not in history.columns:
            continue
        value = float(getattr(current, feature))
        z, median = robust_z(value, baseline[feature].to_numpy(dtype=float), feature=feature)

        if abs(z) < threshold or not _adverse(feature, z):
            continue

        # Was this feature already outside its band at the previous observation? If so the
        # information arrived then, and it was already re-scored on.
        if len(prior_baseline) >= 3:
            prev_value = float(history[feature].iloc[-1])
            prev_z, _ = robust_z(
                prev_value, prior_baseline[feature].to_numpy(dtype=float), feature=feature
            )
            if abs(prev_z) >= threshold and _adverse(feature, prev_z):
                continue

        signals.append(
            Signal(
                kind="feature_deviation",
                feature=feature,
                detail=(
                    f"{feature} at {value:,.4g} vs own median {median:,.4g} "
                    f"(robust z {z:+.1f})"
                ),
                severity=min(abs(z) / PRESSURE_CAP_Z, 1.0),
                value=value,
                baseline=median,
                robust_z=z,
            )
        )
    return signals


def _repayment_signals(
    repayments: pd.DataFrame, as_of: datetime, lookback_days: int = 7
) -> list[Signal]:
    """§7's worked example: "a payment 20 days later than the borrower's historical pattern".

    Lateness is measured against the borrower's own 90th percentile rather than against zero,
    because a borrower who habitually pays five days late is not deteriorating by doing it again —
    and one who has never been late is flagged by a much smaller slip, which is correct.
    """
    if repayments.empty:
        return []

    paid = repayments.dropna(subset=["paid_at"]).copy()
    if paid.empty:
        return []

    paid["late_days"] = (
        pd.to_datetime(paid["paid_at"], utc=True) - pd.to_datetime(paid["due_at"], utc=True)
    ).dt.total_seconds() / 86400.0

    as_of = utc(as_of)
    window_start = as_of - timedelta(days=lookback_days)
    ts = pd.to_datetime(paid["paid_at"], utc=True)
    recent = paid[(ts > window_start) & (ts <= as_of)]
    prior = paid[ts <= window_start]

    if recent.empty or len(prior) < 5:
        return []

    p90 = float(np.percentile(prior["late_days"], 90))
    worst = recent.loc[recent["late_days"].idxmax()]
    excess = float(worst["late_days"]) - p90

    if excess < config.MATERIALITY["payment_late_days_over_p90"]:
        return []

    return [
        Signal(
            kind="payment_lateness",
            feature="repayment_timing",
            detail=(
                f"repayment {worst['repayment_id']} settled {worst['late_days']:.0f}d late vs "
                f"borrower's own p90 of {p90:.0f}d ({excess:+.0f}d beyond pattern)"
            ),
            severity=min(excess / (3 * config.MATERIALITY["payment_late_days_over_p90"]), 1.0),
            value=float(worst["late_days"]),
            baseline=p90,
        )
    ]


def _dispute_signals(
    invoices: pd.DataFrame, as_of: datetime, lookback_days: int = 7
) -> list[Signal]:
    """Newly disputed value as a share of the open book.

    A share, not a count and not an absolute amount: one disputed invoice is a routine quality
    argument on a large book and an existential problem on a small one. §13 also makes this the
    natural place to notice payer-side trouble arriving as a dispute before it arrives as a default.
    """
    if invoices.empty or "disputed" not in invoices.columns:
        return []

    as_of = utc(as_of)
    window_start = as_of - timedelta(days=lookback_days)

    # As-of visibility, same rule the feature pipeline applies: an invoice the originator had not
    # yet synced is not evidence, and an invoice issued after ``as_of`` does not exist yet.
    visible = invoices[
        (pd.to_datetime(invoices["issued_at"], utc=True) <= as_of)
        & (pd.to_datetime(invoices["ingested_at"], utc=True) <= as_of)
    ]
    if visible.empty:
        return []

    settled = pd.to_datetime(visible["settled_at"], utc=True, errors="coerce")
    open_book = visible[settled.isna() | (settled > as_of)]
    receivables = float(open_book["amount"].sum())
    if receivables <= 0:
        return []

    disputed_at = pd.to_datetime(visible["dispute_opened_at"], utc=True, errors="coerce")
    fresh = visible[
        visible["disputed"].fillna(False) & (disputed_at > window_start) & (disputed_at <= as_of)
    ]
    if fresh.empty:
        return []

    disputed_value = float(fresh["amount"].sum())
    share = disputed_value / receivables
    if share < config.MATERIALITY["dispute_value_pct_of_receivables"]:
        return []

    return [
        Signal(
            kind="dispute_spike",
            feature="dispute_rate_90d",
            detail=(
                f"{len(fresh)} invoice(s) worth {disputed_value:,.0f} newly disputed in "
                f"{lookback_days}d — {share:.1%} of the {receivables:,.0f} open book"
            ),
            severity=min(share / (4 * config.MATERIALITY["dispute_value_pct_of_receivables"]), 1.0),
            value=share,
            baseline=config.MATERIALITY["dispute_value_pct_of_receivables"],
        )
    ]


def _concentration_signal(
    current: BorrowerFeatures, history: pd.DataFrame
) -> list[Signal]:
    """A sharp jump in single-payer concentration.

    Watched separately from the z-score on the same feature because concentration can climb fast
    without ever looking anomalous against a noisy history: losing two of five customers moves it
    a long way in one step. §13 requires payer risk be first-class, and concentration is the
    channel through which one payer's problems become the borrower's.
    """
    if history.empty or "payer_concentration_top1_pct" not in history.columns:
        return []

    week_ago = history.tail(2).head(1)  # observations are weekly in the training grid
    if week_ago.empty:
        return []

    prior = float(week_ago["payer_concentration_top1_pct"].iloc[0])
    jump = float(current.payer_concentration_top1_pct) - prior
    if jump < config.MATERIALITY["payer_concentration_jump_7d"]:
        return []

    return [
        Signal(
            kind="concentration_jump",
            feature="payer_concentration_top1_pct",
            detail=(
                f"top payer concentration {prior:.0%} -> "
                f"{current.payer_concentration_top1_pct:.0%} ({jump:+.0%} in a week)"
            ),
            severity=min(jump / (3 * config.MATERIALITY["payer_concentration_jump_7d"]), 1.0),
            value=float(current.payer_concentration_top1_pct),
            baseline=prior,
        )
    ]


def _data_quality_signal(current_dq: float, prior_dq: float | None) -> list[Signal]:
    """A feed going quiet is itself an event worth re-scoring on.

    §6: the score must "visibly degrade in confidence, not silently freeze at the last-known
    value". Freezing is exactly what happens if nothing triggers when the data stops — the last
    score stays on the registry looking current. So the drop triggers a publication whose only
    change is a wider interval, which is the honest signal.
    """
    if prior_dq is None:
        return []
    drop = prior_dq - current_dq
    if drop < config.MATERIALITY["data_quality_drop"]:
        return []

    return [
        Signal(
            kind="data_quality_drop",
            feature="data_quality_score",
            detail=f"data quality {prior_dq:.2f} -> {current_dq:.2f} ({-drop:+.2f})",
            severity=min(drop / (3 * config.MATERIALITY["data_quality_drop"]), 1.0),
            value=current_dq,
            baseline=prior_dq,
        )
    ]


# Which trigger reason wins when several signals fire at once. Ordered by how directly each one
# implies the score is wrong right now, so the published ``trigger_reason`` names the thing a
# lender would want to have been told about.
_REASON_PRIORITY: tuple[tuple[str, TriggerReason], ...] = (
    ("data_quality_drop", "event_data_quality_drop"),
    ("dispute_spike", "event_dispute"),
    ("payment_lateness", "event_repayment"),
    ("concentration_jump", "event_anomaly"),
    ("feature_deviation", "event_anomaly"),
)


def detect(
    current: BorrowerFeatures,
    history: pd.DataFrame,
    *,
    as_of: datetime,
    invoices: pd.DataFrame | None = None,
    repayments: pd.DataFrame | None = None,
    current_dq: float | None = None,
    prior_dq: float | None = None,
) -> AnomalyReport:
    """Decide whether this borrower warrants an out-of-cadence re-score.

    ``history`` holds that borrower's prior feature observations, strictly before ``as_of``. It is
    the caller's job to keep it as-of correct; passing rows from the future would let a borrower's
    later recovery suppress today's warning.
    """
    history = history.sort_values("as_of") if "as_of" in history.columns else history

    if len(history) < config.ANOMALY_MIN_HISTORY:
        # §7 wants deviations from "the borrower's historical pattern". Before there is a pattern,
        # the honest output is no opinion — not a clean bill of health, which is why this carries
        # baseline pressure into the interval rather than zero.
        return AnomalyReport(
            triggered=False,
            trigger_reason=None,
            signals=[],
            pressure=0.35,
            abstained=True,
        )

    # Full history is passed down; each check windows it as its own logic requires — the z-score
    # checks against BASELINE_WINDOW, the jump checks against the single prior observation.
    signals: list[Signal] = []
    signals += _data_quality_signal(current_dq, prior_dq) if current_dq is not None else []
    signals += _dispute_signals(invoices if invoices is not None else pd.DataFrame(), as_of)
    signals += _repayment_signals(repayments if repayments is not None else pd.DataFrame(), as_of)
    signals += _concentration_signal(current, history)
    signals += _feature_signals(current, history)

    signals.sort(key=lambda s: s.severity, reverse=True)

    # Pressure combines signals without letting a long tail of mild ones outweigh one severe one:
    # the worst signal sets the floor, and the rest add a decaying contribution.
    if signals:
        worst = signals[0].severity
        rest = sum(s.severity for s in signals[1:])
        pressure = min(1.0, worst + 0.25 * rest)
    else:
        pressure = 0.0

    kinds = {s.kind for s in signals}
    reason: TriggerReason | None = None
    for kind, mapped in _REASON_PRIORITY:
        if kind in kinds:
            reason = mapped
            break

    return AnomalyReport(
        triggered=bool(signals),
        trigger_reason=reason,
        signals=signals,
        pressure=pressure,
        abstained=False,
    )


# --------------------------------------------------------------------------------------
# CLI — replay the layer over the cohort's history
# --------------------------------------------------------------------------------------


REPLAY_STEP_DAYS = 7
REPLAY_WARMUP_DAYS = 90


def replay(
    borrower_id: str,
    raw,
    *,
    step_days: int = REPLAY_STEP_DAYS,
) -> list[tuple[datetime, AnomalyReport]]:
    """Run the layer over one borrower's whole history and return every verdict.

    The grid is built here from ``compute_features`` rather than read from the labelled training
    matrix, and that is not an implementation detail. The labelled matrix stops 90 days before the
    end of history because a forward-looking label needs a full opportunity window (see
    ``synth.generate.gen_outcomes`` on right-censoring). Replaying only those rows silently skips
    the most recent quarter — which is where the ``feed_goes_dark`` borrower's feeds actually go
    quiet, so the data-quality trigger appeared never to fire when in fact it was never reached.
    Anything that claims to test the *live* path has to cover the live end of the window.
    """
    from continuum.ingestion import features as feat
    from continuum.ingestion import quality
    from continuum.synth.generate import HISTORY_START
    from continuum.synth.profiles import COHORT_BY_ID

    b_raw = raw.for_borrower(borrower_id)
    profile = COHORT_BY_ID.get(borrower_id)
    base_dso = profile.base_dso if profile else 40.0

    history = pd.DataFrame()
    prior_dq: float | None = None
    out: list[tuple[datetime, AnomalyReport]] = []

    for day in range(REPLAY_WARMUP_DAYS, config.HISTORY_DAYS + 1, step_days):
        as_of = utc(HISTORY_START + timedelta(days=day))
        current = feat.compute_features(b_raw, as_of, base_dso=base_dso)

        # data_quality_score is not a model feature — it modulates the interval — so it is not in
        # the matrix and has to be recomputed from the feed events for the trigger to be exercised.
        dq, _ = quality.data_quality_score(
            feat.source_freshness(b_raw.feed_events, as_of), as_of
        )

        report = detect(
            current,
            history,
            as_of=as_of,
            invoices=b_raw.invoices,
            repayments=b_raw.repayments,
            current_dq=dq,
            prior_dq=prior_dq,
        )
        prior_dq = dq
        out.append((as_of, report))

        row = {"as_of": as_of, **feat.features_to_row(current)}
        history = pd.concat([history, pd.DataFrame([row])], ignore_index=True)

    return out


def main() -> None:
    """Show where the layer would have fired, for every borrower, across the whole history.

    This is the readout that says whether the cadence claim is real: if the layer never fires, the
    system is a daily cron job with extra steps, and if it fires on every observation it is noise.
    """
    import argparse

    from continuum.ingestion import store
    from continuum.synth.profiles import COHORT

    parser = argparse.ArgumentParser(description="Replay the anomaly layer over history.")
    parser.add_argument("--borrower", default=None, help="Limit to one borrower id")
    parser.add_argument("--verbose", action="store_true", help="Print every signal, not a count")
    args = parser.parse_args()

    raw = store.load_raw()
    cohort = [p for p in COHORT if args.borrower in (None, p.borrower_id)]

    print("Anomaly layer replay — event-triggered re-scores outside the daily cadence\n")
    print(
        f"{'borrower':<28}{'archetype':<18}{'obs':>5}{'fired':>7}{'rate':>7}{'last 90d':>10}"
        "  top trigger reasons"
    )
    print("-" * 120)

    for profile in cohort:
        verdicts = replay(profile.borrower_id, raw)
        recent_window = verdicts[-13:]  # final quarter of the history

        fired = 0
        recent_fired = 0
        reasons: dict[str, int] = {}
        detail_lines: list[str] = []

        for as_of, report in verdicts:
            if not report.triggered:
                continue
            fired += 1
            reasons[report.trigger_reason] = reasons.get(report.trigger_reason, 0) + 1
            if args.verbose:
                detail_lines.append(
                    f"    {iso(as_of)[:10]}  p={report.pressure:.2f}  "
                    f"{report.trigger_reason:<26} {report.summary()}"
                )
        recent_fired = sum(1 for _, r in recent_window if r.triggered)

        top = ", ".join(f"{r}x{n}" for r, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:3])
        print(
            f"{profile.name:<28}{profile.archetype:<18}{len(verdicts):>5}{fired:>7}"
            f"{fired / max(len(verdicts), 1):>7.0%}"
            f"{f'{recent_fired}/{len(recent_window)}':>10}  {top or '-'}"
        )
        for line in detail_lines:
            print(line)

    print(
        "\nA rate near 0% means the daily cadence is doing all the work and the event path is\n"
        "decorative. A rate near 100% means the thresholds are noise and every re-score is an\n"
        "'anomaly'. The useful range is in between, concentrated in the deteriorating borrowers.\n"
        "\nThe 'last 90d' column is the more honest read of that claim than the overall rate:\n"
        "the improving borrowers fire early and then go quiet, so a whole-history rate makes them\n"
        "look as unsettled as a borrower that is deteriorating now."
    )


if __name__ == "__main__":
    main()
