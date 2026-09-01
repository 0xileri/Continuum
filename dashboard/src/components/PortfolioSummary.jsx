import { gradeTone } from '../format.js'

// Portfolio-level view, above the per-borrower detail.
//
// The dashboard opened straight into one borrower, which answers "how is this borrower?" and never
// "what am I looking at?". A lender holds a book, not a borrower — the first question is always
// the shape of the whole thing, and only then which row to open. This is that first answer.
//
// It also carries the colour legend, because a scale a reader has to infer is a scale they will
// read wrong. Every hue on this page means exactly one band, and this says which.

const BANDS = [
  { tone: 'strong', label: 'Investment grade — strong', grades: 'AAA · AA · A+ · A' },
  { tone: 'good', label: 'Investment grade — adequate', grades: 'A- · BBB · BBB-' },
  { tone: 'watch', label: 'Speculative — watch', grades: 'BB · BB- · B' },
  { tone: 'weak', label: 'Substantial risk', grades: 'B- · CCC' },
  { tone: 'bad', label: 'Distressed / default', grades: 'CC · C · D' },
]

function pct(n, total) {
  return total ? Math.round((n / total) * 100) : 0
}

export default function PortfolioSummary({ borrowers, og }) {
  const scored = borrowers.filter((b) => b.latest)
  const total = scored.length
  if (!total) return null

  const counts = Object.fromEntries(BANDS.map((b) => [b.tone, 0]))
  let attested = 0
  let silent = 0
  let widthSum = 0

  for (const b of scored) {
    const l = b.latest
    counts[gradeTone(l.score)] = (counts[gradeTone(l.score)] ?? 0) + 1
    if (l.attestation?.verified) attested += 1
    if (l.staleness_silent) silent += 1
    const [lo, hi] = l.confidence_interval ?? [0, 0]
    widthSum += hi - lo
  }

  // Below investment grade is the number a credit committee actually asks for.
  const speculative = counts.watch + counts.weak + counts.bad
  const avgWidth = Math.round(widthSum / total)

  return (
    <>
      <div className="pf-stats">
        <div className="pf-stat">
          <div className="label">Borrowers</div>
          <div className="value">{total}</div>
        </div>
        <div className="pf-stat">
          <div className="label">Below investment grade</div>
          <div className="value">
            {speculative}
            <span className="sub">/{total}</span>
          </div>
        </div>
        <div className="pf-stat">
          <div className="label">TEE attested</div>
          <div className="value">
            {attested}
            <span className="sub">/{total}</span>
          </div>
        </div>
        <div className="pf-stat">
          <div className="label">Feeds silent</div>
          <div className="value">{silent}</div>
        </div>
        <div className="pf-stat">
          <div className="label">Avg interval</div>
          <div className="value">
            ±{Math.round(avgWidth / 2)}
            <span className="sub">pts</span>
          </div>
        </div>
        <div className="pf-stat">
          <div className="label">On-chain</div>
          <div className="value">{og?.onchain_count ?? '—'}</div>
        </div>
      </div>

      {/* Distribution: one bar, segmented by band, widths proportional to the book. A grade
          histogram would need axes and space to say the same thing less directly. */}
      <div className="pf-dist" role="img" aria-label="Grade distribution across the portfolio">
        {BANDS.map(({ tone }) =>
          counts[tone] ? (
            <div
              key={tone}
              className={`pf-seg ${tone}`}
              style={{ flex: counts[tone] }}
              title={`${counts[tone]} borrower${counts[tone] === 1 ? '' : 's'}`}
            >
              {counts[tone]}
            </div>
          ) : null
        )}
      </div>

      <div className="pf-legend">
        {BANDS.map(({ tone, label, grades }) => (
          <div className="pf-key" key={tone}>
            <span className={`pf-dot ${tone}`} />
            <div>
              <div className="k-label">{label}</div>
              <div className="k-grades mono">{grades}</div>
            </div>
            <span className="k-count mono">{counts[tone] || '—'}</span>
          </div>
        ))}
      </div>

      <p className="note dim" style={{ marginTop: 14, marginBottom: 0 }}>
        Every colour on this page means one of these five bands and nothing else. Interval width is
        uncertainty, not risk — §11 prices the two separately, so a borrower can be sound and
        expensive at the same time.
      </p>
    </>
  )
}
