"""The published scale. ASSUMPTIONS #3, #6 and #18.

These are the numbers a lender reads and a borrower is priced on, so they are tested as a
contract rather than as implementation detail. In particular ``claude.md`` §9 prints one worked
example — ``score_numeric: 742`` is an ``A-`` — and that anchor is the only external check the
grade ladder has. If a band edge is ever re-tuned, this file is where the brief pushes back.
"""

from __future__ import annotations

import math

import pytest

from continuum import config
from continuum.scoring import calibration as cal
from continuum.schemas import LLMFlags


# --------------------------------------------------------------------------------------
# PD -> points -> grade
# --------------------------------------------------------------------------------------


def test_base_rate_scores_the_anchor():
    """A borrower at the model's own base rate scores exactly ``SCORE_ANCHOR_POINTS``."""
    assert cal.pd_to_points(0.157, 0.157) == pytest.approx(config.SCORE_ANCHOR_POINTS)


def test_doubling_the_odds_costs_one_pdo():
    """The defining property of a scorecard: twice the odds is ``PDO`` points, anywhere."""
    base = 0.10
    odds = base / (1 - base)
    doubled = (2 * odds) / (1 + 2 * odds)

    at_base = cal.pd_to_points(base, base)
    at_doubled = cal.pd_to_points(doubled, base)
    assert at_base - at_doubled == pytest.approx(config.SCORE_POINTS_TO_DOUBLE_ODDS, abs=1e-6)

    # And again a decade lower on the curve, since the claim is "anywhere on the curve".
    low = 0.01
    low_odds = low / (1 - low)
    low_doubled = (2 * low_odds) / (1 + 2 * low_odds)
    assert cal.pd_to_points(low, base) - cal.pd_to_points(low_doubled, base) == pytest.approx(
        config.SCORE_POINTS_TO_DOUBLE_ODDS, abs=1e-6
    )


def test_pd_is_clamped_before_the_log_odds_transform():
    """ASSUMPTIONS #18: a boosted model on 636 rows emits PDs whose logit is a 1200-point score."""
    lo, hi = config.SCORE_PD_CLAMP
    assert cal.pd_to_points(1e-9, 0.157) == pytest.approx(cal.pd_to_points(lo, 0.157))
    assert cal.pd_to_points(1.0 - 1e-9, 0.157) == pytest.approx(cal.pd_to_points(hi, 0.157))


def test_lower_pd_never_scores_worse():
    points = [cal.pd_to_points(p, 0.157) for p in (0.002, 0.01, 0.05, 0.157, 0.4, 0.8)]
    assert points == sorted(points, reverse=True)


# --------------------------------------------------------------------------------------
# Grade ladder
# --------------------------------------------------------------------------------------


def test_the_brief_s_worked_example_holds():
    """§9 prints ``score_numeric: 742`` against ``score: "A-"``. The ladder must reproduce it."""
    assert cal.points_to_grade(742) == "A-"


def test_grade_bands_are_contiguous_and_ordered():
    """No gaps and no overlaps — a numeric score must land in exactly one band."""
    bands = [(g, *cal.grade_band(g)) for g, _ in config.GRADE_BANDS]
    assert bands[0][2] == 1000
    assert bands[-1][1] == 0
    for (upper_grade, lo, hi), (_, next_lo, next_hi) in zip(bands, bands[1:]):
        assert next_hi == lo - 1, f"gap or overlap below {upper_grade}"
        assert lo <= hi


@pytest.mark.parametrize("points", [0, 1, 249, 250, 699, 700, 742, 899, 900, 1000])
def test_every_score_maps_into_its_own_band(points):
    lo, hi = cal.grade_band(cal.points_to_grade(points))
    assert lo <= points <= hi


def test_below_the_floor_is_D():
    assert cal.points_to_grade(-50) == "D"


# --------------------------------------------------------------------------------------
# Confidence interval
# --------------------------------------------------------------------------------------


def _terms(**kwargs):
    base = dict(
        data_quality=0.99,
        novelty_share=0.0,
        fold_std=0.0,
        anomaly_pressure=0.0,
        llm_confidence=1.0,
    )
    base.update(kwargs)
    return cal.uncertainty_terms(**base)


def test_uncertainty_terms_are_normalised():
    terms = _terms(data_quality=-5, novelty_share=99, anomaly_pressure=42, llm_confidence=-3)
    assert all(0.0 <= v <= 1.0 for v in terms.values())


def test_perfect_inputs_give_the_base_width():
    assert cal.half_width(_terms()) == pytest.approx(config.CI_BASE_HALF_WIDTH, rel=0.02)


def test_each_term_widens_the_interval():
    """§6, §7 and §17 each require a specific input to cost confidence. All four must bite."""
    baseline = cal.half_width(_terms())
    for kwargs in (
        {"data_quality": 0.2},
        {"novelty_share": 0.5},
        {"fold_std": config.CI_MODEL_FOLD_STD_REF},
        {"anomaly_pressure": 1.0},
        {"llm_confidence": 0.0},
    ):
        assert cal.half_width(_terms(**kwargs)) > baseline, kwargs


