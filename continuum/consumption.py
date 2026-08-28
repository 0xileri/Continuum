"""Layer 5 — how a lending pool actually reacts to a published score (§11). ASSUMPTIONS #14.

§11's central instruction is a separation of powers:

    *"don't let the AI directly set the rate; let the AI set the risk input and let a transparent,
    governance-auditable formula translate that into terms."*

So nothing in this module calls a model. It is pure arithmetic over an already-published
``ScorePublicationPayload``, and every number it emits is reproducible from the payload plus the
constants in ``config.py``. That is what makes it auditable by a pool's governance process, and it
is also the regulatory framing §12 asks for: the scoring engine supplies a risk input, the pool's
own formula makes the lending decision.

Three properties are worth reading the code for, because they are the ones that were decisions
rather than transcription:

**The rate curve inherits the scorecard's calibration.** The score scale is log-odds-linear by
construction (ASSUMPTIONS #18), so the premium that is linear in probability of deterioration is
exponential in points. Writing it that way means re-tuning the scorecard re-tunes the rate curve
automatically — the two cannot drift apart, which a hand-written per-grade rate table guarantees
they eventually would.

**Uncertainty is priced through the score, not beside it.** §11 wants a wider interval to cost more
and to justify a bigger collateral buffer. Both come from one line — pricing at
``pricing_score``, somewhere between the point estimate and the interval's pessimistic end — rather
than from two separate width-weighted terms that would have to be kept consistent by hand.

**Only published scores move terms.** A re-score that did not clear §10's gate is not an on-chain
fact, so a pool never sees it. ``terms_history`` filters on ``published_onchain``, which is what
makes the gate in ``aggregate.publish_decision`` mean something downstream instead of being a flag
nobody reads.

No pool exists. These are the terms a pool *would* set, computed so the score is demonstrably
consumable end to end (§15 Phase 2's exit criterion in miniature). The constants are placeholders
and per-vertical tuning is the pool operator's, not this module's.

Run:  python -m continuum.consumption --borrower brw_01hxg3e6f4
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime

from continuum import config
from continuum.clock import iso, utc
from continuum.ingestion import store
from continuum.scoring import calibration
from continuum.schemas import BorrowerFeatureRecord, ScorePublicationPayload

# --------------------------------------------------------------------------------------
# The pure formulas — §11's f() and g()
# --------------------------------------------------------------------------------------


def pricing_score(score_numeric: float, ci_lower: float) -> float:
    """The score both formulas are actually evaluated at.

    Sits between the point estimate and the interval's pessimistic end, per
    ``UNCERTAINTY_LOAD_SHARE``. A borrower whose feeds have gone quiet keeps their letter grade for
    a while (the grade ceiling erodes it more slowly) but starts paying immediately, because the
    interval widens downward the moment confidence drops. That is §6's "degrade visibly rather than
    freeze" requirement expressed as money, which is the form a borrower notices.
    """
    load = max(0.0, min(1.0, config.UNCERTAINTY_LOAD_SHARE))
    return score_numeric - load * (score_numeric - ci_lower)


def risk_premium_bps(score: float) -> float:
    """§11's ``f`` — risk premium in basis points, exponential in score points.

    Doubling the modelled odds of deterioration costs ``SCORE_POINTS_TO_DOUBLE_ODDS`` points
    (ASSUMPTIONS #18), and expected loss roughly doubles with those odds, so the premium doubles
    over the same distance. One constant pins the whole curve: the premium at the anchor.
    """
    exponent = (config.SCORE_ANCHOR_POINTS - score) / config.SCORE_POINTS_TO_DOUBLE_ODDS
    return min(config.RISK_PREMIUM_AT_ANCHOR_BPS * (2.0**exponent), config.MAX_RISK_PREMIUM_BPS)


def max_ltv(score: float) -> float:
    """§11's ``g`` — maximum advance rate against eligible receivables, linear in score points."""
    ltv = config.LTV_AT_ANCHOR + (score - config.LTV_ANCHOR_POINTS) / config.LTV_POINTS_PER_DECILE * 0.10
    return round(min(config.MAX_LTV_CEILING, max(config.MAX_LTV_FLOOR, ltv)), 4)


# --------------------------------------------------------------------------------------
# Terms
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolTerms:
    """What a pool charges and lends, plus enough audit trail to reconstruct why.

    ``indicative_*`` is what the formula asked for; ``effective_*`` is what the pool applied after
    §11's circuit breaker and cooldown. Both are reported because a borrower who is told only the
    effective rate cannot tell a damped move from a small one, and §11's dispute flow depends on
    them being able to.
    """

    borrower_id: str
    as_of: datetime
    """The ``published_at`` of the score these terms were derived from — the moment the pool's
    view of this borrower changed, which is what the cooldown measures between."""
    score: str
    score_numeric: int
    confidence_interval: tuple[int, int]
    pricing_score: float

    base_rate_bps: int
    risk_premium_bps: int
    uncertainty_premium_bps: int
    indicative_rate_bps: int
    effective_rate_bps: int

    indicative_max_ltv: float
    effective_max_ltv: float

    prior_rate_bps: int | None = None
    rate_change_bps: int = 0
    circuit_breaker_applied: bool = False
    cooldown_active: bool = False
    cooldown_overridden: bool = False
    borrowing_limit: float | None = None
    eligible_receivables: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def effective_rate_pct(self) -> float:
        return round(self.effective_rate_bps / 100.0, 2)

    def summary(self) -> str:
        return (
            f"{self.score}/{self.score_numeric} -> {self.effective_rate_pct:.2f}% "
            f"(base {self.base_rate_bps / 100:.2f}% + risk {self.risk_premium_bps / 100:.2f}% "
            f"+ uncertainty {self.uncertainty_premium_bps / 100:.2f}%), "
            f"max LTV {self.effective_max_ltv:.0%}"
        )

    def to_dict(self) -> dict:
        return {
            "borrower_id": self.borrower_id,
            "as_of": iso(self.as_of),
            "score": self.score,
            "score_numeric": self.score_numeric,
            "confidence_interval": list(self.confidence_interval),
            "pricing_score": round(self.pricing_score, 1),
            "base_rate_bps": self.base_rate_bps,
            "risk_premium_bps": self.risk_premium_bps,
            "uncertainty_premium_bps": self.uncertainty_premium_bps,
            "indicative_rate_bps": self.indicative_rate_bps,
            "effective_rate_bps": self.effective_rate_bps,
            "effective_rate_pct": self.effective_rate_pct,
            "indicative_max_ltv": self.indicative_max_ltv,
            "effective_max_ltv": self.effective_max_ltv,
            "prior_rate_bps": self.prior_rate_bps,
            "rate_change_bps": self.rate_change_bps,
            "circuit_breaker_applied": self.circuit_breaker_applied,
            "cooldown_active": self.cooldown_active,
            "cooldown_overridden": self.cooldown_overridden,
            "borrowing_limit": self.borrowing_limit,
            "eligible_receivables": self.eligible_receivables,
            "notes": list(self.notes),
        }


def terms_for(
    payload: ScorePublicationPayload,
    *,
    prior_terms: PoolTerms | None = None,
    eligible_receivables: float | None = None,
) -> PoolTerms:
    """Translate one published score into pool terms.

    ``prior_terms`` is what the pool is currently charging this borrower; omit it for a fresh
    facility, where the formula applies unclamped because there is no rate to whipsaw away from.

    **The cooldown is one-sided, deliberately, and the asymmetry is the opposite of what a
    borrower-protection reading suggests.** §11 motivates it as protection against rates
    "whipsawing on noisy data", which argues for damping both directions equally. But a symmetric
    cooldown also holds a pool at a stale cheap rate while a borrower deteriorates, and the ±50bps
    circuit breaker already bounds per-update magnitude in both directions — so frequency damping
    is the second guard, not the only one. A downgrade that crosses a grade boundary therefore
    overrides the cooldown; everything else, increases included, waits it out. Same carve-out and
    same reasoning as ``aggregate.publish_decision`` and ``GRADE_CEILING_HALFWIDTH_MULTIPLE``:
    suppressing bad news to smooth a curve is the worse failure. A pool that prefers a hard
    cooldown with no exception has a defensible position and one line to change.
    """
    notes: list[str] = []
    lo, hi = payload.confidence_interval
    p_score = pricing_score(float(payload.score_numeric), float(lo))

    # Split the premium into the part that is about the borrower's estimated risk and the part
    # that is about not knowing. Reported separately because they have different remedies: the
    # first needs the business to improve, the second needs a feed to start reporting again.
    premium_point = risk_premium_bps(float(payload.score_numeric))
    premium_priced = risk_premium_bps(p_score)
    uncertainty_bps = max(0.0, premium_priced - premium_point)

    indicative_rate = config.POOL_BASE_RATE_BPS + premium_priced
    indicative_ltv = max_ltv(p_score)

    if config.UNCERTAINTY_LOAD_SHARE > 0 and (hi - lo) > 0:
        notes.append(
            f"priced at {p_score:.0f}, not {payload.score_numeric} — "
            f"{config.UNCERTAINTY_LOAD_SHARE:.0%} of the interval's downside "
            f"({payload.score_numeric - lo} points) is charged for (§11)"
        )

    effective_rate = indicative_rate
    effective_ltv = indicative_ltv
    prior_rate = None
    change = 0
    breaker = False
    cooldown = False
    overridden = False

    if prior_terms is not None:
        prior_rate = prior_terms.effective_rate_bps
        grade_moved = payload.score != prior_terms.score
        downgrade = payload.score_numeric < prior_terms.score_numeric

        # Cooldown first: it decides whether the pool re-prices at all.
        elapsed = _hours_between(prior_terms.as_of, payload.published_at)
        if elapsed < config.RATE_COOLDOWN_HOURS:
            if grade_moved and downgrade:
                overridden = True
                notes.append(
                    f"cooldown ({config.RATE_COOLDOWN_HOURS}h, {elapsed:.1f}h elapsed) overridden: "
                    f"downgrade crossed a grade boundary {prior_terms.score} -> {payload.score}"
                )
            else:
                cooldown = True
                effective_rate = float(prior_rate)
                effective_ltv = prior_terms.effective_max_ltv
                notes.append(
                    f"terms held: {elapsed:.1f}h since last change, cooldown is "
                    f"{config.RATE_COOLDOWN_HOURS}h (§11 whipsaw guard)"
                )

        # Then the circuit breaker, on whatever move survived the cooldown.
        if not cooldown:
            cap = float(config.MAX_RATE_CHANGE_BPS_PER_UPDATE)
            requested = effective_rate - prior_rate
            if abs(requested) > cap:
                breaker = True
                allowed = cap if requested > 0 else -cap
                effective_rate = prior_rate + allowed
                notes.append(
                    f"circuit breaker: formula asked for {requested:+.0f}bps, "
                    f"capped at {allowed:+.0f}bps per update (§11)"
                )
        change = int(round(effective_rate - prior_rate))

    limit = None
    if eligible_receivables is not None:
        limit = round(effective_ltv * eligible_receivables, 2)

    return PoolTerms(
        borrower_id=payload.borrower_id,
        as_of=payload.published_at,
        score=payload.score,
        score_numeric=payload.score_numeric,
        confidence_interval=(lo, hi),
        pricing_score=p_score,
        base_rate_bps=int(config.POOL_BASE_RATE_BPS),
        risk_premium_bps=int(round(premium_point)),
        uncertainty_premium_bps=int(round(uncertainty_bps)),
        indicative_rate_bps=int(round(indicative_rate)),
        effective_rate_bps=int(round(effective_rate)),
        indicative_max_ltv=indicative_ltv,
        effective_max_ltv=round(effective_ltv, 4),
        prior_rate_bps=prior_rate,
        rate_change_bps=change,
        circuit_breaker_applied=breaker,
        cooldown_active=cooldown,
        cooldown_overridden=overridden,
        borrowing_limit=limit,
        eligible_receivables=eligible_receivables,
        notes=notes,
    )


def terms_history(
    payloads: list[ScorePublicationPayload],
    *,
    eligible_receivables: float | None = None,
    published_only: bool = True,
) -> list[PoolTerms]:
    """Walk a borrower's score log and replay the terms a pool would have applied.

    ``published_only`` filters to scores that cleared §10's gate, which is the honest default: a
    re-score the registry never received is not something a pool could have priced against. Pass
    ``False`` only to show what the terms *would* have done had every re-score published — useful
    for arguing about where the threshold should sit, misleading if presented as history.

    Sequential by construction: each step's circuit breaker and cooldown are evaluated against the
    terms the previous step actually produced, so the damping compounds the way it would in
    production rather than each row being priced independently from the payload alone.
    """
    out: list[PoolTerms] = []
    prior: PoolTerms | None = None
    for p in sorted(payloads, key=lambda x: x.published_at):
        if published_only and not p.published_onchain:
            continue
        terms = terms_for(p, prior_terms=prior, eligible_receivables=eligible_receivables)
        out.append(terms)
        # Carry forward the last terms that actually *moved* the rate, so the cooldown measures
        # from the last change rather than from the last re-score. Measuring from the last
        # re-score would let a busy cadence keep resetting the clock, and the cooldown would
        # never expire — the guard would silently stop guarding under exactly the load it exists
        # for. An unchanged rate leaves the clock where it was, which is the same reasoning as
        # §10's drift being cumulative against the last *published* score.
        if prior is None or terms.effective_rate_bps != prior.effective_rate_bps:
            prior = terms
    return out


def latest_terms(
    borrower_id: str, *, eligible_receivables: float | None = None
) -> PoolTerms | None:
    """Current pool terms for one borrower, replayed from their published score history."""
    history = terms_history(
        store.load_scores(borrower_id), eligible_receivables=eligible_receivables
    )
    return history[-1] if history else None


def eligible_receivables_from(record: BorrowerFeatureRecord | None) -> float | None:
    """Recover the open receivables book from a published feature record.

    ``onchain_existing_leverage_ratio`` is outstanding / ``revenue_30d`` by construction, so the
    product recovers the collateral base without the consumption layer reaching back into raw
    invoice data it has no business reading — §11 keeps this module to arithmetic over what was
    published. Returns ``None`` when revenue is zero, since the ratio carries no information then.
    """
    if record is None:
        return None
    f = record.features
    if f.revenue_30d <= 0:
        return None
    return round(f.onchain_existing_leverage_ratio * f.revenue_30d, 2)


def _hours_between(earlier: datetime, later: datetime) -> float:
    return (utc(later) - utc(earlier)).total_seconds() / 3600.0


# --------------------------------------------------------------------------------------
# CLI — print the curve, then a worked history
# --------------------------------------------------------------------------------------


def _print_curve() -> None:
    print("§11 rate curve and LTV schedule — pure functions of the published score\n")
    print(f"  base rate {config.POOL_BASE_RATE_BPS}bps, premium "
          f"{config.RISK_PREMIUM_AT_ANCHOR_BPS:.0f}bps at score "
          f"{config.SCORE_ANCHOR_POINTS:.0f}, doubling every "
          f"{config.SCORE_POINTS_TO_DOUBLE_ODDS:.0f} points\n")
    print(f"{'grade':>6}{'score':>7}{'risk bps':>10}{'all-in':>9}{'max LTV':>9}")
    for grade, lower in config.GRADE_BANDS:
        lo, hi = calibration.grade_band(grade)
        mid = (lo + min(hi, 1000)) / 2
        premium = risk_premium_bps(mid)
        capped = "*" if premium >= config.MAX_RISK_PREMIUM_BPS else " "
        print(f"{grade:>6}{mid:>7.0f}{premium:>9.0f}{capped}"
              f"{(config.POOL_BASE_RATE_BPS + premium) / 100:>8.2f}%{max_ltv(mid):>9.0%}")
    print(f"\n  * risk premium capped at {config.MAX_RISK_PREMIUM_BPS:.0f}bps — past here a pool "
          f"declines rather than prices (§11)")


def _print_history(borrower_id: str) -> None:
    scores = store.load_scores(borrower_id)
    if not scores:
        print(f"\nNo scores on file for {borrower_id}. Run: python -m continuum.orchestrator daily")
        return

    receivables = eligible_receivables_from(store.load_feature_record(borrower_id))

    history = terms_history(scores, eligible_receivables=receivables)
    print(f"\n\nTerms replay — {borrower_id}"
          f"  ({len(history)} published of {len(scores)} re-scores)")
    if receivables is not None:
        print(f"  eligible receivables {receivables:,.0f} (from the latest feature record)")
    print()
    print(f"{'published_at':<22}{'grade':>6}{'score':>6}{'priced':>8}{'rate':>8}"
          f"{'chg':>6}{'LTV':>6}{'limit':>13}  guards")
    print("-" * 118)
    for t in history:
        guards = []
        if t.cooldown_active:
            guards.append("cooldown-held")
        if t.cooldown_overridden:
            guards.append("cooldown-overridden")
        if t.circuit_breaker_applied:
            guards.append("breaker")
        limit = f"{t.borrowing_limit:,.0f}" if t.borrowing_limit is not None else "-"
        print(
            f"{iso(t.as_of):<22}{t.score:>6}{t.score_numeric:>6}{t.pricing_score:>8.0f}"
            f"{t.effective_rate_pct:>7.2f}%{t.rate_change_bps:>+6d}{t.effective_max_ltv:>6.0%}"
            f"{limit:>13}  {', '.join(guards)}"
        )

    if history:
        first, last = history[0], history[-1]
        print(
            f"\n  {first.score}/{first.effective_rate_pct:.2f}% -> "
            f"{last.score}/{last.effective_rate_pct:.2f}% over {len(history)} updates; "
            f"max LTV {first.effective_max_ltv:.0%} -> {last.effective_max_ltv:.0%}"
        )
        if last.notes:
            print("\n  latest update:")
            for n in last.notes:
                print(f"    - {n}")

    print(
        "\nNo pool exists — these are the terms a pool would set. §11 keeps the formula separate\n"
        "from the model on purpose: the score is a risk input, the pool makes the lending decision."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="§11 consumption layer — score to pool terms")
    ap.add_argument("--borrower", help="replay the terms history for one borrower")
    ap.add_argument("--curve-only", action="store_true", help="print the rate/LTV schedule only")
    args = ap.parse_args()

    _print_curve()
    if not args.curve_only:
        borrower = args.borrower
        if borrower is None:
            roster = store.load_borrowers()
            borrower = roster[0]["borrower_id"] if roster else None
        if borrower:
            _print_history(borrower)


if __name__ == "__main__":
    main()
