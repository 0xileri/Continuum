"""Central configuration for the Continuum Phase 0 scoring engine.

Every threshold, weight and band in this file is a decision that ``claude.md`` does not
specify. Each block cites its entry in ``ASSUMPTIONS.md`` — if you are tuning this system,
read that file first.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("CONTINUUM_DATA_DIR", PROJECT_ROOT / "data"))

RAW_DIR = DATA_DIR / "raw"
FEATURES_DIR = DATA_DIR / "features"
SCORES_DIR = DATA_DIR / "scores"
MODELS_DIR = DATA_DIR / "models"
EXPLAIN_DIR = DATA_DIR / "explain"
DISPUTES_DIR = DATA_DIR / "disputes"

for _d in (RAW_DIR, FEATURES_DIR, SCORES_DIR, MODELS_DIR, EXPLAIN_DIR, DISPUTES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------------------
# Versioning — stamped into every Score Publication Payload (§9)
# --------------------------------------------------------------------------------------

MODEL_VERSION = "scoring-engine-v0.1.0"
"""Phase 0. §9's example shows v1.3.2; this PoC is honestly pre-1.0."""

# --------------------------------------------------------------------------------------
# Synthetic data generation (ASSUMPTIONS #10)
# --------------------------------------------------------------------------------------

RANDOM_SEED = 20260811
N_BORROWERS = 12
HISTORY_DAYS = 548  # ~18 months

# --------------------------------------------------------------------------------------
# Claude reasoning agent (ASSUMPTIONS #9)
#
# §14: "Sonnet-class for volume, escalate to a stronger model for edge cases/disputes."
# --------------------------------------------------------------------------------------

LLM_MODEL = os.getenv("CONTINUUM_LLM_MODEL", "claude-sonnet-5")
LLM_ESCALATION_MODEL = os.getenv("CONTINUUM_LLM_ESCALATION_MODEL", "claude-opus-5")
LLM_EFFORT_ROUTINE = "medium"
LLM_EFFORT_ESCALATED = "high"
LLM_MAX_TOKENS = 8000
LLM_MAX_RETRIES = 4

LLM_ESCALATION_CONFIDENCE_FLOOR = 0.70
"""Escalation trigger, but not on its own: the routine pass is re-run on the stronger model only
when confidence is below this floor AND at least one risk flag was raised (or a §11 dispute forces
it). Low confidence on an all-clean, thin document file is the correct answer, not an edge case —
escalating those cost an Opus call on 9 of 11 cohort borrowers and moved no flag. See
``llm_agent.DocumentAgent._should_escalate``."""

# --------------------------------------------------------------------------------------
# Data-source SLAs → freshness decay (ASSUMPTIONS #5)
#
# grace_hours: expected reporting interval; no penalty inside it.
# halflife_hours: after grace, freshness halves every this many hours.
# weight: contribution to data_quality_score.
# corroboration: §13 — direct API integrations are trusted above self-reported uploads.
# --------------------------------------------------------------------------------------

FEED_SLA: dict[str, dict[str, float]] = {
    "invoice_feed": {"grace_hours": 26, "halflife_hours": 48, "weight": 0.35, "corroboration": 1.0},
    "bank_feed": {"grace_hours": 26, "halflife_hours": 36, "weight": 0.30, "corroboration": 1.0},
    "accounting_feed": {"grace_hours": 50, "halflife_hours": 96, "weight": 0.20, "corroboration": 1.0},
    "document_feed": {"grace_hours": 720, "halflife_hours": 720, "weight": 0.10, "corroboration": 0.6},
    "onchain_feed": {"grace_hours": 26, "halflife_hours": 72, "weight": 0.05, "corroboration": 1.0},
}

DATA_QUALITY_FLOOR = 0.05
"""Never report 0.0 — a fully dark borrower still has a last-known state, just a worthless one."""

# --------------------------------------------------------------------------------------
# Materiality thresholds for event-triggered re-scoring (ASSUMPTIONS #4)
#
# §13 "gaming the re-score cadence": these are borrower-relative, not absolute, so a
# borrower cannot learn a fixed cutoff and manage cash flows to sit just under it.
# --------------------------------------------------------------------------------------

