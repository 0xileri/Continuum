"""§4's staleness rule, implemented as a rule rather than hoped for as an emergent property.

    *"Staleness rule: when a data source goes silent, the score keeps degrading under continued
    silence rather than plateauing or reversing upward. Silence is treated as worsening
    information, not neutral information."*

The brief names two specific failure modes, and both are live hazards in the decay-only design this
engine started with. Neither is hypothetical — each was reachable with the constants already in
``config.py``:

**Plateau.** ``quality.feed_freshness`` decays exponentially, so a dark feed's freshness asymptotes
to zero within a few weeks. ``data_quality_score`` then floors at ``DATA_QUALITY_FLOOR``, the
interval hits ``CI_MAX_HALF_WIDTH``, and ``grade_ceiling`` stops falling at 1000 − 3 × 140 = 580.
A borrower dark for a year lands on exactly the same letter as one dark for a month. That is
"plateauing" in the brief's own words, and no amount of re-tuning the half-lives fixes it: a bounded
function of silence cannot keep degrading.

**Reversal.** Some features improve mechanically as time passes with no new data.
``days_since_last_late_payment`` is the unambiguous case — it counts up from the last *known* late
payment, so a borrower whose bank feed goes dark the day after a late payment looks steadily better
behaved every day thereafter. ``on_time_repayment_rate_180d`` does the same more slowly as late
repayments age out of the window with nothing arriving to replace them. Silence rendered as
improvement is the precise inversion of the rule.

So this module adds two things the decay could not provide:

1. **An unbounded, monotone penalty** in score points, proportional to weighted silence-days past a
   grace period. It has no ceiling short of the score floor, so continued silence keeps costing.
2. **A ratchet** — while any weighted feed is silent, the published numeric score may not rise
   above the last score published before the silence began. Recovery is allowed on evidence, never
   on the passage of time. The ratchet releases the instant a silent feed reports again.

The penalty is applied to the score itself rather than only to the interval, deliberately. §4's
subject is "the score", and a borrower whose grade holds while only their confidence band widens
has not visibly degraded to the reader who looks at the letter — which is most readers, and every
smart contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from continuum import config
from continuum.clock import iso, utc


@dataclass
class StalenessAssessment:
    """How silent this borrower is, what it costs, and whether the ratchet is engaged."""

    silent: bool
    weighted_silence_days: float
    """Σ over feeds of ``weight × days_of_silence_past_grace``. The penalty's driver.

    Weighted so a dark invoice feed (0.35) costs seven times a dark on-chain feed (0.05), and
    summed rather than maxed so two half-dark feeds are worse than one."""

    penalty_points: float
    """Non-negative. The caller subtracts it."""

    per_feed: dict[str, float] = field(default_factory=dict)
    """Days of silence past grace, per feed. Attribution for the dashboard and §11 disputes."""

    worst_feed: str = ""
    worst_days: float = 0.0
    never_reported: list[str] = field(default_factory=list)
    """Feeds with no sync on record. Silent, but with no measurable duration — see ``assess``."""
    ratchet_ceiling: int | None = None
    """Highest numeric score publishable while the silence lasts, or ``None`` when not engaged."""

    def as_dict(self) -> dict:
        return {
            "silent": self.silent,
            "weighted_silence_days": round(self.weighted_silence_days, 3),
            "penalty_points": round(self.penalty_points, 2),
            "per_feed_days": {k: round(v, 2) for k, v in self.per_feed.items()},
            "worst_feed": self.worst_feed,
            "worst_days": round(self.worst_days, 2),
            "never_reported": list(self.never_reported),
            "ratchet_ceiling": self.ratchet_ceiling,
            "grace_hours": config.STALENESS_GRACE_HOURS,
            "points_per_weighted_day": config.STALENESS_POINTS_PER_DAY,
            "rule": (
                "§4 — silence is worsening information: the penalty is unbounded in duration and "
                "the ratchet forbids the score rising while a feed stays quiet."
            ),
        }

    def summary(self) -> str:
        if not self.silent:
            return "all feeds reporting"
        if self.never_reported and self.weighted_silence_days == 0.0:
            return f"{', '.join(self.never_reported)} has never reported (§4)"
        return (
            f"{self.worst_feed} silent {self.worst_days:.0f}d "
            f"(−{self.penalty_points:.0f} pts, §4)"
        )


def assess(
    source_freshness: dict[str, datetime | None],
    as_of: datetime,
    *,
    ratchet_ceiling: int | None = None,
) -> StalenessAssessment:
    """Measure silence and price it.

    ``source_freshness`` is §6's block: last successful sync per feed, or ``None`` for a feed that
    has never reported. A never-reporting feed counts as silent — "we have never had this data"
    cannot be less alarming than "we had it last week" — but contributes no *duration* penalty,
    because there is no start date to measure one from and inventing one would be fabricating
    evidence. It still engages the ratchet, and its zero freshness already widens the interval
    through ``data_quality_score``.
    """
    as_of = utc(as_of)
    per_feed: dict[str, float] = {}
    weighted = 0.0
    never_reported: list[str] = []

    for feed, sla in config.FEED_SLA.items():
        # Per-feed grace, floored by the global minimum. "Silent" must mean past *this feed's own*
        # expected reporting interval: the document feed syncs monthly by design, and a flat grace
        # would score it as permanently silent and make every borrower permanently stale.
        grace = max(float(sla["grace_hours"]), config.STALENESS_GRACE_HOURS)

        last = source_freshness.get(feed)
        if last is None:
            # Never reported. There is no start date to measure a duration from, so this adds no
            # duration penalty — inventing one would be fabricating evidence about how long the
            # silence has run. It does count as silent, which engages the ratchet: we know the feed
            # is not reporting, we just cannot say for how long. Its freshness is already 0.0 in
            # ``data_quality_score``, which widens the interval independently.
            never_reported.append(feed)
            per_feed[feed] = 0.0
            continue

        age_hours = (as_of - utc(last)).total_seconds() / 3600.0
        days = max(0.0, age_hours - grace) / 24.0

        per_feed[feed] = days
        weight = float(sla["weight"]) if config.STALENESS_FEED_WEIGHTS_FROM_SLA else 1.0
        weighted += weight * days

    penalty = config.STALENESS_POINTS_PER_DAY * weighted
    worst_feed = max(per_feed, key=lambda f: per_feed[f]) if per_feed else ""
    worst_days = per_feed.get(worst_feed, 0.0)
    silent = weighted > 0.0 or bool(never_reported)

    return StalenessAssessment(
        silent=silent,
        weighted_silence_days=weighted,
        penalty_points=penalty,
        per_feed=per_feed,
        worst_feed=worst_feed if silent else "",
        worst_days=worst_days,
        never_reported=never_reported,
        ratchet_ceiling=ratchet_ceiling if (silent and config.STALENESS_RATCHET) else None,
    )


def ratchet_ceiling_from_history(
    history: list, source_freshness: dict[str, datetime | None], as_of: datetime
) -> int | None:
    """Highest score publishable under §4's no-upward-reversal clause, or ``None``.

    §4 is stricter than "do not exceed the pre-silence level":

        *"the score keeps degrading under continued silence rather than plateauing or reversing
        upward."*

    Under continued silence the published series must be **monotonically non-increasing**. Any
    upward step is a reversal, including one that stays below where the borrower was when their
    feeds went quiet — a borrower who falls from 737 to 675 and then climbs back to 684 while still
    dark has been rewarded for silence, just less than they might have been.

    So the ceiling chains to the **most recent recorded score**, not to the last fully-fresh one.
    Each score in the chain was itself capped, so the anchor cannot drift upward, and the series
    cannot ratchet up one legal-looking step at a time. The last fully-fresh score is taken as an
    additional cap for the first silent observation, which is when there is no silent predecessor
    to chain to yet.

    The ratchet releases the moment a silent feed reports again: recovery is allowed on evidence,
    never on the passage of time.

    Returns ``None`` when nothing is silent, or when there is no history to anchor to — a
    borrower's first-ever score has nothing to reverse against.
    """
    if not config.STALENESS_RATCHET:
        return None
    if not assess(source_freshness, as_of).silent:
        return None
    if not history:
        return None

    caps = [history[-1].score_numeric]
    for payload in reversed(history):
        if getattr(payload, "staleness_silent", None) is False:
            caps.append(payload.score_numeric)
            break
    return min(caps)


def apply(points: float, assessment: StalenessAssessment) -> tuple[float, list[str]]:
    """Apply the penalty and the ratchet to a score in points. Returns ``(points, notes)``.

    Order matters and is the same reasoning as the LLM penalty preceding the grade ceiling in
    ``aggregate.score``: the penalty comes off first so that a borrower who is *both* silent and
    deteriorating pays for both, and the ratchet then caps whatever is left. Capping first would let
    the penalty land harmlessly below the ceiling and price two independent problems as one.
    """
    notes: list[str] = []
    out = points

    if assessment.penalty_points > 0:
        out -= assessment.penalty_points
        notes.append(
            f"§4 staleness: −{assessment.penalty_points:.1f} pts for "
            f"{assessment.weighted_silence_days:.2f} weighted silence-days "
            f"({assessment.worst_feed} quiet {assessment.worst_days:.0f}d)"
        )

    if assessment.ratchet_ceiling is not None and out > assessment.ratchet_ceiling:
        notes.append(
            f"§4 ratchet: held at {assessment.ratchet_ceiling} — the score may not rise while a "
            f"feed is silent, because the passage of time is not evidence of recovery "
            f"(would have been {out:.0f})"
        )
        out = float(assessment.ratchet_ceiling)

    return max(0.0, out), notes


# --------------------------------------------------------------------------------------
# CLI — show the rule biting, which is the only way to argue about the constants
# --------------------------------------------------------------------------------------


def main() -> None:
    from datetime import timedelta

    from continuum.scoring import calibration

    now = datetime(2026, 8, 28, tzinfo=__import__("datetime").timezone.utc)
    print("§4 staleness rule — silence is worsening information\n")
    print(
        f"  grace {config.STALENESS_GRACE_HOURS:.0f}h, then "
        f"{config.STALENESS_POINTS_PER_DAY} pts per weighted silence-day; ratchet "
        f"{'on' if config.STALENESS_RATCHET else 'off'}\n"
    )

    start = 742.0
    print("A borrower at 742 (A-) whose invoice + accounting feeds go dark:\n")
    print(f"{'silence':>10}{'weighted d':>13}{'penalty':>10}{'score':>8}{'grade':>7}")
    print("-" * 50)
    for days in (0, 2, 7, 14, 30, 60, 90, 180, 365):
        freshness = {feed: now - timedelta(hours=1) for feed in config.FEED_SLA}
        for feed in ("invoice_feed", "accounting_feed"):
            freshness[feed] = now - timedelta(days=days)
        a = assess(freshness, now)
        pts, _ = apply(start, a)
        print(
            f"{days:>9}d{a.weighted_silence_days:>13.2f}{a.penalty_points:>10.1f}"
            f"{pts:>8.0f}{calibration.points_to_grade(pts):>7}"
        )

    print(
        "\nNo plateau: the penalty is linear and unbounded in duration, so the letter keeps\n"
        "falling for as long as nobody reports. That is the half of §4 an exponential freshness\n"
        "decay structurally cannot deliver — a bounded function of silence stops costing.\n"
    )

    print("Total silence, every feed dark:\n")
    print(f"{'silence':>10}{'weighted d':>13}{'penalty':>10}{'score':>8}{'grade':>7}")
    print("-" * 50)
    for days in (7, 30, 90, 180, 365):
        freshness = {feed: now - timedelta(days=days) for feed in config.FEED_SLA}
        a = assess(freshness, now)
        pts, _ = apply(start, a)
        print(
            f"{days:>9}d{a.weighted_silence_days:>13.2f}{a.penalty_points:>10.1f}"
            f"{pts:>8.0f}{calibration.points_to_grade(pts):>7}"
        )

    print("\nAnd the ratchet, on a borrower whose features drift upward under silence:\n")
    freshness = {feed: now - timedelta(hours=1) for feed in config.FEED_SLA}
    freshness["invoice_feed"] = now - timedelta(days=20)
    a = assess(freshness, now, ratchet_ceiling=700)
    pts, notes = apply(760.0, a)
    print(f"  raw formula wanted 760, published {pts:.0f}")
    for n in notes:
        print(f"    - {n}")
    print(
        "\n  Without the ratchet, days_since_last_late_payment alone lifts a dark borrower's score\n"
        "  every day — silence rendered as good news, which is the reversal §4 forbids."
    )


if __name__ == "__main__":
    main()
