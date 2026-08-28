# Assumptions Register

Every decision in this codebase that `claude.md` does **not** specify. Numbered so code can
cite them (`# see ASSUMPTIONS #7`).

Each entry says what I assumed and how to change it. Entries marked **YOUR CALL** are product
decisions I made only to keep the PoC running — they are yours to overrule, and I flagged them
rather than letting them pass as settled.

---

## 1. `shap` replaced by LightGBM's native TreeSHAP

§7 requires "SHAP values or equivalent" and §14 lists "explainable via SHAP". I use
`booster.predict(X, pred_contrib=True)`, LightGBM's built-in exact TreeSHAP, instead of the
`shap` package.

**Why:** identical numbers for tree models — `shap.TreeExplainer` delegates to this same
routine — with no `numba`/`llvmlite` build dependency. Fewer moving parts on Windows.
**Cost:** no `shap` plotting helpers; the dashboard renders the waterfall itself.

## 2. Feature store is Parquet + JSON on local disk, not Postgres/TimescaleDB

§14 recommends Postgres + TimescaleDB + pgvector. Phase 0 uses `data/` files behind a
repository interface in `continuum/ingestion/store.py`.

**Why:** §14 also says "don't over-engineer the data layer for an MVP", and a PoC on one
machine should not need a database daemon. All access goes through the store module, so
swapping in Postgres is one file, not a refactor.
**When to change:** as soon as two processes write concurrently, or Phase 1 needs real
retention/audit guarantees.

## 3. **YOUR CALL** — Letter-grade → numeric band mapping

§9 gives exactly one anchor: `score_numeric: 742` ⇒ `"A-"`, and §7 says "e.g. AA–D". Credora's
public convention is a 1000-point scale. I derived a 15-grade ladder consistent with that anchor:

| Grade | Numeric band | | Grade | Numeric band |
|---|---|---|---|---|
| AAA | 900–1000 | | BB  | 610–649 |
| AA  | 850–899  | | BB- | 570–609 |
| A+  | 800–849  | | B   | 520–569 |
| A   | 770–799  | | B-  | 470–519 |
| A-  | 730–769  | ← §9's worked example (742) | CCC | 400–469 |
| BBB | 690–729  | ← base-rate anchor (700) | CC  | 330–399 |
| BBB-| 650–689  | | C   | 250–329 |
|     |          | | D   | 0–249 |

Bands are in `continuum/config.py:GRADE_BANDS`. The widths are mine and are not
calibrated to any real default-rate term structure — that needs the historical loan tape §17
flags as the cold-start problem. **Rating scales are customer-facing and a positioning decision
(§12 warns about the word "rating" specifically). Replace these before anything ships.**

## 4. **YOUR CALL** — Materiality thresholds for event-triggered re-scores

§7 requires an immediate re-score when a data event "crosses a materiality threshold" but never
sets one. My defaults (`continuum/config.py:MATERIALITY`):

| Trigger | Threshold |
|---|---|
| Robust z-score of any monitored feature | \|z\| ≥ 3.0 |
| Payment later than borrower's own pattern | ≥ 15 days beyond p90 of their history |
| New invoice dispute | any dispute on ≥ 5% of receivables by value |
| Top-payer concentration jump | +10 percentage points in 7 days |
| Provisional score delta vs. last published | ≥ 25 numeric points |
| Data-quality drop | `data_quality_score` falls ≥ 0.15 |

§13's "gaming the re-score cadence" note is why these are all borrower-relative (z-scores
against their own history) rather than absolute cutoffs a borrower could learn and sit under.

## 5. `data_quality_score` formula and staleness decay

§6 and §9 require the field and require confidence to degrade visibly on silence, but give no
formula. Mine, in `continuum/ingestion/quality.py`:

```
per-feed freshness  f_i = exp(-max(0, age_hours - grace_i) / halflife_i)
data_quality_score  = Σ(w_i · f_i · corroboration_i) / Σ w_i
```

Weights: invoice 0.35, bank 0.30, accounting 0.20, docs 0.10, on-chain 0.05. Grace periods and
half-lives per feed are in `config.py:FEED_SLA`. `corroboration_i` is 1.0 for direct-API sources
and 0.6 for self-reported/uploaded ones — that's §13's "treat single-source, unverifiable data
as lower-confidence input" made numeric.

