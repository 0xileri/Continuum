import { FEED_LABELS, fmtDateTime, fmtPct } from '../format.js'

// §6, the requirement the brief singles out as most differentiating:
//
//   "Build a data-quality/staleness flag per borrower — if a data source goes silent (originator
//   stops syncing), the score should visibly degrade in confidence, not silently freeze at the
//   last-known value. This single design decision is what most differentiates you from the
//   'static rating' problem you're trying to solve."
//
// The aggregate alone cannot satisfy that: 0.42 tells a lender the data is degraded, not *which*
// feed went dark. So the per-feed decay is drawn, with its last heartbeat next to it.

function tone(freshness) {
  if (freshness >= 0.75) return 'var(--strong)'
  if (freshness >= 0.35) return 'var(--watch)'
  return 'var(--bad)'
}

export default function DataQualityPanel({ record, feedSla }) {
  if (!record) return <p className="empty">No feature record yet.</p>

  const detail = record.feed_freshness_detail ?? {}
  const freshness = record.source_freshness ?? {}
  const level = record.staleness?.level ?? 'fresh'

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 12 }}>
        <span className="mono" style={{ fontSize: 20, fontWeight: 600 }}>
          {record.data_quality_score.toFixed(2)}
        </span>
        <span
          className={`badge ${level === 'fresh' ? 'ok' : level === 'degraded' ? 'warn' : 'on'}`}
        >
          {level}
        </span>
        {record.staleness?.degraded_feeds?.length > 0 && (
          <span className="dim" style={{ fontSize: 12 }}>
            {record.staleness.degraded_feeds.map((f) => FEED_LABELS[f] ?? f).join(', ')}
          </span>
        )}
      </div>

      <table className="data">
        <thead>
          <tr>
            <th>Feed</th>
            <th style={{ width: 120 }}>Freshness</th>
            <th className="num">Weight</th>
            <th>Last heartbeat</th>
          </tr>
        </thead>
        <tbody>
          {Object.keys(feedSla ?? detail).map((feed) => {
            const f = detail[feed] ?? 0
            const sla = feedSla?.[feed]
            return (
              <tr key={feed}>
                <td>
                  {FEED_LABELS[feed] ?? feed}
                  {sla?.corroboration < 1 && (
                    <span className="badge off" style={{ marginLeft: 6 }}>
                      self-reported ×{sla.corroboration}
                    </span>
                  )}
                </td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <div className="bar-track" style={{ flex: 1 }}>
                      <div
                        className="bar-fill"
                        style={{ width: `${Math.max(f * 100, 1.5)}%`, background: tone(f) }}
                      />
                    </div>
                    <span className="mono dim" style={{ fontSize: 11, width: 32 }}>
                      {f.toFixed(2)}
                    </span>
                  </div>
                </td>
                <td className="num dim">{sla ? fmtPct(sla.weight, 0) : '—'}</td>
                <td className="dim" style={{ fontSize: 11.5 }}>
                  {freshness[feed] ? fmtDateTime(freshness[feed]) : 'never reported'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <p className="note dim" style={{ marginTop: 10, marginBottom: 0 }}>
        Exponential decay after each feed's grace period, weighted and discounted for self-reported
        sources (§13, ASSUMPTIONS #5). Decay rather than a cliff, so a feed going quiet shows up as
        a gradual visible slide instead of a step nobody notices.
      </p>
    </>
  )
}
