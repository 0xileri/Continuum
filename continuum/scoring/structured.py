"""§7 part 1 — the structured cash-flow model.

A gradient-boosted tree model over the Layer 1 feature store, producing a probability of
deterioration and a per-feature attribution for every prediction.

Two design points worth stating explicitly:

**LightGBM, not XGBoost.** §14 names either. LightGBM ships native TreeSHAP through
``predict(..., pred_contrib=True)``, which removes the ``shap`` package (and its numba/llvmlite
toolchain) from the dependency set for identical numbers on tree models. §7 asks for "SHAP values
or equivalent" — these are the SHAP values, computed by the booster itself. See ASSUMPTIONS #1.

**The model is deliberately small.** 12 borrowers and 636 observations cannot support a
credit model of any real capacity, and pretending otherwise is exactly the trap §17 flags under
"cold-start data problem". Depth, leaf count and minimum leaf size are all clamped hard, and
``train.py`` reports borrower-grouped cross-validated metrics rather than in-sample ones, so the
number you read is the number the model would have earned on borrowers it had never seen.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from continuum import config
from continuum.ingestion.features import MODEL_FEATURES
from continuum.schemas import BorrowerFeatures

MODEL_PATH = config.MODELS_DIR / "structured.txt"
META_PATH = config.MODELS_DIR / "structured.meta.json"

# Hyperparameters. Chosen for a tiny, heavily autocorrelated dataset: shallow trees, large
# minimum leaves, aggressive column and row subsampling. A deeper model fits the 12 borrowers
# rather than the risk process, and the grouped CV in train.py makes that visible immediately.
PARAMS: dict = {
    "objective": "binary",
    "metric": ["binary_logloss", "auc"],
    "first_metric_only": True,
    "learning_rate": 0.05,
    "num_leaves": 8,
    "max_depth": 3,
    "min_child_samples": 30,
    "feature_fraction": 0.70,
    "bagging_fraction": 0.80,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "verbose": -1,
    "seed": config.RANDOM_SEED,
    "deterministic": True,
    "force_row_wise": True,
}
"""``first_metric_only`` makes early stopping watch logloss, not AUC. With only two or three
held-out borrowers per fold, validation AUC is a step function over a handful of distinct
predictions and stops improving almost immediately; logloss keeps moving and gives the booster
room to reach a sensible number of rounds."""

MAX_ROUNDS = 400
EARLY_STOPPING = 40


@dataclass
class StructuredModel:
    """A trained booster plus everything needed to reproduce and audit its outputs."""

    booster: lgb.Booster
    feature_names: tuple[str, ...]
    model_version: str
    artifact_sha256: str
    n_train_rows: int
    n_train_borrowers: int
    cv_metrics: dict = field(default_factory=dict)
    base_rate: float = 0.0
    feature_ranges: dict = field(default_factory=dict)
    """Per-feature ``[p01, p99]`` over the training matrix, used by ``novelty()``.

    Kept in the model artifact rather than recomputed from the training data at scoring time,
    because the aggregator must not need the training set to score a borrower — the two run in
    different places the moment this leaves Phase 0."""

    # ---- inference -------------------------------------------------------------------

    def _row(self, features: BorrowerFeatures) -> pd.DataFrame:
        values = {k: float(getattr(features, k)) for k in self.feature_names}
        return pd.DataFrame([values], columns=list(self.feature_names))

    def predict_pd(self, features: BorrowerFeatures) -> float:
        """Probability the borrower deteriorates over the outcome horizon."""
        x = self._row(features)
        return float(self.booster.predict(x)[0])

    def predict_pd_batch(self, x: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.booster.predict(x[list(self.feature_names)]), dtype=float)

    def novelty(self, features: BorrowerFeatures) -> tuple[float, list[str]]:
        """Share of features outside their training range, and which ones.

        A tree model extrapolates by not extrapolating: outside the range it saw, every leaf
        boundary has already been crossed and the prediction flattens. That is a safe failure mode
        for the point estimate and a dangerous one for the confidence attached to it, because the
        model looks equally sure about a borrower unlike anything in its training set. §7 asks for
        a confidence interval rather than a point estimate; this is the term that makes the interval
        respond to *this* borrower rather than widening everyone equally.

        Returns ``(share, offending_feature_names)``. Empty ranges give ``(0.0, [])`` rather than
        an error, so a model trained before this field existed still scores.
        """
        if not self.feature_ranges:
            return 0.0, []
        outside: list[str] = []
        for name in self.feature_names:
            bounds = self.feature_ranges.get(name)
            if not bounds:
                continue
            lo, hi = float(bounds[0]), float(bounds[1])
            value = float(getattr(features, name))
            if value < lo or value > hi:
                outside.append(name)
        return len(outside) / max(len(self.feature_names), 1), outside

    def attribute(self, features: BorrowerFeatures) -> list[dict]:
        """TreeSHAP attribution for one prediction, largest absolute contribution first.

        Contributions are in log-odds space (the booster's raw output space), which is where
        they sum exactly to the prediction. §7 requires this be "available on request" — it is
        persisted per score by the aggregator and surfaced on the dashboard.
        """
        x = self._row(features)
        contrib = np.asarray(self.booster.predict(x, pred_contrib=True), dtype=float)[0]
        # LightGBM appends the expected-value (base) term as the final column.
        base, feature_contrib = float(contrib[-1]), contrib[:-1]

        out = [
            {
                "feature": name,
                "value": float(getattr(features, name)),
                "contribution": float(c),
                "direction": "increases_risk" if c > 0 else "decreases_risk",
            }
            for name, c in zip(self.feature_names, feature_contrib)
        ]
        out.sort(key=lambda d: abs(d["contribution"]), reverse=True)

        total = float(feature_contrib.sum())
        for d in out:
            d["share_of_total_abs"] = (
                round(abs(d["contribution"]) / float(np.abs(feature_contrib).sum()), 4)
                if np.abs(feature_contrib).sum() > 0
                else 0.0
            )

        return [{"_base_log_odds": round(base, 6), "_feature_sum_log_odds": round(total, 6)}] + out

    # ---- persistence -----------------------------------------------------------------

    def save(self) -> Path:
        self.booster.save_model(str(MODEL_PATH))
        meta = {
            "feature_names": list(self.feature_names),
            "model_version": self.model_version,
            "artifact_sha256": self.artifact_sha256,
            "n_train_rows": self.n_train_rows,
            "n_train_borrowers": self.n_train_borrowers,
            "cv_metrics": self.cv_metrics,
            "base_rate": self.base_rate,
            "feature_ranges": self.feature_ranges,
            "params": PARAMS,
        }
        with open(META_PATH, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        return MODEL_PATH

    @classmethod
    def load(cls) -> "StructuredModel":
        if not MODEL_PATH.exists() or not META_PATH.exists():
            raise FileNotFoundError(
                f"No trained model at {MODEL_PATH}. Run: python -m continuum.scoring.train"
            )
        with open(META_PATH, encoding="utf-8") as fh:
            meta = json.load(fh)
        booster = lgb.Booster(model_file=str(MODEL_PATH))
        return cls(
            booster=booster,
            feature_names=tuple(meta["feature_names"]),
            model_version=meta["model_version"],
            artifact_sha256=meta["artifact_sha256"],
            n_train_rows=meta["n_train_rows"],
            n_train_borrowers=meta["n_train_borrowers"],
            cv_metrics=meta.get("cv_metrics", {}),
            base_rate=meta.get("base_rate", 0.0),
            feature_ranges=meta.get("feature_ranges", {}),
        )


def training_feature_ranges(x: pd.DataFrame) -> dict[str, list[float]]:
    """Per-feature ``[p01, p99]`` over the training matrix.

    Percentiles rather than min/max: with 636 autocorrelated observations the extremes are single
    weeks, and a novelty flag that fires whenever a borrower beats the one best week ever recorded
    is not measuring novelty.
    """
    return {
        c: [float(np.percentile(x[c], 1)), float(np.percentile(x[c], 99))]
        for c in x.columns
    }


def artifact_digest(booster: lgb.Booster) -> str:
    """sha256 over the serialised booster.

    This is the value that goes into the §9 attestation's ``measurement_hash``. It proves which
    model artifact produced a score — it is *not* a proof that the model ran honestly, which is
    §8's distinction and the reason ASSUMPTIONS #11 records the Phase 0 attestation type as
    ``none`` rather than dressing this up as a TEE measurement.
    """
    return hashlib.sha256(booster.model_to_string().encode("utf-8")).hexdigest()


def fit(
    x: pd.DataFrame, y: np.ndarray, num_boost_round: int, valid: tuple | None = None
) -> lgb.Booster:
    """Fit one booster. Split out so CV folds and the final model share exact configuration."""
    train_set = lgb.Dataset(x[list(MODEL_FEATURES)], label=y, free_raw_data=False)
    callbacks = []
    valid_sets = None
    if valid is not None:
        vx, vy = valid
        valid_sets = [lgb.Dataset(vx[list(MODEL_FEATURES)], label=vy, reference=train_set)]
        callbacks.append(lgb.early_stopping(EARLY_STOPPING, verbose=False))

    return lgb.train(
        PARAMS,
        train_set,
        num_boost_round=num_boost_round,
        valid_sets=valid_sets,
        callbacks=callbacks,
    )
