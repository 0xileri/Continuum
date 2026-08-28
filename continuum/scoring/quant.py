"""§5.1 — the weighted quant score. The Wave 3 scorer.

    *"Quant score: a transparent weighted formula over revenue_trend_90d,
    days_sales_outstanding, payer_concentration_top1_pct, and on_time_repayment_rate_180d — not a
    trained model this wave. Keep it simple and explainable; this is a placeholder, say so in the
    docs."*

So: four features, four weights, four piecewise-linear maps onto [0,1], one weighted mean. A lender
can reproduce any published number here with a calculator, which is the point — §3 puts a trained
model out of scope precisely because there is no default data to fit or backtest one against, and a
formula that *looked* fitted would be the worse artifact.

**This is a placeholder and the code says so in three places** — here, in the explanation artifact
every score carries, and in ``config.QUANT_MODEL_VARIANCE``, which widens every Wave 3 confidence
interval by a fixed amount to represent exactly this. Uncalibrated is a property of the output, not
a caveat in a README nobody opens.

**What is deliberately absent.** No interaction terms, no per-sector adjustment, no cohort
percentile normalisation. Each would improve how the synthetic cohort ranks and none could be
justified from evidence, which makes them overfitting performed by hand. The pivots are ordinary
invoice-financing operating values (``config.QUANT_PIVOTS``), not statistics of the data being
scored — so the scale does not move when the generator does.

Attribution is exact rather than approximate: each feature's contribution to the composite is
``weight × (normalised − 0.5)``, and those contributions sum to the composite minus 0.5 by
construction. That gives the dashboard the same additive explainability trail TreeSHAP gave the
trained model, without either the dependency or the pretence of a fitted attribution.

Run:
    python -m continuum.scoring.quant            # print the scale and the cohort's scores
"""

from __future__ import annotations

from dataclasses import dataclass, field

from continuum import config
from continuum.schemas import BorrowerFeatures

QUANT_FEATURES: tuple[str, ...] = tuple(config.QUANT_WEIGHTS)
"""The four §5.1 features, in weight order. Named once so the scorer, the explanation artifact and
the tests cannot disagree about what the formula reads."""


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def normalise(feature: str, value: float) -> float:
    """Map one raw feature onto [0,1], where 1 is healthy.

    Piecewise-linear around a stated pivot: the pivot scores 0.5, and moving ``span`` in the good
    direction reaches 1.0. Clamped at both ends, so an extraordinary value cannot dominate the
    composite — a borrower with 100% on-time repayment and a collapsing book is not a good credit,
    and an unclamped map would let one perfect feature carry them.
    """
    pivot, span, direction = config.QUANT_PIVOTS[feature]
    if span <= 0:
        return 0.5
    signed = (value - pivot) / span
    if direction == "lower_is_better":
        signed = -signed
    return _clamp(0.5 + signed)


@dataclass
class QuantResult:
    """One evaluation of the formula, with everything needed to reproduce it by hand."""

    composite: float
    """Weighted mean of the normalised features, in [0,1]. 1 is healthy."""
    pseudo_pd: float
    """The composite mapped into ``[QUANT_PD_FLOOR, QUANT_PD_CEILING]`` so it can enter the same
    ``calibration.pd_to_points`` scorecard the trained model used. Named *pseudo* throughout
    because it is a monotone rescaling of a stated prior, not an estimated probability."""
    contributions: list[dict] = field(default_factory=list)
    out_of_range: list[str] = field(default_factory=list)
    novelty_share: float = 0.0

    def attribution(self) -> list[dict]:
        """The additive trail, in the shape the dashboard's waterfall already consumes.

        Contributions are reported in composite units (not log-odds) and sum exactly to
        ``composite - 0.5``, so the header row's arithmetic is checkable on screen.
        """
        header = {
            "_base_log_odds": 0.5,
            "_feature_sum_log_odds": round(self.composite - 0.5, 6),
            "_units": "composite_health_index",
        }
        return [header] + self.contributions


