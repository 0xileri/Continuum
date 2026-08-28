"""Feature store and score log. ASSUMPTIONS #2 and #12.

§14 recommends Postgres + TimescaleDB + pgvector; Phase 0 uses local Parquet/JSONL behind this
interface. Every read and write in the engine goes through here, so swapping in a real database
is a change to this one module — not a refactor of the scoring path.

Two persistence patterns, deliberately different:

- **Feature records** are a time-series table, keyed ``(borrower_id, as_of)``, overwritable.
- **Score publications** are an append-only log per borrower (ASSUMPTIONS #12). §11's dispute
  flow needs an immutable record of what was published, when, and why — including scores later
  superseded by an appeal. Nothing in this module can overwrite a published score.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from continuum import config
from continuum.clock import iso, utc
from continuum.schemas import BorrowerFeatureRecord, ScorePublicationPayload

# --------------------------------------------------------------------------------------
# Raw event tables
# --------------------------------------------------------------------------------------


def load_raw():
    """Load the raw event tables produced by ``continuum.synth.generate``."""
    from continuum.ingestion.features import RawData
    from continuum.synth.profiles import PAYERS_BY_ID

    inv = pd.read_parquet(config.RAW_DIR / "invoices.parquet")
    bank = pd.read_parquet(config.RAW_DIR / "bank_transactions.parquet")
    rep = pd.read_parquet(config.RAW_DIR / "repayments.parquet")
    feeds = pd.read_parquet(config.RAW_DIR / "feed_events.parquet")

    # Static payer health snapshot. A real deployment would source this from a payer-side
    # credit feed (§6 "payer-side credit signal"); here it comes from the payer universe.
    payer_health = {p.payer_id: p.base_health for p in PAYERS_BY_ID.values()}

    return RawData(inv, bank, rep, feeds, payer_health)


def load_borrowers() -> list[dict]:
    with open(config.RAW_DIR / "borrowers.json", encoding="utf-8") as fh:
        return json.load(fh)


def load_documents(borrower_id: str | None = None) -> list[dict]:
    """Documents for the Claude agent. ``_truth`` keys are ground truth for evaluation only."""
    path = config.RAW_DIR / "documents.jsonl"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            if borrower_id is None or d["borrower_id"] == borrower_id:
                out.append(d)
    return out


# --------------------------------------------------------------------------------------
# Feature records
# --------------------------------------------------------------------------------------


def save_feature_record(record: BorrowerFeatureRecord) -> Path:
    """Persist one §9 Borrower Feature Record as JSON."""
    path = config.FEATURES_DIR / f"{record.borrower_id}.json"
    payload = json.loads(record.model_dump_json())
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return path


def load_feature_record(borrower_id: str) -> BorrowerFeatureRecord | None:
    path = config.FEATURES_DIR / f"{borrower_id}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return BorrowerFeatureRecord.model_validate(json.load(fh))


def save_feature_matrix(df: pd.DataFrame) -> Path:
    """Training matrix: one row per (borrower_id, as_of) observation."""
    path = config.FEATURES_DIR / "training_matrix.parquet"
    df.to_parquet(path, index=False)
    return path


def load_feature_matrix() -> pd.DataFrame:
    return pd.read_parquet(config.FEATURES_DIR / "training_matrix.parquet")


# --------------------------------------------------------------------------------------
# Score publication log — append-only (ASSUMPTIONS #12)
# --------------------------------------------------------------------------------------


def append_score(payload: ScorePublicationPayload) -> Path:
    """Append to the immutable per-borrower score log. Never overwrites."""
    path = config.SCORES_DIR / f"{payload.borrower_id}.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(payload.model_dump_json() + "\n")
    return path


def load_scores(borrower_id: str) -> list[ScorePublicationPayload]:
    """Full published history for one borrower, oldest first."""
    path = config.SCORES_DIR / f"{borrower_id}.jsonl"
    if not path.exists():
        return []
    out: list[ScorePublicationPayload] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(ScorePublicationPayload.model_validate_json(line))
    return sorted(out, key=lambda p: utc(p.published_at))


def latest_score(borrower_id: str) -> ScorePublicationPayload | None:
    scores = load_scores(borrower_id)
    return scores[-1] if scores else None


def clear_scores(borrower_id: str | None = None) -> None:
    """Reset the score log. Demo/dev convenience — an append-only log has no other way back.

    Deliberately explicit rather than folded into the scoring path, so no ordinary run can
    destroy publication history.
    """
    targets = (
        [config.SCORES_DIR / f"{borrower_id}.jsonl"]
        if borrower_id
        else list(config.SCORES_DIR.glob("*.jsonl"))
    )
    for p in targets:
        p.unlink(missing_ok=True)


# --------------------------------------------------------------------------------------
# Explainability artifacts (§7 "Explainability is not optional")
# --------------------------------------------------------------------------------------


def save_explanation(ref: str, explanation: dict) -> Path:
    path = config.EXPLAIN_DIR / f"{ref}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(explanation, fh, indent=2)
    return path


def load_explanation(ref: str) -> dict | None:
    path = config.EXPLAIN_DIR / f"{ref}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------------------
# Disputes (§11) — ASSUMPTIONS #13
# --------------------------------------------------------------------------------------


def append_dispute(borrower_id: str, dispute: dict) -> Path:
    path = config.DISPUTES_DIR / f"{borrower_id}.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dispute) + "\n")
    return path


def load_disputes(borrower_id: str) -> list[dict]:
    path = config.DISPUTES_DIR / f"{borrower_id}.jsonl"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
