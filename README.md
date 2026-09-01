# Continuum — verifiable continuous credit rating on 0G

Continuous AI credit scoring for the **invoice-financing** vertical of RWA lending. Borrowers are
re-scored from live financial signal instead of once at origination, and the score is published on
0G Chain in a form a lending pool can consume directly.

**Dashboard:** https://continuumonx.up.railway.app — read-only, serving the published
score record.

**Live on 0G Chain mainnet** — [`0x96406Be24513D9eCDE067a8CBa27eEeb3e1A7b3C`](https://chainscan.0g.ai/address/0x96406Be24513D9eCDE067a8CBa27eEeb3e1A7b3C)
(chain 16661, block 43138713) — **15 score-publish transactions, 12 carrying verified 0G Compute
TEE attestations**, across all 12 borrowers. Transactions and on-chain parameters are enumerated in [`deployments/integration_proof_mainnet.json`](deployments/integration_proof_mainnet.json),
which is generated from the registry's own `ScorePublished` events rather than from a build log.

> **Verifying the contract.** The source has moved on since deployment, so building from `main`
> will not reproduce the deployed bytecode. Build from the tag
> [`mainnet-registry-0x96406Be2`](https://github.com/0xileri/Continuum/tree/mainnet-registry-0x96406Be2) — solc 0.8.24, optimizer on at 200 runs, `via_ir`, evm `paris`.
> `eth_getCode` at the address above is byte-for-byte identical to that build's
> `deployedBytecode`, metadata hash included.

Built for **0G Bridge by AKINDO — Wave 3**. Scope, decisions and open questions live in
[`docs/wave3-brief.pdf`](docs/wave3-brief.pdf) (the brief) and [`WAVE3.md`](WAVE3.md) (what this build
assumed, reduced or left open).

---

## The one-paragraph version

An AI credit score is only worth anything if you can tell it wasn't fabricated. Continuum runs its
document-reasoning step on **0G Compute**, where a TEE-held key signs the response and the broker
verifies that signature before the output is allowed to move a score. The Borrower Feature Record
behind each score goes to **0G Storage**, and the published score — with its confidence interval,
its trigger reason, its attestation reference and its storage root hash — lands in
`ContinuumScoreRegistry` on **0G Chain**, which enforces the cooldown and circuit-breaker rules in
bytecode rather than in the operator's own off-chain code.

---

## Architecture

```
                        ┌──────────────────────────────────────┐
   synthetic borrower   │  Layer 1 — ingestion                 │
   events (invoices,    │  continuum/synth, continuum/ingestion│
   bank, repayments,    │  → §6 Borrower Feature Record        │
   documents, feed      └───────────────┬──────────────────────┘
   heartbeats)                          │
                                        ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │  Layer 2 — scoring                                                 │
   │                                                                    │
   │   §5.1 weighted quant score      §5.1 reasoning agent              │
   │   continuum/scoring/quant.py     ─────────────────────┐            │
   │   (4 features, not a model)                           │            │
   │                                          ┌────────────▼──────────┐ │
   │   §4 staleness rule                      │  0G COMPUTE           │ │
   │   continuum/scoring/staleness.py         │  TEE-signed inference │ │
   │                                          │  → llm_flags          │ │
   │   §7 anomaly / early warning             │  → attestation        │ │
   │   continuum/scoring/anomaly.py           └────────────┬──────────┘ │
   │                                                       │            │
   │   aggregation + calibration ◄─────────────────────────┘            │
   │   continuum/scoring/aggregate.py  → §6 Score Publication Payload   │
   └───────────────┬──────────────────────────────┬─────────────────────┘
                   │                              │
                   ▼                              ▼
     ┌───────────────────────────┐   ┌──────────────────────────────────┐
     │  0G STORAGE               │   │  0G CHAIN                        │
     │  feature record + docs    │   │  ContinuumScoreRegistry.sol      │
     │  → merkle root hash ──────┼──►│  cooldown + ±50bps breaker       │
     └───────────────────────────┘   │  enforced on-chain (§7)          │
                                     └────────────────┬─────────────────┘
                                                      │
                                     ┌────────────────▼─────────────────┐
                                     │  Dashboard (React + Vite)        │
                                     │  score history, trigger reason,  │
                                     │  0G Explorer / Storage links     │
                                     └──────────────────────────────────┘
```

| 0G component | What it does here | Code |
|---|---|---|
| **0G Compute** | Runs the document-reasoning call inside a TEE; `processResponse` verifies the provider's signature before the flags move a score | `og-bridge/compute.mjs`, `continuum/og/compute.py` |
| **0G Storage** | Canonical, tamper-evident copy of every Borrower Feature Record; only the merkle root goes on-chain | `og-bridge/storage.mjs`, `continuum/og/storage.py` |
| **0G Chain** | `ContinuumScoreRegistry` — the published score, plus §4's cooldown and §5.4's circuit breaker in bytecode. Live at [`0x96406Be2…`](https://chainscan.0g.ai/address/0x96406Be24513D9eCDE067a8CBa27eEeb3e1A7b3C) | `contracts/src/ContinuumScoreRegistry.sol` |

---

## Scope, stated up front

Three things are true and worth reading before the demo, not after.

**1. The aggregation step does not run inside an 0G Compute job — the reasoning call does.**
§5.2 offers this exact fallback and asks for it to be flagged rather than mocked. 0G Compute serves
inference and fine-tuning against registered providers; it does not execute arbitrary code, so there
is no call shape that would take Continuum's aggregation function. The reasoning call is genuinely
attested; the arithmetic runs off-chain and is bound to its inputs by a local `measurement_hash`.
Full reasoning in [`WAVE3.md` §A1](WAVE3.md).

**2. The quant score is a weighted formula, not a trained model.** §3 puts XGBoost/LightGBM out of
scope because no real default data exists yet to fit or backtest against. Every confidence interval
carries a fixed model-variance floor so that "never fitted" shows up as a number rather than as a
caveat. The earlier phase's LightGBM implementation is still in the tree behind
`CONTINUUM_SCORER=structured`, off the Wave 3 path.

**3. Single operator.** No multi-operator consensus, no claim of trustlessness. The 0G attestation
proves a genuine enclave produced the reasoning output; nothing here proves the underlying invoice
data was real. Direct-API integration over borrower-supplied documents is the Phase 2 mitigation.

---

## Quickstart (no 0G, no keys, no cost)

The whole engine runs locally with the 0G writes disabled. Scores publish with
`attestation.type = "none"` and no chain reference, and the dashboard renders those as absences.

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt

# 1. synthetic cohort  → data/raw/
python -m continuum.synth.generate

# 2. score everyone across a trailing window → data/scores/
CONTINUUM_LLM_BACKEND=offline python -m continuum.orchestrator backfill --weeks 14

# 3. read layer + dashboard
python -m uvicorn continuum.api:app --port 8787
cd dashboard && npm install && npm run dev        # http://localhost:5173
```

Inspect the pieces on their own:

```bash
python -m continuum.scoring.quant          # §5.1 — the formula, and the cohort it produces
python -m continuum.scoring.staleness      # §4 — silence priced, with the ratchet biting
python -m continuum.scoring.anomaly        # §7 — where the event path would have fired
python -m continuum.consumption            # §5.4 — the rate/LTV curve
python -m continuum.orchestrator show --borrower brw_01hxk6j9m3   # the feed_goes_dark borrower
```

`brw_01hxk6j9m3` is the one to look at: two feeds go dark two thirds of the way through its history,
and §4's rule turns that into a score that keeps falling and never once ticks up.

Tests:

```bash
python -m pytest              # 116 tests — scale, staleness, publish gate, consumption, attestation
cd contracts && forge test    # 23 tests — the on-chain cooldown and circuit breaker
```

---

## Going live on 0G

Everything below spends 0G. Run the preflight first — it costs nothing and checks every
precondition, printing the exact fix for each.

```bash
cd og-bridge && npm install
export OG_PRIVATE_KEY=0x...          # never commit this
node og-bridge/doctor.mjs
```

**1. Fund the wallet.** Galileo testnet: https://faucet.0g.ai

**2. Fund the 0G Compute ledger** (on-chain minimum is 0.1 0G; the scoring path deliberately never does this for
you):

```bash
node og-bridge/fund.mjs --amount 0.2 --yes
```

**3. Deploy the registry — testnet first**, per §9's Day 3 sequencing:

```bash
cd contracts
forge script script/Deploy.s.sol:Deploy --rpc-url https://evmrpc-testnet.0g.ai --broadcast
export CONTINUUM_REGISTRY_ADDRESS=0x...
```

**4. Publish.** Pin a Compute provider first — the provider identity is part of what the attestation
attests to, and `doctor.mjs` prints the export line for one with an acknowledged TEE signer:

```bash
export CONTINUUM_OG_COMPUTE_PROVIDER=0x...
export CONTINUUM_OG_PUBLISH=1
python scripts/publish_wave3.py --limit 5 --yes
```

**5. Promote to mainnet** once testnet looks right. `CONTINUUM_OG_NETWORK=mainnet` moves the RPC,
the explorer, the storage indexer and the dashboard's links together:

```bash
export CONTINUUM_OG_NETWORK=mainnet
node og-bridge/doctor.mjs                       # re-check everything on the new network
cd contracts && forge script script/Deploy.s.sol:Deploy --rpc-url https://evmrpc.0g.ai --broadcast
export CONTINUUM_REGISTRY_ADDRESS=0x...
python scripts/publish_wave3.py --limit 5 --yes
```

`publish_wave3.py` writes `deployments/integration_proof_<network>.json` — the contract address,
every publish transaction with its Explorer URL, and each score's storage root and attestation job
id. That file is the §10 submission artifact.

By default it **skips any borrower whose attestation did not verify**, because an unattested score
shown as Integration Proof is precisely the overclaim §11 warns about. `--allow-unattested`
overrides that deliberately.

### Deploying the dashboard

The read-only dashboard deploys itself: pushing to `main` runs
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml), which builds this checkout and
ships it to Railway.

It needs one secret, once — `RAILWAY_TOKEN`, a **project** token from the Railway dashboard
(project → Settings → Tokens), added under GitHub → Settings → Secrets and variables → Actions.

The workflow exists because Railway's own push-to-deploy is a GitHub App webhook that was never
firing for this repo, so every deploy needed the source re-attached by hand. Connecting the App
properly is the better fix and needs admin on the repository; this works without it, and becomes
redundant rather than harmful if the App is connected later.

It uses `railway up` rather than `railway redeploy` deliberately: `redeploy` reuses the previous
build and would silently ship stale code, which is the exact failure the workflow exists to
prevent.

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `OG_PRIVATE_KEY` | — | Signs 0G transactions. Read only by the Node bridge; never crosses into Python. |
| `CONTINUUM_OG_NETWORK` | `testnet` | `testnet` \| `mainnet`. Moves RPC, explorer, indexer and dashboard links together. |
| `CONTINUUM_REGISTRY_ADDRESS` | from `deployments/` | Deployed registry. |
| `CONTINUUM_OG_COMPUTE_PROVIDER` | first acknowledged | Pin for reproducibility. |
| `CONTINUUM_OG_PUBLISH` | off | Whether scoring also writes to 0G Storage and the chain. |
| `CONTINUUM_OG_REQUIRE_ATTESTATION` | off | Make an unverified attestation a hard failure. Set it for submission runs. |
| `CONTINUUM_LLM_BACKEND` | `0g-compute` | `0g-compute` \| `anthropic` \| `offline`. |
| `CONTINUUM_SCORER` | `quant` | `quant` (§5.1) \| `structured` (the deferred LightGBM path). |

---

## The two rules §4 says to follow exactly

**Cooldown — boundary-crossing downgrades always publish.** A fixed cooldown damps noise and also
delays a collapse. A pool must never lend against a stale grade when risk has clearly worsened, so a
downgrade that crosses a grade boundary overrides the window; everything else, upgrades included,
waits. Enforced in `aggregate.publish_decision` **and** in `ContinuumScoreRegistry.publishScore`, so
the guarantee does not depend on the operator's own code.

**Staleness — silence is worsening information.** Two failure modes had to be closed:

- *Plateau.* An exponential freshness decay asymptotes and the interval caps, so a decay-only design
  stops costing after a few weeks — a borrower dark for a year lands on the same letter as one dark
  for a month. So the penalty is **linear and unbounded in duration**: 3.2 points per weighted
  silence-day, forever.
- *Reversal.* `days_since_last_late_payment` counts up with no new data, so silence reads as good
  behaviour. So a **ratchet** holds the published score monotone non-increasing while any feed is
  silent, releasing the moment a feed reports again. Recovery is allowed on evidence, never on the
  passage of time.

Both are in `continuum/scoring/staleness.py`, with the constants and the reasoning in
[`WAVE3.md` §C3–C6](WAVE3.md).

---

## Repository

```
continuum/            the engine
  scoring/quant.py        §5.1 weighted score        scoring/staleness.py   §4
  scoring/aggregate.py    the fan-in                 scoring/anomaly.py     §7 early warning
  scoring/attestation.py  §6 attestation block       consumption.py         §5.4 rate/LTV
  og/                     0G integration (Python side)
  api.py                  read layer + §11 dispute endpoint
contracts/            Foundry — ContinuumScoreRegistry.sol + 23 tests
og-bridge/            Node bridge to the 0G SDKs (0G has no Python SDK)
dashboard/            React + Vite
scripts/              publish_wave3.py, demo_event.py
tests/                116 pytest tests
```

## Docs

- [`WAVE3.md`](WAVE3.md) — **read this first.** Every assumption, scope reduction and open question,
  including the three §12 questions and what is not done.
- [`DESIGN.md`](DESIGN.md) — the design system, in the `DESIGN.md` format an AI agent reads to
  keep generated UI consistent. Nine sections plus colour and type tokens.
- [`docs/wave3-brief.pdf`](docs/wave3-brief.pdf) — the source brief. Authoritative wherever it
  disagrees with this README.
- [`ASSUMPTIONS.md`](ASSUMPTIONS.md) — the earlier off-chain phase's decisions. Still governs the
  parts of the engine Wave 3 did not touch; `WAVE3.md` §E says which.
- [`claude.md`](claude.md) — the original Phase 0 product pack, **superseded** by the Wave 3 brief
  but kept because `ASSUMPTIONS.md` cites its section numbers throughout.
