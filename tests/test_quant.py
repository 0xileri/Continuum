"""§5.1's weighted quant score — the Wave 3 scorer.

§5.1 asks for "a transparent weighted formula ... Keep it simple and explainable; this is a
placeholder, say so in the docs." So the tests check the properties that make it *transparent*
rather than the properties that would make it accurate — accuracy is not claimable without the
default data §3 says does not exist yet, and a test suite that implied otherwise would be the
overclaim §11 warns about.

What is actually asserted: the formula reads exactly the four features the brief names, its
attribution is exactly additive, its weights sum to one, a borrower at every pivot scores the
anchor, and its uncertainty is visibly larger than a fitted model's would be.
"""

from __future__ import annotations

import pytest

from continuum import config
from continuum.scoring import calibration
from continuum.scoring.quant import QUANT_FEATURES, QuantScorer, evaluate, normalise


# --------------------------------------------------------------------------------------
# The formula reads what the brief says it reads
# --------------------------------------------------------------------------------------


def test_reads_exactly_the_four_features_the_brief_names():
    """§5.1 names them explicitly. Adding a fifth would make this a different, unstated model."""
    assert set(QUANT_FEATURES) == {
        "revenue_trend_90d",
        "days_sales_outstanding",
        "payer_concentration_top1_pct",
        "on_time_repayment_rate_180d",
    }


def test_weights_sum_to_one():
    assert sum(config.QUANT_WEIGHTS.values()) == pytest.approx(1.0)


def test_every_feature_has_a_pivot_and_a_plausible_range():
    for name in QUANT_FEATURES:
        assert name in config.QUANT_PIVOTS
        assert name in config.QUANT_PLAUSIBLE_RANGES


def test_no_other_feature_can_move_the_score(features):
    """The formula must ignore everything outside its four inputs.

    A weighted formula that quietly reads a fifth feature is no longer the transparent placeholder
    §5.1 describes — it is an unstated model, and nobody could reconstruct a published score.
    """
    baseline = evaluate(features).composite
    for name in ("revenue_30d", "cash_runway_days", "payer_risk_score", "dispute_rate_90d"):
        moved = features.model_copy(update={name: float(getattr(features, name)) * 3 + 7})
        assert evaluate(moved).composite == pytest.approx(baseline)


# --------------------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------------------


def test_the_pivot_scores_one_half():
    for name, (pivot, _, _) in config.QUANT_PIVOTS.items():
        assert normalise(name, pivot) == pytest.approx(0.5)


def test_a_full_span_in_the_good_direction_reaches_one():
    assert normalise("on_time_repayment_rate_180d", 0.90 + 0.20) == pytest.approx(1.0)
    assert normalise("days_sales_outstanding", 45.0 - 40.0) == pytest.approx(1.0)


def test_direction_is_respected():
    """Higher DSO is worse; higher on-time repayment is better."""
    assert normalise("days_sales_outstanding", 80) < normalise("days_sales_outstanding", 20)
    assert normalise("on_time_repayment_rate_180d", 0.99) > normalise(
        "on_time_repayment_rate_180d", 0.60
    )
    assert normalise("payer_concentration_top1_pct", 0.85) < normalise(
        "payer_concentration_top1_pct", 0.20
    )
    assert normalise("revenue_trend_90d", 0.30) > normalise("revenue_trend_90d", -0.30)


def test_normalisation_is_clamped_so_one_feature_cannot_carry_a_borrower():
    assert normalise("on_time_repayment_rate_180d", 99.0) == 1.0
    assert normalise("days_sales_outstanding", -500.0) == 1.0
    assert normalise("days_sales_outstanding", 5000.0) == 0.0


# --------------------------------------------------------------------------------------
# Composition and attribution
# --------------------------------------------------------------------------------------


def test_a_borrower_at_every_pivot_scores_the_anchor(features):
    """The property that ties this scorer to the grade ladder the earlier phase established.

    Wholly ordinary in all four features → ``SCORE_ANCHOR_POINTS`` → mid-BBB. If this breaks, every
    published letter has silently shifted.
    """
    neutral = features.model_copy(
        update={name: config.QUANT_PIVOTS[name][0] for name in QUANT_FEATURES}
    )
    scorer = QuantScorer()
    points = calibration.pd_to_points(scorer.predict_pd(neutral), scorer.base_rate)
    assert points == pytest.approx(config.SCORE_ANCHOR_POINTS, abs=0.5)
    assert calibration.points_to_grade(points) == "BBB"