MATERIALITY = {
    "robust_z_abs": 3.0,
    "payment_late_days_over_p90": 15.0,
    "dispute_value_pct_of_receivables": 0.05,
    "payer_concentration_jump_7d": 0.10,
    "provisional_score_delta": 25.0,
    "data_quality_drop": 0.15,
}

MONITORED_FEATURES = (
    "revenue_30d",
    "days_sales_outstanding",
    "payer_concentration_top1_pct",
    "on_time_repayment_rate_180d",
    "dispute_rate_90d",
    "invoice_volume_30d",
)
"""Features the anomaly layer watches for out-of-cadence triggers (§7 part 3)."""

ANOMALY_MIN_HISTORY = 14
"""Robust z-scores below this many observations are unreliable; the layer abstains."""

# --------------------------------------------------------------------------------------
# Score calibration: PD -> 0-1000 numeric -> letter grade (ASSUMPTIONS #18)
#
# §7: "combine 1-3 into a single score (recommend keeping Credora's convention of a
# letter-grade scale, e.g. AA-D, since lenders are already trained on it)".
# --------------------------------------------------------------------------------------

SCORE_ANCHOR_POINTS = 700.0
"""Numeric score awarded to a borrower sitting exactly at the model's own base rate.

Anchored on the base rate rather than on an absolute default probability, because the model's
target is *90-day deterioration* in a receivables book, not annualised default. A rating agency's
letters are anchored to observed default frequencies; these are not, and mapping a deterioration
probability onto a default-anchored scale would silently overstate precision. 700 lands mid-BBB, so
the median borrower in the vertical reads as ordinary rather than as good or bad."""

SCORE_POINTS_TO_DOUBLE_ODDS = 70.0
"""Points of score per doubling of the odds of deterioration — the classic scorecard PDO.

Fixes the slope of the whole scale with one interpretable number: a borrower twice as likely to
deteriorate as another scores 70 points lower, everywhere on the curve."""

SCORE_PD_CLAMP = (0.002, 0.98)
"""PD is clamped before the log-odds transform. A boosted model on 636 rows can emit 0.0006, and
ln(odds) of that is a 1200-point score — arithmetic precision standing in for evidence."""

GRADE_BANDS: tuple[tuple[str, int], ...] = (
    ("AAA", 900),
    ("AA", 850),
    ("A+", 800),
    ("A", 770),
    ("A-", 730),
    ("BBB", 690),
    ("BBB-", 650),
    ("BB", 610),
    ("BB-", 570),
    ("B", 520),
    ("B-", 470),
    ("CCC", 400),
    ("CC", 330),
    ("C", 250),
    ("D", 0),
)
"""Lower bound of each band, best first. Calibrated so §9's worked example holds: a numeric of
742 is an ``A-``, as printed in the brief."""

GRADE_CEILING_HALFWIDTH_MULTIPLE = 3.0
"""Every point of confidence-interval half-width removes this many points from the *highest grade
attainable*, regardless of what the features say.

This is §6's requirement with teeth. Widening the interval alone lets a borrower whose feeds went
dark keep an investment-grade letter on the strength of stale data — the published grade "silently
freezes at the last-known value" exactly as §6 warns, and only a lender who reads the interval
notices. Capping the grade by interval width makes the letter itself move, which is what a
consuming pool and a borrower both actually look at.

Deliberately one-sided: uncertainty lowers the best attainable score and never raises a poor one.
Symmetry would mean a deteriorating borrower's terms improve when their data quality drops, which
is a manipulation route (§13) as well as being wrong."""

LLM_FLAG_PENALTIES = {
    "covenant_breach": 90.0,
    "payer_deterioration": 60.0,
    "adverse_news_detected": 45.0,
}
"""Numeric-score penalty per raised flag, before confidence scaling.

§7 wants the LLM's output to "feed back into the score as a feature". With 12 synthetic borrowers
there is no data to fit that coefficient, so the honest substitute is a fixed, published,
hard-capped penalty rather than a learned weight presented as if it were fitted. Ordering reflects
what each flag implies about repayment: a breached covenant is a contractual fact, payer distress
attacks the cash flow the facility is secured on, adverse news is the weakest of the three."""

