import { useState } from 'react'
import { fmtDateTime } from '../format.js'

// §11: "Build a borrower-facing dispute/appeal flow: if a borrower believes a downgrade is wrong
// (stale data, a payer dispute resolved after the score update), there needs to be a
// human-reviewable override path, logged."
//
// ASSUMPTIONS #13 records what Phase 0 actually implements — the data model, the endpoint and this
// display, with no reviewer UI and no auth. The copy below says so rather than implying an appeal
// gets adjudicated: filing records the appeal and triggers an escalation-tier document re-read
// whose result a human compares against the contested score. Nothing here republishes.

const REASONS = [
  ['stale_data', 'Data used was stale or missing'],
  ['resolved_dispute', 'A payer dispute has since been resolved'],
  ['incorrect_document', 'A document was misread or does not apply'],
  ['payer_error', 'The payer, not the borrower, caused the miss'],
  ['other', 'Other'],
]

export default function DisputePanel({ borrowerId, disputes, latest, maxChars, onFiled }) {
  const [reason, setReason] = useState('stale_data')
  const [narrative, setNarrative] = useState('')
  const [contact, setContact] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onFiled({
        reason,
        narrative,
        contact,
        disputed_score_ref: latest?.explainability_ref ?? null,
      })
      setNarrative('')
      setContact('')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {disputes?.length > 0 && (
        <div className="scroll-y" style={{ marginBottom: 16 }}>
          <table className="data">
            <thead>
              <tr>
                <th>Filed</th>
                <th>Reason</th>
                <th>Contested</th>
                <th>Re-read</th>
              </tr>
            </thead>
            <tbody>
              {[...disputes].reverse().map((d) => {
                const r = d.reassessment ?? {}
                return (
                  <tr key={d.dispute_id}>
                    <td className="dim">{fmtDateTime(d.filed_at)}</td>
                    <td className="mono" style={{ fontSize: 11.5 }}>
                      {d.reason}
                      {d.narrative && (
                        <div className="dim" style={{ fontSize: 11, fontFamily: 'var(--sans)' }}>
                          {d.narrative}
                        </div>
                      )}
                    </td>
                    <td className="mono">
                      {d.disputed_score} {d.disputed_score_numeric}
                    </td>
                    <td>
                      {r.performed ? (
                        <>
                          <span className="mono">
                            {r.score} {r.score_numeric}
                          </span>{' '}
                          <span
                            className={`badge ${
                              r.delta_vs_disputed > 0 ? 'ok' : r.delta_vs_disputed < 0 ? 'on' : 'off'
                            }`}
                          >
                            {r.delta_vs_disputed > 0 ? '+' : ''}
                            {r.delta_vs_disputed}
                          </span>
                        </>
                      ) : (
                        <span className="badge off" title={r.reason}>
                          not performed
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <form onSubmit={submit}>
        <label className="field">
          <span>Reason</span>
          <select value={reason} onChange={(e) => setReason(e.target.value)}>
            {REASONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>
            What is wrong with the score? ({narrative.length}/{maxChars ?? 2000})
          </span>
          <textarea
            value={narrative}
            maxLength={maxChars ?? 2000}
            onChange={(e) => setNarrative(e.target.value)}
            placeholder="Read by a human reviewer. It is never sent to a model — see below."
          />
        </label>
        <label className="field">
          <span>Contact (optional)</span>
          <input value={contact} onChange={(e) => setContact(e.target.value)} maxLength={200} />
        </label>

        {error && (
          <div className="callout error" style={{ marginBottom: 12 }}>
            {error}
          </div>
        )}

        <button className="primary" disabled={busy || !latest}>
          {busy ? 'Filing…' : 'File dispute'}
        </button>
      </form>

      <p className="note dim" style={{ marginTop: 12, marginBottom: 0 }}>
        Filing appends to an immutable log and triggers an escalation-model re-read of this
        borrower's documents at the contested timestamp. <strong>Nothing is republished</strong> —
        the re-read is a second opinion for a human reviewer, not an override, because an
        unauthenticated endpoint that could move a live rate is a worse failure than a slow appeal
        (ASSUMPTIONS #13). Your narrative is stored and shown to that reviewer; it is never placed
        in a model prompt, since the agent it would reach is the one §13 treats as under injection
        attack.
      </p>
    </>
  )
}
