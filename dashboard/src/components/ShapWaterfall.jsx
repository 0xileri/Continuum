import { fmtInt } from '../format.js'

// §7: "Every score needs a feature-attribution breakdown (SHAP values or equivalent) available on
// request — both because lenders will demand it and because it's your best defense if a borrower
// disputes a downgrade."
//
// ASSUMPTIONS #1 records that the numbers come from LightGBM's native TreeSHAP rather than the
// `shap` package, which drops a build dependency and means this component draws the waterfall
// itself. Contributions are in log-odds, the space where they sum exactly to the prediction — so
// the bars are comparable to each other and to the base term, which a probability-space rescaling
// would quietly break.

export default function ShapWaterfall({ attribution, limit = 10 }) {
  if (!attribution?.length) return <p className="empty">No attribution recorded for this score.</p>

  const header = attribution.find((r) => r._base_log_odds !== undefined) ?? {}
  const rows = attribution.filter((r) => r.feature).slice(0, limit)
  const max = Math.max(...rows.map((r) => Math.abs(r.contribution)), 1e-6)

  return (
    <>
      <table className="data">
        <thead>
          <tr>
            <th>Feature</th>
            <th className="num">Value</th>
            <th style={{ width: 190 }}>Contribution to log-odds</th>
            <th className="num">±</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const frac = Math.abs(r.contribution) / max
            const raises = r.contribution > 0
            return (
              <tr key={r.feature}>
                <td className="mono" style={{ fontSize: 11.5 }}>
                  {r.feature}
                </td>
                <td className="num">
                  {Math.abs(r.value) >= 1000 ? fmtInt(r.value) : r.value.toFixed(3)}
                </td>
                <td>
                  {/* Zero at the centre so direction is readable without reading the sign. */}
                  <div style={{ display: 'flex', alignItems: 'center', height: 14 }}>
                    <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end' }}>
                      {!raises && (
                        <div
                          className="bar-fill"
                          style={{
                            width: `${frac * 100}%`,
                            background: 'var(--strong)',
                            height: 7,
                            minWidth: 2,
                          }}
                        />
                      )}
                    </div>
                    <div style={{ width: 1, background: 'var(--line)', height: 12 }} />
                    <div style={{ flex: 1 }}>
                      {raises && (
                        <div
                          className="bar-fill"
                          style={{
                            width: `${frac * 100}%`,
                            background: 'var(--bad)',
                            height: 7,
                            minWidth: 2,
                          }}
                        />
                      )}
                    </div>
                  </div>
                </td>
                <td className="num" style={{ color: raises ? 'var(--bad)' : 'var(--strong)' }}>
                  {r.contribution > 0 ? '+' : ''}
                  {r.contribution.toFixed(3)}
                </td>
              </tr>
            )
          })}
          <tr className="muted">
            <td colSpan={2}>base log-odds (cohort expectation)</td>
            <td />
            <td className="num">{(header._base_log_odds ?? 0).toFixed(3)}</td>
          </tr>
          <tr className="muted">
            <td colSpan={2}>sum of feature contributions</td>
            <td />
            <td className="num">
              {(header._feature_sum_log_odds ?? 0) > 0 ? '+' : ''}
              {(header._feature_sum_log_odds ?? 0).toFixed(3)}
            </td>
          </tr>
        </tbody>
      </table>
      <p className="note dim" style={{ marginTop: 10, marginBottom: 0 }}>
        Red raises modelled probability of deterioration, green lowers it. Exact TreeSHAP from the
        booster itself (ASSUMPTIONS #1) — the contributions plus the base term reconstruct the
        prediction exactly, so this is the arithmetic, not an approximation of it.
      </p>
    </>
  )
}
