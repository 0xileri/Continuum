import { fmtSigned, gradeTone } from '../format.js'

// Sparkline of the last 40 recorded scores. Deliberately unlabelled and unscaled against the
// others: it says "this borrower has been moving" or "this borrower has been flat", which is the
// only question the list view has to answer before the reader clicks through.
function Spark({ points, width = 62, height = 18 }) {
  if (!points || points.length < 2) return <svg width={width} height={height} />
  const values = points.map((p) => p.score_numeric)
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const d = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * (width - 2) + 1
      const y = height - 2 - ((v - lo) / span) * (height - 4)
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  const rising = values[values.length - 1] >= values[0]
  return (
    <svg width={width} height={height}>
      <path
        d={d}
        fill="none"
        stroke={rising ? 'var(--strong)' : 'var(--bad)'}
        strokeWidth="1.3"
        opacity="0.85"
      />
    </svg>
  )
}

export default function Roster({ borrowers, selectedId, onSelect }) {
  if (!borrowers?.length) {
    return (
      <p className="empty" style={{ padding: 16 }}>
        No borrowers. Run <span className="mono">python -m continuum.synth.generate</span>.
      </p>
    )
  }

  return (
    <div className="roster">
      {borrowers.map((b) => {
        const latest = b.latest
        return (
          <button
            key={b.borrower_id}
            className={`roster-item ${b.borrower_id === selectedId ? 'selected' : ''}`}
            onClick={() => onSelect(b.borrower_id)}
          >
            <div className="name">{b.name}</div>
            <div className="sub">
              {b.archetype}
              {b.dark_feeds?.length > 0 && ' · dark feed'}
            </div>
            <div className="right">
              <span className={`grade ${gradeTone(latest?.score)}`}>{latest?.score ?? '—'}</span>
              <Spark points={b.spark} />
              <span className="dim mono" style={{ fontSize: 10.5 }}>
                {latest ? latest.score_numeric : '—'}
                {latest?.prior_score_numeric != null && latest.score_delta !== 0 && (
                  <span className={latest.score_delta > 0 ? 'up' : 'down'}>
                    {' '}
                    {fmtSigned(latest.score_delta)}
                  </span>
                )}
              </span>
            </div>
          </button>
        )
      })}
    </div>
  )
}
