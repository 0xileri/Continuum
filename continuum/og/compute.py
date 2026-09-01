"""§5.2 — the reasoning call on 0G Compute, and the attestation it returns.

**The scope reduction, stated once here and repeated wherever it matters.** §5.2 asks for the
aggregation step to run as an 0G Compute job and provides a fallback if that proves awkward. §12
asked for the question to be settled early. It is settled: 0G Compute Network serves **inference
and fine-tuning against registered model providers** — a marketplace of TEE-hosted model endpoints
behind an OpenAI-compatible API — and does not execute arbitrary code. There is no call shape that
takes ``aggregate.score`` and runs it. So the fallback applies: the reasoning call runs on 0G
Compute, the aggregation arithmetic runs locally, and this is flagged in README.md, WAVE3.md, the
per-score explanation artifact, and the dashboard. Nothing is mocked.

What that buys is still the thing §2 says is the hardest part of the product: the ``llm_flags`` that
move a published score were produced inside a TEE by a named model, and the provider's signature
over that response is verified before the flags are used.

The prompt and the response schema are shared with ``scoring.llm_agent`` rather than rewritten, so
the two paths — direct Anthropic API, and 0G Compute — cannot drift into asking different questions
and having their answers compared.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from continuum import config
from continuum.og.bridge import BridgeError, BridgeUnavailable, call_bridge
from continuum.schemas import Attestation, LLMFlags

log = logging.getLogger(__name__)


@dataclass
class ComputeResult:
    """Flags plus the attestation that covers them."""

    flags: LLMFlags
    attestation: Attestation
    raw_content: str = ""
    provider: str = ""
    model: str = ""

    @property
    def attested(self) -> bool:
        return self.attestation.type == "0g-compute" and self.attestation.verified


def reason_over_documents(
    borrower_name: str,
    sector: str,
    as_of: datetime,
    documents: list[dict],
) -> ComputeResult:
    """Produce §6's ``llm_flags`` by running the reasoning prompt on 0G Compute.

    Falls back to zero-confidence offline flags on any failure, exactly as the direct-API path
    does. The fallback is not an approximation of the agent: it raises no flags and reports
    confidence 0.0, which widens the published interval rather than clearing the borrower. A stub
    that guessed would make a system with nothing attached look like one with a perfect model.
    """
    from continuum.scoring import llm_agent

    # The demo-mode/mainnet incompatibility is enforced at import by config._check_demo_vs_mainnet,
    # so there is nothing to re-check here.
    visible = llm_agent.visible_documents(documents, as_of)

    if not visible:
        # No documents is a legitimate state, not a failure. It means no document-based flag can be
        # raised either way, which is what confidence 0.25 says — and that widens the published
        # interval rather than clearing the borrower. Raising here instead made a document-less
        # borrower permanently unscoreable on mainnet while protecting nothing: an unattested score
        # is stopped at publication, not at scoring.
        return ComputeResult(
            flags=LLMFlags(
                covenant_breach=False,
                adverse_news_detected=False,
                payer_deterioration=False,
                confidence=0.25,
                evidence_refs=[],
                rationale="No documents received as of this timestamp; no document-based flags "
                "can be raised either way.",
                source="0g-compute",
                output_mode="none",
            ),
            attestation=Attestation(),
        )

    # The 0G providers serve an OpenAI-compatible chat endpoint with no server-side schema
    # enforcement, so this is the text-JSON path: the prompt carries the field definitions and
    # Pydantic validates the result before anything downstream sees it. The flags are stamped
    # output_mode="text_json" for exactly that reason — validated by us, not by the endpoint.
    system = llm_agent.SYSTEM_PROMPT + llm_agent.JSON_MODE_SUFFIX
    user = llm_agent.build_user_message(borrower_name, sector, as_of, visible, json_mode=True)

    try:
        result = call_bridge(
            "compute.mjs",
            {
                "system": system,
                "user": user,
                "provider": config.OG_COMPUTE_PROVIDER or None,
                "maxTokens": config.LLM_MAX_TOKENS,
                "temperature": 0,
            },
        )
    # A Compute failure degrades to zero-confidence flags on every network, mainnet included.
    #
    # Raising here was protecting the wrong thing. Scoring and publishing are separate decisions:
    # a score computed without the agent is a legitimate, *less confident* score — the interval
    # widens and the grade ceiling falls, which is the engine's standard response to a missing
    # signal everywhere else. Refusing to produce it at all meant one provider outage stopped the
    # scheduled run for the whole cohort, and nothing downstream was any safer, because an
    # unattested score is already blocked at publication by OG_REQUIRE_ATTESTATION and by
    # publish_wave3.py's --allow-unattested gate.
    #
    # Logged at warning, not swallowed: the attestation comes back type="none", which every
    # consumer can see and the dashboard renders as an absence.
    except BridgeUnavailable as exc:
        log.warning("0G Compute unavailable; scoring unattested: %s", exc)
        return ComputeResult(
            flags=llm_agent.offline_flags(f"0G Compute unavailable: {exc}"),
            attestation=Attestation(),
        )
    except BridgeError as exc:
        log.warning("0G Compute call failed; scoring unattested: %s", exc)
        return ComputeResult(
            flags=llm_agent.offline_flags(f"0G Compute failed: {exc}"),
            attestation=Attestation(),
        )

    attestation = Attestation.model_validate(result.get("attestation", {}))
    content = result.get("content", "")

    try:
        parsed = llm_agent.DocumentAssessment.model_validate(
            {
                k: v
                for k, v in json.loads(llm_agent._extract_json(content)).items()
                if k in llm_agent.DocumentAssessment.model_fields
            }
        )
    except (ValueError, ValidationError) as exc:
        log.warning("0G Compute response did not validate; scoring without flags: %s", exc)
        return ComputeResult(
            flags=llm_agent.offline_flags(
                f"0G Compute returned an unusable response ({type(exc).__name__}); "
                f"the call was attested but its content could not be validated"
            ),
            # The attestation is kept: a verified signature over a malformed answer is still a true
            # fact about what the provider returned, and discarding it would hide a provider that
            # is reliably producing garbage.
            attestation=attestation,
            raw_content=content,
        )

    shown = {d["doc_id"] for d in visible}
    refs = [r for r in parsed.evidence_refs if r in shown]

    flags = LLMFlags(
        covenant_breach=parsed.covenant_breach,
        adverse_news_detected=parsed.adverse_news_detected,
        payer_deterioration=parsed.payer_deterioration,
        confidence=float(min(max(parsed.confidence, 0.0), 1.0)),
        evidence_refs=refs,
        rationale=parsed.rationale.strip(),
        source="0g-compute",
        model_used=attestation.model,
        escalated=False,
        output_mode="text_json",
    )

    if not attestation.verified:
        # The provider answered but its TEE signature did not check out. The flags are kept and the
        # attestation says plainly that it is unverified — which is the whole reason `verified` is a
        # separate field from `type`. Refusing to score here would discard a real response to
        # enforce a publication rule at the wrong layer; OG_REQUIRE_ATTESTATION and
        # publish_wave3.py both already refuse to publish this, and the dashboard renders it as
        # "returned, NOT verified" rather than as an attestation.
        log.error(
            "0G Compute returned flags for %s but the TEE signature did not verify (%s) — "
            "scoring with them, marked unverified; this score cannot be published attested",
            borrower_name,
            result.get("verify_error", "") or "signature did not verify",
        )

    return ComputeResult(
        flags=flags,
        attestation=attestation,
        raw_content=content,
        provider=attestation.compute_node,
        model=attestation.model,
    )


def main() -> None:
    """Smoke-test the Compute path against one borrower, and print the attestation."""
    import argparse

    from continuum.clock import iso
    from continuum.ingestion import store
    from continuum.orchestrator import data_horizon
    from continuum.scoring import attestation as att_mod

    parser = argparse.ArgumentParser(description="Run one reasoning call on 0G Compute.")
    parser.add_argument("--borrower", required=True)
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    borrower = next(
        b for b in store.load_borrowers() if b["borrower_id"] == args.borrower
    )
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else data_horizon()
    documents = store.load_documents(args.borrower)

    print(f"0G Compute reasoning — {borrower['name']}  as_of {iso(as_of)}")
    print(f"  network {config.OG_NETWORK}  documents {len(documents)}\n")

    result = reason_over_documents(borrower["name"], borrower.get("sector", ""), as_of, documents)

    print(json.dumps(result.flags.model_dump(), indent=2))
    print(f"\nattestation: {att_mod.describe(result.attestation)}")
    print(json.dumps(result.attestation.model_dump(mode="json"), indent=2))

    if not result.attested:
        print(
            "\nNOT ATTESTED. The score would publish with attestation.type='none'. For a run whose\n"
            "output is Wave 3 Integration Proof, set CONTINUUM_OG_REQUIRE_ATTESTATION=1 so an\n"
            "unattested score is a hard failure rather than a quiet one."
        )


if __name__ == "__main__":
    main()
