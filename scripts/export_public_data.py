"""Export the published record to ``data/public/`` for a read-only deployment.

The deployed dashboard needs data, and `data/` is gitignored — generated artifacts do not belong in
a repo. But the *published record* is a different thing from the generator's working files: the
score log, the feature records and the explanation artifacts are precisely what this product claims
anyone should be able to audit. Committing those is consistent with the claim.

Two things are stripped on the way out, both for the same reason ``api.public_document`` uses an
allowlist:

``_truth`` and ``scenario_tag`` are the generator's answer key — which borrower was built to
default, which document was built to contain a breach. Publishing them next to the agent's flags
would turn the explainability trail into a marking scheme, and a demo where the model is graded
against something the repository already discloses proves nothing.

The raw event tables (``*.parquet``) are not exported at all. Nothing on the read path opens them —
``store.load_raw`` is reachable only from the dispute endpoint, which a read-only instance closes —
so shipping them would add megabytes and a parquet engine to an image that needs neither.

Run:
    python scripts/export_public_data.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from continuum import config  # noqa: E402

PUBLIC = config.PROJECT_ROOT / "data" / "public"

# Present in the generator's documents, absent from anything a reader should see.
STRIP = ("_truth", "scenario_tag", "source")


def export() -> dict[str, int]:
    counts: dict[str, int] = {}

    for sub in ("raw", "scores", "features", "explain"):
        (PUBLIC / sub).mkdir(parents=True, exist_ok=True)

    # ---- roster ---------------------------------------------------------------------
    borrowers = json.loads((config.RAW_DIR / "borrowers.json").read_text(encoding="utf-8"))
    # `archetype` stays: it is the scenario label the dashboard already displays next to each
    # borrower, so it is disclosed either way and hiding it here would only make the export
    # inconsistent with the running UI.
    (PUBLIC / "raw" / "borrowers.json").write_text(
        json.dumps(borrowers, indent=2), encoding="utf-8"
    )
    counts["borrowers"] = len(borrowers)

    # ---- documents, with the answer key removed ---------------------------------------
    src = config.RAW_DIR / "documents.jsonl"
    kept = 0
    with (PUBLIC / "raw" / "documents.jsonl").open("w", encoding="utf-8") as out:
        for line in src.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            doc = {k: v for k, v in json.loads(line).items() if k not in STRIP}
            out.write(json.dumps(doc) + "\n")
            kept += 1
    counts["documents"] = kept

    # ---- the published record ---------------------------------------------------------
    for sub, pattern in (("scores", "*.jsonl"), ("features", "*.json"), ("explain", "*.json")):
        source_dir = config.DATA_DIR / sub
        n = 0
        for path in sorted(source_dir.glob(pattern)):
            shutil.copy2(path, PUBLIC / sub / path.name)
            n += 1
        counts[sub] = n

    # An empty disputes directory, so a read-only instance does not 500 looking for one.
    (PUBLIC / "disputes").mkdir(parents=True, exist_ok=True)
    (PUBLIC / "models").mkdir(parents=True, exist_ok=True)

    return counts


def verify() -> list[str]:
    """Fail loudly if any stripped key survived. This is the whole point of the script."""
    problems: list[str] = []
    text = (PUBLIC / "raw" / "documents.jsonl").read_text(encoding="utf-8")
    for key in STRIP:
        if f'"{key}"' in text:
            problems.append(f"{key} still present in exported documents")

    # Explanations embed the feature record and the llm_flags; neither should carry truth keys,
    # but the check is cheap and this file is about to become public.
    for path in (PUBLIC / "explain").glob("*.json"):
        body = path.read_text(encoding="utf-8")
        for key in ("_truth", "scenario_tag"):
            if f'"{key}"' in body:
                problems.append(f"{key} present in {path.name}")
                break
    return problems


def main() -> int:
    if not (config.RAW_DIR / "borrowers.json").exists():
        print("No cohort on disk. Run: python -m continuum.synth.generate")
        return 1

    counts = export()
    problems = verify()

    print(f"Exported the published record to {PUBLIC.relative_to(config.PROJECT_ROOT)}\n")
    for name, n in counts.items():
        print(f"  {name:<12}{n:>5}")

    total = sum(f.stat().st_size for f in PUBLIC.rglob("*") if f.is_file())
    print(f"\n  {total / 1_048_576:.1f} MB")

    if problems:
        print("\nREFUSING TO SHIP — the export still contains generator ground truth:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\n  no ground-truth keys present; safe to commit")
    print("  the container reads this via CONTINUUM_DATA_DIR=/app/data/public")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
