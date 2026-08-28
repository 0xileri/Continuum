# Continuum — Phase 0 PoC

Continuous AI credit scoring for the **invoice-financing** vertical of RWA lending.

This is the Phase 0 proof-of-concept defined in `claude.md` §15: **off-chain only**, synthetic
data, one vertical. No smart contracts, no oracle, no Chainlink.

**Phase 0 exit criteria (§15):** *"You can show a live dashboard where a borrower's score
visibly moves in response to a real data event, with an explainability trail."*
That is what `scripts/demo_event.py` + the dashboard demonstrate end to end.

---

## What's here

| Layer (§5) | Status in Phase 0 | Code |
|---|---|---|
| 1. Data Ingestion | Built (synthetic sources) | `continuum/synth/`, `continuum/ingestion/` |
| 2. AI Scoring Engine | Built (all 4 sub-parts of §7) | `continuum/scoring/` |
| 3. Verifiability / Attestation | **Stubbed deliberately** — see ASSUMPTIONS #11 | `continuum/scoring/attestation.py` |
| 4. On-chain Publishing | **Out of scope** (user constraint) | — |
| 5. Consumption Layer | Formula only, no pool | `continuum/consumption.py` |

### The scoring ensemble (§7)

1. **Structured cash-flow model** — LightGBM binary classifier over the §9 feature block.
   Trained on synthetic loan-tape outcomes, backtestable, exact TreeSHAP attributions.
2. **Claude reasoning agent** — reads unstructured docs (covenants, dispute mail, news) and
   emits the strict `llm_flags` object. Structured output via Pydantic, never free text.
3. **Anomaly / early-warning layer** — robust z-scores over each borrower's own history;
   crossing a materiality threshold fires an **immediate out-of-cadence re-score**.
4. **Aggregation + calibration** — fuses 1–3 into a letter grade (AA–D), a 0–1000 numeric,
   and a **confidence interval** that widens on stale, single-source, or anomalous data.

---

## Quickstart

```bash
python -m pip install -r requirements.txt

python -m continuum.synth.generate      # 1. synthetic borrowers -> data/raw/
python -m continuum.scoring.train       # 2. fit LightGBM -> data/models/
python -m continuum.orchestrator daily  # 3. score everyone -> data/scores/

cd dashboard && npm install && npm run dev
```

The dashboard reads the JSON in `data/` through the FastAPI server:

```bash
python -m uvicorn continuum.api:app --reload --port 8787
```

### Seeing a score move (the §15 exit criterion)

```bash
python scripts/demo_event.py brw_01hx8k2m4n --scenario payer_default
```

This injects a real data event (a top-payer invoice goes 30 days past due), the anomaly layer
flags it as material, an **event-triggered** re-score fires, and the dashboard shows the grade
drop with the SHAP waterfall explaining exactly which features moved it.

Other scenarios: `dispute_spike`, `covenant_breach`, `feed_goes_dark`.
`feed_goes_dark` is the important one — it demonstrates §6's requirement that the score
**degrades in confidence rather than silently freezing** when a source stops reporting.

---

## Claude API

The reasoning agent needs `ANTHROPIC_API_KEY` in the environment (see `.env.example`).
It is never read from a file in the repo and never logged.

**Without a key the PoC still runs end to end** — `llm_agent.py` falls back to a deterministic
offline stub and stamps `llm_flags.source = "offline_fixture"` so a stubbed flag is never
mistaken for a real model judgement. See ASSUMPTIONS #8.

---

## Trust posture (§8, §13)

Phase 0 is a **single-operator system with no attestation**. The `attestation` block in every
published payload is explicitly typed `"none"` with `"phase_0_offchain_no_attestation"` in the
provider field. Per §13's oracle-operator warning, that overclaim is not made anywhere in
this codebase — TEE integration is Phase 1 work.

---

## Docs

- `ASSUMPTIONS.md` — **every** decision not specified in `claude.md`, numbered and justified.
  Read this first; several entries are product calls that are yours to make, not mine.
- `claude.md` — the source brief. Authoritative over this README wherever they disagree.
