import { fmtSigned, gradeTone } from '../format.js'

// Sparkline of the last 40 recorded scores. Deliberately unlabelled and unscaled against the
// others: it says "this borrower has been moving" or "this borrower has been flat", which is the
// only question the list view has to answer before the reader clicks through.
function Spark({ points, tone = 'neutral', width = 62, height = 18 }) {
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
  // Coloured by the borrower's grade, not by whether the line happens to end lower than it
  // started. The binary version painted every mild drift full vermilion — and in a cohort that
  // mostly drifts down, that meant the roster screamed about its most ordinary borrowers and had
  // nothing louder left for the ones actually failing. Matching the grade chip beside it also
  // means the two never disagree about how worried to be.
  return (
    <svg width={width} height={height}>
      <path
        d={d}
        fill="none"
        stroke={`var(--${tone})`}
        strokeWidth="1.4"
        opacity="0.9"
        strokeLinecap="round"
        strokeLinejoin="round"
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
              <Spark points={b.spark} tone={gradeTone(latest?.score)} />
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

            {/* The row's second line. A credit list has to answer "which of these needs me
                today", and the letter alone does not: a BBB with a 90-point interval and a dark
                feed is a different proposition from a BBB that is fully reported. */}
            {latest && (
              <div className="metrics">
                <span title="confidence interval half-width">
                  ±{Math.round((latest.confidence_interval[1] - latest.confidence_interval[0]) / 2)}
                </span>
                <span className="m-sep">·</span>
                <span title="data quality score">dq {latest.data_quality_score.toFixed(2)}</span>
                <span className="m-sep">·</span>
                <span
                  className={`att ${latest.attestation?.verified ? '' : 'no'}`}
                  title={
                    latest.attestation?.verified
                      ? 'reasoning attested by a verified 0G Compute TEE signature'
                      : 'no verified attestation on this score'
                  }
                >
                  {latest.attestation?.verified ? 'attested' : 'unattested'}
                </span>
                {latest.staleness_silent && (
                  <>
                    <span className="m-sep">·</span>
                    <span className="silent" title="a weighted feed is past its reporting grace">
                      silent
                    </span>
                  </>
                )}
              </div>
            )}
          </button>
        )
      })}
    </div>
  )
}