def evaluate(features: BorrowerFeatures) -> QuantResult:
    """Score one borrower. Pure function of four numbers — no I/O, no model, no clock."""
    contributions: list[dict] = []
    composite = 0.0

    for name in QUANT_FEATURES:
        weight = config.QUANT_WEIGHTS[name]
        raw = float(getattr(features, name))
        normalised = normalise(name, raw)
        composite += weight * normalised

        # Signed against the 0.5 neutral point, so a feature at its pivot contributes nothing and
        # the sign says "better or worse than an ordinary borrower" rather than "large or small".
        contribution = weight * (normalised - 0.5)
        contributions.append(
            {
                "feature": name,
                "value": raw,
                "normalised": round(normalised, 4),
                "weight": weight,
                "contribution": round(contribution, 6),
                "direction": "decreases_risk" if contribution > 0 else "increases_risk",
                "pivot": config.QUANT_PIVOTS[name][0],
            }
        )

    contributions.sort(key=lambda d: abs(d["contribution"]), reverse=True)
    total_abs = sum(abs(c["contribution"]) for c in contributions) or 1.0
    for c in contributions:
        c["share_of_total_abs"] = round(abs(c["contribution"]) / total_abs, 4)

    out_of_range = [
        name
        for name in QUANT_FEATURES
        if not (
            config.QUANT_PLAUSIBLE_RANGES[name][0]
            <= float(getattr(features, name))
            <= config.QUANT_PLAUSIBLE_RANGES[name][1]
        )
    ]

    floor, ceiling = config.QUANT_PD_FLOOR, config.QUANT_PD_CEILING
    pseudo_pd = ceiling - (ceiling - floor) * _clamp(composite)

    return QuantResult(
        composite=composite,
        pseudo_pd=pseudo_pd,
        contributions=contributions,
        out_of_range=out_of_range,
        novelty_share=len(out_of_range) / len(QUANT_FEATURES),
    )


