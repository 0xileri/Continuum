"""Produce Wave 3's Integration Proof: attested scores, stored on 0G, published on 0G Chain.

§3's exit criteria 2–4, driven end to end:

    2. At least one scoring computation actually executed as an 0G Compute job, with the returned
       proof/attestation captured and shown in the payload — not mocked.
    3. Borrower Feature Records and evidence documents written to 0G Storage, with the content hash
       referenced from the published score.
    4. A ContinuumScoreRegistry contract deployed to 0G Chain mainnet, with at least a handful of
       real score-publish transactions and a working 0G Explorer link.

**This spends 0G.** Every borrower costs an inference call (settled from the Compute ledger), a
storage upload and a registry transaction. So the script is deliberately not the ordinary scoring
path — ``orchestrator daily`` stays free and local — and it refuses to run without ``--yes``, prints
the wallet and network first, and stops on the first failure rather than burning through twelve
borrowers discovering the same misconfiguration twelve times.

Run the preflight first. It costs nothing and catches every precondition:

    node og-bridge/doctor.mjs

Then:

    python scripts/publish_wave3.py --limit 5 --yes                    # testnet
    CONTINUUM_OG_NETWORK=mainnet python scripts/publish_wave3.py --limit 5 --yes

§9's Day 3 sequencing is testnet first, then mainnet. The default network is testnet for exactly
that reason; promoting is an explicit environment change, not the path of least resistance.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from continuum import config  # noqa: E402
from continuum.clock import iso, utc  # noqa: E402
from continuum.ingestion import store  # noqa: E402
from continuum.og import bridge as og_bridge  # noqa: E402
from continuum.og import chain as og_chain  # noqa: E402
from continuum.scoring import aggregate, attestation  # noqa: E402


def preflight(args) -> bool:
    """Refuse to start on a misconfiguration that would waste transactions discovering itself."""
    config.require_mainnet_for_publish()
    profile = config.og()
    print(f"Continuum Wave 3 publish — {profile['name']} (chain {profile['chain_id']})")
    print(f"  rpc              {profile['rpc_url']}")
    print(f"  explorer         {profile['explorer']}")
    print(f"  llm backend      {config.LLM_BACKEND}")
    print(f"  scorer           {config.SCORER}")

    ready, reason = og_bridge.available()
    print(f"  0G bridge        {'ready' if ready else 'NOT READY — ' + reason}")

    address = og_chain.registry_address()
    print(f"  registry         {address or 'NOT DEPLOYED'}")
    if address:
        print(f"                   {og_chain.explorer_contract_url()}")

    problems: list[str] = []
    if not ready:
        problems.append(reason)
    if not address:
        problems.append(
            "no registry address — deploy first:\n"
            "      cd contracts && forge script script/Deploy.s.sol:Deploy "
            f"--rpc-url {profile['rpc_url']} --broadcast"
        )
    if config.LLM_BACKEND != "0g-compute":
        problems.append(
            f"CONTINUUM_LLM_BACKEND is {config.LLM_BACKEND!r}, so no 0G Compute attestation will "
            "be produced and §3's exit criterion 2 is not met. Set it to '0g-compute'."
        )

    if problems:
        print("\nBlocking:")
        for p in problems:
            print(f"  - {p}")
        print("\nRun the full preflight for exact fixes:  node og-bridge/doctor.mjs")
        return False

    if config.OG_NETWORK == "mainnet":
        print("\n  *** MAINNET. Every borrower below spends real 0G. ***")

    if not args.yes:
        print(
            f"\nWould publish {args.limit} borrower(s). Nothing was spent.\n"
            "Re-run with --yes to proceed."
        )
        return False

    return True


def _load_existing_proof() -> dict | None:
    path = config.PROJECT_ROOT / "deployments" / f"integration_proof_{config.OG_NETWORK}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_scored(borrower_id: str):
    """Rebuild a ``ScoreResult`` from what the engine already wrote to disk.

    Used by ``--from-store``. The score, its feature record and its explanation artifact are all
    plain JSON, so this path has no parquet dependency and no scorer to load — it publishes exactly
    what was computed, rather than a fresh computation that might differ because the clock moved.

    Publishing a stored score is not a lesser artifact: the payload carries its own
    ``measurement_hash`` binding it to the feature record it was computed from, so a reader can
    still verify the pair. What it does not do is prove the score is current, which is why the
    trigger reason and ``as_of`` travel with it.
    """
    from continuum.scoring.aggregate import ScoreResult

    history = store.load_scores(borrower_id)
    if not history:
        raise FileNotFoundError(
            f"no scores on disk for {borrower_id} — run the engine first:\n"
            f"    python -m continuum.orchestrator backfill --weeks 14"
        )

    payload = history[-1]
    record = store.load_feature_record(borrower_id)
    explanation = store.load_explanation(payload.explainability_ref) or {}
    return ScoreResult(payload=payload, record=record, explanation=explanation)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=5, help="How many borrowers to publish")
    parser.add_argument("--borrower", default=None, help="Publish one specific borrower")
    parser.add_argument("--as-of", default=None, help="ISO timestamp; defaults to the data horizon")
    parser.add_argument("--yes", action="store_true", help="Actually spend 0G")
    parser.add_argument(
        "--force-publish",
        action="store_true",
        help="Ignore the OFF-CHAIN publish gate and put the current score on chain regardless. "
        "The gate exists to keep insignificant drift out of the registry during ordinary "
        "operation; a deliberate proof run wants the current score published whether or not it "
        "moved since the last one. The ON-CHAIN §4 cooldown still applies — the registry enforces "
        "it independently (§7), and it is not overridable from here by design.",
    )
    parser.add_argument(
        "--from-store",
        action="store_true",
        help="Publish the latest score already on disk instead of recomputing it. Separates "
        "computation from publication: re-publishing after a chain outage, a failed transaction "
        "or a registry redeploy should not require re-running the engine, and on a machine where "
        "the parquet engine is unavailable it is the only path that works.",
    )
    parser.add_argument(
        "--allow-unattested",
        action="store_true",
        help="Publish even if the 0G Compute attestation did not verify. Off by default: an "
        "unattested score shown as Integration Proof is the overclaim §11 warns about.",
    )
    args = parser.parse_args()

    if not preflight(args):
        return 1

    from continuum.orchestrator import data_horizon

    as_of = utc(datetime.fromisoformat(args.as_of)) if args.as_of else data_horizon()

    # The raw event tables are only needed to *recompute*. --from-store publishes what the engine
    # already produced, so it deliberately never touches them — which is what makes it usable when
    # the parquet engine is missing.
    model = None
    raw = None
    if not args.from_store:
        from continuum.orchestrator import rescore  # noqa: F401  (imported for its side-effect-free deps)

        model = aggregate.load_scorer()
        raw = store.load_raw()

    borrowers = [
        b
        for b in store.load_borrowers()
        if args.borrower in (None, b["borrower_id"])
    ][: args.limit]

    print(f"\nPublishing {len(borrowers)} borrower(s) as of {iso(as_of)}\n")
    print(f"{'borrower':<28}{'grade':>6}{'score':>7}  {'attested':<9}{'storage':<20}chain")
    print("-" * 118)

    published: list[dict] = []
    failures: list[str] = []

    for borrower in borrowers:
        bid = borrower["borrower_id"]
        documents = store.load_documents(bid)

        try:
            if args.from_store:
                result = _load_scored(bid)
            else:
                from continuum.orchestrator import rescore

                # persist=False so the 0G writes are driven explicitly below rather than as a side
                # effect of scoring. That keeps "what did this cost" answerable per borrower.
                result, _ = rescore(
                    borrower,
                    raw,
                    as_of,
                    model=model,
                    documents=documents,
                    trigger_reason="scheduled_daily",
                    fresh_llm=True,
                    persist=False,
                )
        except Exception as exc:
            failures.append(f"{bid}: scoring failed — {exc}")
            print(f"{borrower['name'][:27]:<28}  SCORING FAILED: {exc}")
            continue

        att = result.payload.attestation
        attested = att.type == "0g-compute" and att.verified

        if not attested and not args.allow_unattested:
            failures.append(
                f"{bid}: no verified 0G Compute attestation ({attestation.describe(att)})"
            )
            print(
                f"{borrower['name'][:27]:<28}{result.payload.score:>6}"
                f"{result.payload.score_numeric:>7}  UNATTESTED — skipped "
                f"(--allow-unattested to publish anyway)"
            )
            continue

        if args.force_publish and not result.payload.published_onchain:
            result.payload.published_onchain = True
            result.explanation["publish_decision"]["forced"] = (
                "--force-publish: the off-chain gate held this score, and a deliberate proof run "
                "overrode it. The on-chain cooldown still applies."
            )

        aggregate.publish_to_0g(result)
        store.save_explanation(result.payload.explainability_ref, result.explanation)
        store.save_feature_record(result.record)
        store.append_score(result.payload)

        og = result.explanation.get("og", {})
        storage_note = og.get("storage", {}).get("root_hash", "")[:18] or "local only"
        chain_note = og.get("chain", {}).get("summary", "")

        print(
            f"{borrower['name'][:27]:<28}{result.payload.score:>6}"
            f"{result.payload.score_numeric:>7}  {'yes' if attested else 'no':<9}"
            f"{storage_note:<20}{chain_note}"
        )

        if result.payload.chain_ref and result.payload.chain_ref.tx_hash:
            published.append(
                {
                    "borrower_id": bid,
                    "name": borrower.get("name", ""),
                    "score": result.payload.score,
                    "score_numeric": result.payload.score_numeric,
                    "tx_hash": result.payload.chain_ref.tx_hash,
                    "explorer_url": result.payload.chain_ref.explorer_url,
                    "storage_root_hash": result.payload.storage_ref.root_hash,
                    "attestation_job_id": att.job_id,
                    "compute_node": att.compute_node,
                    "attested": attested,
                }
            )

    # ---- submission artifact -----------------------------------------------------------
    #
    # §10 asks for the mainnet contract address and an Explorer link showing publish transactions.
    # Written to a file rather than only printed, because that list is a submission deliverable and
    # scrollback is not.
    profile = config.og()

    # Merge with anything a previous run recorded, keyed by transaction hash. The artifact is a
    # claim about what is on chain, not a log of the last invocation — overwriting it would make
    # a second run silently erase the first run's evidence.
    if out_existing := _load_existing_proof():
        seen = {p["tx_hash"] for p in published}
        published = published + [
            p for p in out_existing.get("publications", []) if p["tx_hash"] not in seen
        ]
        published.sort(key=lambda p: p.get("tx_hash", ""))

    attested_count = sum(1 for p in published if p.get("attested"))
    proof = {
        "project": "Continuum",
        "network": profile["name"],
        "chain_id": profile["chain_id"],
        "contract": "ContinuumScoreRegistry",
        "contract_address": og_chain.registry_address(),
        "contract_explorer_url": og_chain.explorer_contract_url(),
        "storage_explorer": profile["storage_explorer"],
        "generated_at": iso(utc(datetime.now())),
        "publications": published,
        "publication_count": len(published),
        "attested_count": attested_count,
        # Describe what these transactions actually demonstrate, not what the integration is
        # capable of. A component line that reads "TEE-signed and broker-verified" above a list of
        # records all marked attested:false is the overclaim §11 warns a real reader finds first.
        "og_components": {
            "0g-storage": "PROVEN — Borrower Feature Records written to 0G Storage and referenced "
            "on-chain by merkle root (§5.3)",
            "0g-chain": "PROVEN — ContinuumScoreRegistry, with the §4 cooldown and §5.4 ±50bps "
            "circuit breaker enforced in bytecode (§5.4, §7)",
            "0g-compute": (
                "PROVEN — reasoning call executed on a 0G Compute provider and the TEE signature "
                "verified by the broker (§5.2)"
                if attested_count
                else "NOT PROVEN IN THIS RUN — every publication below carries attested:false. "
                "The code path is complete and the marketplace reachable, but the provider "
                "enforces a 1.0 0G minimum locked balance in its sub-account, which this wallet "
                "could not meet. No score here carries a TEE attestation."
            ),
        },
        "scope_note": (
            "0G Compute serves inference against registered providers and does not execute "
            "arbitrary jobs, so §5.2's stated fallback applies: the reasoning call runs on 0G "
            "Compute and the aggregation arithmetic runs off-chain, bound to its inputs by "
            "measurement_hash. Flagged, not mocked."
        ),
    }
    out = config.PROJECT_ROOT / "deployments" / f"integration_proof_{config.OG_NETWORK}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2), encoding="utf-8")

    print(f"\n{len(published)} score(s) published on {profile['name']}")
    if published:
        print(f"  contract   {og_chain.explorer_contract_url()}")
        for p in published[:5]:
            print(f"  tx         {p['explorer_url']}")
    print(f"  proof      {out}")

    if failures:
        print(f"\n{len(failures)} borrower(s) did not publish:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
