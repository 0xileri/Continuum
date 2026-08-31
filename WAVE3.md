# Wave 3 — Assumptions, Scope Reductions and Open Questions

§12 of the brief asks the build agent to "say so explicitly rather than silently deciding" whenever
it has to guess. This file is that register. It covers only the Wave 3 retarget; `ASSUMPTIONS.md`
holds the decisions made for the earlier off-chain phase, and the entries there that still govern
this build are cross-referenced rather than repeated.

Entries marked **YOUR CALL** are product decisions, not technical ones. I made them to keep the
build moving and they are yours to overrule.

---

## A. The three §12 questions, answered

### A1. Does 0G Compute's SDK support the aggregation step, or only inference calls?

**Answered: inference (and fine-tuning) only. §5.2's stated fallback applies.**

§12 asked for this to be settled "as soon as this is known, don't wait until Day 3". It was settled
on day one by reading the SDK, and the answer is a capability limit rather than a time constraint:

> 0G Compute Network is a **marketplace of TEE-hosted model providers** reached over an
> OpenAI-compatible chat endpoint. `broker.inference` exposes `listService`, `getServiceMetadata`,
> `getRequestHeaders` and `processResponse`. There is no call shape that accepts a function and
> executes it.

So Continuum takes the fallback §5.2 provides:

> *"fall back to running just the LLM reasoning call through 0G Compute and flag this scope
> reduction explicitly in the README rather than silently mocking it."*

**What runs on 0G Compute:** the document-reasoning call that produces `llm_flags`. The response is
signed by a key held inside the provider's TEE, and `broker.inference.processResponse` verifies
that signature before the flags are allowed to move a score. That verification result is what fills
§6's `attestation` block.

**What does not:** the aggregation arithmetic in `continuum/scoring/aggregate.py` — the scorecard
transform, the interval, the staleness rule, the publish gate. It runs off-chain on the operator's
machine and is bound to its inputs by `attestation.measurement_hash`, a local sha256 over
(scorer kind + scorer digest + version + feature record).

This distinction is stated in the README, in the per-score explanation artifact's
`trust_disclaimer`, in `/meta` and `/og` on the API, and on the dashboard as body text rather than
a footnote. Nothing is mocked anywhere.

**The honest framing for the demo:** 0G Compute makes the *reasoning* verifiable, which is the part
of the pipeline where a human would otherwise have to trust that a model was consulted at all. It
does not make the arithmetic verifiable. Claiming otherwise is the overclaim §11 says a real reader
finds faster than it is worth making.

### A2. 0G Chain deployment sequencing

Handled as §9 Day 3 and §12 specify — **testnet first, mainnet for submission**.

- `config.OG_NETWORK` defaults to **testnet**, deliberately. A default of mainnet would mean every
  careless local run spends real 0G. Promotion is one environment variable.
- `contracts/script/Deploy.s.sol` asserts `block.chainid` is 16602 or 16661 and refuses anything
  else, so a misconfigured RPC cannot deploy the registry to an unrelated chain.
- `og-bridge/doctor.mjs` is a preflight that costs nothing and checks every precondition —
  balance, storage indexer, Compute marketplace, ledger funding, registry presence, scorer
  authorisation — and prints the exact fix for each. **Run it before the mainnet promotion**, which
  is the time §12 asks to be budgeted for.
- `scripts/publish_wave3.py` refuses to run without `--yes`, prints the network and wallet first,
  and skips any borrower whose attestation did not verify unless `--allow-unattested` is passed.

**Verified network parameters** (checked against docs.0g.ai, August 2026, in `config.OG_NETWORKS`):

| | mainnet | Galileo testnet |
|---|---|---|
| chain id | 16661 | 16602 |
| RPC | `https://evmrpc.0g.ai` | `https://evmrpc-testnet.0g.ai` |
| explorer | `https://chainscan.0g.ai` | `https://chainscan-galileo.0g.ai` |
| storage indexer | `https://indexer-storage-turbo.0g.ai` | `https://indexer-storage-testnet-turbo.0g.ai` |
| faucet | — | `https://faucet.0g.ai` |

### A3. ERC-7857 Agentic ID cost and complexity

**Not attempted, and correctly so.** §5.5 is explicit: *"Don't start this until Section 5.1-5.4 are
working end to end — a half-built registry with a working Agentic ID scores worse than a complete
registry without one."* Sections 5.1–5.4 are built but not yet *deployed* (see §D below), so by the
brief's own gate this stretch goal is not reachable. No assessment of its cost was made because
making one would have been time spent against a gate that is not open.

---

## B. Scope reductions, stated plainly

### B1. The reasoning call is on 0G Compute; the aggregation is not

See A1. This is the one scope reduction §5.2 explicitly sanctions, and it is taken for the reason
§5.2 anticipated.

