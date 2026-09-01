import { fmtInt } from '../format.js'

// §7: "Every score needs a feature-attribution breakdown (SHAP values or equivalent) available on
// request — both because lenders will demand it and because it's your best defense if a borrower
// disputes a downgrade."
//
// Wave 3 note: with the §5.1 weighted scorer these contributions are exact by construction —
// weight x (normalised - 0.5), summing to the composite minus its neutral point — rather than
// a TreeSHAP approximation. The header row carries a _units key so the panel never mislabels
// one scorer's attribution as the other's.
// ASSUMPTIONS #1 records that on the trained-model path the numbers come from TreeSHAP rather than the
// `shap` package, which drops a build dependency and means this component draws the waterfall
// itself. Contributions are in whichever space the scorer sums exactly in — log-odds for the
// booster, composite-health-index points for the weighted formula — so
// the bars are comparable to each other and to the base term, which a probability-space rescaling
// would quietly break.

export default function ShapWaterfall({ attribution, limit = 10 }) {
  if (!attribution?.length) return <p className="empty">No attribution recorded for this score.</p>

  const header = attribution.find((r) => r._base_log_odds !== undefined) ?? {}

  // The header row carries the units the scorer actually worked in. The weighted formula
  // attributes in composite-health-index points; the trained booster attributes in log-odds.
  // Hardcoding either label made the panel lie about one of them.
  const composite = header._units === 'composite_health_index'
  const units = composite ? 'composite' : 'log-odds'
  const baseLabel = composite ? 'neutral baseline (all features at pivot)' : 'base log-odds (cohort expectation)'
  const rows = attribution.filter((r) => r.feature).slice(0, limit)
  const max = Math.max(...rows.map((r) => Math.abs(r.contribution)), 1e-6)

  return (
    <>
      <table className="data">
        <thead>
          <tr>
            <th>Feature</th>
            <th className="num">Value</th>
            <th style={{ width: 190 }}>Contribution to {units}</th>
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
            <td colSpan={2}>{baseLabel}</td>
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
        Red raises modelled risk, green lowers it. The contributions plus the base term reconstruct
        the score exactly, so this is the arithmetic itself rather than an approximation of it.
      </p>
    </>
  )
}