def test_attribution_is_exactly_additive(features):
    """§5.1 wants explainability. Contributions must reconstruct the composite, not approximate it.

    This is the property the trained model needed TreeSHAP for and this scorer gets for free — and
    it is worth asserting precisely because "for free" is how it silently stops being true.
    """
    result = evaluate(features)
    total = sum(c["contribution"] for c in result.contributions)
    assert total == pytest.approx(result.composite - 0.5, abs=1e-9)

    header = result.attribution()[0]
    assert header["_feature_sum_log_odds"] == pytest.approx(result.composite - 0.5, abs=1e-5)
    assert header["_units"] == "composite_health_index"


def test_attribution_is_ordered_by_magnitude(features):
    contributions = [abs(c["contribution"]) for c in evaluate(features).contributions]
    assert contributions == sorted(contributions, reverse=True)


def test_a_healthier_borrower_scores_higher(features):
    good = features.model_copy(
        update={
            "on_time_repayment_rate_180d": 1.0,
            "days_sales_outstanding": 25.0,
            "revenue_trend_90d": 0.25,
            "payer_concentration_top1_pct": 0.20,
        }
    )
    bad = features.model_copy(
        update={
            "on_time_repayment_rate_180d": 0.55,
            "days_sales_outstanding": 95.0,
            "revenue_trend_90d": -0.35,
            "payer_concentration_top1_pct": 0.85,
        }
    )
    assert evaluate(good).composite > evaluate(bad).composite
    assert evaluate(good).pseudo_pd < evaluate(bad).pseudo_pd


# --------------------------------------------------------------------------------------
# Honesty about being unfitted
# --------------------------------------------------------------------------------------


def test_pseudo_pd_stays_inside_a_deliberately_modest_range(features):
    """An unfitted prior must not claim a borrower is a 0.1% or a 90% risk."""
    for on_time in (0.0, 0.5, 1.0):
        for dso in (5.0, 45.0, 200.0):
            probe = features.model_copy(
                update={"on_time_repayment_rate_180d": on_time, "days_sales_outstanding": dso}
            )
            pd_hat = evaluate(probe).pseudo_pd
            assert config.QUANT_PD_FLOOR <= pd_hat <= config.QUANT_PD_CEILING


def test_the_scorer_declares_a_model_variance_floor():
    """§3 says this is a placeholder for a model that was never fitted. The interval is where that
    is stated in numbers rather than in prose."""
    scorer = QuantScorer()
    assert scorer.cv_metrics["quant_model_variance"] == config.QUANT_MODEL_VARIANCE
    assert scorer.cv_metrics["quant_model_variance"] > 0
    assert scorer.n_train_rows == 0
    assert "unfitted" in scorer.cv_metrics["scheme"]


def test_wave3_intervals_are_wider_than_a_fitted_models_would_be():
    common = dict(data_quality=0.96, novelty_share=0.0, anomaly_pressure=0.0, llm_confidence=0.8)
    fitted = calibration.half_width(calibration.uncertainty_terms(fold_std=0.02, **common))
    unfitted = calibration.half_width(
        calibration.uncertainty_terms(
            fold_std=0.0, model_variance_floor=config.QUANT_MODEL_VARIANCE, **common
        )
    )
    assert unfitted > fitted


def test_novelty_flags_features_outside_their_operating_range(features):
    probe = features.model_copy(update={"days_sales_outstanding": 400.0})
    share, offenders = QuantScorer().novelty(probe)
    assert "days_sales_outstanding" in offenders
    assert share == pytest.approx(0.25)


# --------------------------------------------------------------------------------------
# The artifact digest — what the attestation binds
# --------------------------------------------------------------------------------------


def test_artifact_digest_changes_when_a_weight_changes(monkeypatch):
    """Re-tuning a weight must be visible as a changed measurement, not an unremarked drift.

    A trained model hashes its serialised booster; this scorer's whole content is its weights and
    pivots, so hashing those gives the attestation the same property.
    """
    before = QuantScorer().artifact_sha256
    monkeypatch.setitem(config.QUANT_WEIGHTS, "revenue_trend_90d", 0.99)
    assert QuantScorer().artifact_sha256 != before


def test_artifact_digest_is_stable_across_instances():
    assert QuantScorer().artifact_sha256 == QuantScorer().artifact_sha256
