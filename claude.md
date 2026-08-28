# Continuous AI Credit Rating for RWA Lending — Product Pack

**Codename options:** Pulse Protocol / Continuum / LiveScore / Vitals Protocol (pick one, referred to below as "the Protocol")

---

## 0. How to Use This Document

This is a build brief, not marketing copy. It's structured so you can hand the whole thing to a frontier LLM (or Claude Code / an agentic dev environment) with an instruction like *"Build this system, starting with Phase 0"* and get a coherent build-out rather than a generic DeFi lending dApp. Section 17 at the end is a condensed kickoff prompt you can paste on its own once you've read and edited everything above it to reflect your actual decisions (chain, name, first vertical, etc.) — the LLM will build a better product if those decisions are already made rather than left open.

Sections 1–6 are strategic (read and edit these yourself — an LLM can't decide your wedge for you). Sections 7–14 are technical spec (an LLM can execute directly against these). Sections 15–16 are business/risk (informs your product decisions and investor conversations).

---

## 1. Executive Summary

RWA private credit on-chain is now a real market — over $14B in active loans across Maple, Centrifuge, Goldfinch, Huma, Credix and others as of mid-2026 — but the underwriting model underneath almost all of it is still a point-in-time human judgment call, refreshed rarely if at all after origination. That's the TradFi rating-agency problem (score once, drift stale for months) reproduced on-chain.

The opportunity is **continuous, cash-flow-indexed re-scoring**: an AI system that ingests a borrower's live financial signal (invoices, repayment behavior, bank/accounting data, on-chain flows) and re-scores them on a rolling basis — daily, or event-triggered — publishing that score in a form smart contracts can consume directly to move interest rates, LTV/collateral requirements, and borrowing limits without a human in the loop for every adjustment.

**Important reality check before you build:** this space is not empty. Section 2 lays out who's already here and why the wedge is narrower — and different — than "continuous AI credit scoring for crypto," full stop. The defensible version of this idea is continuous scoring for individual RWA borrowers/receivables in specific verticals (trade finance, invoice factoring, SME lending), not general crypto counterparty risk — that lane already has a well-funded incumbent.

---

## 2. Market Reality Check — Who's Already Doing Pieces of This

Be honest with the frontier LLM about this landscape, or it will build you something that duplicates an incumbent and calls it novel.

| Player | What they actually do | Why it's not full overlap with your wedge |
|---|---|---|
| **Credora (acquired by RedStone, 2025)** | The closest existing thing to "continuous AI credit rating on-chain." Privacy-preserving real-time credit scoring (AA–D letter grades on a 1000-point scale), now distributing scores via Chainlink Functions + Space and Time to protocols like Morpho and SparkLend. Also launched a "Consensus Ratings Protocol" aggregating inputs from multiple institutional risk desks, and a separate A+–D rating framework for tokens/vaults/lending pairs using Monte Carlo simulation. | Operates mainly at the **counterparty / protocol / vault / token level** — is this exchange, market maker, or lending market solvent — not at the level of a single SME's invoices or a single trade-finance receivable. Also privacy-preserving-proof-centric (cryptographic attestations about portfolio health), not primarily an LLM-driven cash-flow-document reasoning engine. |
| **Gauntlet, Chaos Labs** | Protocol-parameter risk management (collateral factors, liquidation thresholds) for money markets like Aave. | Protocol-level risk tuning, not individual borrower credit scoring. |
| **Huma Finance (PayFi)** | Real-world receivables/invoice-backed under-collateralized lending, with "Data Service Providers" and "external adapters" doing AI-assisted underwriting, live on Solana/EVM/Stellar. $4B+ txn volume. | Underwriting AI runs mostly at **origination and pool-structuring** time (tranches, advance rates); no evidence of a continuously re-published, per-borrower live score that smart contracts re-price against on a rolling basis the way this brief proposes. This is your closest functional competitor — study their DSP/EA architecture before you design yours. |
| **Maple, Centrifuge, Goldfinch** | The incumbents you named. Maple uses human "pool delegates" who stake capital as first-loss and underwrite manually. Centrifuge securitizes receivables via off-chain originators in a senior/junior tranche structure. Goldfinch funds emerging-market debt funds. All three still rely on human/delegate underwriting quality as the core risk control; none continuously re-score at the individual borrower level in a way that auto-moves pricing. | This confirms your original premise — but note the market has already been through one bad cycle (Centrifuge's Codex Finance default, Goldfinch's Tugende default in 2022–23) caused partly by underwriting that didn't catch deterioration in time. That's your strongest case-study evidence for why continuous scoring matters — use it. |
| **Particula, Agio Ratings** | Token-level and institutional counterparty ratings firms bringing TradFi-style rating methodology to crypto. | Adjacent category (asset/token ratings vs. borrower/receivable ratings). |

**The actual whitespace**, stated precisely: *continuous, event-driven re-scoring of individual RWA borrowers or receivables pools (SME invoices, trade finance, revenue-based financing) using live operational/financial data — not portfolio-level counterparty risk (Credora's lane) and not one-time origination underwriting (everyone else's lane) — published on-chain in a form that directly parametrizes a lending pool's rate curve and collateral terms.*

If you build this, position it explicitly against that gap, and expect Credora/RedStone and Huma to be your most likely acquirers, competitors, or — more realistically for a first product — potential integration partners rather than people you're racing head-on.

---

## 3. Product Vision

**One sentence:** A live credit-scoring layer for RWA lending pools that re-scores individual borrowers continuously from real financial signal instead of once at origination, and publishes that score on-chain so pool terms (rate, LTV, borrowing limit) move automatically as risk changes.

**What it is NOT (scope discipline matters here):**
- Not a lending protocol itself, at least not first. It's an infrastructure/oracle layer that existing or new lending pools plug into. (You can layer a reference lending pool on top later to prove the model, the way Chainlink didn't need to be a DEX to power DEXs.)
- Not a general crypto-counterparty risk score (that's Credora's lane).
- Not a KYC/identity/reputation product (on-chain wallet reputation scoring is a separate, more commoditized category — don't conflate it with this).

---

## 4. Who Pays For This (ICP)

Pick **one** first customer type — don't build for all three simultaneously:

1. **RWA lending protocol as B2B infra buyer** (most likely first wedge). A Centrifuge-style or Huma-style pool operator licenses your live-scoring API/oracle to parametrize their pool instead of building their own AI underwriting stack. You're the "Plaid for continuous credit risk" for RWA pools. Revenue: per-score fee or basis-point cut of pool interest.
2. **Asset originator** (an invoice-financing fintech, trade-finance desk, or receivables factor) who wants to tokenize their book and needs a credible, continuously-updating risk signal to attract on-chain capital. You're selling trust/credibility, priced as a subscription or per-borrower fee.
3. **Institutional lender/allocator** who wants an independent, continuously-updating risk overlay on top of *existing* RWA pools (Maple, Centrifuge, Huma) they already hold exposure to — closer to a "Credora for RWA specifically" positioning, sold as a data/analytics subscription, no smart-contract integration required for v1.

Recommendation: **start with option 1, in the invoice-financing/trade-finance vertical specifically**, because (a) cash-flow ground truth arrives fast (invoices settle in weeks not years, so your model gets frequent, low-latency feedback to prove itself against), (b) it's the vertical where Huma has proven demand exists, and (c) it's underserved relative to Maple's institutional-crypto-desk focus and Goldfinch's emerging-market-fund focus.

---

## 5. System Architecture — Overview

Five layers. Design each to be independently replaceable/upgradeable — this is important because the trust/verifiability layer (Layer 3) will change fastest as zkML/TEE tooling matures, and you don't want that dependency baked into your scoring logic.

```
[1. Data Ingestion]  →  [2. Scoring Engine]  →  [3. Verifiability/Attestation]  →  [4. On-chain Publishing/Oracle]  →  [5. Consumption Layer]
     (raw signal)         (the actual AI)          (proof it ran honestly)          (score becomes on-chain fact)      (pool logic reacts)
```

---

## 6. Layer 1 — Data Ingestion

**Goal:** turn messy real-world financial signal into a normalized, timestamped feature stream per borrower.

**Source categories to integrate (roughly in build-priority order for the invoice-financing vertical):**

| Source type | Example integrations | What it tells you |
|---|---|---|
| Accounting/ERP data | Codat, Plaid (for bank-linked cash flow), QuickBooks/Xero APIs | Real-time revenue, expenses, cash runway |
| Invoice/receivables platforms | Direct API from the originator's invoicing system, or document upload + OCR/LLM extraction | Invoice-level payer, amount, due date, dispute status |
| Bank transaction data | Plaid, Mono/Okra (useful if you go emerging-market-first), stablecoin payment rails | Repayment behavior, bounced payments, overdraft frequency |
| On-chain data | Wallet history, stablecoin flow analysis, prior on-chain repayment record within the Protocol itself | Track record, velocity of funds, existing on-chain leverage |
| Covenant/document data | Loan agreement text, financial statements — parsed by an LLM agent, not a human analyst | Covenant breaches, red-flag language, undisclosed liabilities |
| Macro/market context | Interest rate environment, sector-specific stress indicators, payer-side credit signal (is the *invoice payer*, not just the borrower, healthy) | Systemic risk overlay, not just idiosyncratic risk |

**Pipeline design:**
- Event-driven ingestion where possible (webhook on new bank transaction, new invoice, new repayment) rather than pure polling — this is what makes "continuous" real instead of marketing.
- Normalize everything into a single **Borrower Feature Store** (see schema in Section 9) keyed by borrower ID, with every fact timestamped and source-tagged.
- Build a data-quality/staleness flag per borrower — if a data source goes silent (originator stops syncing), the score should visibly degrade in confidence, not silently freeze at the last-known value. This single design decision is what most differentiates you from the "static rating" problem you're trying to solve.

---

## 7. Layer 2 — AI Scoring Engine

**Don't use one model for everything.** Use an ensemble, each piece suited to what it's good at:

1. **Structured cash-flow model** — gradient-boosted trees (XGBoost/LightGBM) trained on the normalized feature store: revenue trend, payment velocity, days-sales-outstanding, concentration risk (how much revenue depends on one payer), volatility of cash flow. This is your primary quantitative score driver and should be retrainable/backtestable against real default outcomes as you accumulate them.
2. **LLM reasoning agent (Claude API)** — reads unstructured signal: loan covenants, dispute correspondence, financial statement footnotes, news about the borrower or their major counterparties. Outputs a structured risk-flag object (not a free-text opinion) that feeds back into the score as a feature, e.g. `{covenant_breach: bool, adverse_news_detected: bool, confidence: float, evidence_refs: [...]}`. This is where your existing Claude API / prompt-engineering experience is directly reusable.
3. **Anomaly/early-warning layer** — lightweight statistical or time-series model watching for sudden deviations (a payment 20 days later than the borrower's historical pattern, a spike in invoice disputes) that should trigger an **immediate re-score outside the normal cadence**, rather than waiting for the next scheduled run.
4. **Score aggregation + calibration** — combine 1–3 into a single score (recommend keeping Credora's convention of a letter-grade scale, e.g. AA–D, since lenders are already trained on it) plus a **confidence interval**, not just a point estimate. Publish both. A lending pool consuming an uncertain score should be able to react differently (e.g., require larger collateral buffer) than one consuming a high-confidence score.

**Continuous re-scoring cadence — make this explicit, it's core to the pitch:**
- Scheduled baseline: e.g. daily re-score for active borrowers (cron via GitHub Actions or a proper job scheduler once you outgrow that).
- Event-triggered re-score: any new data event from Layer 1 that crosses a materiality threshold triggers an immediate re-score, independent of the daily cycle.
- Publish a "last updated" and "trigger reason" alongside every score — this is your entire value proposition made visible; don't bury it.

**Explainability is not optional.** Every score needs a feature-attribution breakdown (SHAP values or equivalent) available on request — both because lenders will demand it and because it's your best defense if a borrower disputes a downgrade.

---

## 8. Layer 3 — Trust & Verifiability

This is the layer most people building "AI oracle" products skip, and it's the reason most AI-oracle pitches don't survive institutional due diligence: *why should anyone trust that the score wasn't fabricated, or that the AI operator isn't front-running its own borrowers?*

Practical 2026 answer — **TEE attestation first, ZK later for narrow claims:**

- Run the scoring inference inside a **Trusted Execution Environment** (options in production today: Marlin Oyster, Phala Network, Automata's verifiable-AI service). The enclave signs its output with a key bound to the exact code that ran, so a smart contract can verify "this specific model, on this specific input, produced this specific output" without trusting the operator directly. Automata's DCAP attestation contracts already verify Intel SGX/TDX attestations natively in EVM.
- Reserve **zkML** (via EZKL or similar) for small, high-value, infrequent claims where the cost of proving is worth it — e.g., "this borrower's score is above threshold X" as a boolean proof for a specific lending decision — rather than trying to zk-prove full model inference, which remains orders of magnitude slower than native inference as of 2026.
- Be upfront in your own materials that neither TEE nor zkML solves the **oracle problem underneath the AI**: a cryptographic proof that a model ran correctly says nothing about whether the underlying invoice data was real. Your data-source integrity (Layer 1 provenance, source attestation, ideally direct API integration rather than self-reported documents) matters as much as the model layer.
- **Decentralization roadmap, stated honestly for v1:** you will start as a single AI operator (centralized trust in your infrastructure and your model). Have a credible path to multi-operator consensus scoring (multiple independent nodes running the same model against the same data, slashing on divergence) as a v2/v3 commitment — this is what will eventually let you make the "impossible to manipulate unilaterally" claim seriously.

---

## 9. Data Schemas (starter versions — an LLM can extend these)

**Borrower Feature Record**
```json
{
  "borrower_id": "brw_01hx...",
  "as_of": "2026-08-11T00:00:00Z",
  "source_freshness": {
    "accounting_feed": "2026-08-10T22:14:00Z",
    "bank_feed": "2026-08-11T00:00:00Z",
    "invoice_feed": "2026-08-09T15:02:00Z"
  },
  "features": {
    "revenue_30d": 184200.50,
    "revenue_trend_90d": 0.06,
    "days_sales_outstanding": 41,
    "payer_concentration_top1_pct": 0.34,
    "on_time_repayment_rate_180d": 0.97,
    "days_since_last_late_payment": 63
  },
  "llm_flags": {
    "covenant_breach": false,
    "adverse_news_detected": false,
    "confidence": 0.91,
    "evidence_refs": ["doc_88a...", "doc_88b..."]
  },
  "data_quality_score": 0.94
}
```

**Score Publication Payload**
```json
{
  "borrower_id": "brw_01hx...",
  "score": "A-",
  "score_numeric": 742,
  "confidence_interval": [710, 768],
  "prior_score": "A",
  "trigger_reason": "scheduled_daily",
  "model_version": "scoring-engine-v1.3.2",
  "attestation": {
    "type": "tee",
    "provider": "automata-dcap",
    "measurement_hash": "0x...",
    "signature": "0x..."
  },
  "published_at": "2026-08-11T06:00:03Z",
  "explainability_ref": "explain_9182..."
}
```

---

## 10. Layer 4 — On-Chain Publishing / Oracle Design

- **Score Registry contract:** a mapping of `borrower_id → latest score struct` (score, confidence bounds, timestamp, attestation hash), updatable only by an authorized scorer/oracle address (or a decentralized set of them in v2+).
- **Update cadence vs. gas cost tradeoff:** don't publish every re-score on-chain if nothing materially changed — publish only on threshold-crossing changes (e.g., score moves more than X points, or a scheduled daily checkpoint) and batch updates across borrowers where possible. Consider an L2/rollup for the registry itself regardless of which chain the lending pools live on, then relay via a standard cross-chain messaging pattern (or just deploy the registry natively on whichever chain your first design-partner pool already uses — don't force a new chain dependency on your first customer).
- **Rate limiting / circuit breakers, mandatory:** cap how much a single score update can move a pool's interest rate or collateral requirement in one step, and require a cooldown between updates for the same borrower. Continuous scoring without a circuit breaker is a manipulation vector (see Section 11) and also a bad user experience for borrowers who don't want their rate whipsawing on noisy data.
- **Existing oracle infra to build on rather than reinvent:** Chainlink Functions (this is literally the pattern Credora already uses to get scores from an off-chain compute environment on-chain) is the most proven path; evaluate it before building custom oracle infrastructure from scratch.

---

## 11. Layer 5 — Consumption Layer (how a lending pool actually reacts)

Design the **rate curve and collateral function as pure, auditable formulas** that take the published score (and its confidence interval) as input — don't let the AI directly set the rate; let the AI set the *risk input* and let a transparent, governance-auditable formula translate that into terms. This separation matters for trust and for regulatory framing (see Section 12): the AI is a risk-scoring input, not the entity making the lending decision.

Example (illustrative, tune per vertical):
```
base_rate = pool_base_rate
risk_premium = f(score, confidence_interval_width)   // wider interval → higher premium, not just lower score
max_ltv = g(score)
rate_change_per_update ≤ ±50bps   // circuit breaker
```

Build a **borrower-facing dispute/appeal flow**: if a borrower believes a downgrade is wrong (stale data, a payer dispute resolved after the score update), there needs to be a human-reviewable override path, logged on-chain, that doesn't require rebuilding trust in the whole system every time the model is imperfect — because it will be.

---

## 12. Regulatory & Compliance Considerations

Flag these for real legal review before launch — this section is context for your counsel, not legal advice itself:

- **Credit rating agency regulation analogs.** In the US, entities that issue credit ratings used by the public to make investment decisions can fall under NRSRO-style regulatory frameworks (Section 15E of the Exchange Act) depending on how the ratings are used and distributed. Whether an on-chain, algorithmically-published score triggers this depends heavily on framing (risk-scoring infrastructure vs. "rating" marketed as investment guidance) and on your actual user base (institutional vs. retail). Get counsel on this before you use the word "rating" in customer-facing marketing.
- **Data privacy.** You're ingesting sensitive financial data (bank transactions, invoices with counterparty names, revenue). Build for data minimization and, ideally, the privacy-preserving pattern Credora uses (cryptographic proofs about creditworthiness without exposing raw financials) rather than storing raw sensitive data longer than necessary.
- **Jurisdiction.** RWA lending already sits in a regulatory gray zone in most jurisdictions (see how Maple and Goldfinch each handle KYC/accreditation differently). Your scoring layer inherits whatever regulatory posture your lending-pool customers have — decide early whether you're regulation-agnostic infrastructure (like an oracle) or need your own compliance posture.
- **Human oversight requirement.** Increasingly a regulatory expectation (and good practice regardless) that fully-automated adverse credit decisions have a human-reviewable path — this is also just Section 11's dispute flow, framed for compliance rather than UX.

---

## 13. Manipulation Resistance & Adversarial Considerations

Build a threat model before writing scoring code, not after:

- **Data spoofing:** a borrower feeding fabricated invoices or manipulated bank data. Mitigate via direct-API integrations over borrower-uploaded documents wherever possible, cross-source corroboration (does the invoice appear in the payer's own accounting feed too, if you can get it), and treating single-source, unverifiable data as lower-confidence input (wider confidence interval → automatically higher risk premium per Section 11's formula).
- **Payer-side risk laundering:** borrower is fine, but their concentrated payer is deteriorating — make payer-level risk a first-class feature, not an afterthought.
- **Gaming the re-score cadence:** a borrower timing cash movements around known scoring windows. Event-driven + randomized-timing scoring reduces this relative to fixed daily-at-midnight scoring.
- **Oracle-operator risk:** until you're decentralized (Section 8's roadmap), you are a trusted party. Be explicit about this in your own docs rather than overclaiming trustlessness you haven't earned yet — institutional counterparties will find the overclaim faster than retail will, and it costs you the deal.

---

## 14. Technical Stack Recommendation

Tailored to reuse what you already have (React/Vite frontend experience, Python, Node.js, GitHub Actions automation, Claude API integration experience from your existing DeFi research pipeline work):

| Component | Recommendation | Why |
|---|---|---|
| Data ingestion pipelines | Python (pandas/polars) + Node.js for webhook handlers | Matches your existing stack |
| Scheduled scoring jobs (MVP) | GitHub Actions cron, same pattern as your existing DeFi digest pipeline | You've already built and operated this pattern — reuse it before reaching for heavier infra |
| Feature store | Postgres (with a proper time-series extension like TimescaleDB) for structured features; a vector store (pgvector or similar) if you need semantic search over covenant/document text | Simple, boring, correct — don't over-engineer the data layer for an MVP |
| Structured scoring model | XGBoost/LightGBM via Python, versioned and backtestable | Industry-standard for tabular credit-risk modeling, explainable via SHAP |
| LLM reasoning agent | Claude API (Sonnet-class for volume, escalate to a stronger model for edge cases/disputes), structured JSON output mode | Directly reuses your existing prompt-engineering and Claude API experience |
| Attestation/TEE | Start by evaluating Automata's verifiable-AI service or Phala Network before building custom enclave infra | Don't build TEE infrastructure from scratch as a solo/small team — integrate |
| On-chain oracle relay | Chainlink Functions (proven pattern, same one Credora uses) | Fastest credible path to "score on-chain," not a novel R&D project |
| Smart contracts | Solidity + Foundry, deployed on whichever chain your first design-partner pool already runs on | Don't force a new-chain dependency on your first customer |
| Frontend (score dashboard, borrower/lender views) | React + Vite, matching your portfolio site stack | Direct reuse of your existing frontend skillset |
| Explainability surface | SHAP value visualization on the frontend, per-score audit trail page | Non-negotiable for institutional trust |

---

## 15. Build Roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| **Phase 0 — Proof of Concept (4–6 weeks)** | Pick ONE vertical (recommend invoice financing), ONE data source integration, build the structured scoring model + Claude reasoning agent, run scoring **off-chain only** on a small set of real or historical borrower data | You can show a live dashboard where a borrower's score visibly moves in response to a real data event, with an explainability trail |
| **Phase 1 — On-chain publishing (4–6 weeks)** | Add Layer 3 (start with a single trusted attestation, defer full TEE if needed for speed) + Layer 4 (Score Registry contract via Chainlink Functions) | A published score is verifiably on-chain and queryable by a smart contract |
| **Phase 2 — Reference lending pool integration (6–8 weeks)** | Either integrate with a real design-partner pool, or build a minimal reference lending pool yourself that consumes the score to set rate/LTV, with circuit breakers and dispute flow live | A live loan's terms visibly adjust in response to a re-score, end to end |
| **Phase 3 — First paying design partner** | Take Phase 0–2 to an actual RWA originator or lending protocol as a pilot integration | Signed pilot agreement / real capital flowing through the scored pool |
| **Phase 4 — Decentralization roadmap** | Multi-operator scoring consensus, broader data-source coverage, zkML for specific high-value claims | Reduced single-operator trust assumption, documented publicly |

---

## 16. Business Model & Go-To-Market

- **Pricing (start simple):** per-score API fee or a basis-point cut of interest generated on pools using your score, billed to the lending-pool operator (your ICP from Section 4, option 1) rather than to individual borrowers.
- **Wedge tactic:** don't try to convince Maple/Centrifuge/Goldfinch to change their model first — they have incumbent processes and delegate relationships to protect. Target either (a) a newer/smaller RWA pool operator in the invoice-financing/trade-finance vertical who has no legacy underwriting process to defend, or (b) an asset originator who wants to tokenize a receivables book for the first time and needs credibility, not a switch from an existing process.
- **Proof point to chase early:** find one borrower cohort where your continuous score would have caught a deterioration *before* a static origination-time rating would have — even a backtested case study on public/historical data is a strong sales artifact.

---

## 17. Risks & Open Questions (be honest with yourself here)

- **Cold-start data problem:** you need real default/repayment outcomes to backtest and calibrate the scoring model credibly — where does that training data come from before you have live borrowers? (Consider partnering with an existing originator who has historical loan-tape data.)
- **Trust bootstrapping:** institutional lenders will not trust a single-operator AI score with real capital on day one. Your entire Phase 0–2 sequence should be designed to build a public, auditable track record before asking for trust, not after.
- **Regulatory framing risk:** see Section 12 — get this reviewed before "rating" appears in any customer-facing material.
- **Incumbent response:** Credora/RedStone or Huma could extend into your specific wedge faster than you can build it, given their funding and existing oracle relationships. Your realistic outcomes here are: out-execute in a narrow vertical they're not focused on, or become a plausible acquisition/partnership target rather than a head-on competitor.

---

## 18. Appendix — Kickoff Prompt for a Frontier LLM

*(Edit the bracketed decisions below before using — don't leave them open for the LLM to guess.)*

> Build the Phase 0 proof-of-concept described in this document for [PROJECT NAME]: a continuous AI credit-scoring engine for the [invoice-financing / trade-finance / SME lending — pick one] vertical of RWA lending.
>
> Scope for this session: (1) a data ingestion pipeline for [chosen data source] normalized into the Borrower Feature Record schema in Section 9, (2) a structured XGBoost/LightGBM scoring model plus a Claude API reasoning agent producing the `llm_flags` object, (3) a score aggregation function producing the Score Publication Payload schema with a confidence interval, (4) a simple dashboard (React/Vite) showing a borrower's score history and explainability trail. Do NOT build on-chain components yet — this phase is off-chain only, using [real historical data / synthetic data modeled on real invoice-financing patterns — pick one]. Follow the architecture, schemas, and manipulation-resistance considerations in Sections 6–13 of the attached document. Flag any assumption you make that isn't specified here rather than silently deciding it.


