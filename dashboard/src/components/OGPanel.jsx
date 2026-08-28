import { fmtDateTime } from '../format.js'

// §3 exit criterion 5 and §10's Integration Proof, made visible:
//
//   "A React/Vite dashboard showing score history, trigger reason, and 0G Explorer / 0G Storage
//    links per score — the 'verifiability made visible' story is the pitch."
//
// The panel is built around one editorial decision: **it shows what is NOT covered as prominently
// as what is.** §5.2's fallback means the 0G Compute attestation covers the reasoning call and not
// the aggregation arithmetic, and §11 is blunt that "judges and any real institutional reader will
// find the overclaim faster than it's worth making". So the scope note is body text, not a
// footnote, and an unverified attestation renders in a different colour from a verified one rather
// than both reading as "0G".

function Row({ label, children }) {
  return (
    <tr>
      <td style={{ width: 150, color: 'var(--dim)' }}>{label}</td>
      <td>{children}</td>
    </tr>
  )
}

function Hash({ value, href, chars = 22 }) {
  if (!value) return <span className="dim">—</span>
  const short = value.length > chars ? `${value.slice(0, chars)}…` : value
  return href ? (
    <a className="mono" href={href} target="_blank" rel="noreferrer" style={{ fontSize: 11.5 }}>
      {short}
    </a>
  ) : (
    <span className="mono" style={{ fontSize: 11.5 }}>
      {short}
    </span>
  )
}

export default function OGPanel({ og, payload }) {
  if (!og) return null

  const att = payload?.attestation
  const storage = payload?.storage_ref
  const chain = payload?.chain_ref

  const attested = att?.type === '0g-compute' && att?.verified
  const attemptedButUnverified = att?.type === '0g-compute' && !att?.verified

  return (
    <>
      <div className="toggle-row">
        <span className={`badge ${og.network?.includes('mainnet') ? 'ok' : 'info'}`}>
          {og.network}
        </span>
        <span className="badge">chain {og.chain_id}</span>
        {og.registry_address ? (
          <a
            className="badge ok"
            href={og.registry_explorer_url}
            target="_blank"
            rel="noreferrer"
            style={{ textDecoration: 'none' }}
          >
            registry deployed ↗
          </a>
        ) : (
          <span className="badge off">registry not deployed</span>
        )}
        <span className={`badge ${og.bridge_ready ? 'ok' : 'off'}`}>
          bridge {og.bridge_ready ? 'ready' : 'not configured'}
        </span>
      </div>

      <table className="data" style={{ marginBottom: 14 }}>
        <tbody>
          {/* --- §5.2 Compute --------------------------------------------------------- */}
          <Row label="0G Compute">
            {attested ? (
              <span className="badge ok">attested · TEE signature verified</span>
            ) : attemptedButUnverified ? (
              <span className="badge on">returned, NOT verified</span>
            ) : (
              <span className="badge off">no attestation — computed locally</span>
            )}
          </Row>
          {att?.type === '0g-compute' && (
            <>
              <Row label="provider">
                <Hash value={att.compute_node} href={`${og.explorer}/address/${att.compute_node}`} />
              </Row>
              <Row label="model">
                <span className="mono" style={{ fontSize: 11.5 }}>
                  {att.model || '—'}
                </span>
              </Row>
              <Row label="job id">
                <Hash value={att.job_id} chars={30} />
              </Row>
              <Row label="proof ref">
                <Hash value={att.proof_ref} />
              </Row>
            </>
          )}

          {/* --- §5.3 Storage --------------------------------------------------------- */}
          <Row label="0G Storage">
            {storage?.provider === '0g-storage' ? (
              <span className="badge ok">feature record stored</span>
            ) : (
              <span className="badge off">local only — not written to 0G</span>
            )}
          </Row>
          {storage?.root_hash && (
            <>
              <Row label="root hash">
                <Hash
                  value={storage.root_hash}
                  href={`${og.storage_explorer}/tx/${storage.root_hash}`}
                  chars={26}
                />
              </Row>
              <Row label="uri">
                <Hash value={storage.uri} chars={26} />
              </Row>
            </>
          )}

          {/* --- §5.4 Chain ----------------------------------------------------------- */}
          <Row label="0G Chain">
            {chain?.tx_hash ? (
              <span className="badge ok">published to the registry</span>
            ) : payload?.published_onchain ? (
              <span className="badge warn">cleared the gate, not yet on chain</span>
            ) : (
              <span className="badge off">held by the publish gate</span>
            )}
          </Row>
          {chain?.tx_hash && (
            <>
              <Row label="transaction">
                <Hash value={chain.tx_hash} href={chain.explorer_url} chars={26} />
              </Row>
              <Row label="block">
                <span className="mono" style={{ fontSize: 11.5 }}>
                  {chain.block_number}
                </span>
              </Row>
              <Row label="contract">
                <Hash
                  value={chain.contract_address}
                  href={`${og.explorer}/address/${chain.contract_address}`}
                />
              </Row>
            </>
          )}

          <Row label="local digest">
            <Hash value={att?.measurement_hash} chars={26} />
          </Row>
        </tbody>
      </table>

      {/* §5.2's scope reduction, as body text rather than a footnote. */}
      <div className="callout trust" style={{ marginBottom: 10 }}>
        <strong>What the attestation covers.</strong> {og.compute_scope}
      </div>

      <p className="note dim" style={{ margin: 0 }}>
        {og.trust_statement}
      </p>

      {!og.bridge_ready && og.bridge_reason && (
        <p className="note dim" style={{ marginTop: 8, marginBottom: 0 }}>
          Bridge not configured: <span className="mono">{og.bridge_reason}</span>. Scores still
          compute; they publish with <span className="mono">attestation.type="none"</span> and no
          chain reference, which is what these badges are reporting.
        </p>
      )}
    </>
  )
}

/** The submission-facing view: every score that actually made it on-chain. §10. */
export function OGProofPanel({ og }) {
  if (!og) return null
  const rows = og.onchain_publications ?? []

  return (
    <>
      <div className="toggle-row">
        <span className="badge info">{og.network}</span>
        <span className={`badge ${rows.length ? 'ok' : 'off'}`}>
          {rows.length} publish transaction{rows.length === 1 ? '' : 's'}
        </span>
        {og.registry_address && (
          <a
            className="badge ok"
            href={og.registry_explorer_url}
            target="_blank"
            rel="noreferrer"
            style={{ textDecoration: 'none' }}
          >
            contract on 0G Explorer ↗
          </a>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="empty">
          No scores have been published on-chain yet. Deploy the registry and run{' '}
          <span className="mono">python scripts/publish_wave3.py</span>.
        </p>
      ) : (
        <div className="scroll-y">
          <table className="data">
            <thead>
              <tr>
                <th>Borrower</th>
                <th>Score</th>
                <th>Published</th>
                <th>Transaction</th>
                <th>Storage root</th>
                <th>Attested</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.tx_hash}>
                  <td>{r.borrower_name || r.borrower_id}</td>
                  <td className="mono">
                    {r.score} {r.score_numeric}
                  </td>
                  <td className="dim">{fmtDateTime(r.published_at)}</td>
                  <td>
                    <Hash value={r.tx_hash} href={r.explorer_url} chars={18} />
                  </td>
                  <td>
                    <Hash
                      value={r.storage_root_hash}
                      href={`${og.storage_explorer}/tx/${r.storage_root_hash}`}
                      chars={16}
                    />
                  </td>
                  <td>
                    {r.attested ? (
                      <span className="badge ok">yes</span>
                    ) : (
                      <span className="badge off">no</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
