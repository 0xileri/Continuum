import { fmtDate } from '../format.js'

// The unstructured side of Layer 1 (§6), as the agent saw it.
//
// The API serves these through an allowlist that strips `_truth` and `scenario_tag` — the
// generator's ground truth and the template name. Rendering either next to the agent's flags would
// turn the explainability trail into an answer key, and a demo where the agent is graded against
// something already on the page proves nothing. Provenance is shown because §13 makes it an input
// to the assessment rather than metadata: a payer's own dispute letter and a self-signed
// compliance certificate are not equally good evidence about a borrower.

export default function DocumentsPanel({ documents, evidenceRefs = [] }) {
  if (!documents?.length) return <p className="empty">No documents on file for this borrower.</p>

  const cited = new Set(evidenceRefs)

  return (
    <div>
      {documents.map((d) => (
        <details className="doc-row" key={d.doc_id}>
          <summary>
            <span className="mono" style={{ fontSize: 11.5 }}>
              {d.doc_id}
            </span>
            <strong style={{ fontSize: 13 }}>{d.title}</strong>
            <span className="badge">{d.doc_type}</span>
            <span className={`badge ${d.provenance === 'third_party' ? 'info' : 'off'}`}>
              {d.provenance}
            </span>
            {cited.has(d.doc_id) && <span className="badge warn">cited as evidence</span>}
            <span className="dim" style={{ fontSize: 11, marginLeft: 'auto' }}>
              {fmtDate(d.created_at)}
            </span>
          </summary>
          <pre className="doc">{d.body}</pre>
        </details>
      ))}
    </div>
  )
}