### B2. Deployed to testnet; mainnet deployment still requires your key

**Galileo testnet is live and proven** — see `deployments/testnet.json` and
`deployments/integration_proof_testnet.json`:

| | |
|---|---|
| Registry | `0xceB1a3B3bA1B2588A8Ec434F8d406D757262eE28` |
| Publish transactions | 5, across 4 borrowers |
| 0G Storage | feature records written; one round-tripped with merkle proof |
| §4 cooldown | refused a same-band republish, 21399s remaining |
| §4 override | a boundary-crossing downgrade published inside that same window |
| §5.4 breaker | clamped a 975bps move to exactly 50bps |
| 0G Compute | **not attested** — see C14 |

**Mainnet is the remaining §3 exit criterion.**

Deploying `ContinuumScoreRegistry` to 0G Chain mainnet requires a funded private key signing a
real, irreversible transaction. Funding the 0G Compute ledger (minimum 3 0G) and writing to 0G
Storage have the same property. I do not execute financial transactions or spend funds on your
behalf, so what is delivered instead is everything up to the signature:

- the contract, its Foundry project, and a test suite covering both on-chain rules
- `contracts/script/Deploy.s.sol` with a chain-id guard
- `og-bridge/doctor.mjs`, which verifies every precondition without spending anything
- `og-bridge/fund.mjs`, which funds the Compute ledger behind an explicit `--yes`
- `scripts/publish_wave3.py`, which produces the scores and the submission artifact

**What you need to run** is in the README's "Going live" section. The 0G side is confirmed
reachable — the preflight found the storage indexer serving 6 trusted nodes and 2 Compute providers
with acknowledged TEE signers on Galileo — so the remaining work is funding and four commands.

### B3. The trained LightGBM model is off the Wave 3 path but not deleted

§3 puts a trained model out of scope. `config.SCORER` defaults to `quant`, the §5.1 weighted
formula, and every Wave 3 score uses it.

`continuum/scoring/structured.py` and `train.py` — the LightGBM implementation and its
borrower-grouped cross-validation from the earlier phase — are still in the tree and reachable with
`CONTINUUM_SCORER=structured`. Deleting working code to satisfy a scope boundary is destructive
rather than disciplined, and §3's reason for the exclusion ("no real default data exists yet to
train or backtest against") is an argument about evidence, not about the code. It is Phase 2
material, and the aggregator's scorer interface is what makes restoring it a config change.

**Nothing on the Wave 3 path imports LightGBM.** `aggregate.load_scorer` imports it lazily, so a
machine without it still scores.

### B4. The cohort is larger than §5.1 asks for

§5.1 says "5-10 invoice-financing borrowers with a few weeks of simulated revenue, repayment, and
invoice events". This build keeps the existing cohort: **12 borrowers × 18 months**, seeded at
`RANDOM_SEED = 20260811`.

**Why:** §4's two rules cannot be demonstrated on a few weeks of data. The staleness rule's whole
claim is that a score *keeps* degrading over sustained silence — the `feed_goes_dark` borrower goes
quiet at 88% through the history and the interesting behaviour is the ten weeks after that. The
cooldown rule needs a series of re-scores to hold anything back. A three-week cohort would make
both rules untestable and the demo unconvincing.

A superset of what the brief asks for, in the direction that makes the specified rules observable.
Flagged rather than assumed.

### B5. No reference lending pool

§3 excludes it. `continuum/consumption.py` implements the pure rate/LTV formula §5.4 asks to keep
in the repo, and `ContinuumScoreRegistry` computes the same curve on-chain — but nothing consumes
the score to move a live position.

---

## C. Decisions the brief does not specify

### C1. **YOUR CALL** — the §5.1 formula's weights and pivots

§5.1 names the four features and says "weighted", not what the weights are.

| Feature | Weight | Pivot | Span | Direction |
|---|---|---|---|---|
| `on_time_repayment_rate_180d` | 0.35 | 0.90 | 0.20 | higher better |
| `days_sales_outstanding` | 0.25 | 45 | 40 | lower better |
| `revenue_trend_90d` | 0.22 | 0.00 | 0.40 | higher better |
| `payer_concentration_top1_pct` | 0.18 | 0.40 | 0.40 | lower better |

Ordering reflects how directly each bears on getting paid back: the borrower's own track record on
this facility leads, then the receivable turning into cash, then direction of travel, then
concentration — which is a fragility multiplier rather than a present fact.

**These are a stated prior, not a fitted result.** Nothing in Wave 3 has the default outcomes needed
to fit them, and a formula whose coefficients were tuned until the synthetic cohort ranked nicely
would be a trained model with extra steps and no held-out set. Pivots are ordinary
invoice-financing operating values, not percentiles of the cohort being scored — so the scale does
not move when the generator does.

`config.QUANT_WEIGHTS`, `config.QUANT_PIVOTS`. **Replace before anything ships.**

