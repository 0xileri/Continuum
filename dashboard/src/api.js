// Thin client over continuum.api. Every path is relative so it works through the Vite proxy in
// dev and against a same-origin build in preview.

// Dev: the Vite server proxies /api to the API process on another port.
// Prod: the API serves this bundle itself, so paths are same-origin at the root.
const BASE = import.meta.env.PROD ? '' : '/api'

async function get(path) {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${res.status} ${detail}`)
  }
  return res.json()
}

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(payload.detail ?? `${res.status} ${res.statusText}`)
  return payload
}

export const api = {
  health: () => get('/health'),
  meta: () => get('/meta'),
  rateCurve: () => get('/rate-curve'),
  og: () => get('/og'),
  borrowers: () => get('/borrowers'),
  borrower: (id) => get(`/borrowers/${id}`),
  explanation: (id, ref) => get(`/borrowers/${id}/explanations/${ref}`),
  fileDispute: (id, body) => post(`/borrowers/${id}/disputes`, body),
}
