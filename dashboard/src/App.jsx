import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import {
  fmtDateTime,
  fmtSigned,
  gradeTone,
  triggerLabel,
} from './format.js'

import AnomalyPanel from './components/AnomalyPanel.jsx'
import DataQualityPanel from './components/DataQualityPanel.jsx'
import DisputePanel from './components/DisputePanel.jsx'
import DocumentsPanel from './components/DocumentsPanel.jsx'
import IntervalBreakdown from './components/IntervalBreakdown.jsx'
import LlmFlagsPanel from './components/LlmFlagsPanel.jsx'
import OGPanel, { OGProofPanel } from './components/OGPanel.jsx'
import Roster from './components/Roster.jsx'
import ScoreBuildUp from './components/ScoreBuildUp.jsx'
import ScoreHistoryChart from './components/ScoreHistoryChart.jsx'
import ShapWaterfall from './components/ShapWaterfall.jsx'
import StalenessPanel from './components/StalenessPanel.jsx'
import TermsPanel from './components/TermsPanel.jsx'

export default function App() {
  const [meta, setMeta] = useState(null)
  const [og, setOg] = useState(null)
  const [health, setHealth] = useState(null)
  const [borrowers, setBorrowers] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  // The explanation currently on screen. Defaults to the latest score's; clicking a point on the
  // chart loads that one instead, so every panel below the chart describes the same re-score.
  // Splitting them lets the header show one score while the waterfall explains another, which for
  // an explainability surface is the one mistake worth designing against.
  const [explanation, setExplanation] = useState(null)
  const [explanationRef, setExplanationRef] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([api.meta(), api.health(), api.borrowers(), api.og()])
      .then(([m, h, b, o]) => {
        setMeta(m)
        setHealth(h)
        setBorrowers(b)
        setOg(o)
        if (b.length) setSelectedId((cur) => cur ?? b[0].borrower_id)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const loadBorrower = useCallback(async (id) => {
    const d = await api.borrower(id)
    setDetail(d)
    setExplanation(d.explanation)
    setExplanationRef(d.latest?.explainability_ref ?? null)
    return d
  }, [])

  useEffect(() => {
    if (!selectedId) return
    setDetail(null)
    setExplanation(null)
    loadBorrower(selectedId).catch((e) => setError(e.message))
  }, [selectedId, loadBorrower])

  async function selectScore(score) {
    if (!score?.explainability_ref || score.explainability_ref === explanationRef) return
    setExplanationRef(score.explainability_ref)
    try {
      setExplanation(await api.explanation(selectedId, score.explainability_ref))
    } catch (e) {
      // An explanation can legitimately be absent for a score written before this artifact
      // existed. Say so rather than leaving the previous score's trail on screen under a new
      // header — a stale waterfall is worse than an empty one.
      setExplanation(null)
      setError(e.message)
    }
  }

  async function fileDispute(body) {
    await api.fileDispute(selectedId, body)
    await loadBorrower(selectedId)
  }

  if (loading) return <div className="main">Loading…</div>

  if (error && !detail) {
    return (
      <div className="main">
        <div className="callout error">
          <strong>Cannot reach the scoring API.</strong> {error}
          <div style={{ marginTop: 8 }} className="mono">
            python -m uvicorn continuum.api:app --port 8787
          </div>
        </div>
      </div>
    )
  }

  const scores = detail?.scores ?? []
  const shown = scores.find((s) => s.explainability_ref === explanationRef) ?? detail?.latest
  const record = detail?.feature_record

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="masthead">
          <img className="mark" src="/mark.svg" alt="" width="34" height="34" />
          <div>
            <h1>Continuum</h1>
            <p>Continuous credit scoring · invoice financing</p>
            <span className="ver mono">{meta?.model_version}</span>
          </div>
        </div>
        <Roster borrowers={borrowers} selectedId={selectedId} onSelect={setSelectedId} />
      </aside>

      <main className="main">
        {health?.next_step && (
          <div className="callout">
            The engine has not produced scores yet. Next step:{' '}
            <span className="mono">{health.next_step}</span>
          </div>
        )}

        {/* §8/§13. Served by the API rather than written into the frontend, so the disclosure
            cannot be edited out of the UI without editing the engine that makes the claim. */}
        <div className="callout trust">
          <strong>Wave 3 — {meta?.scorer?.kind === 'quant' ? 'weighted formula, not a trained model' : meta?.scorer?.kind}.</strong>{' '}
          {meta?.scorer?.statement}
        </div>

        <section className="panel">
          <h2>0G integration proof</h2>
          <p className="note">
            §10 — the mainnet contract, its Explorer link, and every score that reached it.
          </p>
          <OGProofPanel og={og} />
        </section>

        {!detail ? (
          <p className="empty">Select a borrower.</p>
        ) : (
          <>
            <header className="detail-head">
              <span className={`grade lg ${gradeTone(shown?.score)}`}>{shown?.score ?? '—'}</span>
              <div>
                <h2>{detail.borrower.name}</h2>
                <div className="meta">
                  {detail.borrower.borrower_id} · {detail.borrower.sector} ·{' '}
                  {detail.borrower.archetype}
                </div>
              </div>

              <div className="headline-numbers">
                <div className="stat">
                  <div className="label">Score</div>
                  <div className="value">
                    {shown?.score_numeric ?? '—'}
                    {shown?.prior_score_numeric != null && shown.score_delta !== 0 && (
                      <span
                        className={shown.score_delta > 0 ? 'up' : 'down'}
                        style={{ fontSize: 13, marginLeft: 5 }}
                      >
                        {fmtSigned(shown.score_delta)}
                      </span>
                    )}
                  </div>
                </div>
                <div className="stat">
                  <div className="label">Interval</div>
                  <div className="value sm">
                    {shown ? `${shown.confidence_interval[0]}–${shown.confidence_interval[1]}` : '—'}
                  </div>
                </div>
                <div className="stat">
                  <div className="label">Data quality</div>
                  <div className="value sm">{shown?.data_quality_score?.toFixed(2) ?? '—'}</div>
                </div>
                <div className="stat">
                  <div className="label">Published</div>
                  <div className="value sm">
                    {shown?.published_onchain ? (
                      <span className="badge ok">yes</span>
                    ) : (
                      <span className="badge off">held</span>
                    )}
                  </div>
                </div>
              </div>
            </header>

            {/* §7: "Publish a 'last updated' and 'trigger reason' alongside every score — this is
                your entire value proposition made visible; don't bury it." So it sits directly
                under the headline, not in a detail drawer. */}
            <div className="callout">
              <strong>{triggerLabel(shown?.trigger_reason)}</strong>
              <span className="dim mono" style={{ fontSize: 11 }}>
                {' '}
                {shown?.trigger_reason}
              </span>
              <br />
              data as of {fmtDateTime(shown?.as_of)} · scored{' '}
              {fmtDateTime(shown?.published_at)}
              {shown?.triggered_by_detail && (
                <>
                  <br />
                  {shown.triggered_by_detail}
                </>
              )}
            </div>

            <section className="panel">
              <h2>Score history</h2>
              <p className="note">
                {scores.length} re-scores, {scores.filter((s) => s.published_onchain).length}{' '}
                cleared §10's publish gate.
              </p>
              <ScoreHistoryChart
                scores={scores}
                gradeBands={meta?.grade_bands}
                selectedRef={explanationRef}
                onSelect={selectScore}
              />
            </section>

            <div className="grid-2">
              <section className="panel">
                <h2>How this score was built</h2>
                <p className="note">
                  The arithmetic in the order the engine performed it (§7 part 4).
                </p>
                <ScoreBuildUp explanation={explanation} />
              </section>

              <section className="panel">
                <h2>Why the interval is this wide</h2>
                <p className="note">Four terms, each published with its weight (ASSUMPTIONS #6).</p>
                <IntervalBreakdown explanation={explanation} />
              </section>
            </div>

            <section className="panel">
              <h2>Explainability trail — feature attribution</h2>
              <p className="note">
                §7: a feature-attribution breakdown available on request, and the defence if this
                borrower disputes a downgrade.
              </p>
              <ShapWaterfall attribution={explanation?.feature_attribution} />
            </section>

            <div className="grid-2">
              <section className="panel">
                <h2>Data quality &amp; staleness</h2>
                <p className="note">
                  §6 — the score must degrade in confidence when a source goes quiet, not freeze.
                </p>
                <DataQualityPanel record={record} feedSla={meta?.feed_sla} />
              </section>

              <section className="panel">
                <h2>Staleness rule (§4)</h2>
                <p className="note">
                  Silence is worsening information — what it took off this score, and whether the
                  ratchet is holding.
                </p>
                <StalenessPanel
                  staleness={explanation?.staleness}
                  meta={meta?.staleness}
                />
              </section>

              <section className="panel">
                <h2>Document reasoning agent</h2>
                <p className="note">§7 part 2 — structured flags from unstructured evidence.</p>
                <LlmFlagsPanel
                  flags={shown?.llm_flags ?? record?.llm_flags}
                  penalties={meta?.llm_flag_penalties}
                  llmExplain={explanation?.llm}
                />
              </section>
            </div>

            <div className="grid-2">
              <section className="panel">
                <h2>Early-warning layer</h2>
                <p className="note">§7 part 3 — what decided whether to score out of cadence.</p>
                <AnomalyPanel anomaly={explanation?.anomaly} materiality={meta?.materiality} />
              </section>

              <section className="panel">
                <h2>Publish decision</h2>
                <p className="note">
                  §10 — threshold-crossing and cooldown discipline, modelled now so Phase 1
                  inherits it.
                </p>
                {explanation?.publish_decision ? (
                  <>
                    <div className="toggle-row">
                      <span
                        className={`badge ${
                          explanation.publish_decision.published_onchain ? 'ok' : 'off'
                        }`}
                      >
                        {explanation.publish_decision.published_onchain
                          ? 'published'
                          : 'recorded, not republished'}
                      </span>
                      <span className="badge">
                        threshold {explanation.publish_decision.threshold_points} pts
                      </span>
                      <span className="badge">
                        cooldown {explanation.publish_decision.cooldown_hours}h
                      </span>
                    </div>
                    <p style={{ margin: 0, fontSize: 12.5 }}>
                      {explanation.publish_decision.reason}
                    </p>
                    {explanation.publish_decision.measured_against && (
                      <p className="note dim" style={{ marginTop: 8, marginBottom: 0 }}>
                        Measured against the last <em>published</em> score —{' '}
                        {explanation.publish_decision.measured_against.score}/
                        {explanation.publish_decision.measured_against.score_numeric} on{' '}
                        {fmtDateTime(explanation.publish_decision.measured_against.published_at)} —
                        not the last computed one, so slow drift cannot escape the gate one
                        defensible hop at a time.
                      </p>
                    )}
                    <p className="note dim" style={{ marginTop: 8, marginBottom: 0 }}>
                      Attestation{' '}
                      <span className="mono">{explanation.attestation?.type}</span> ·{' '}
                      <span className="mono">{explanation.attestation?.provider}</span>
                      <br />
                      <span className="mono" style={{ fontSize: 10.5 }}>
                        {explanation.attestation?.measurement_hash?.slice(0, 26)}…
                      </span>
                    </p>
                  </>
                ) : (
                  <p className="empty">No publish decision recorded.</p>
                )}
              </section>
            </div>

            <section className="panel">
              <h2>0G references for this score</h2>
              <p className="note">
                §5.2, §5.3, §5.4 — the attestation, the stored feature record, and the registry
                transaction, each linked to where it can be checked.
              </p>
              <OGPanel og={og} payload={shown} />
            </section>

            <section className="panel">
              <h2>Consumption layer — pool terms</h2>
              <p className="note">
                §11 — a transparent formula over the published score, not the model setting a rate.
              </p>
              <TermsPanel terms={detail.terms} receivables={detail.eligible_receivables} />
            </section>

            <div className="grid-2">
              <section className="panel">
                <h2>Documents on file</h2>
                <p className="note">The unstructured Layer 1 source, as the agent received it.</p>
                <DocumentsPanel
                  documents={detail.documents}
                  evidenceRefs={shown?.llm_flags?.evidence_refs}
                />
              </section>

              <section className="panel">
                <h2>Dispute this score</h2>
                <p className="note">§11 — the human-reviewable appeal path.</p>
                <DisputePanel
                  borrowerId={selectedId}
                  disputes={detail.disputes}
                  latest={detail.latest}
                  maxChars={meta?.dispute?.narrative_max_chars}
                  onFiled={fileDispute}
                />
              </section>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