LLM_PENALTY_CAP = 150.0
"""Maximum total the reasoning agent can move the score. Two notches, roughly.

A single model call reading borrower-supplied documents must not be able to dominate the
quantitative model — that is the surface §13's data-spoofing attack aims at, since documents are the
easiest input to fabricate."""

LLM_PENALTY_CONFIDENCE_FLOOR = 0.4
"""Fraction of a flag's penalty that applies even at zero stated confidence.

Scaling penalties linearly to zero would mean an agent that hedges pays nothing, which is an
incentive to hedge. A flag raised at all is evidence; low confidence discounts it, it does not
erase it."""

CI_MODEL_FOLD_STD_REF = 0.10
"""Cross-validated AUC spread across folds at which model uncertainty saturates the interval.
Read from the trained model's own metrics, so an unstable model publishes wider intervals without
anyone having to remember to widen them."""

CI_NOVELTY_WEIGHT = 2.0
"""Multiplier on the share of features outside their training range. At 2.0, half the features
being out-of-range saturates the term — the model is being asked about a borrower unlike anything
it was fitted on."""

# --------------------------------------------------------------------------------------
# Confidence interval (ASSUMPTIONS #6)
# --------------------------------------------------------------------------------------

CI_BASE_HALF_WIDTH = 18.0  # numeric points on the 0–1000 scale, at perfect data quality
CI_WEIGHTS = {
    "data_quality": 1.60,
    "model_variance": 1.10,
    "anomaly_pressure": 0.80,
    "llm_confidence": 0.50,
}
CI_MAX_HALF_WIDTH = 140.0

# --------------------------------------------------------------------------------------
# Scoring cadence (§7)
# --------------------------------------------------------------------------------------

DAILY_JITTER_MINUTES = 90
"""§13 randomized timing, made deterministic per (borrower, date). ASSUMPTIONS #17."""

PUBLISH_THRESHOLD_POINTS = 10.0
"""§10 gas-cost discipline: below this delta a re-score is recorded but not republished.
Phase 0 has no chain, but the behaviour is modelled now so Phase 1 inherits it."""

RESCORE_COOLDOWN_HOURS = 6
"""Minimum gap between event-triggered publications for one borrower. §10 circuit breaker."""

# --------------------------------------------------------------------------------------
# Consumption layer (ASSUMPTIONS #14) — illustrative, per §11
# --------------------------------------------------------------------------------------

POOL_BASE_RATE_BPS = 650
"""The pool's own cost of capital plus operating spread — nothing to do with the borrower.
§11 keeps this separate from the risk premium so a pool can re-price its own funding without
touching the risk curve, and so a borrower can see which half of their rate is about them."""

RISK_PREMIUM_AT_ANCHOR_BPS = 250.0
"""Risk premium charged to a borrower sitting exactly at ``SCORE_ANCHOR_POINTS``.

The whole premium curve is pinned by this one number plus the scorecard's own PDO. Because the
score scale is log-odds-linear by construction (ASSUMPTIONS #18 — 70 points per doubling of the
odds), and a credit spread is roughly proportional to expected loss, the premium that is *linear
in PD* is exponential in points:

    premium = RISK_PREMIUM_AT_ANCHOR_BPS · 2 ** ((ANCHOR - score) / PDO)

So the consumption layer inherits the scoring calibration instead of inventing a second,
inconsistent one. Re-tune the scorecard and the rate curve follows automatically; there is no way
for the two to drift apart. That property is why this is a single constant rather than a table."""

MAX_RISK_PREMIUM_BPS = 3000.0
"""Cap on the risk half of the rate. The exponential curve is unbounded — a CCC lands near
4900bps and a D at over 100% — which past a point stops being a price and becomes a refusal to
lend. A real pool declines the loan instead; capping here keeps the arithmetic honest and leaves
"should we lend at all" as the pool's decision rather than something a formula smuggles in."""

