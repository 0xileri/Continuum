// §7 part 3. What the early-warning layer saw, and whether it decided to score out of cadence.
//
// The abstention state is rendered separately from "no signals" on purpose: `AnomalyReport`
// distinguishes them because the layer having *no opinion* (too little history) is uncertainty,
// while a clean read is reassurance, and the aggregator treats them differently. A panel that
// collapsed both into "no anomalies" would misrepresent the score's own confidence.

export default function AnomalyPanel({ anomaly, materiality }) {
  if (!anomaly) return <p className="empty">No anomaly report on this score.</p>

  return (
    <>
      <div className="toggle-row">
        {anomaly.abstained ? (
          <span className="badge warn">abstained — history too short for a baseline</span>
        ) : anomaly.triggered ? (
          <span className="badge on">fired · {anomaly.trigger_reason}</span>
        ) : (
          <span className="badge ok">no deviation from this borrower's own pattern</span>
        )}
        <span className="badge">pressure {anomaly.pressure.toFixed(2)}</span>
      </div>

      {anomaly.signals?.length > 0 ? (
        <table className="data">
          <thead>
            <tr>
              <th>Signal</th>
              <th>Detail</th>
              <th className="num">Severity</th>
            </tr>
          </thead>
          <tbody>
            {anomaly.signals.map((s, i) => (
              <tr key={`${s.kind}-${s.feature}-${i}`}>
                <td className="mono" style={{ fontSize: 11.5 }}>
                  {s.kind}
                </td>
                <td>{s.detail}</td>
                <td className="num">{s.severity.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="dim" style={{ fontSize: 12.5, margin: '4px 0 0' }}>
          {anomaly.abstained
            ? 'Fewer observations than the layer needs, so it reports no opinion rather than a clean bill of health — and carries baseline pressure into the interval accordingly.'
            : 'Nothing crossed a materiality threshold at this observation.'}
        </p>
      )}

      {materiality && (
        <p className="note dim" style={{ marginTop: 10, marginBottom: 0 }}>
          Thresholds are borrower-relative, not absolute (§13): robust z ≥{' '}
          {materiality.robust_z_abs}, payment ≥ {materiality.payment_late_days_over_p90}d beyond the
          borrower's own p90, dispute ≥{' '}
          {(materiality.dispute_value_pct_of_receivables * 100).toFixed(0)}% of receivables. There
          is no fixed number a borrower can learn and sit under.
        </p>
      )}
    </>
  )
}
