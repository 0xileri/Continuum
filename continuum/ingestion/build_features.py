"""Build the as-of feature matrix used to train and backtest the structured model.

Joins each labelled observation from ``outcomes.parquet`` to features computed strictly as of
that observation's timestamp (see ``features.py`` on lookahead).

Run:  python -m continuum.ingestion.build_features
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from continuum import config
from continuum.ingestion import store
from continuum.ingestion.features import MODEL_FEATURES, compute_features, features_to_row
from continuum.synth.profiles import COHORT_BY_ID


def main() -> None:
    raw = store.load_raw()
    outcomes = pd.read_parquet(config.RAW_DIR / "outcomes.parquet")

    rows: list[dict] = []
    print(f"Computing as-of features for {len(outcomes)} labelled observations...")

    for borrower_id, group in outcomes.groupby("borrower_id"):
        br = raw.for_borrower(borrower_id)
        profile = COHORT_BY_ID.get(borrower_id)
        base_dso = profile.base_dso if profile else 40.0

        for obs in group.itertuples():
            feats = compute_features(br, obs.as_of, base_dso=base_dso)
            row = {
                "borrower_id": borrower_id,
                "as_of": obs.as_of,
                "label_bad_90d": int(obs.label_bad_90d),
                "archetype": obs.archetype,
                **features_to_row(feats),
            }
            rows.append(row)

        print(f"  {borrower_id}  {len(group):>3} observations")

    df = pd.DataFrame(rows)
    path = store.save_feature_matrix(df)

    print(f"\nWrote {len(df)} x {len(MODEL_FEATURES)} feature matrix to {path}")
    print(f"  positive rate: {df['label_bad_90d'].mean():.1%}")

    # Sanity check: any feature that is constant carries no information and usually signals a
    # derivation bug rather than a genuinely flat signal.
    const = [c for c in MODEL_FEATURES if df[c].nunique() <= 1]
    if const:
        print(f"  WARNING constant features (no signal): {const}")

    nulls = {c: int(df[c].isna().sum()) for c in MODEL_FEATURES if df[c].isna().any()}
    if nulls:
        print(f"  WARNING null features: {nulls}")

    print("\n  feature separation by label (mean | bad - good):")
    good, bad = df[df.label_bad_90d == 0], df[df.label_bad_90d == 1]
    seps = []
    for c in MODEL_FEATURES:
        sd = df[c].std()
        if sd > 0:
            seps.append((abs(bad[c].mean() - good[c].mean()) / sd, c, good[c].mean(), bad[c].mean()))
    for effect, c, g, b in sorted(seps, reverse=True)[:10]:
        print(f"    {c:<34} good={g:>12.2f}  bad={b:>12.2f}  effect={effect:.2f}sd")

    # ---- Calendar-time leakage check -------------------------------------------------
    # In this cohort all deterioration happens in the back half of the history, so any
    # feature strongly correlated with observation index lets the model learn "later = worse"
    # instead of learning credit risk. That backtests well and fails in production, which is
    # precisely the trap §17 flags around backtest credibility. Cumulative sums and
    # short-history artifacts are the usual culprits.
    df = df.sort_values(["borrower_id", "as_of"])
    df["_obs_idx"] = df.groupby("borrower_id").cumcount()
    leaky = []
    for c in MODEL_FEATURES:
        within = df.groupby("borrower_id").apply(
            lambda g, col=c: g[col].corr(g["_obs_idx"]), include_groups=False
        )
        r = float(within.mean())
        if abs(r) > 0.60:
            leaky.append((abs(r), c, r))
    print("\n  calendar-time correlation (mean within-borrower r vs observation index):")
    if leaky:
        for _, c, r in sorted(leaky, reverse=True):
            print(f"    WARNING {c:<34} r={r:+.2f}  <- likely time proxy, review derivation")
    else:
        print("    OK - no feature exceeds |r|=0.60 within borrower")

    print("\nNext: python -m continuum.scoring.train")


if __name__ == "__main__":
    main()
