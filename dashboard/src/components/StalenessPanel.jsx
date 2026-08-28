import { FEED_LABELS } from '../format.js'

// §4's staleness rule, shown as what it costs rather than as a flag.
//
//   "when a data source goes silent, the score keeps degrading under continued silence rather than
//    plateauing or reversing upward. Silence is treated as worsening information, not neutral
//    information."
//
// Separate from DataQualityPanel on purpose. That panel answers "how fresh is each feed"; this one
// answers "what did the silence take off this score, and is the ratchet holding". They are
// different questions with different remedies, and merging them buries the second — which is the
// one a borrower would dispute.

export default function StalenessPanel({ staleness, meta }) {
  if (!staleness) return <p className="empty">No staleness assessment on this score.</p>

  const silent = staleness.silent
  const perFeed = Object.entries(staleness.per_feed_days ?? {}).filter(([, d]) => d > 0)

  return (
    <>
      <div className="toggle-row">
        {silent ? (
          <span className="badge on">
            {staleness.worst_feed} silent {Math.round(staleness.worst_days)}d
          </span>
        ) : (
          <span className="badge ok">all feeds reporting</span>
        )}
        {staleness.penalty_points > 0 && (
          <span className="badge on">−{Math.round(staleness.penalty_points)} points</span>
        )}
        {staleness.ratchet_ceiling != null && (
          <span className="badge warn">ratchet held at {staleness.ratchet_ceiling}</span>
        )}
      </div>

      {perFeed.length > 0 ? (
        <table className="data">
          <thead>
            <tr>
              <th>Feed</th>
              <th className="num">Days past its own grace</th>
            </tr>
          </thead>
          <tbody>
            {perFeed
              .sort((a, b) => b[1] - a[1])
              .map(([feed, days]) => (
                <tr key={feed}>
                  <td>{FEED_LABELS[feed] ?? feed}</td>
                  <td className="num down">{days.toFixed(1)}</td>
                </tr>
              ))}
            <tr className="muted">
              <td>weighted silence-days</td>
              <td className="num">{staleness.weighted_silence_days?.toFixed(2)}</td>
            </tr>
          </tbody>
        </table>
      ) : (
        <p className="dim" style={{ fontSize: 12.5, margin: '4px 0 0' }}>
          {staleness.never_reported?.length
            ? `${staleness.never_reported.join(', ')} has never reported. No duration can be measured, so no duration penalty applies — but the ratchet is engaged.`
            : 'Every feed reported inside its own expected interval.'}
        </p>
      )}

      {staleness.notes?.length > 0 && (
        <ul className="note" style={{ marginTop: 10, marginBottom: 0, paddingLeft: 18 }}>
          {staleness.notes.map((n, i) => (
            <li key={i} style={{ color: 'var(--muted)' }}>
              {n}
            </li>
          ))}
        </ul>
      )}

      <p className="note dim" style={{ marginTop: 10, marginBottom: 0 }}>
        {meta?.statement ?? ''} The penalty is{' '}
        <strong>{staleness.points_per_weighted_day} points per weighted silence-day</strong> and has
        no ceiling — a borrower dark for a year does not sit at the same letter as one dark for a
        month. Grace is per feed, so a monthly document sync is not treated as silence.
      </p>
    </>
  )
}
