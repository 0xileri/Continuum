"""§5.3 — 0G Storage as the feature store.

    *"Each Borrower Feature Record (Section 6) and any synthetic evidence documents get written to
    0G Storage; the on-chain payload carries only the resulting content hash/URI, not the raw
    record."*

The split is a privacy property as much as a gas one. §11 lists data privacy as a live concern for
this product, and a merkle root commits to a borrower's financials without disclosing them into a
public registry.

**Local state is a cache, never the source of truth.** §8's stack table is explicit that 0G Storage
holds "the canonical tamper-evident copy" and the local store is "just a fast local mirror". So the
write order is: local first (so a failed upload never loses the record), then 0G, then the returned
root hash is attached to the payload. A record with no ``storage_ref`` is a record that exists only
on this machine, and the dashboard says so rather than implying otherwise.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from continuum import config
from continuum.clock import now, utc
from continuum.og.bridge import BridgeError, BridgeUnavailable, call_bridge
from continuum.schemas import BorrowerFeatureRecord, StorageRef

log = logging.getLogger(__name__)


@dataclass
class StorageResult:
    ref: StorageRef
    ok: bool
    error: str = ""
    already_stored: bool = False
    explorer_url: str = ""

    def summary(self) -> str:
        if not self.ok:
            return f"0G Storage write failed — {self.error}"
        dup = " (already stored)" if self.already_stored else ""
        return f"0G Storage {self.ref.root_hash[:18]}…{dup}"


def put_json(payload: dict, *, name: str) -> StorageResult:
    """Write one JSON document to 0G Storage and return its §6 ``storage_ref``.

    Serialised with sorted keys and no incidental whitespace, so the merkle root is a function of
    the content rather than of how it happened to be formatted. That matters here more than it
    would elsewhere: the root hash is what goes on-chain, and a record that re-serialises to a
    different root on a different machine would break every audit that starts from the registry.
    """
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{name}.json"
        path.write_text(body, encoding="utf-8")

        try:
            result = call_bridge("storage.mjs", {"action": "upload", "path": str(path)})
        except BridgeUnavailable as exc:
            if config.OG_NETWORK == "mainnet":
                raise RuntimeError(
                    "0G Storage unavailable in mainnet publish path — refusing to publish. "
                    "0G Storage is required for mainnet; local-only fallback is not acceptable."
                ) from exc
            log.warning("0G Storage unavailable, keeping the local copy only (testnet/demo mode): %s", exc)
            return StorageResult(ref=StorageRef(provider="local"), ok=False, error=str(exc))
        except BridgeError as exc:
            if config.OG_NETWORK == "mainnet":
                raise RuntimeError(
                    "0G Storage upload failed in mainnet publish path — refusing to publish. "
                    "0G Storage is required for mainnet; local-only fallback is not acceptable."
                ) from exc
            log.warning("0G Storage upload failed (testnet/demo mode): %s", exc)
            return StorageResult(ref=StorageRef(provider="local"), ok=False, error=str(exc))

    return StorageResult(
        ref=StorageRef(
            provider="0g-storage",
            root_hash=result.get("root_hash", ""),
            uri=result.get("uri", ""),
            tx_hash=result.get("tx_hash", ""),
            uploaded_at=utc(now()),
            size_bytes=int(result.get("size_bytes", len(body))),
        ),
        ok=True,
        already_stored=bool(result.get("already_stored")),
        explorer_url=result.get("explorer_url", ""),
    )


def put_feature_record(record: BorrowerFeatureRecord) -> StorageResult:
    """Write one §6 Borrower Feature Record to 0G Storage."""
    payload = json.loads(record.model_dump_json())
    return put_json(payload, name=f"feature_{record.borrower_id}_{int(utc(record.as_of).timestamp())}")


def put_documents(borrower_id: str, documents: list[dict]) -> StorageResult:
    """Write a borrower's evidence documents to 0G Storage.

    §5.3 names "any synthetic evidence documents" alongside the feature records. Ground-truth keys
    are stripped before the write for the same reason they are stripped from a prompt and from the
    API: ``_truth`` and ``scenario_tag`` are the generator's answer key, and publishing them to
    permanent, content-addressed, publicly retrievable storage would put the answers next to the
    exam forever.
    """
    clean = [
        {
            "doc_id": d["doc_id"],
            "borrower_id": d["borrower_id"],
            "doc_type": d["doc_type"],
            "title": d["title"],
            "body": d["body"],
            "created_at": d["created_at"],
            "provenance": d.get("provenance", "self_reported"),
        }
        for d in documents
    ]
    return put_json({"borrower_id": borrower_id, "documents": clean}, name=f"docs_{borrower_id}")


def fetch(root_hash: str, out_path: str | Path) -> bool:
    """Retrieve a record by root hash, verifying the merkle proof on the way back.

    ``withProof=true`` on the bridge side is not optional: verifying the proof is the entire reason
    the root hash is the identifier. Without it this would be an ordinary download from a URL that
    happens to look like a hash.
    """
    try:
        call_bridge(
            "storage.mjs",
            {"action": "download", "root_hash": root_hash, "path": str(out_path)},
        )
        return True
    except BridgeError as exc:
        log.warning("0G Storage fetch failed for %s: %s", root_hash, exc)
        return False


def explorer_url(ref: StorageRef) -> str:
    """Link to the 0G Storage explorer for a stored record. §10 wants these on screen."""
    if ref.provider != "0g-storage" or not ref.root_hash:
        return ""
    return f"{config.og()['storage_explorer']}/tx/{ref.root_hash}"
