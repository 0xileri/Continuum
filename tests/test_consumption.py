"""§11's consumption layer. ASSUMPTIONS #14 and the ``UNCERTAINTY_LOAD_SHARE`` call.

§11's central instruction is a separation of powers — "don't let the AI directly set the rate; let
the AI set the risk input and let a transparent, governance-auditable formula translate that into
terms" — so the property that matters most is that these are *pure functions of the payload*. A
test that had to load a model to check a rate would already be evidence the separation had broken.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from continuum import config, consumption

from conftest import T0, make_payload


# --------------------------------------------------------------------------------------
# The pure formulas
# --------------------------------------------------------------------------------------


def test_premium_at_the_anchor_is_the_configured_constant():
    assert consumption.risk_premium_bps(config.SCORE_ANCHOR_POINTS) == pytest.approx(
        config.RISK_PREMIUM_AT_ANCHOR_BPS
    )


def test_the_rate_curve_inherits_the_scorecard_calibration():
    """One PDO down the scale doubles the premium — the property that keeps the two in step.

    If someone re-tunes ``SCORE_POINTS_TO_DOUBLE_ODDS`` without touching the rate curve, this is
    what catches it. The alternative design, a hand-written per-grade rate table, has no such
    check and drifts silently.
    """
    anchor = config.SCORE_ANCHOR_POINTS
    pdo = config.SCORE_POINTS_TO_DOUBLE_ODDS
    assert consumption.risk_premium_bps(anchor - pdo) == pytest.approx(
        2 * consumption.risk_premium_bps(anchor)
    )
    assert consumption.risk_premium_bps(anchor + pdo) == pytest.approx(
        0.5 * consumption.risk_premium_bps(anchor)
    )


def test_premium_is_capped_rather_than_unbounded():
    """Past a point an exponential stops being a price and becomes a refusal to lend; §11 leaves
    "should we lend at all" to the pool rather than smuggling it into a formula."""
    assert consumption.risk_premium_bps(50) == pytest.approx(config.MAX_RISK_PREMIUM_BPS)


def test_ltv_is_linear_and_clamped_into_its_envelope():
    assert consumption.max_ltv(config.LTV_ANCHOR_POINTS) == pytest.approx(config.LTV_AT_ANCHOR)
    assert consumption.max_ltv(
        config.LTV_ANCHOR_POINTS + config.LTV_POINTS_PER_DECILE
    ) == pytest.approx(config.LTV_AT_ANCHOR + 0.10)
    assert consumption.max_ltv(1000) == config.MAX_LTV_CEILING
    assert consumption.max_ltv(0) == config.MAX_LTV_FLOOR


def test_pricing_score_sits_at_the_pessimistic_end_of_the_band():
    """``UNCERTAINTY_LOAD_SHARE`` = 1.0 prices the interval's downside outright."""
    assert consumption.pricing_score(700, 640) == pytest.approx(
        700 - config.UNCERTAINTY_LOAD_SHARE * 60
    )


# --------------------------------------------------------------------------------------
# Terms
# --------------------------------------------------------------------------------------


def test_a_wider_interval_costs_money_at_the_same_point_score():
    """§11: a wider interval must raise the premium, "not just [a] lower score"."""
    tight = consumption.terms_for(make_payload(confidence_interval=(690, 710)))
    wide = consumption.terms_for(make_payload(confidence_interval=(610, 790)))
    assert wide.effective_rate_bps > tight.effective_rate_bps
    assert wide.uncertainty_premium_bps > tight.uncertainty_premium_bps
    # ...and demands a bigger collateral buffer, from the same one line.
    assert wide.effective_max_ltv < tight.effective_max_ltv


def test_the_premium_splits_into_risk_and_uncertainty():
    """Reported separately because they have different remedies: one needs the business to
    improve, the other needs a feed to start reporting again."""
    terms = consumption.terms_for(make_payload(confidence_interval=(620, 780)))
    assert terms.risk_premium_bps == pytest.approx(
        consumption.risk_premium_bps(700), abs=1
    )
    assert terms.uncertainty_premium_bps > 0
    assert terms.indicative_rate_bps == pytest.approx(
        config.POOL_BASE_RATE_BPS + terms.risk_premium_bps + terms.uncertainty_premium_bps, abs=2
    )


def test_a_fresh_facility_prices_unclamped():
    """No prior terms means no rate to whipsaw away from, so neither guard applies."""
    terms = consumption.terms_for(make_payload(score_numeric=400, score="CCC"))
    assert not terms.circuit_breaker_applied and not terms.cooldown_active
    assert terms.effective_rate_bps == terms.indicative_rate_bps


