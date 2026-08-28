// §7 part 2's output, rendered with its provenance attached.
//
// ASSUMPTIONS #8 is explicit about the risk this panel exists to mitigate: when no API key is
// present the agent raises no flags at zero confidence, and "a demo audience can still read 'no
// flags raised' as 'nothing wrong' rather than 'nothing was read'". So the offline case is not
// rendered as a clean bill of health — it gets its own badge, its own colour and the failure
// reason in full.
//
// `output_mode` is shown for the same reason. Schema-enforced and text-JSON output are both
// validated before use, but they are not equally trustworthy, and §7's "not a free-text opinion"
// requirement deserves to be visibly met rather than assumed.

const FLAGS = [
  ['covenant_breach', 'Covenant breach'],
  ['payer_deterioration', 'Payer deterioration'],
  ['adverse_news_detected', 'Adverse news'],
]

export default function LlmFlagsPanel({ flags, penalties, llmExplain }) {
  if (!flags) return <p className="empty">No document assessment on this score.</p>

  const offline = flags.source === 'offline_fixture'

  return (
    <>
      <div className="toggle-row">
        {offline ? (
          <span className="badge on">offline_fixture — no model read these documents</span>
        ) : (
          <>
            <span className="badge info">{flags.model_used || 'claude'}</span>
            {flags.escalated && <span className="badge warn">escalated</span>}
            <span className={`badge ${flags.output_mode === 'schema_enforced' ? 'ok' : 'warn'}`}>
              {flags.output_mode}
            </span>
          </>
        )}
        <span className="badge">confidence {flags.confidence.toFixed(2)}</span>
      </div>

      <table className="data">
        <thead>
          <tr>
            <th>Flag</th>
            <th>State</th>
            <th className="num">Penalty if raised</th>
          </tr>
        </thead>
        <tbody>
          {FLAGS.map(([key, label]) => (
            <tr key={key}>
              <td>{label}</td>
              <td>
                {flags[key] ? (
                  <span className="badge on">raised</span>
                ) : (
                  <span className="badge off">{offline ? 'not assessed' : 'clear'}</span>
                )}
              </td>
              <td className="num dim">−{penalties?.[key] ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {llmExplain?.penalty_points !== undefined && llmExplain.penalty_points !== 0 && (
        <p className="note" style={{ marginTop: 10, marginBottom: 0 }}>
          Applied: <span className="mono down">{llmExplain.penalty_points.toFixed(1)}</span> points,
          capped at {llmExplain.penalty_cap}. Confidence discounts a raised flag but never erases
          it — a floor keeps hedging from being free.
        </p>
      )}

      {flags.rationale && (
        <p className="note" style={{ marginTop: 10, marginBottom: 0, color: 'var(--muted)' }}>
          {flags.rationale}
        </p>
      )}

      {flags.evidence_refs?.length > 0 && (
        <p className="note dim" style={{ marginTop: 6, marginBottom: 0 }}>
          Evidence: <span className="mono">{flags.evidence_refs.join(', ')}</span> — citations are
          filtered against the documents actually shown, so a hallucinated reference never reaches
          the audit trail.
        </p>
      )}

      {offline && (
        <div className="callout error" style={{ marginTop: 12, marginBottom: 0 }}>
          <strong>No document assessment was made.</strong> This is not "nothing is wrong" — it is
          "nothing was read". Zero confidence widens the published interval and therefore raises the
          risk premium under §11, which is the same direction §6 requires for a stale feed. Set{' '}
          <span className="mono">ANTHROPIC_API_KEY</span> and re-run to get a real judgement.
        </div>
      )}
    </>
  )
}
