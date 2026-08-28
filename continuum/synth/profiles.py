"""Borrower archetypes and the payer universe for the synthetic cohort. ASSUMPTIONS #10.

The cohort is deliberately composed, not randomly drawn. Each archetype exists to exercise a
specific requirement in the brief:

- ``stable``            — control group; the score should stay flat and tight.
- ``mild_decline``      — slow drift; a static origination rating would miss this entirely.
  This is the §2 case study (Centrifuge/Goldfinch 2022-23 defaults) in miniature.
- ``sharp_decline``     — fast deterioration; the anomaly layer (§7 part 3) should fire an
  out-of-cadence re-score well before the next daily run.
- ``defaulting``        — sharp decline ending in default; supplies a positive training label.
- ``improving``         — score must move UP too, or the system is just a decay function.
- ``feed_goes_dark``    — healthy borrower whose accounting+invoice feeds stop reporting.
  §6: confidence must visibly degrade rather than freezing at last-known value.

§13's "payer-side risk laundering" case is carried by the payer universe rather than by a
borrower archetype of its own: ``pay_castr``, ``pay_kestl``, ``pay_orion`` and ``pay_cedar`` all
have negative ``health_drift``, so borrowers concentrated in them (Pennington, Kingsley, Selwyn,
Halloway) deteriorate through their *payers* while their own trading holds up. That is the
harder version of the case — the risk has no borrower-side tell — which is why it is modelled
inside otherwise-ordinary profiles instead of being labelled as its own archetype.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Payer:
    """An invoice payer. §13 makes payer health a first-class risk input, not an afterthought."""

    payer_id: str
    name: str
    sector: str
    base_health: float
    """0-1. Drives how promptly they settle and how likely they are to dispute."""
    health_drift: float = 0.0
    """Per-year change in health. Negative = the payer is deteriorating."""


PAYERS: tuple[Payer, ...] = (
    Payer("pay_grtwl", "Greatwall Retail Group", "retail", 0.88),
    Payer("pay_nordm", "Nordmark Logistics AB", "logistics", 0.82),
    Payer("pay_helix", "Helix Manufacturing PLC", "manufacturing", 0.79),
    Payer("pay_atlas", "Atlas Grocery Holdings", "retail", 0.85),
    Payer("pay_verid", "Veridian Health Systems", "healthcare", 0.91),
    Payer("pay_castr", "Castellan Property Services", "construction", 0.62, -0.28),
    Payer("pay_bluep", "Bluepoint Foods Ltd", "food_beverage", 0.74),
    Payer("pay_orion", "Orion Freight Solutions", "logistics", 0.70, -0.15),
    Payer("pay_kestl", "Kestrel Apparel Co", "apparel", 0.66, -0.34),
    Payer("pay_summt", "Summit Industrial Supply", "industrial", 0.80),
    Payer("pay_lumen", "Lumen Energy Partners", "energy", 0.86, 0.06),
    Payer("pay_cedar", "Cedarbrook Hospitality", "hospitality", 0.58, -0.22),
)

PAYERS_BY_ID = {p.payer_id: p for p in PAYERS}


@dataclass(frozen=True)
class BorrowerProfile:
    """A synthetic SME borrowing against its receivables."""

    borrower_id: str
    name: str
    sector: str
    archetype: str

    base_monthly_revenue: float
    revenue_noise: float
    """Coefficient of variation of monthly revenue. Higher = lumpier, riskier cash flow."""

    base_dso: float
    """Days sales outstanding at full health."""

    payer_ids: tuple[str, ...]
    payer_weights: tuple[float, ...]
    """Share of receivables by value. First entry is the top payer (§9 concentration feature)."""

    health_start: float
    health_end: float
    """Health trajectory endpoints over the full history window, in [0, 1]."""
    health_shape: str = "linear"
    """linear | late_cliff | early_dip_recover | step_down"""

    defaults: bool = False
    default_at_pct: float = 1.0
    """Fraction through the history where default occurs."""

    dark_feeds: tuple[str, ...] = ()
    dark_from_pct: float = 1.0
    """Feeds that stop reporting, and when. Exercises §6 staleness handling."""

    documents: tuple[str, ...] = field(default=())
    """Document scenario tags consumed by the document generator."""


COHORT: tuple[BorrowerProfile, ...] = (
    # ---- 4 stable -------------------------------------------------------------------
    BorrowerProfile(
        "brw_01hx8k2m4n", "Meridian Components Ltd", "manufacturing", "stable",
        base_monthly_revenue=182_000, revenue_noise=0.11, base_dso=38,
        payer_ids=("pay_helix", "pay_summt", "pay_nordm"),
        payer_weights=(0.34, 0.41, 0.25),
        health_start=0.84, health_end=0.86,
        documents=("clean_covenant", "routine_statement"),
    ),
    BorrowerProfile(
        "brw_01hx9p3q7r", "Talbot Fresh Produce", "food_beverage", "stable",
        base_monthly_revenue=96_500, revenue_noise=0.18, base_dso=31,
        payer_ids=("pay_atlas", "pay_grtwl", "pay_bluep"),
        payer_weights=(0.44, 0.33, 0.23),
        health_start=0.80, health_end=0.81,
        documents=("clean_covenant",),
    ),
    BorrowerProfile(
        "brw_01hxa4s8t2", "Ravensworth Medical Supply", "healthcare", "stable",
        base_monthly_revenue=241_000, revenue_noise=0.08, base_dso=44,
        payer_ids=("pay_verid", "pay_summt"),
        payer_weights=(0.62, 0.38),
        health_start=0.89, health_end=0.90,
        documents=("clean_covenant", "routine_statement"),
    ),
    BorrowerProfile(
        "brw_01hxb6u9v5", "Ardent Print & Packaging", "manufacturing", "stable",
        base_monthly_revenue=68_400, revenue_noise=0.15, base_dso=35,
        payer_ids=("pay_grtwl", "pay_bluep", "pay_kestl"),
        payer_weights=(0.38, 0.36, 0.26),
        health_start=0.76, health_end=0.75,
        documents=(),
    ),
    # ---- 3 mild decline: the "static rating goes stale" case (§2) --------------------
    BorrowerProfile(
        "brw_01hxc7w2x8", "Pennington Textiles", "apparel", "mild_decline",
        base_monthly_revenue=127_000, revenue_noise=0.21, base_dso=47,
        payer_ids=("pay_kestl", "pay_grtwl", "pay_atlas"),
        payer_weights=(0.51, 0.28, 0.21),
        health_start=0.78, health_end=0.52, health_shape="linear",
        documents=("margin_pressure_statement", "soft_covenant_warning"),
    ),
    BorrowerProfile(
        "brw_01hxd8y3z1", "Kingsley Civil Works", "construction", "mild_decline",
        base_monthly_revenue=310_000, revenue_noise=0.26, base_dso=58,
        payer_ids=("pay_castr", "pay_summt", "pay_helix"),
        payer_weights=(0.47, 0.30, 0.23),
        health_start=0.74, health_end=0.49, health_shape="step_down",
        documents=("margin_pressure_statement", "dispute_correspondence"),
    ),
    BorrowerProfile(
        "brw_01hxe9a4b6", "Selwyn Transport Group", "logistics", "mild_decline",
        base_monthly_revenue=154_000, revenue_noise=0.19, base_dso=41,
        payer_ids=("pay_orion", "pay_nordm", "pay_summt"),
        payer_weights=(0.43, 0.35, 0.22),
        health_start=0.77, health_end=0.55,
        documents=("soft_covenant_warning",),
    ),
    # ---- 2 sharp decline, one defaulting --------------------------------------------
    BorrowerProfile(
        "brw_01hxf2c5d9", "Halloway Interiors", "construction", "sharp_decline",
        base_monthly_revenue=88_000, revenue_noise=0.31, base_dso=62,
        payer_ids=("pay_cedar", "pay_castr"),
        payer_weights=(0.66, 0.34),
        health_start=0.71, health_end=0.24, health_shape="late_cliff",
        documents=("covenant_breach_notice", "dispute_correspondence", "adverse_news"),
    ),
    BorrowerProfile(
        "brw_01hxg3e6f4", "Brightwater Seafood Co", "food_beverage", "defaulting",
        base_monthly_revenue=142_000, revenue_noise=0.28, base_dso=54,
        payer_ids=("pay_bluep", "pay_cedar", "pay_atlas"),
        payer_weights=(0.49, 0.31, 0.20),
        health_start=0.68, health_end=0.11, health_shape="late_cliff",
        defaults=True, default_at_pct=0.93,
        documents=("covenant_breach_notice", "adverse_news", "dispute_correspondence"),
    ),
    # ---- 2 improving: the score must be able to go up -------------------------------
    BorrowerProfile(
        "brw_01hxh4g7h2", "Copperfield Engineering", "industrial", "improving",
        base_monthly_revenue=73_500, revenue_noise=0.22, base_dso=49,
        payer_ids=("pay_summt", "pay_helix", "pay_lumen"),
        payer_weights=(0.39, 0.33, 0.28),
        health_start=0.51, health_end=0.79, health_shape="early_dip_recover",
        documents=("recovery_statement",),
    ),
    BorrowerProfile(
        "brw_01hxj5i8k7", "Lowgate Renewables", "energy", "improving",
        base_monthly_revenue=205_000, revenue_noise=0.17, base_dso=45,
        payer_ids=("pay_lumen", "pay_verid", "pay_nordm"),
        payer_weights=(0.45, 0.30, 0.25),
        health_start=0.58, health_end=0.83,
        documents=("recovery_statement", "routine_statement"),
    ),
    # ---- 1 feed goes dark: §6's staleness requirement, isolated ---------------------
    BorrowerProfile(
        "brw_01hxk6j9m3", "Fenwick Industrial Spares", "industrial", "feed_goes_dark",
        base_monthly_revenue=119_000, revenue_noise=0.14, base_dso=40,
        payer_ids=("pay_summt", "pay_helix", "pay_nordm"),
        payer_weights=(0.37, 0.34, 0.29),
        health_start=0.81, health_end=0.80,
        dark_feeds=("accounting_feed", "invoice_feed"), dark_from_pct=0.88,
        documents=("clean_covenant",),
    ),
)

COHORT_BY_ID = {b.borrower_id: b for b in COHORT}


def health_at(profile: BorrowerProfile, t: float) -> float:
    """Borrower health in [0,1] at ``t`` = fraction through the history window.

    Shapes are deliberately different so the anomaly layer is tested against gradual drift
    (which it should NOT fire on) as well as cliffs (which it should).
    """
    a, b = profile.health_start, profile.health_end
    shape = profile.health_shape

    if shape == "linear":
        f = t
    elif shape == "late_cliff":
        # Flat, then falls away hard in the last third — the case daily re-scoring catches
        # and an annual review does not.
        f = 0.18 * t if t < 0.66 else 0.12 + (t - 0.66) / 0.34 * 0.88
    elif shape == "early_dip_recover":
        # Dips below the start before recovering past it.
        f = -0.55 * (t / 0.35) if t < 0.35 else (t - 0.35) / 0.65 * 1.55 - 0.55
    elif shape == "step_down":
        # Two discrete step changes — a covenant reset and a contract loss.
        f = 0.0 if t < 0.40 else (0.45 if t < 0.72 else 1.0)
    else:
        f = t

    return max(0.02, min(1.0, a + (b - a) * f))