def test_circuit_breaker_caps_a_single_move():
    """§11 states the cap explicitly: ``rate_change_per_update ≤ ±50bps``."""
    # Intervals must track their own score: terms are priced at the interval's lower bound, so
    # leaving the factory's default band on both payloads would price them identically and the
    # breaker would have nothing to clamp.
    prior = consumption.terms_for(
        make_payload(score_numeric=760, score="A-", confidence_interval=(740, 780))
    )
    after = consumption.terms_for(
        make_payload(
            score_numeric=430,
            score="CCC",
            confidence_interval=(400, 460),
            published_at=T0 + timedelta(hours=config.RATE_COOLDOWN_HOURS + 1),
        ),
        prior_terms=prior,
    )
    assert after.circuit_breaker_applied
    assert abs(after.rate_change_bps) <= config.MAX_RATE_CHANGE_BPS_PER_UPDATE
    # The indicative rate still records what the formula actually asked for, so a borrower can
    # tell a damped move from a small one.
    assert after.indicative_rate_bps - prior.effective_rate_bps > (
        config.MAX_RATE_CHANGE_BPS_PER_UPDATE
    )


def test_cooldown_holds_an_ordinary_reprice():
    prior = consumption.terms_for(
        make_payload(score_numeric=700, score="BBB", confidence_interval=(682, 718))
    )
    after = consumption.terms_for(
        make_payload(
            score_numeric=670,
            score="BBB-",
            confidence_interval=(652, 688),
            published_at=T0 + timedelta(hours=config.RATE_COOLDOWN_HOURS - 2),
        ),
        prior_terms=prior,
    )
    # Same asymmetry as the publish gate: this one crosses a boundary downward, so it goes through.
    assert after.cooldown_overridden and not after.cooldown_active

    upgrade = consumption.terms_for(
        make_payload(
            score_numeric=745,
            score="A-",
            confidence_interval=(727, 763),
            published_at=T0 + timedelta(hours=config.RATE_COOLDOWN_HOURS - 2),
        ),
        prior_terms=prior,
    )
    assert upgrade.cooldown_active
    assert upgrade.effective_rate_bps == prior.effective_rate_bps


def test_terms_history_only_prices_published_scores():
    """§10's gate is what makes this filter mean something: a re-score the registry never
    received is not something a pool could have priced against."""
    payloads = [
        make_payload(score_numeric=700, published_at=T0, published_onchain=True),
        make_payload(
            score_numeric=660,
            score="BBB-",
            published_at=T0 + timedelta(days=1),
            published_onchain=False,
        ),
        make_payload(
            score_numeric=620,
            score="BB",
            published_at=T0 + timedelta(days=2),
            published_onchain=True,
        ),
    ]
    published = consumption.terms_history(payloads)
    assert [t.score_numeric for t in published] == [700, 620]
    assert len(consumption.terms_history(payloads, published_only=False)) == 3


def test_cooldown_measures_from_the_last_change_not_the_last_rescore():
    """A busy cadence must not keep resetting the clock, or the guard stops guarding under
    exactly the load it exists for."""
    payloads = [
        make_payload(score_numeric=700, published_at=T0 + timedelta(hours=h))
        for h in (0, 1, 2, 3)
    ] + [
        make_payload(
            score_numeric=690, score="BBB", published_at=T0 + timedelta(hours=26)
        )
    ]
    history = consumption.terms_history(payloads)
    # The four identical scores leave the rate unchanged, so the clock stays at T0 and the
    # 26-hour-later reprice is outside the 24h cooldown.
    assert not history[-1].cooldown_active


def test_borrowing_limit_is_ltv_times_receivables():
    terms = consumption.terms_for(make_payload(), eligible_receivables=250_000)
    assert terms.borrowing_limit == pytest.approx(terms.effective_max_ltv * 250_000, abs=0.01)


def test_eligible_receivables_recovered_from_the_feature_record(record):
    expected = (
        record.features.onchain_existing_leverage_ratio * record.features.revenue_30d
    )
    assert consumption.eligible_receivables_from(record) == pytest.approx(expected, abs=0.01)
    assert consumption.eligible_receivables_from(None) is None


def test_terms_serialise_for_the_dashboard():
    d = consumption.terms_for(make_payload()).to_dict()
    for key in (
        "effective_rate_bps",
        "indicative_rate_bps",
        "uncertainty_premium_bps",
        "effective_max_ltv",
        "pricing_score",
        "notes",
    ):
        assert key in d