class QuantScorer:
    """Adapter presenting the §5.1 formula through the interface the aggregator already speaks.

    The aggregator was written against a trained booster (``predict_pd``, ``novelty``, ``attribute``,
    ``base_rate``, ``cv_metrics``), and Wave 3 swaps the scorer rather than the pipeline. Matching
    that shape keeps the swap to one line in ``aggregate.score`` and leaves the publish gate, the
    interval, the attestation and the consumption layer untouched — which is also what makes
    restoring the trained model in Phase 2 a configuration change rather than a rewrite.
    """

    model_version = config.MODEL_VERSION
    scorer_kind = "weighted_quant_v1"

    def __init__(self) -> None:
        self.feature_names = QUANT_FEATURES
        self.n_train_rows = 0
        self.n_train_borrowers = 0
        self.cv_metrics = {
            # No folds, because nothing was fitted. The interval still needs a model-variance
            # signal, so QUANT_MODEL_VARIANCE stands in for a fold spread and says why.
            "scheme": "none — unfitted weighted formula (Wave 3 §3, §5.1)",
            "auc_fold_std": 0.0,
            "quant_model_variance": config.QUANT_MODEL_VARIANCE,
        }
        self.feature_ranges = {k: list(v) for k, v in config.QUANT_PLAUSIBLE_RANGES.items()}

    @property
    def base_rate(self) -> float:
        """PD of a borrower whose four features all sit exactly on their pivots.

        Derived rather than configured, so the anchor grade stays put if the PD range is re-tuned:
        a wholly ordinary borrower scores ``SCORE_ANCHOR_POINTS``, which is the property
        ``pd_to_points`` is built around.
        """
        return config.QUANT_PD_CEILING - (config.QUANT_PD_CEILING - config.QUANT_PD_FLOOR) * 0.5

    @property
    def artifact_sha256(self) -> str:
        """Digest over the formula's own definition.

        A trained model hashes its serialised booster; this scorer's entire content is its weights
        and pivots, so hashing those gives the attestation the same property — a published score is
        bound to the exact scoring rule that produced it, and re-tuning a weight is visible as a
        changed measurement rather than an unremarked drift.
        """
        import hashlib
        import json

        spec = json.dumps(
            {
                "kind": self.scorer_kind,
                "weights": config.QUANT_WEIGHTS,
                "pivots": {k: list(v) for k, v in config.QUANT_PIVOTS.items()},
                "pd_range": [config.QUANT_PD_FLOOR, config.QUANT_PD_CEILING],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(spec.encode("utf-8")).hexdigest()

    # ---- the scorer interface --------------------------------------------------------

    def predict_pd(self, features: BorrowerFeatures) -> float:
        return evaluate(features).pseudo_pd

    def novelty(self, features: BorrowerFeatures) -> tuple[float, list[str]]:
        result = evaluate(features)
        return result.novelty_share, result.out_of_range

    def attribute(self, features: BorrowerFeatures) -> list[dict]:
        return evaluate(features).attribution()

    @classmethod
    def load(cls) -> "QuantScorer":
        """Present for interface parity with ``StructuredModel.load``. Nothing to load."""
        return cls()


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main() -> None:
    from continuum.scoring import calibration

    scorer = QuantScorer()
    print("§5.1 weighted quant score — the Wave 3 scorer (NOT a trained model)\n")
    print(f"  artifact sha256 {scorer.artifact_sha256[:16]}...   base rate {scorer.base_rate:.3f}\n")

    print(f"{'feature':<34}{'weight':>8}{'pivot':>10}{'span':>8}  direction")
    print("-" * 78)
    for name in QUANT_FEATURES:
        pivot, span, direction = config.QUANT_PIVOTS[name]
        print(f"{name:<34}{config.QUANT_WEIGHTS[name]:>8.2f}{pivot:>10.2f}{span:>8.2f}  {direction}")

    print("\nWhat the formula does to a grade — one feature moved at a time from its pivot:\n")
    from continuum.ingestion.features import MODEL_FEATURES  # noqa: F401  (schema sanity)

    neutral = {name: config.QUANT_PIVOTS[name][0] for name in QUANT_FEATURES}
    base = BorrowerFeatures(
        revenue_30d=150_000,
        days_since_last_late_payment=90,
        **neutral,
    )
    base_pts = calibration.pd_to_points(scorer.predict_pd(base), scorer.base_rate)
    print(f"  all four at pivot ->  {base_pts:.0f}  {calibration.points_to_grade(base_pts)}\n")

    print(f"{'feature':<34}{'value':>10}{'score':>8}{'grade':>7}")
    for name in QUANT_FEATURES:
        pivot, span, direction = config.QUANT_PIVOTS[name]
        for sign in (-1, 1):
            good = sign if direction == "higher_is_better" else -sign
            value = pivot + sign * span
            probe = base.model_copy(update={name: value})
            pts = calibration.pd_to_points(scorer.predict_pd(probe), scorer.base_rate)
            marker = "+" if good > 0 else "-"
            print(
                f"  {marker} {name:<30}{value:>10.2f}{pts:>8.0f}"
                f"{calibration.points_to_grade(pts):>7}"
            )

    print(
        "\nRead the spread as the formula's whole dynamic range. It is narrow on purpose:\n"
        "QUANT_PD_FLOOR/CEILING refuse to let an unfitted prior claim a borrower is a 0.1% or a\n"
        "90% risk. Phase 2 replaces this with a model fitted on a real loan tape (§3)."
    )

    try:
        from continuum.ingestion import store
        from continuum.ingestion.features import compute_features
        from continuum.orchestrator import _base_dso, data_horizon
    except Exception:
        return

    try:
        raw = store.load_raw()
        borrowers = store.load_borrowers()
    except FileNotFoundError:
        print("\n(no cohort generated; run python -m continuum.synth.generate to score one)")
        return

    as_of = data_horizon()
    print(f"\n\nCohort at {as_of.date()}:\n")
    print(f"{'borrower':<28}{'composite':>11}{'score':>7}{'grade':>7}   top driver")
    print("-" * 92)
    rows = []
    for b in borrowers:
        f = compute_features(
            raw.for_borrower(b["borrower_id"]), as_of, base_dso=_base_dso(b["borrower_id"])
        )
        result = evaluate(f)
        pts = calibration.pd_to_points(result.pseudo_pd, scorer.base_rate)
        rows.append((pts, b, result))
    for pts, b, result in sorted(rows, reverse=True, key=lambda r: r[0]):
        top = result.contributions[0]
        print(
            f"{b['name'][:27]:<28}{result.composite:>11.3f}{pts:>7.0f}"
            f"{calibration.points_to_grade(pts):>7}   {top['feature']} "
            f"{top['contribution']:+.3f}"
        )


if __name__ == "__main__":
    main()
