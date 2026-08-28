import { fmtBps, fmtDate, fmtMoney, fmtPct } from '../format.js'

// §11 — Layer 5. What a pool would charge, replayed from the published score log.
//
// The panel splits the rate into base, risk and uncertainty because those three have different
// remedies and a borrower is entitled to know which they are paying: the base is the pool's own
// funding, the risk half needs the business to improve, and the uncertainty half needs a feed to
// start reporting again. Collapsing them into one number hides the only one the borrower can fix
// this week.
//
// Guard columns (`cooldown`, `breaker`) are shown rather than silently applied — §11 asks for the
// circuit breaker, and a borrower told only the effective rate cannot tell a damped move from a
// small one.

export default function TermsPanel({ terms, receivables }) {
  if (!terms?.length) {
    return (
      <p className="empty">
        No published scores yet, so the pool has nothing to price against. §10's gate is what
        decides that — a re-score the registry never received is not something a pool could react to.
      </p>
    )
  }

  const latest = terms[terms.length - 1]
  const first = terms[0]

  return (
    <>
      <div className="headline-numbers" style={{ marginLeft: 0, marginBottom: 14 }}>
        <div className="stat">
          <div className="label">All-in rate</div>
          <div className="value">{fmtBps(latest.effective_rate_bps)}</div>
        </div>
        <div className="stat">
          <div className="label">Base</div>
          <div className="value sm dim">{fmtBps(latest.base_rate_bps)}</div>
        </div>
        <div className="stat">
          <div className="label">Risk premium</div>
          <div className="value sm">{fmtBps(latest.risk_premium_bps)}</div>
        </div>
        <div className="stat">
          <div className="label">Uncertainty</div>
          <div className="value sm">{fmtBps(latest.uncertainty_premium_bps)}</div>
        </div>
        <div className="stat">
          <div className="label">Max LTV</div>
          <div className="value sm">{fmtPct(latest.effective_max_ltv)}</div>
        </div>
        {latest.borrowing_limit != null && (
          <div className="stat">
            <div className="label">Borrowing limit</div>
            <div className="value sm">{fmtMoney(latest.borrowing_limit)}</div>
          </div>
        )}
      </div>

      <div className="scroll-y">
        <table className="data">
          <thead>
            <tr>
              <th>Published</th>
              <th>Grade</th>
              <th className="num">Priced at</th>
              <th className="num">Rate</th>
              <th className="num">Δ</th>
              <th className="num">LTV</th>
              <th>Guards</th>
            </tr>
          </thead>
          <tbody>
            {[...terms].reverse().map((t) => (
              <tr key={t.as_of}>
                <td className="dim">{fmtDate(t.as_of)}</td>
                <td className="mono">
                  {t.score} <span className="dim">{t.score_numeric}</span>
                </td>
                {/* pricing_score, not score_numeric: §11 requires a wider interval to cost money,
                    and this is where that happens — the pessimistic end of the band is what the
                    formula is evaluated at. */}
                <td className="num dim">{Math.round(t.pricing_score)}</td>
                <td className="num">{fmtBps(t.effective_rate_bps)}</td>
                <td
                  className={`num ${t.rate_change_bps > 0 ? 'down' : t.rate_change_bps < 0 ? 'up' : 'dim'}`}
                >
                  {t.rate_change_bps ? `${t.rate_change_bps > 0 ? '+' : ''}${t.rate_change_bps}` : '—'}
                </td>
                <td className="num">{fmtPct(t.effective_max_ltv)}</td>
                <td>
                  {t.cooldown_active && <span className="badge warn">held</span>}{' '}
                  {t.cooldown_overridden && <span className="badge on">override</span>}{' '}
                  {t.circuit_breaker_applied && <span className="badge warn">capped</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {latest.notes?.length > 0 && (
        <ul className="note dim" style={{ marginTop: 10, marginBottom: 0, paddingLeft: 18 }}>
          {latest.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}

      <p className="note dim" style={{ marginTop: 10, marginBottom: 0 }}>
        {first.score}/{fmtBps(first.effective_rate_bps)} → {latest.score}/
        {fmtBps(latest.effective_rate_bps)} across {terms.length} published updates
        {receivables != null && <> · eligible receivables {fmtMoney(receivables)}</>}.{' '}
        <strong>No pool exists</strong> — these are the terms a pool would set. §11 keeps the
        formula separate from the model on purpose: the AI sets the risk input, the pool makes the
        lending decision (ASSUMPTIONS #14).
      </p>
    </>
  )
}
