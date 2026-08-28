"""§9's ``attestation`` block, built honestly for Phase 0.

§8 is blunt about why this layer exists: *"why should anyone trust that the score wasn't fabricated,
or that the AI operator isn't front-running its own borrowers?"* Phase 0 does not answer that
question. It runs on one operator's machine, with no enclave and no proof.

What this module produces is therefore a **tamper-evidence digest, not an attestation**: a hash
binding a published score to the exact model artifact and the exact input record that produced it.
That is genuinely useful — it makes silent retro-editing of a score or its inputs detectable — and it
is genuinely not what §8 asks for. The ``type`` field says ``"none"`` and the provider string says
``phase_0_offchain_no_attestation`` so no consumer can mistake one for the other.

§13's oracle-operator warning is the reason this file is written this way rather than labelled
``"tee"`` with a placeholder provider: *"be explicit about this in your own docs rather than
overclaiming trustlessness you haven't earned yet — institutional counterparties will find the
overclaim faster than retail will, and it costs you the deal."*

Phase 1 replaces ``measurement_hash`` with an enclave measurement and populates ``signature`` with
the enclave's signature over the payload. The shape here is already the shape that needs, so the
change is a new provider implementation rather than a schema migration.
"""

from __future__ import annotations

import hashlib
import json

from continuum.schemas import Attestation, BorrowerFeatureRecord

PROVIDER = "phase_0_offchain_no_attestation"


def input_digest(record: BorrowerFeatureRecord) -> str:
    """sha256 over the canonicalised feature record.

    Canonical JSON — sorted keys, no whitespace — so the digest is a function of the *content*
    rather than of serialisation order. Without that, re-serialising the same record with a
    different key order produces a different hash and the audit trail becomes noise.
    """
    payload = json.loads(record.model_dump_json())
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build(
    record: BorrowerFeatureRecord,
    *,
    model_artifact_sha256: str,
    model_version: str,
) -> Attestation:
    """Bind (model artifact, model version, input record) into one verifiable digest.

    All three are needed. The artifact hash alone does not pin which inputs were used; the input
    hash alone does not pin which model read them; and the version string is what a human reads in
    the payload, so it must be inside the hash rather than beside it.
    """
    measurement = hashlib.sha256(
        "|".join(
            [
                "continuum-phase0",
                model_version,
                model_artifact_sha256,
                input_digest(record),
            ]
        ).encode("utf-8")
    ).hexdigest()

    return Attestation(
        type="none",
        provider=PROVIDER,
        measurement_hash=f"0x{measurement}",
        signature=None,
    )


def verify(
    attestation: Attestation,
    record: BorrowerFeatureRecord,
    *,
    model_artifact_sha256: str,
    model_version: str,
) -> bool:
    """Recompute the digest and compare. This is the whole of what Phase 0 can check.

    A ``True`` here means: the record on file is byte-for-byte the record that was scored, by the
    model artifact named. It does **not** mean the score was computed honestly, that the model was
    the one advertised at the time, or that the underlying invoice data was real — §8's point that
    a proof about model execution says nothing about input integrity applies with full force.
    """
    expected = build(
        record, model_artifact_sha256=model_artifact_sha256, model_version=model_version
    )
    return expected.measurement_hash == attestation.measurement_hash