### C2. The formula's uncertainty is asserted, not measured

A trained model supplies a cross-validated fold spread to the confidence interval. An unfitted
formula has none, and reporting zero would make the placeholder look *more* certain than the model
it stands in for — exactly backwards.

`config.QUANT_MODEL_VARIANCE = 0.45` is a fixed floor on the interval's `model_variance` term. Every
Wave 3 interval is visibly wider than a calibrated model's would be, which is the honest rendering
of "this scorer was never fitted". Replacing it with a real fold spread is a Phase 2 deliverable.

### C3. **YOUR CALL** — §4's staleness constants

§4 specifies the *rule* exactly and gives no numbers.

| Constant | Value | Note |
|---|---|---|
| `STALENESS_GRACE_HOURS` | 48 (floor) | Real grace is `max(FEED_SLA[feed].grace_hours, 48)` — see C4 |
| `STALENESS_POINTS_PER_DAY` | 3.2 | Per *weighted* silence-day; feed weights from `FEED_SLA` |
| `STALENESS_RATCHET` | on | The no-upward-reversal clause |

Calibrated so a month of total silence costs about a notch and a quarter is disqualifying — the rate
at which a credit committee would actually lose patience with an originator that stopped reporting.
Unbounded by construction: §4 says the score must *keep* degrading, and a bounded function of
silence stops costing.

### C4. Staleness grace is per-feed, floored — not flat

Not specified. A flat grace reads the document feed — which syncs monthly by design — as
permanently silent, which makes every borrower permanently stale, which means the ratchet can never
find a fully-fresh observation to anchor to and therefore never engages at all.

**This was a live bug**, caught by the `feed_goes_dark` borrower's score climbing 24 points while
two of its feeds were dark. Fixed by taking each feed's own `FEED_SLA.grace_hours` as its grace,
floored at the global minimum.

### C5. The ratchet chains to the previous score, not the pre-silence score

§4 says the score must not reverse upward "under continued silence". The looser reading — never
exceed the level you were at when the feeds went quiet — permits a borrower to fall to 675 and climb
back to 684 while still dark. That is still being rewarded for silence, just less.

So the published series is **monotone non-increasing while any feed is silent**. The ceiling chains
to the most recent recorded score; each was itself capped, so the anchor cannot drift upward. The
ratchet releases the moment a silent feed reports again: recovery is allowed on evidence, never on
the passage of time.

### C6. A feed that has never reported is silent, with no duration

Not specified. There is no start date to measure silence from, and inventing one would fabricate
evidence about how long it has run. So it adds no *duration* penalty but does engage the ratchet —
"this feed has never reported" is knowledge that the data is absent, not neutrality about it. Its
zero freshness already widens the interval through `data_quality_score`.

### C7. The on-chain rate curve is an integer approximation

§7 requires the ±50bps circuit breaker "at the contract level". §5.4's curve is exponential and
Solidity has no fractional exponent, so `_indicativeRateBps` splits the exponent into whole
doublings plus a linear interpolation across the remaining fraction of a PDO.

Error against the true exponential is under 6% mid-interval and exactly zero at every doubling —
well inside the ±50bps clamp that governs how far any single update can move, and vastly inside the
honesty of an unfitted scorecard. A fixed-point library would buy false precision.

### C8. Borrower ids are hashed for storage, kept as strings in events

§7's struct types `borrowerId` as `string`. A string mapping key costs a keccak per access anyway;
the explicit `bytes32` key makes that cost visible, and the event carries the readable id for the
dashboard and for judges' Explorer verification.

### C9. The registry records attestation status; it does not require it

`publishScore` takes an `attested` boolean and stores it. It cannot verify a TEE signature it never
sees, and a contract that pretended to would be the overclaim §11 warns against. A consumer filters
on the flag. `CONTINUUM_OG_REQUIRE_ATTESTATION=1` makes an unattested score a hard failure
off-chain, which is where the check can actually be made.

### C10. 0G writes are opt-in per run, not automatic

`config.OG_PUBLISH_ON_SCORE` defaults off. A backfill of twelve borrowers across fourteen weekly
checkpoints is 168 re-scores; having that spend gas because someone ran the ordinary daily command
would be a bad surprise on testnet and an expensive one on mainnet. `scripts/publish_wave3.py` is
the intended entry point.

### C11. The Compute provider should be pinned

`config.OG_COMPUTE_PROVIDER` is empty by default, which means "take the first marketplace service
with an acknowledged TEE signer". That is fine for a demo and wrong for anything reproducible — the
provider identity is part of what the attestation attests to. `doctor.mjs` prints the export line
for a specific provider. **Pin one before the submission run.**

### C16. An attestation upgrade publishes even when the score has not moved

