"""Train and backtest the §7 structured model.

Run:  python -m continuum.scoring.train

**Cross-validation is grouped by borrower, not random.** Observations are 7 days apart, so
consecutive rows for one borrower are near-duplicates; a random split puts a borrower's own
neighbouring weeks on both sides of the fold boundary and reports an AUC that is mostly
memorisation. Grouping by borrower measures the only thing a lender cares about — how the model
behaves on a borrower it has never scored before.

The metrics printed here are out-of-fold. Read them as a smoke test on a 12-borrower synthetic
cohort, not as evidence of predictive power: §17's cold-start problem is real and unresolved, and
calibration against actual defaults is a Phase 3 exercise with a design partner's loan tape.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from continuum import config
from continuum.ingestion import store
from continuum.ingestion.features import MODEL_FEATURES
from continuum.scoring.structured import (
    MAX_ROUNDS,
    StructuredModel,
    artifact_digest,
    fit,
    training_feature_ranges,
)

N_SPLITS = 4


def borrower_folds(df: pd.DataFrame, n_splits: int = N_SPLITS) -> list[list[str]]:
    """Assign whole borrowers to folds so every fold's validation set contains positives.

    ``StratifiedGroupKFold`` is the obvious tool and it does not work here. Deterioration is a
    borrower-level property in this cohort — 5 of the 12 borrowers never go bad at all — so
    stratifying on the observation label cannot rescue a split that happens to hold out three
    all-clean borrowers. When that happens the fold has no positives, AUC is undefined, early
    stopping halts at round 1, and those borrowers' out-of-fold predictions come back as a flat
    base rate that quietly destroys every aggregate metric.

    So stratify at the level the label actually lives: split borrowers into "ever bad" and "never
    bad", sort each stratum by positive rate, and deal round-robin. Dealing in sorted order also
    spreads severity, so no single fold holds every severe case.
    """
    rates = (
        df.groupby("borrower_id")["label_bad_90d"].mean().sort_values(ascending=False)
    )
    ever_bad = [b for b, r in rates.items() if r > 0]
    never_bad = [b for b, r in rates.items() if r == 0]

    folds: list[list[str]] = [[] for _ in range(n_splits)]
    for stratum in (ever_bad, never_bad):
        for i, borrower in enumerate(stratum):
            folds[i % n_splits].append(borrower)
    return folds


def _ks(y: np.ndarray, p: np.ndarray) -> float:
    """Kolmogorov-Smirnov separation. Standard credit-risk readout alongside AUC."""
    pos, neg = np.sort(p[y == 1]), np.sort(p[y == 0])
    if not len(pos) or not len(neg):
        return float("nan")
    grid = np.sort(np.unique(p))
    cdf_pos = np.searchsorted(pos, grid, side="right") / len(pos)
    cdf_neg = np.searchsorted(neg, grid, side="right") / len(neg)
    return float(np.max(np.abs(cdf_pos - cdf_neg)))


def main() -> None:
    df = store.load_feature_matrix().sort_values(["borrower_id", "as_of"]).reset_index(drop=True)
    x = df[list(MODEL_FEATURES)]
    y = df["label_bad_90d"].to_numpy(dtype=int)

    n_borrowers = df["borrower_id"].nunique()
    print(f"Training on {len(df)} observations across {n_borrowers} borrowers")
    print(f"  features: {len(MODEL_FEATURES)}   positive rate: {y.mean():.1%}")
    print(f"  CV: {N_SPLITS} folds, whole borrowers held out, stratified on ever-bad\n")

    oof = np.full(len(df), np.nan)
    best_iters: list[int] = []
    fold_aucs: list[float] = []

    for k, held in enumerate(borrower_folds(df, N_SPLITS), start=1):
        va = df.index[df["borrower_id"].isin(held)].to_numpy()
        tr = df.index[~df["borrower_id"].isin(held)].to_numpy()

        booster = fit(x.iloc[tr], y[tr], MAX_ROUNDS, valid=(x.iloc[va], y[va]))
        best = booster.best_iteration or MAX_ROUNDS
        best_iters.append(best)
        oof[va] = booster.predict(x.iloc[va][list(MODEL_FEATURES)], num_iteration=best)

        fold_auc = roc_auc_score(y[va], oof[va]) if len(set(y[va])) > 1 else float("nan")
        if np.isfinite(fold_auc):
            fold_aucs.append(float(fold_auc))
        print(
            f"  fold {k}: train={len(tr):>3} valid={len(va):>3} "
            f"pos_valid={y[va].mean():>5.1%} rounds={best:>3} auc={fold_auc:.3f}"
        )
        print(f"          held out: {', '.join(sorted(held))}")

    # ---- Out-of-fold metrics ---------------------------------------------------------
    auc = roc_auc_score(y, oof)
    ap = average_precision_score(y, oof)
    ks = _ks(y, oof)
    auc_fold_std = float(np.std(fold_aucs)) if len(fold_aucs) > 1 else 0.0
    print("\nOut-of-fold (borrower-grouped):")
    print(f"  AUC              {auc:.3f}   (fold sd {auc_fold_std:.3f})")
    print(f"  average precision {ap:.3f}   (base rate {y.mean():.3f})")
    print(f"  KS               {ks:.3f}")

    # ---- Per-archetype behaviour -----------------------------------------------------
    # The metric that actually matters on a cohort this small: does mean predicted risk order
    # the archetypes the way the generator built them?
    df["_oof"] = oof
    print("\n  mean out-of-fold PD by archetype (should rise with severity):")
    by_arch = (
        df.groupby("archetype")
        .agg(n=("_oof", "size"), label=("label_bad_90d", "mean"), pd_mean=("_oof", "mean"))
        .sort_values("pd_mean")
    )
    for arch, r in by_arch.iterrows():
        print(f"    {arch:<18} n={int(r.n):>4}  label={r.label:>6.1%}  mean_PD={r.pd_mean:.3f}")

    print("\n  per-borrower out-of-fold PD (first vs last quarter of history):")
    for bid, g in df.groupby("borrower_id"):
        g = g.sort_values("as_of")
        q = max(1, len(g) // 4)
        early, late = g["_oof"].iloc[:q].mean(), g["_oof"].iloc[-q:].mean()
        print(
            f"    {bid}  {g['archetype'].iloc[0]:<16} "
            f"early={early:.3f}  late={late:.3f}  delta={late - early:+.3f}"
        )

    # ---- Final model on all data -----------------------------------------------------
    rounds = int(np.median(best_iters))
    print(f"\nFitting final model on all {len(df)} rows with {rounds} rounds "
          f"(median of fold best_iteration)")
    booster = fit(x, y, rounds)

    model = StructuredModel(
        booster=booster,
        feature_names=MODEL_FEATURES,
        model_version=config.MODEL_VERSION,
        artifact_sha256=artifact_digest(booster),
        n_train_rows=len(df),
        n_train_borrowers=n_borrowers,
        cv_metrics={
            "auc_oof": round(float(auc), 4),
            "average_precision_oof": round(float(ap), 4),
            "ks_oof": round(float(ks), 4),
            "auc_fold_std": round(auc_fold_std, 4),
            "n_splits": N_SPLITS,
            "cv_scheme": "whole borrowers held out, stratified on ever-bad",
            "rounds": rounds,
        },
        base_rate=round(float(y.mean()), 4),
        feature_ranges=training_feature_ranges(x),
    )
    path = model.save()

    print(f"  saved {path}")
    print(f"  artifact sha256 {model.artifact_sha256[:16]}...  version {model.model_version}")

    gains = pd.Series(
        booster.feature_importance(importance_type="gain"), index=list(MODEL_FEATURES)
    ).sort_values(ascending=False)
    print("\n  top features by gain:")
    for name, g in gains.head(8).items():
        print(f"    {name:<34}{g:>12,.1f}")
    unused = [n for n, g in gains.items() if g == 0]
    if unused:
        print(f"  unused by the final model: {unused}")

    print(
        "\nCaveat, stated because it matters: 12 synthetic borrowers cannot calibrate a credit\n"
        "model. These numbers show the pipeline is wired correctly and the features carry the\n"
        "signal the generator put there. Real calibration needs a design partner's loan tape\n"
        "(claude.md §17)."
    )
    print("\nNext: python -m continuum.scoring.llm_agent --borrower brw_01hxf2c5d9")


if __name__ == "__main__":
    main()
