"""§7 part 4a — calibration: turning a probability into a grade and an interval.

Kept separate from ``aggregate.py`` on purpose. This module holds only pure functions of numbers:
PD in, points out; uncertainty terms in, interval width out. No model loading, no I/O, no clock.
That makes the entire published scale testable in isolation and re-calibratable without touching the
scoring path — which matters because §17 flags calibration as the thing that will actually change
once a design partner's loan tape arrives.

**What the letters mean, stated plainly.** These grades rank borrowers by modelled probability of
receivables deterioration over 90 days, anchored on this cohort's own base rate. They are *not*
default-frequency-anchored ratings in the NRSRO sense, and an ``A-`` here is not an assertion that
this borrower defaults as often as an agency-rated A- credit. §12 warns specifically about the word
"rating"; the code keeps the distinction by anchoring relatively and saying so.
"""

from __future__ import annotations

import math

from continuum import config
from continuum.schemas import Grade, LLMFlags

# --------------------------------------------------------------------------------------
# PD -> numeric score
# --------------------------------------------------------------------------------------


def _logit(p: float) -> float:
    lo, hi = config.SCORE_PD_CLAMP
    p = min(max(p, lo), hi)
    return math.log(p / (1.0 - p))


def pd_to_points(pd: float, base_rate: float) -> float:
    """Scorecard transform: log-odds distance from the base rate, scaled to points.

    ``factor = PDO / ln 2`` is the standard construction, so the returned scale has the property
    that a doubling of the odds of deterioration costs exactly ``SCORE_POINTS_TO_DOUBLE_ODDS``
    points wherever it happens on the curve. Linear-in-PD alternatives do not: they compress the
    difference between a 2% and a 6% borrower, which is the range where lending decisions actually
    get made in this vertical.
    """
    factor = config.SCORE_POINTS_TO_DOUBLE_ODDS / math.log(2.0)
    delta = _logit(pd) - _logit(base_rate)
    return config.SCORE_ANCHOR_POINTS - factor * delta


def points_to_grade(points: float) -> Grade:
    """Map a numeric score to its letter band."""
    for grade, lower in config.GRADE_BANDS:
        if points >= lower:
            return grade  # type: ignore[return-value]
    return "D"


def grade_band(grade: str) -> tuple[int, int]:
    """``(lower, upper)`` numeric bounds of a grade band. Used by the dashboard to draw bands."""
    bands = config.GRADE_BANDS
    for i, (name, lower) in enumerate(bands):
        if name == grade:
            upper = 1000 if i == 0 else bands[i - 1][1] - 1
            return lower, upper
    raise KeyError(grade)


# --------------------------------------------------------------------------------------
# Confidence interval
# --------------------------------------------------------------------------------------


def uncertainty_terms(
    *,
    data_quality: float,
    novelty_share: float,
    fold_std: float,
    anomaly_pressure: float,
    llm_confidence: float,
) -> dict[str, float]:
    """The four §-cited uncertainty inputs, each normalised to 0-1.

    Returned as a dict rather than a scalar because it is published: §7 requires explainability, and
    "the interval is wide" is not an explanation. A borrower disputing their premium under §11 is
    entitled to know whether it was their own data going stale, an anomaly, a thin document file, or
    the model being out of its depth.
    """
    return {
        # §6: a feed going quiet must cost confidence, not be silently absorbed.
        "data_quality": max(0.0, min(1.0, 1.0 - data_quality)),
        # §17's cold-start problem, quantified per prediction: how far outside its training
        # distribution this borrower sits, plus how unstable the model was across held-out folds.
        "model_variance": max(
            0.0,
            min(
                1.0,
                config.CI_NOVELTY_WEIGHT * novelty_share
                + fold_std / config.CI_MODEL_FOLD_STD_REF,
            ),
        ),
        # §7 part 3: a borrower behaving unusually may be fine, but the evidence that the score is
        # right has weakened either way.
        "anomaly_pressure": max(0.0, min(1.0, anomaly_pressure)),
        # A thin or contradictory document file is uncertainty, not absence of risk.
        "llm_confidence": max(0.0, min(1.0, 1.0 - llm_confidence)),
    }


def half_width(terms: dict[str, float]) -> float:
    """Confidence-interval half-width in score points.

    Multiplicative on a base width so every term is a percentage widening rather than a fixed point
    cost — the alternative lets four mild terms add to a wider interval than one catastrophic one.
    """
    widening = sum(config.CI_WEIGHTS[k] * terms.get(k, 0.0) for k in config.CI_WEIGHTS)
    return min(config.CI_BASE_HALF_WIDTH * (1.0 + widening), config.CI_MAX_HALF_WIDTH)


