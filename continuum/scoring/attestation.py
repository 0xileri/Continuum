"""§6's ``attestation`` block — filled by 0G Compute, per Wave 3 §2 and §4.

§2 is the reason this project is on 0G at all:

    *"why should anyone trust that an AI-computed credit score wasn't fabricated? The original plan
    was to stand up TEE infrastructure (Automata/Phala/Marlin Oyster) or fall back to Chainlink
    Functions... 0G Compute Network's verifiable inference is that layer, natively."*

and §4 says what that means for this file specifically:

    *"the original Phase 0 POC stubbed this with an explicit placeholder type rather than omitting
    it or faking TEE fields. That placeholder is now filled by the 0G Compute attestation object."*

**Two different claims live in this block, and conflating them would be the overclaim §11 warns
about.** They are kept as separate fields:

``type`` / ``proof_ref`` / ``verified`` — the 0G Compute attestation. A TEE-held key signed the
reasoning response, and ``broker.inference.processResponse`` checked that signature. This proves a
genuine enclave, running the named model, produced the ``llm_flags`` that fed this score.

``measurement_hash`` — a local sha256 binding (scorer identity + scorer artifact + input feature
record). This proves the *published score* corresponds to that exact record under that exact
scoring rule. 0G's signature does not cover it, because the arithmetic in ``aggregate.score`` runs
here rather than in the enclave — that is the §5.2 scope reduction, and pretending otherwise by
folding the digest into the attested surface is exactly the kind of quiet upgrade that does not
survive scrutiny.

Neither says the underlying invoice data was real. That remains a Layer 1 provenance problem, and
§11 flags direct-API integration as the Phase 2 mitigation.
"""

from __future__ import annotations

import hashlib
import json

from continuum.schemas import Attestation, BorrowerFeatureRecord

NOT_ATTESTED = "not_attested"
OG_PROVIDER = "0g-compute-network"


def input_digest(record: BorrowerFeatureRecord) -> str:
    """sha256 over the canonicalised feature record.

    Canonical JSON — sorted keys, no whitespace — so the digest is a function of the *content*
    rather than of serialisation order. Without that, re-serialising the same record with a
    different key order produces a different hash and the audit trail becomes noise.

    The record's own ``compute_attestation`` is excluded: it is a property of how the record's
    flags were obtained, not part of the borrower's data, and including it would make the digest
    depend on which Compute provider happened to answer. Two runs that read the same inputs must
    produce the same input digest.
    """
    payload = json.loads(record.model_dump_json())
    payload.pop("compute_attestation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def measurement(*, scorer_kind: str, scorer_sha256: str, model_version: str, digest: str) -> str:
    """Bind scorer identity, scorer content, version and inputs into one digest.

    All four are needed. The artifact hash alone does not pin which inputs were used; the input
    hash alone does not pin which scorer read them; the kind distinguishes a weighted formula from
    a booster that happened to hash the same way; and the version string is what a human reads in
    the payload, so it must be inside the hash rather than beside it.
    """
    joined = "|".join(["continuum-wave3", scorer_kind, model_version, scorer_sha256, digest])
    return "0x" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def build(
    record: BorrowerFeatureRecord,
    *,
    scorer_sha256: str,
    model_version: str,
    scorer_kind: str = "weighted_quant_v1",
    compute: Attestation | None = None,
) -> Attestation:
    """Assemble §6's attestation block for one published score.

    ``compute`` is whatever ``og.compute`` captured from the 0G Compute call that produced this
    record's ``llm_flags`` — passed through verbatim per §5.2 ("store it verbatim in the attestation
    block") rather than reshaped. When it is absent or unverified the block stays ``type="none"``
    and says so in the provider field, which is the same honesty the pre-0G stub had.
    """
    digest = input_digest(record)
    measurement_hash = measurement(
        scorer_kind=scorer_kind,
        scorer_sha256=scorer_sha256,
        model_version=model_version,
        digest=digest,
    )

    source = compute or record.compute_attestation
    if source is None or source.type != "0g-compute":
        return Attestation(
            type="none",
            provider=NOT_ATTESTED,
            measurement_hash=measurement_hash,
            signature=None,
        )

    return Attestation(
        type="0g-compute",
        provider=OG_PROVIDER,
        job_id=source.job_id,
        proof_ref=source.proof_ref,
        compute_node=source.compute_node,
        verified=source.verified,
        model=source.model,
        measurement_hash=measurement_hash,
        signature=source.signature,
    )


def verify(
    attestation: Attestation,
    record: BorrowerFeatureRecord,
    *,
    scorer_sha256: str,
    model_version: str,
    scorer_kind: str = "weighted_quant_v1",
) -> bool:
    """Recompute the local measurement and compare.

    This checks the half that is ours: the record on file is byte-for-byte the record that was
    scored, under the scorer named. It deliberately does **not** re-verify the 0G signature — that
    check belongs to the broker, needs the network, and its result is already recorded in
    ``attestation.verified``. A function that silently returned ``True`` for an unverified 0G
    attestation because the local digest matched would be answering a different question than the
    one its name asks.
    """
    expected = measurement(
        scorer_kind=scorer_kind,
        scorer_sha256=scorer_sha256,
        model_version=model_version,
        digest=input_digest(record),
    )
    return expected == attestation.measurement_hash


def describe(attestation: Attestation) -> str:
    """One line for a CLI or a dashboard badge. Never rounds an unverified answer up."""
    if attestation.type != "0g-compute":
        return "no attestation — score computed locally, unverified (§5.2 fallback)"
    state = "verified" if attestation.verified else "RETURNED BUT NOT VERIFIED"
    return (
        f"0G Compute {state} · provider {attestation.compute_node[:12]}… "
        f"· model {attestation.model or '?'} · job {attestation.job_id[:16]}…"
    )
