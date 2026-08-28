// ASSUMPTIONS #6: "Each term, its weight, and its contribution to the final width are persisted
// per score in the explanation artifact — see §7's explainability requirement, and §11: a borrower
// paying a wider risk premium is entitled to know which of the four caused it."
//
// This is that panel. "The interval is wide" is not an explanation; "your accounting feed has not
// reported in three weeks and that is 62% of the widening" is.

const TERM_LABELS = {
  data_quality: 'Data quality',
  model_variance: 'Model variance',
  anomaly_pressure: 'Anomaly pressure',
  llm_confidence: 'Document confidence',
}

const TERM_NOTES = {
  data_quality: '§6 — a feed going quiet costs confidence rather than being absorbed silently',
  model_variance: '§17 cold start — fold instability plus how far outside training this borrower sits',
  anomaly_pressure: '§7 part 3 — behaving unusually weakens the evidence, even if the score holds',
  llm_confidence: 'A thin or contradictory document file is uncertainty, not absence of risk',
}

export default function IntervalBreakdown({ explanation }) {
  const interval = explanation?.interval
  if (!interval) return null

  const contributions = interval.term_contributions ?? {}
  const total = Object.values(contributions).reduce((a, b) => a + b, 0)

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 12 }}>
        <span className="mono" style={{ fontSize: 20, fontWeight: 600 }}>
          ±{Math.round(interval.half_width_points)}
        </span>
        <span className="dim" style={{ fontSize: 12 }}>
          points, from a base of ±{interval.base_half_width}
        </span>
        {interval.capped && (
          <span className="badge warn">capped at ±{interval.max_half_width}</span>
        )}
      </div>

      <table className="data">
        <thead>
          <tr>
            <th>Term</th>
            <th className="num">Raw</th>
            <th className="num">Weight</th>
            <th style={{ width: 130 }}>Share of widening</th>
            <th className="num">Effect</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(contributions)
            .sort((a, b) => b[1] - a[1])
            .map(([name, contribution]) => {
              const raw = interval.terms?.[name] ?? 0
              const weight = contribution && raw ? contribution / raw : 0
              const share = total > 0 ? contribution / total : 0
              return (
                <tr key={name}>
                  <td>
                    {TERM_LABELS[name] ?? name}
                    <div className="dim" style={{ fontSize: 11 }}>
                      {TERM_NOTES[name]}
                    </div>
                  </td>
                  <td className="num">{raw.toFixed(3)}</td>
                  <td className="num dim">{weight.toFixed(2)}</td>
                  <td>
                    <div className="bar-track">
                      <div
                        className="bar-fill"
                        style={{
                          width: `${Math.max(share * 100, contribution > 0 ? 2 : 0)}%`,
                          background: 'var(--accent)',
                        }}
                      />
                    </div>
                  </td>
                  <td className="num">{contribution.toFixed(3)}</td>
                </tr>
              )
            })}
        </tbody>
      </table>

      <p className="note dim" style={{ marginTop: 10, marginBottom: 0 }}>
        A heuristic uncertainty band, not a calibrated posterior (ASSUMPTIONS #6). Every point of
        half-width also removes {explanation.interval?.ceiling_multiple ?? 3} points from the
        highest grade attainable, so uncertainty moves the letter and not only the range.
      </p>
    </>
  )
}