def grade_ceiling(half_width_points: float) -> float:
    """Highest numeric score attainable at this interval width. See config for the reasoning."""
    return 1000.0 - config.GRADE_CEILING_HALFWIDTH_MULTIPLE * half_width_points


# --------------------------------------------------------------------------------------
# LLM flag penalty
# --------------------------------------------------------------------------------------


def llm_penalty(flags: LLMFlags) -> tuple[float, list[str]]:
    """Points deducted for raised risk flags, and which flags drove it.

    Returns ``(penalty, reasons)``. ``penalty`` is non-negative; the caller subtracts it.

    Takes only the flags on purpose. Discounting the penalty by document-feed freshness or
    corroboration looks like the natural second input and is a trap: ``FEED_SLA["document_feed"]``
    already carries ``corroboration = 0.6`` into ``data_quality_score``, so the discount is applied
    once already, in the interval. Applying it again here would mean a borrower could soften a
    covenant-breach penalty by letting their document feed go quiet — §13's data-spoofing surface
    with an extra door. Staleness widens the interval; it never cheapens a raised flag.

    An offline stub needs no special case: ``offline_flags`` raises nothing, so the loop below
    returns ``(0.0, [])`` and its zero confidence widens the interval instead (ASSUMPTIONS #8).
    """
    raised: list[str] = []
    gross = 0.0
    for name, points in config.LLM_FLAG_PENALTIES.items():
        if getattr(flags, name, False):
            gross += points
            raised.append(name)

    if not raised:
        return 0.0, []

    floor = config.LLM_PENALTY_CONFIDENCE_FLOOR
    scaled = gross * (floor + (1.0 - floor) * max(0.0, min(1.0, flags.confidence)))
    return min(scaled, config.LLM_PENALTY_CAP), raised


# --------------------------------------------------------------------------------------
# CLI — print the scale so it can be eyeballed and argued with
# --------------------------------------------------------------------------------------


def main() -> None:
    from continuum.scoring.structured import StructuredModel

    try:
        model = StructuredModel.load()
        base_rate, fold_std = model.base_rate, model.cv_metrics.get("auc_fold_std", 0.0)
    except FileNotFoundError:
        base_rate, fold_std = 0.157, 0.04
        print("(no trained model found; using illustrative base rate)\n")

    print(f"Score scale — anchored at {config.SCORE_ANCHOR_POINTS:.0f} for PD = base rate "
          f"{base_rate:.3f}")
    print(f"  {config.SCORE_POINTS_TO_DOUBLE_ODDS:.0f} points per doubling of the odds\n")
    print(f"{'PD':>8}{'points':>9}{'grade':>7}")
    for pd in (0.002, 0.01, 0.03, 0.06, 0.10, 0.157, 0.25, 0.40, 0.60, 0.80, 0.95):
        pts = pd_to_points(pd, base_rate)
        print(f"{pd:>8.3f}{min(max(pts, 0), 1000):>9.0f}{points_to_grade(pts):>7}")

    print("\nInterval width and the grade ceiling it implies:\n")
    print(f"{'scenario':<44}{'half-width':>11}{'max grade':>11}")
    scenarios = [
        ("pristine: fresh feeds, no anomaly, clear docs", dict(
            data_quality=0.99, novelty_share=0.0, fold_std=fold_std,
            anomaly_pressure=0.0, llm_confidence=0.85)),
        ("typical: good feeds, thin document file", dict(
            data_quality=0.96, novelty_share=0.0, fold_std=fold_std,
            anomaly_pressure=0.0, llm_confidence=0.22)),
        ("anomaly firing, data still fresh", dict(
            data_quality=0.96, novelty_share=0.06, fold_std=fold_std,
            anomaly_pressure=1.0, llm_confidence=0.5)),
        ("feeds dark ~2 months (§6 degradation)", dict(
            data_quality=0.41, novelty_share=0.11, fold_std=fold_std,
            anomaly_pressure=0.5, llm_confidence=0.2)),
        ("feeds dark, borrower unlike training set", dict(
            data_quality=0.15, novelty_share=0.40, fold_std=fold_std,
            anomaly_pressure=1.0, llm_confidence=0.0)),
    ]
    for label, kwargs in scenarios:
        hw = half_width(uncertainty_terms(**kwargs))
        ceiling = grade_ceiling(hw)
        print(f"{label:<44}{hw:>10.0f}p{points_to_grade(ceiling):>11}")

    print(
        "\nRead the last two rows against §6: a borrower whose feeds go quiet cannot hold a top\n"
        "grade on stale data, and the interval — which §11 turns into risk premium — roughly\n"
        "doubles. That is the 'degrades rather than freezes' requirement, in numbers."
    )


if __name__ == "__main__":
    main()