Exponential decay (not a cliff) so a feed going quiet shows up as a gradual, visible slide.

## 6. Confidence-interval derivation

§7 and §9 require a CI, not a method. I widen a base interval multiplicatively:

```
half_width = base_sigma · (1 + w_dq·(1 - data_quality) + w_var·model_variance
                             + w_anom·anomaly_pressure + w_llm·(1 - llm_confidence))
```

`model_variance` combines two things, both read off the trained artifact rather than recomputed
per score: the cross-validated AUC spread across held-out folds (`cv_metrics.auc_fold_std`,
saturating at `CI_MODEL_FOLD_STD_REF`) and `novelty_share` — the fraction of this borrower's
features sitting outside the range the model was fitted on. The first says the model is
generally unstable; the second says it is out of its depth *on this borrower specifically*, which
is the one §17's cold-start problem actually bites on. **Not** a calibrated Bayesian posterior.
Honest framing: this is a heuristic uncertainty band. Real calibration needs out-of-sample
default outcomes (§17's cold-start problem again).

Each term, its weight, and its contribution to the final width are persisted per score in the
explanation artifact — see §7's explainability requirement, and §11: a borrower paying a wider
risk premium is entitled to know which of the four caused it.

## 7. Anomaly layer is a robust z-score, not a learned time-series model

§7 asks for "lightweight statistical or time-series model". I use median/MAD robust z-scores
over each borrower's own rolling history.

**Why:** ~40 observations per borrower is far too little to fit ARIMA or a learned detector
without overfitting. MAD is outlier-resistant, needs no training, and stays interpretable —
which matters because it can fire a re-score a borrower may dispute (§11).

## 8. **YOUR CALL** — Offline fallback when `ANTHROPIC_API_KEY` is absent

Not specified. `llm_agent.offline_flags` raises **no flags at all** and reports
`confidence = 0.0`, tagged `llm_flags.source = "offline_fixture"` and
`output_mode = "none"`. The same path handles every failed call — bad key, rate limit,
connection error — not only a missing key.

**It does not approximate the agent, and that is the point.** Zero confidence propagates
through `CI_WEIGHTS["llm_confidence"]` into a wider published interval and therefore a higher
risk premium under §11: the score gets visibly less certain because a scoring input is
missing, which is the same direction §6 requires for a stale feed. A keyword-matching stub was
the obvious alternative and was rejected — it would have produced flags indistinguishable in
shape from model output, and reading the synthetic `_truth` fields to make the offline demo
look sharp would make a system with no model attached appear to have a perfect one. That is
the overclaim §13 warns costs you institutional deals.

**Why fall back at all:** the §15 exit criterion is a working dashboard, and a missing key
shouldn't break the demo. **Risk you should know about:** a demo audience can still read
"no flags raised" as "nothing wrong" rather than "nothing was read". The `source` field, the
`rationale` string (which names the failure reason), the trigger detail line, and the
dashboard's `offline_fixture` badge are the mitigation. Tell me if you'd rather it hard-fail
instead.

## 9. Claude model pairing and `effort`

§14 says "Sonnet-class for volume, escalate to a stronger model for edge cases/disputes". So:
`claude-sonnet-5` for routine document passes, `claude-opus-5` on escalation (low LLM
confidence, suspected covenant breach, or an active dispute). Adaptive thinking on both.
`output_config.effort` = `"medium"` routine / `"high"` escalated. The effort levels are mine.

## 10. Synthetic cohort size and history length

"A small set of borrowers" — I generated **12 borrowers × 18 months** of daily cash flow,
invoices, and repayments, seeded at `RANDOM_SEED = 20260811` for reproducibility.

Cohort composition is deliberate, not random: 4 stable, 3 mildly deteriorating, 2 sharply
deteriorating (one of which defaults), 2 improving, 1 with a feed that goes dark mid-history.
The last one exists purely to exercise §6's staleness requirement.

**Not real data.** Nothing backtested here is evidence about real-world model performance.
§16's "backtested case study on public/historical data" needs a real loan tape.

## 11. `attestation` block in Phase 0

§9's payload has a `tee` attestation example; §8 wants TEE-first. Phase 0 is off-chain by your
constraint, so there is no enclave. Every payload carries:

```json
"attestation": {
  "type": "none",
  "provider": "phase_0_offchain_no_attestation",
  "measurement_hash": "<sha256 of model artifact + input feature record>",
  "signature": null
}
```

The hash is a real content digest — it gives a tamper-evident audit trail and is the natural
value to bind to an enclave measurement later — but it is **not** an attestation and the schema
says so out loud. §13's oracle-operator-risk note is why this isn't dressed up as more.

## 12. Score-history retention and the append-only log

Not specified. Every re-score appends to `data/scores/<borrower_id>.jsonl` and nothing is ever
overwritten, including scores later superseded by a dispute. §11's dispute flow needs an
immutable "what did we publish, when, and why" record to appeal against.

## 13. Dispute flow is recorded, not adjudicated

§11 requires a human-reviewable override path. Phase 0 implements the data model and the API
endpoint (`POST /borrowers/{id}/disputes`) plus dashboard display. There is no reviewer UI, no
auth, and no on-chain logging — §11's "logged on-chain" is Phase 1+ by your off-chain constraint.

## 14. Consumption-layer formula is illustrative only

§11's `f()` and `g()` are explicitly illustrative in the brief. `continuum/consumption.py`
implements a worked version (risk premium rising with both grade and CI width, LTV falling with
grade, ±50bps per-update cap, 24h per-borrower cooldown) to demonstrate the score is consumable.
**No pool exists.** The constants are placeholders — per-vertical tuning is yours.

## 15. FastAPI read layer

§14 lists Python and React/Vite but no API framework for Phase 0. FastAPI + uvicorn were
already installed in the environment, and §14 lists Node.js only for webhook handlers (not
needed until real ingestion). The API is **read-only except the dispute endpoint**, and has
**no authentication** — it binds localhost for the demo. Do not expose it.

## 16. Timezone and clock handling

All timestamps are UTC, ISO-8601 with a trailing `Z`, matching §9's examples. Scoring reads
"now" from an injectable clock (`continuum/clock.py`) so demo scenarios and backtests are
deterministic and don't depend on wall time.

## 17. Randomized scoring timing implemented as jitter

§13 recommends "event-driven + randomized-timing scoring". The daily run applies deterministic
per-borrower jitter (±90 min, derived from a hash of borrower ID + date) rather than scoring
everyone at midnight. Deterministic so it's reproducible; unpredictable to a borrower who
doesn't know the salt. The real defense is the event-driven path, not the jitter.

The jitter moves the **data cut-off** (`as_of`), not only the moment of publication. Randomising
publication alone would leave the cut-off at a predictable midnight, and §13's attack — "a
borrower timing cash movements around known scoring windows" — works just as well against a
predictable cut-off published at a random time. See `continuum/orchestrator.jitter_minutes`.

## 18. Score calibration — scorecard construction and the anchor

§7 says only "combine 1–3 into a single score ... recommend keeping Credora's convention of a
letter-grade scale". The construction is mine (`continuum/config.py`, applied in
`calibration.pd_to_points`):