Not specified. The publish gate's other rules all ask "has the score moved enough to be worth
gas?", which is the right question for drift and the wrong one for provenance: an unattested score
becoming one with a verified TEE signature does not change the number, it changes what the number
is worth to a pool filtering on `attested`.

One-directional, like the cooldown override. Losing attestation — a Compute outage, say — is not
news worth gas, so the better record stands until real score movement carries it. Checked before
the cooldown, which is safe because the rule fires only on the `false -> true` transition and so
cannot produce the update storm a cooldown exists to damp.

### C12. A Node bridge, because 0G has no Python SDK

The engine is Python (§8's stack table). 0G ships TypeScript and Go SDKs. `og-bridge/` is a thin
Node layer — one JSON object in on stdin, one out on stdout — behind `continuum/og/bridge.py`.

Reimplementing the broker's signing and micropayment settlement in Python to avoid a subprocess
would be the wrong trade: the bridge is small, and the vendor SDK is the thing that will track 0G's
changes. `OG_PRIVATE_KEY` is read by the Node side from its own environment and never crosses the
boundary in either direction; the bridge returns the signing *address*.

### C13. Everything 0G degrades visibly rather than failing closed

A score computed while 0G was unreachable is still a valid score. It carries `attestation.type =
"none"`, `storage_ref.provider = "local"` and no `chain_ref`, and the dashboard renders all three as
absences. This is the same discipline `ASSUMPTIONS.md #8` applied to a missing Claude key: the
honest failure is a visible gap, never a plausible-looking placeholder.

---

## C14. The 0G Compute provider enforces a 1.0 0G sub-account minimum

Discovered on the live testnet run, and it is the binding constraint on attestation:

- The SDK refuses to *create* a ledger below **3 0G**, claiming "the contract requires" it. **That
  is network-dependent, and the SDK states it as universal.** `LedgerManager.MIN_ACCOUNT_BALANCE`
  read from the chain is **0.1 0G on Galileo testnet** but **3.0 0G on mainnet**. So the guard is
  wrong on testnet and correct on mainnet. `fund.mjs --direct` skips it and still refuses anything
  below the *real* on-chain minimum, which it reads from the chain rather than assuming — that is
  why the same flag is safe on both networks and useful only on one.
- The **provider** separately requires **1.0 0G locked** in its sub-account, enforced server-side:
  `insufficient balance: your locked balance is 0.125 0G, but the required minimum is 1.0 0G`. There
  is no way around this one, and none should be sought — it is the provider's own policy.

Budget, per network:

| | testnet | mainnet |
|---|---|---|
| ledger minimum (on-chain) | 0.1 0G | **3.0 0G** |
| provider sub-account minimum | 1.0 0G | 1.0 0G (assumed same; provider-side) |
| deploy + a handful of publishes | ~0.02 0G | ~0.02 0G |
| **attested total** | **~1.2 0G** | **~3.1 0G** |
| **unattested (Storage + Chain only)** | ~0.03 0G | ~0.03 0G |

Mainnet has **12 Compute providers, all with acknowledged TEE signers** (testnet has 2), and 4
storage indexer nodes.

---

## D. What is not done

1. **Mainnet deployment and the first publish transactions.** See B2. Everything is proven on
   testnet; mainnet needs a funded key and is the one hard §3 criterion outstanding.
2. **Agentic ID (§5.5).** Correctly gated — see A3.
3. **A demo video, X post and pitch deck (§10).** Submission artifacts that need the mainnet
   address from step 1, and a person.
4. **An attested score.** The Compute path runs end to end — broker, provider selection, request
   signing — and fails at the provider's 1.0 0G sub-account minimum (C14). Every published score
   therefore carries `attestation.type = "none"`, which the payload, the artifact and the dashboard
   all state plainly. ~1.2 0G in the wallet closes this.

---

## E. Carried forward from the earlier phase

These `ASSUMPTIONS.md` entries still govern this build unchanged: **#3** (grade bands — §6's `742 →
A-` anchor still holds), **#5** (`data_quality_score` formula), **#6** (confidence-interval
derivation), **#7** (robust z-score anomaly layer), **#8** (offline fallback raises no flags at zero
confidence), **#10** (synthetic cohort composition — see B4), **#12** (append-only score log),
**#13** (disputes recorded, not adjudicated), **#14** (consumption formula illustrative), **#15**
(API is localhost-only and unauthenticated), **#16** (UTC, injectable clock), **#17** (jittered
cut-off), **#18** (scorecard construction — the quant score feeds the same `pd_to_points`), **#19**
(publish gating).

Superseded: **#1** (TreeSHAP — the quant formula's attribution is exactly additive by construction,
so no SHAP is involved on the Wave 3 path), **#11** (the Phase 0 attestation stub, now filled by 0G
Compute per §4), **#2** (0G Storage is now the canonical feature store; local files are the cache
§8's stack table describes).
