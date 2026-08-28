import { fmtPct } from '../format.js'

// The arithmetic that produced the published number, in the order `aggregate.score` performs it.
//
// This exists because §11 gives a borrower the right to dispute a downgrade, and a score they
// cannot reconstruct is not disputable. Every row here is read straight from the explanation
// artifact — the dashboard does no arithmetic of its own, so a discrepancy between this panel and
// the engine is impossible rather than merely unlikely.

export default function ScoreBuildUp({ explanation }) {
  const b = explanation?.score_build_up
  if (!b) return null

  const rows = [
    {
      label: 'Structured model',
      detail: `PD ${(b.pd * 100).toFixed(2)}% against a ${(b.base_rate * 100).toFixed(1)}% base rate`,
      value: b.points_from_model.toFixed(1),
    },
    {
      label: 'Document agent',
      detail:
        explanation.llm?.flags_raised?.length
          ? `${explanation.llm.flags_raised.join(', ')} at confidence ${explanation.llm.confidence?.toFixed(2)}`
          : 'no flags raised',
      value: b.llm_penalty.toFixed(1),
      tone: b.llm_penalty < 0 ? 'down' : undefined,
    },
    {
      label: '= after documents',
      detail: '',
      value: b.points_after_llm.toFixed(1),
      strong: true,
    },
    {
      label: 'Grade ceiling',
      detail: b.ceiling_binding
        ? 'BINDING — uncertainty is capping the attainable grade (§6)'
        : 'not binding',
      value: b.grade_ceiling.toFixed(1),
      tone: b.ceiling_binding ? 'down' : undefined,
    },
  ]

  return (
    <>
      <table className="data">
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td style={{ fontWeight: r.strong ? 600 : 400 }}>{r.label}</td>
              <td className="dim" style={{ fontSize: 11.5 }}>
                {r.detail}
              </td>
              <td className={`num ${r.tone ?? ''}`} style={{ fontWeight: r.strong ? 600 : 400 }}>
                {r.value}
              </td>
            </tr>
          ))}
          <tr>
            <td style={{ fontWeight: 700 }}>Published</td>
            <td className="dim" style={{ fontSize: 11.5 }}>
              band {b.grade_band?.[0]}–{b.grade_band?.[1]} · interval{' '}
              {b.confidence_interval?.[0]}–{b.confidence_interval?.[1]}
            </td>
            <td className="num" style={{ fontWeight: 700 }}>
              {b.score_numeric} {b.grade}
            </td>
          </tr>
        </tbody>
      </table>

      {explanation.model_uncertainty && (
        <p className="note dim" style={{ marginTop: 10, marginBottom: 0 }}>
          Model fitted on {explanation.model_uncertainty.n_train_rows} observations across{' '}
          {explanation.model_uncertainty.n_train_borrowers} borrowers · out-of-range features{' '}
          {fmtPct(explanation.model_uncertainty.novelty_share, 1)}
          {explanation.model_uncertainty.out_of_range_features?.length
            ? ` (${explanation.model_uncertainty.out_of_range_features.join(', ')})`
            : ''}
          . The LLM penalty comes off before the ceiling is applied, deliberately — reversing them
          would price a covenant breach and stale feeds as one problem.
        </p>
      )}
    </>
  )
}