```
points = ANCHOR - (PDO / ln 2) · (logit(PD) - logit(base_rate))
```

| Constant | Value | Why |
|---|---|---|
| `SCORE_ANCHOR_POINTS` | 700 | Score of a borrower sitting exactly at the model's own base rate. Lands mid-BBB, so the median borrower reads as ordinary rather than good or bad. |
| `SCORE_POINTS_TO_DOUBLE_ODDS` | 70 | Classic scorecard PDO: twice the odds of deterioration costs 70 points, anywhere on the curve. |
| `SCORE_PD_CLAMP` | (0.002, 0.98) | A boosted model on 636 rows emits PDs near 6e-4; `logit` of that is a 1200-point score — arithmetic precision standing in for evidence. |

**Anchored on the cohort's own base rate, not on an absolute default probability.** The model's
target is 90-day *deterioration* in a receivables book, not annualised default. Agency letters are
anchored to observed default frequencies; these are not. Mapping a deterioration probability onto
a default-anchored scale would silently overstate precision, which is exactly the overclaim §12
warns about — so the scale is relative and says so, in `calibration.py`'s module docstring and
here. **This is the number to change first when a real loan tape arrives (§17).**

## 19. **YOUR CALL** — Publish gating, and a deliberately one-sided cooldown

§10 asks for two things — "publish only on threshold-crossing changes" and "require a cooldown
between updates for the same borrower" — without numbers or precedence. Phase 0 has no chain, so
this is modelled as the `published_onchain` flag on every payload rather than a transaction; the
behaviour is what Phase 1 inherits. Implemented in `aggregate.publish_decision`.