def test_half_width_saturates_and_never_exceeds_the_cap():
    """Every term maxed gives the widest interval the engine can publish.

    Note what this shows: with the current weights the saturation point is
    ``CI_BASE_HALF_WIDTH × (1 + Σ CI_WEIGHTS)`` = 18 × 5.0 = 90 points, which is **below**
    ``CI_MAX_HALF_WIDTH`` (140). The cap is therefore a guard rail that never binds today rather
    than a constraint doing work — it would only engage if the weights or the base width were
    raised. Asserted as the real maximum rather than as the cap so the test says what actually
    happens; the cap is asserted separately as an upper bound that must hold regardless.
    """
    worst = _terms(
        data_quality=0.0, novelty_share=1.0, fold_std=1.0, anomaly_pressure=1.0, llm_confidence=0.0
    )
    saturated = config.CI_BASE_HALF_WIDTH * (1.0 + sum(config.CI_WEIGHTS.values()))
    assert cal.half_width(worst) == pytest.approx(min(saturated, config.CI_MAX_HALF_WIDTH))
    assert cal.half_width(worst) <= config.CI_MAX_HALF_WIDTH


def test_the_cap_binds_when_the_weights_would_exceed_it(monkeypatch):
    """The guard rail still has to work, so it is tested with weights that reach it."""
    monkeypatch.setattr(config, "CI_BASE_HALF_WIDTH", 60.0)
    worst = _terms(
        data_quality=0.0, novelty_share=1.0, fold_std=1.0, anomaly_pressure=1.0, llm_confidence=0.0
    )
    assert cal.half_width(worst) == pytest.approx(config.CI_MAX_HALF_WIDTH)


def test_the_model_variance_floor_widens_an_unfitted_scorers_interval():
    """§3/§5.1: the Wave 3 scorer was never fitted, and the interval is where that is stated."""
    baseline = cal.half_width(_terms())
    floored = cal.half_width(
        cal.uncertainty_terms(
            data_quality=0.99,
            novelty_share=0.0,
            fold_std=0.0,
            anomaly_pressure=0.0,
            llm_confidence=1.0,
            model_variance_floor=config.QUANT_MODEL_VARIANCE,
        )
    )
    assert floored > baseline


def test_grade_ceiling_falls_as_the_interval_widens():
    """§6 with teeth: uncertainty must move the letter, not only the range."""
    tight = cal.grade_ceiling(cal.half_width(_terms()))
    dark = cal.grade_ceiling(cal.half_width(_terms(data_quality=0.15, anomaly_pressure=0.8)))
    assert dark < tight
    # A borrower whose feeds have gone dark cannot hold a top grade on stale data.
    assert cal.points_to_grade(dark) != "AAA"


def test_grade_ceiling_is_one_sided():
    """It caps the best attainable score; it never lifts a poor one."""
    assert cal.grade_ceiling(0.0) == 1000.0
    assert cal.grade_ceiling(50.0) < 1000.0


# --------------------------------------------------------------------------------------
# LLM penalty
# --------------------------------------------------------------------------------------


def _flags(**kwargs) -> LLMFlags:
    base = dict(
        covenant_breach=False,
        adverse_news_detected=False,
        payer_deterioration=False,
        confidence=1.0,
        evidence_refs=[],
    )
    base.update(kwargs)
    return LLMFlags(**base)


def test_no_flags_costs_nothing():
    assert cal.llm_penalty(_flags()) == (0.0, [])


def test_offline_stub_costs_nothing_directly():
    """ASSUMPTIONS #8: the stub raises nothing, so the penalty is zero and the *interval* pays."""
    penalty, raised = cal.llm_penalty(
        _flags(confidence=0.0, source="offline_fixture", output_mode="none")
    )
    assert (penalty, raised) == (0.0, [])


def test_flag_penalties_are_ordered_by_severity():
    """A contractual breach must cost more than adverse press about the same borrower."""
    breach, _ = cal.llm_penalty(_flags(covenant_breach=True))
    payer, _ = cal.llm_penalty(_flags(payer_deterioration=True))
    news, _ = cal.llm_penalty(_flags(adverse_news_detected=True))
    assert breach > payer > news > 0


def test_low_confidence_discounts_but_never_erases():
    """Scaling to zero would make hedging free, which is an incentive to hedge."""
    sure, _ = cal.llm_penalty(_flags(covenant_breach=True, confidence=1.0))
    unsure, _ = cal.llm_penalty(_flags(covenant_breach=True, confidence=0.0))
    assert 0 < unsure < sure
    assert unsure == pytest.approx(
        config.LLM_FLAG_PENALTIES["covenant_breach"] * config.LLM_PENALTY_CONFIDENCE_FLOOR
    )


def test_penalty_is_capped_so_documents_cannot_dominate_the_model():
    """§13: documents are the easiest input to fabricate, so the agent gets a hard ceiling."""
    penalty, raised = cal.llm_penalty(
        _flags(
            covenant_breach=True,
            adverse_news_detected=True,
            payer_deterioration=True,
            confidence=1.0,
        )
    )
    assert set(raised) == set(config.LLM_FLAG_PENALTIES)
    assert sum(config.LLM_FLAG_PENALTIES.values()) > config.LLM_PENALTY_CAP
    assert penalty == pytest.approx(config.LLM_PENALTY_CAP)


def test_logit_clamp_symmetry():
    lo, hi = config.SCORE_PD_CLAMP
    assert cal._logit(lo) < 0 < cal._logit(hi)
    assert math.isfinite(cal._logit(0.0)) and math.isfinite(cal._logit(1.0))