UNCERTAINTY_LOAD_SHARE = 1.0
"""**YOUR CALL.** How much of the confidence interval's downside the borrower pays for.

§11 requires that a wider interval raise the premium "not just [a] lower score", and that an
uncertain score can demand a larger collateral buffer. Rather than bolt on a second width×weight
term, both fall out of pricing at a single *pricing score*:

    pricing_score = score - UNCERTAINTY_LOAD_SHARE · (score - ci_lower)

1.0 prices the pessimistic end of the band outright — the conservative lender's read, and what
makes a borrower's feeds going dark cost them money without anyone having to decide it should.
0.0 ignores uncertainty entirely and charges on the point estimate. Anything between shares the
cost of not knowing between pool and borrower. This is a commercial decision, not a technical
one, and the interval is a heuristic band (ASSUMPTIONS #6) rather than a calibrated posterior —
so treat 1.0 as a starting point to argue with, not a derived answer."""

MAX_RATE_CHANGE_BPS_PER_UPDATE = 50  # §11 circuit breaker, stated explicitly in the brief
RATE_COOLDOWN_HOURS = 24
"""Minimum gap between rate changes for one borrower. §11's second whipsaw guard: the circuit
breaker bounds how far one update can move, this bounds how often. A downgrade that crosses a
grade boundary overrides it, for the same reason it overrides the publish gate — see
``consumption.terms_for``."""

MAX_LTV_CEILING = 0.90
MAX_LTV_FLOOR = 0.20
"""Advance-rate envelope. The ceiling is short of 1.0 because lending 100% against receivables
leaves the pool no equity cushion at any grade; the floor is not 0.0 because a formula that
outputs "no credit" has made the underwriting decision that §11 reserves for the pool."""

LTV_ANCHOR_POINTS = 700.0
LTV_AT_ANCHOR = 0.65
LTV_POINTS_PER_DECILE = 80.0
"""``g(score)``: LTV moves linearly in points — 80 points buys 10 percentage points of advance
rate — clamped into the envelope above. Linear on purpose, unlike the rate curve: LTV is a
collateral-coverage ratio a credit committee has to be able to reason about at a glance, and
nobody sanity-checks an exponent. The rate curve is exponential because expected loss is; a
haircut schedule is not the same kind of object."""

# --------------------------------------------------------------------------------------
# API read layer (ASSUMPTIONS #15) and the §11 dispute path (ASSUMPTIONS #13)
# --------------------------------------------------------------------------------------

API_ALLOW_REMOTE = os.getenv("CONTINUUM_API_ALLOW_REMOTE", "") == "1"
"""ASSUMPTIONS #15 says the API is unauthenticated and localhost-only. That is a deployment note
nobody reads, so ``continuum.api`` also *enforces* it: non-loopback peers get 403 unless this is
set deliberately. An unauthenticated read layer over borrower financials plus one endpoint that
spends model calls should take more than a stray ``--host 0.0.0.0`` to expose."""

API_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)
"""Vite dev (5173) and preview (4173). Enumerated rather than ``*`` — the dashboard is the only
intended browser client, and a wildcard on an unauthenticated API lets any page the operator has
open read this data cross-origin."""

DISPUTE_REASSESS_COOLDOWN_MINUTES = 30
"""Minimum gap between escalated document re-reads for one borrower (§11 dispute path).

``POST /borrowers/{id}/disputes`` triggers an Opus-tier re-read, and the endpoint has no auth by
ASSUMPTIONS #15. Without a gap, filing disputes in a loop is a denial-of-wallet against the
operator. Disputes inside the window are still recorded in full — only the re-read is skipped, with
the reason written into the record, because refusing to *log* a borrower's appeal would defeat the
point of §11 having one."""

DISPUTE_NARRATIVE_MAX_CHARS = 2000
"""Cap on borrower-supplied dispute text. It is stored and shown to a human reviewer; it is never
sent to a model — see ``api.file_dispute``."""