| Rule | Value | Note |
|---|---|---|
| Threshold | `PUBLISH_THRESHOLD_POINTS` = 10 | Measured against the last **published** score, not the last computed one |
| Cooldown | `RESCORE_COOLDOWN_HOURS` = 6 | Applies to `event_*` triggers only; the daily checkpoint is not rate-limited |
| Grade move | always publishes | A letter change is what a consuming pool and a borrower both read |

Two decisions worth your explicit sign-off:

**Drift is cumulative, measured against the last published score.** Hop-to-hop, a borrower sliding
nine points a day never crosses a ten-point threshold; after a fortnight the registry is ~120
points stale with every individual decision defensible. §10's concern is the on-chain value's
error, which accumulates.

**The cooldown does not suppress downgrades that cross a grade boundary.** A fixed cooldown also
delays a collapse — a borrower falling off a cliff at 03:00 would hold their old grade until 09:00
while a pool lends against it. Damping noise at the cost of delaying bad news is the worse failure,
so a boundary-crossing downgrade overrides the cooldown and everything else waits. Same asymmetry,
and same reasoning, as `GRADE_CEILING_HALFWIDTH_MULTIPLE` (#3). **If you'd rather have a hard
cooldown with no exception — defensible, it's a stronger anti-manipulation stance — this is the
line to change.**

## 20. **YOUR CALL** — Claude API egress endpoint

The Anthropic SDK honours `ANTHROPIC_BASE_URL` from the environment, so a shell configured to
route through a proxy or an LLM gateway silently redirects this engine's egress too. What travels
over it is borrower documents: counterparty names, revenue figures, disputes, covenant terms.

Phase 0 **warns and proceeds** — `DocumentAgent.__init__` logs the endpoint whenever it is not
`api.anthropic.com`. That is right for synthetic data and wrong for real borrower data, where §12's
data-privacy requirement makes an unreviewed third-party hop a compliance problem rather than a
log line.

**Production default should be to hard-fail on an unexpected endpoint**, with an explicit
allowlist for whatever gateway your infrastructure actually sanctions. I did not make that the
Phase 0 default because it would break a demo on any machine with a gateway configured, for data
that carries no privacy risk. **This is the line to change before real data reaches it.**

## 21. `cash_runway_days` when spend is unobservable

Not specified. The obvious implementation — trailing-30-day burn with a divide-by-zero floor —
inverts the feature exactly when it matters most: a borrower who has defaulted and stopped paying
anyone, or whose bank feed has gone quiet, has zero observable burn, so a floored denominator
reports *unbounded* runway and the feature pins to its most flattering value. This was a live bug;
the defaulting borrower in the synthetic cohort read `cash_runway_days = 720.0` (the ceiling) three
weeks after default.

The denominator now widens 30d → 90d → 180d → full history until it finds observable spend, on the
reasoning that a cost base does not vanish when it stops being reported. Only when there is no
debit anywhere on record does runway read `0.0`, and `novelty_share` widens the interval for it.

**Related, and deliberate:** a repayment's terminal `defaulted` status is withheld from the feature
layer until it has sat unpaid for `features.DEFAULT_OBSERVATION_DAYS` (30, matching the training
label's lateness threshold). Surfacing it at `due_at` would hand the scorer the outcome the instant
it became true — the same lookahead the `_visible_*` helpers exist to prevent — and would make the
demo look sharper than the system is, with the score collapsing at the exact hour of default rather
than degrading across the weeks of missed payment that precede it.
