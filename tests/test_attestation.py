"""§6's attestation block, and the boundary between what 0G proves and what it does not.

§4 says the earlier phase's explicit placeholder "is now filled by the 0G Compute attestation
object". §2 says that layer is the reason this product is on 0G at all. So the tests here are less
about hashing than about a claim: a payload must never say "attested" when it is not, and must never
let the 0G signature appear to cover arithmetic that ran on the operator's own machine.
"""

from __future__ import annotations

import json

import pytest

from continuum.schemas import Attestation
from continuum.scoring import attestation

SCORER_SHA = "a" * 64
VERSION = "continuum-scoring-v0.1.0-wave3"


def _og(**overrides) -> Attestation:
    base = dict(
        type="0g-compute",
        provider="0g-compute-network",
        job_id="chatcmpl-abc123",
        proof_ref="0x" + "de" * 16,
        compute_node="0xProviderAddress",
        verified=True,
        model="llama-3.3-70b-instruct",
    )
    base.update(overrides)
    return Attestation(**base)


def _build(record, compute=None):
    return attestation.build(
        record, scorer_sha256=SCORER_SHA, model_version=VERSION, compute=compute
    )


# --------------------------------------------------------------------------------------
# The 0G attestation
# --------------------------------------------------------------------------------------


def test_a_verified_compute_result_fills_the_block_verbatim(record):
    """§5.2: "Capture whatever proof/attestation artifact 0G Compute returns and store it verbatim"."""
    source = _og()
    att = _build(record, compute=source)

    assert att.type == "0g-compute"
    assert att.provider == "0g-compute-network"
    assert att.job_id == source.job_id
    assert att.proof_ref == source.proof_ref
    assert att.compute_node == source.compute_node
    assert att.model == source.model
    assert att.verified is True


def test_an_unverified_result_is_still_typed_but_flagged(record):
    """A response that came back and failed verification is not the same as no response.

    Collapsing them would let an unverified answer render identically to a verified one, which is
    exactly the overclaim §11 says a real reader finds faster than it is worth making.
    """
    att = _build(record, compute=_og(verified=False))
    assert att.type == "0g-compute"
    assert att.verified is False
    assert "NOT VERIFIED" in attestation.describe(att)


def test_no_compute_result_leaves_an_honest_empty_block(record):
    att = _build(record)
    assert att.type == "none"
    assert att.provider == attestation.NOT_ATTESTED
    assert att.job_id == "" and att.proof_ref == ""
    assert not att.verified
    assert "no attestation" in attestation.describe(att)


def test_the_record_can_carry_its_own_compute_attestation(record):
    """The reasoning call attaches its attestation to the feature record; the aggregator picks it
    up from there rather than needing it threaded through every call."""
    record.compute_attestation = _og()
    assert _build(record).type == "0g-compute"


def test_describe_never_rounds_an_unverified_answer_up(record):
    assert "verified" in attestation.describe(_build(record, compute=_og()))
    unverified = attestation.describe(_build(record, compute=_og(verified=False)))
    assert "NOT VERIFIED" in unverified


# --------------------------------------------------------------------------------------
# The local measurement — what 0G does NOT cover
# --------------------------------------------------------------------------------------


def test_the_measurement_is_computed_locally_either_way(record):
    """It binds the published score to its inputs, whether or not 0G was reachable."""
    with_og = _build(record, compute=_og())
    without = _build(record)
    assert with_og.measurement_hash == without.measurement_hash
    assert with_og.measurement_hash.startswith("0x")
    assert len(with_og.measurement_hash) == 66


def test_digest_is_stable_across_serialisation_order(record):
    first = attestation.input_digest(record)
    round_tripped = record.model_validate(
        json.loads(json.dumps(json.loads(record.model_dump_json()), sort_keys=False))
    )
    assert attestation.input_digest(round_tripped) == first


def test_the_digest_ignores_which_provider_answered(record):
    """Two runs reading the same inputs must produce the same input digest.

    Including the attestation would make the digest depend on which Compute provider happened to
    be available, which would break the only thing it is for.
    """
    before = attestation.input_digest(record)
    record.compute_attestation = _og(compute_node="0xSomeoneElse")
    assert attestation.input_digest(record) == before


def test_a_changed_input_changes_the_measurement(record):
    before = _build(record)
    record.features.revenue_30d += 0.01
    assert _build(record).measurement_hash != before.measurement_hash


def test_the_scorer_is_inside_the_hash(record):
    """The input hash alone does not pin which scoring rule read the inputs."""
    a = attestation.build(record, scorer_sha256="a" * 64, model_version=VERSION)
    b = attestation.build(record, scorer_sha256="b" * 64, model_version=VERSION)
    c = attestation.build(
        record, scorer_sha256="a" * 64, model_version=VERSION, scorer_kind="structured_lightgbm"
    )
    assert len({a.measurement_hash, b.measurement_hash, c.measurement_hash}) == 3


def test_the_version_string_is_inside_the_hash(record):
    a = attestation.build(record, scorer_sha256=SCORER_SHA, model_version="v0.1.0")
    b = attestation.build(record, scorer_sha256=SCORER_SHA, model_version="v0.2.0")
    assert a.measurement_hash != b.measurement_hash


def test_verify_accepts_the_matching_triple_and_rejects_tampering(record):
    att = _build(record, compute=_og())
    assert attestation.verify(att, record, scorer_sha256=SCORER_SHA, model_version=VERSION)

    record.data_quality_score = 0.99
    assert not attestation.verify(att, record, scorer_sha256=SCORER_SHA, model_version=VERSION)


def test_verify_rejects_a_substituted_scorer(record):
    att = _build(record)
    assert not attestation.verify(att, record, scorer_sha256="b" * 64, model_version=VERSION)


def test_verify_does_not_launder_an_unverified_0g_attestation(record):
    """``verify`` checks the local binding only, and must not be read as re-checking the TEE.

    The 0G signature check needs the network and its result already lives in ``verified``. A
    function that returned True here for an unverified attestation would be answering a different
    question than its name asks.
    """
    att = _build(record, compute=_og(verified=False))
    assert attestation.verify(att, record, scorer_sha256=SCORER_SHA, model_version=VERSION)
    assert att.verified is False  # ...and the payload still says so
