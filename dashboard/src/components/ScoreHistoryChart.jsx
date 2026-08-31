import { useMemo, useState } from 'react'
import { fmtDateTime, fmtSigned, gradeTone, triggerLabel } from '../format.js'

// §15's exit criterion lives in this component: "a live dashboard where a borrower's score visibly
// moves in response to a real data event".
//
// Three things it has to show at once, and the layout is driven by that rather than by taste:
//
//   1. The score moving.  A line.
//   2. The confidence interval around it — §7 requires the interval be published alongside the
//      point estimate, so it is drawn as a band, not hidden in a tooltip. A borrower whose feeds
//      go dark keeps roughly their score and visibly loses their certainty, and that has to be
//      the first thing the eye catches.
//   3. Whether each re-score was actually published.  §10 gates publication on a threshold, so a
//      chart that draws every re-score identically hides the gate. Published points are filled;
//      re-scores the threshold held back are hollow.
//
// Hand-rolled SVG rather than a charting library: three marks, one axis pair, and the alternative
// is 200kB of dependency to draw a line.

const PAD = { top: 14, right: 54, bottom: 26, left: 44 }

export default function ScoreHistoryChart({
  scores,
  gradeBands,
  selectedRef,
  onSelect,
  height = 260,
  width = 860,
}) {
  const [hover, setHover] = useState(null)

  const model = useMemo(() => {
    if (!scores?.length) return null

    const times = scores.map((s) => new Date(s.published_at).getTime())
    const t0 = Math.min(...times)
    const t1 = Math.max(...times)
    const span = t1 - t0 || 1

    const lows = scores.map((s) => s.confidence_interval[0])
    const highs = scores.map((s) => s.confidence_interval[1])
    // Pad the domain by a tenth of its own range so the band never touches the frame, and floor
    // the visible range at 90 points: on a stable borrower the whole series fits in 15 points and
    // an auto-fitted axis turns ordinary noise into a mountain range.
    const rawLo = Math.min(...lows)
    const rawHi = Math.max(...highs)
    const mid = (rawLo + rawHi) / 2
    const half = Math.max((rawHi - rawLo) / 2, 45)
    const lo = Math.max(0, mid - half * 1.12)
    const hi = Math.min(1000, mid + half * 1.12)

    const x = (t) => PAD.left + ((t - t0) / span) * (width - PAD.left - PAD.right)
    const y = (v) =>
      PAD.top + (1 - (v - lo) / (hi - lo || 1)) * (height - PAD.top - PAD.bottom)

    const pts = scores.map((s, i) => ({
      s,
      i,
      cx: x(times[i]),
      cy: y(s.score_numeric),
      loY: y(s.confidence_interval[0]),
      hiY: y(s.confidence_interval[1]),
    }))

    return { pts, x, y, lo, hi, t0, t1 }
  }, [scores, width, height])

  if (!model) return <p className="empty">No scores recorded yet.</p>

  const { pts, y, lo, hi } = model

  const bandPath =
    `M ${pts.map((p) => `${p.cx.toFixed(1)},${p.hiY.toFixed(1)}`).join(' L ')} ` +
    `L ${[...pts].reverse().map((p) => `${p.cx.toFixed(1)},${p.loY.toFixed(1)}`).join(' L ')} Z`

  const linePath = `M ${pts.map((p) => `${p.cx.toFixed(1)},${p.cy.toFixed(1)}`).join(' L ')}`

  // Grade boundaries inside the visible range, so the reader can see which letter the line is in
  // and how close it is to the next one — the thing a lending pool actually reacts to.
  const visibleBands = (gradeBands ?? []).filter((b) => b.lower > lo && b.lower < hi)

  return (
    <div className="chart-wrap">
      {/* viewBox rather than a fixed width: the 860-unit coordinate system stays, but the chart
          scales to its container instead of overflowing into a horizontal scroll. A score history
          is read as a shape — whether the line is falling, and how wide the band around it is —
          and a shape you have to scroll sideways to see is not being read at all. */}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Score history with confidence interval"
        onMouseLeave={() => setHover(null)}
      >
        {visibleBands.map((b) => (
          <g key={b.grade}>
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={y(b.lower)}
              y2={y(b.lower)}
              stroke="var(--line)"
              strokeDasharray="2 4"
            />
            <text x={width - PAD.right + 6} y={y(b.lower) + 3.5}>
              {b.grade}
            </text>
          </g>
        ))}

        {[lo, (lo + hi) / 2, hi].map((v) => (
          <text key={v} x={PAD.left - 8} y={y(v) + 3.5} textAnchor="end">
            {Math.round(v)}
          </text>
        ))}

        <path d={bandPath} fill="var(--band)" stroke="none" />
        <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth="1.8" />

        {pts.map((p) => {
          const isSel = p.s.explainability_ref === selectedRef
          return (
            <circle
              key={`${p.s.published_at}-${p.i}`}
              cx={p.cx}
              cy={p.cy}
              r={isSel ? 5 : 3.4}
              // §10 made visible: filled means it cleared the publish gate and a pool saw it;
              // hollow means it was recorded but held back under the threshold.
              fill={p.s.published_onchain ? 'var(--accent)' : 'var(--bg)'}
              stroke={isSel ? '#fff' : 'var(--accent)'}
              strokeWidth={isSel ? 2 : 1.4}
              style={{ cursor: 'pointer' }}
              onMouseEnter={(e) =>
                setHover({ p, x: e.clientX, y: e.clientY })
              }
              onClick={() => onSelect?.(p.s)}
            />
          )
        })}

        <text x={PAD.left} y={height - 8}>
          {new Date(model.t0).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}
        </text>
        <text x={width - PAD.right} y={height - 8} textAnchor="end">
          {new Date(model.t1).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}
        </text>
      </svg>

      {hover && (
        <div
          className="tooltip"
          style={{ left: Math.min(hover.x + 14, window.innerWidth - 320), top: hover.y - 10 }}
        >
          <div className="t-head">
            <span className={`grade ${gradeTone(hover.p.s.score)}`}>{hover.p.s.score}</span>{' '}
            {hover.p.s.score_numeric}{' '}
            <span className="dim">
              [{hover.p.s.confidence_interval[0]}–{hover.p.s.confidence_interval[1]}]
            </span>
          </div>
          <div className="dim">{fmtDateTime(hover.p.s.published_at)}</div>
          <div style={{ marginTop: 4 }}>
            {triggerLabel(hover.p.s.trigger_reason)}
            {hover.p.s.prior_score_numeric !== null && (
              <> · {fmtSigned(hover.p.s.score_delta)} pts</>
            )}
          </div>
          <div className="dim" style={{ marginTop: 4 }}>
            {hover.p.s.published_onchain
              ? 'Published — cleared the §10 gate'
              : 'Recorded, not republished (§10 threshold)'}
          </div>
          {hover.p.s.triggered_by_detail && (
            <div className="dim" style={{ marginTop: 4 }}>
              {hover.p.s.triggered_by_detail}
            </div>
          )}
        </div>
      )}

      <div className="toggle-row" style={{ marginTop: 8, marginBottom: 0 }}>
        <span className="badge info">band = confidence interval (§7)</span>
        <span className="badge">filled = published (§10)</span>
        <span className="badge off">hollow = held back under threshold</span>
        <span className="dim" style={{ fontSize: 11 }}>
          click a point to load its explainability trail
        </span>
      </div>
    </div>
  )
}
