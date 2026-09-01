"""§5.4 — publishing a score to ContinuumScoreRegistry on 0G Chain.

This is the step Wave 3's Integration Proof is measured on (§3, §10): a mainnet contract address,
an 0G Explorer link showing publish transactions, and payload fields that actually resolve to the
0G artifacts they name.

**The publish gate is the contract's, not this module's.** §7 puts the §4 cooldown and §5.4 circuit
breaker on-chain "so the guarantee is actually on-chain", and a client that pre-checked the cooldown
and skipped would hand the guarantee straight back to the client. So this module always attempts
the transaction and reports what the registry decided. A cooldown rejection comes back as a
structured result rather than an exception, because "the on-chain gate held this update" is a real
outcome worth recording next to the score, not an error.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from continuum import config
from continuum.og.bridge import BridgeError, BridgeUnavailable, call_bridge
from continuum.schemas import ChainRef, ScorePublicationPayload

log = logging.getLogger(__name__)

DEPLOYMENTS_DIR = config.PROJECT_ROOT / "deployments"


@dataclass
class ChainPublishResult:
    published: bool
    ref: ChainRef | None = None
    rejected_by: str = ""
    """``cooldown`` when the registry's §4 gate refused. Empty when the publish succeeded."""
    seconds_remaining: int = 0
    error: str = ""

    def summary(self) -> str:
        if self.published and self.ref:
            return f"published {self.ref.tx_hash[:18]}… on {self.ref.network}"
        if self.rejected_by == "cooldown":
            hours = self.seconds_remaining / 3600.0
            return f"held by the on-chain §4 cooldown ({hours:.1f}h remaining)"
        return f"not published — {self.error or 'unknown'}"


def registry_address() -> str:
    """The deployed registry for the active network.

    Environment first, then ``deployments/<network>.json`` written by the deploy script. Reading
    the deployment file means a fresh clone that has already deployed does not need an env var
    exported in every shell, and keeping the env override first means a promotion from testnet to
    mainnet is one variable rather than an edit to a tracked file.
    """
    if config.OG_REGISTRY_ADDRESS:
        return config.OG_REGISTRY_ADDRESS
    path = DEPLOYMENTS_DIR / f"{config.OG_NETWORK}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("address", "")
        except (json.JSONDecodeError, OSError):
            log.warning("could not read %s", path)
    return ""


def record_deployment(address: str, *, tx_hash: str = "", block: int = 0) -> Path:
    """Persist a deployment so the engine and dashboard can find the registry without an env var."""
    DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)
    profile = config.og()
    path = DEPLOYMENTS_DIR / f"{config.OG_NETWORK}.json"
    path.write_text(
        json.dumps(
            {
                "network": profile["name"],
                "chain_id": profile["chain_id"],
                "address": address,
                "tx_hash": tx_hash,
                "block_number": block,
                "explorer_url": f"{profile['explorer']}/address/{address}",
                "contract": "ContinuumScoreRegistry",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def publish_score(payload: ScorePublicationPayload) -> ChainPublishResult:
    """Publish one §6 payload to the registry. Returns what the chain decided.

    Only the fields §7's struct carries are sent — the score, its interval, the trigger reason and
    the two 0G references. The rest of the payload stays off-chain behind the 0G Storage root hash,
    per §5.3.
    """
    address = registry_address()
    if not address:
        return ChainPublishResult(
            published=False,
            error=(
                "no ContinuumScoreRegistry address for "
                f"{config.OG_NETWORK}. Deploy it first:\n"
                "    cd contracts && forge script script/Deploy.s.sol:Deploy "
                f"--rpc-url {config.og()['rpc_url']} --broadcast"
            ),
        )

    if not payload.attestation.proof_ref or not re.fullmatch(r"0x[0-9a-fA-F]{64}", payload.attestation.proof_ref):
        raise RuntimeError("invalid canonical 0G attestation proof ref: refusing to publish")
    if not payload.storage_ref.root_hash or not re.fullmatch(r"0x[0-9a-fA-F]{64}", payload.storage_ref.root_hash):
        raise RuntimeError("invalid canonical 0G storage root hash: refusing to publish")

    body = {
        "contract": address,
        "borrower_id": payload.borrower_id,
        "score_numeric": payload.score_numeric,
        "confidence_interval": list(payload.confidence_interval),
        "trigger_reason": payload.trigger_reason,
        "attestation": {
            "proof_ref": payload.attestation.proof_ref,
            "verified": payload.attestation.verified,
        },
        "storage_ref": {"root_hash": payload.storage_ref.root_hash},
    }

    try:
        result = call_bridge("publish.mjs", body)
    except BridgeUnavailable as exc:
        if config.OG_NETWORK == "mainnet" and not config.OG_ALLOW_OFFLINE_DEMO:
            raise RuntimeError(f"0G Chain bridge unavailable in mainnet publish path: {exc}") from exc
        return ChainPublishResult(published=False, error=str(exc))
    except BridgeError as exc:
        if config.OG_NETWORK == "mainnet" and not config.OG_ALLOW_OFFLINE_DEMO:
            raise RuntimeError(f"0G Chain publish failed in mainnet publish path: {exc}") from exc
        return ChainPublishResult(published=False, error=str(exc))

    if not result.get("published"):
        return ChainPublishResult(
            published=False,
            rejected_by=result.get("rejected_by", ""),
            seconds_remaining=int(result.get("seconds_remaining", 0)),
            error=result.get("note", ""),
        )

    return ChainPublishResult(
        published=True,
        ref=ChainRef(
            network=result.get("network", ""),
            chain_id=int(result.get("chain_id", 0)),
            tx_hash=result.get("tx_hash", ""),
            contract="ContinuumScoreRegistry",
            contract_address=address,
            block_number=int(result.get("block_number", 0)),
            explorer_url=result.get("explorer_url", ""),
        ),
    )


def load_integration_proof() -> dict:
    """The §10 proof artifact for the active network, or an empty shell if it has not been built.

    Written by ``og-bridge/proof.mjs`` from the registry's ``ScorePublished`` events, so it states
    what the chain holds rather than what any local score log remembers. Missing is a normal state
    — nothing has been published yet — and returns empty rather than raising, so the dashboard can
    say "none yet" instead of erroring.
    """
    path = DEPLOYMENTS_DIR / f"integration_proof_{config.OG_NETWORK}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("could not read %s: %s", path, exc)
        return {}


def explorer_contract_url() -> str:
    address = registry_address()
    return f"{config.og()['explorer']}/address/{address}" if address else ""
