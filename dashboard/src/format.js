// Shared formatting. Kept in one place so the same number never renders two ways on one page.

export const fmtInt = (n) =>
  n === null || n === undefined || Number.isNaN(n) ? '—' : Math.round(n).toLocaleString('en-GB')

export const fmtMoney = (n) =>
  n === null || n === undefined
    ? '—'
    : new Intl.NumberFormat('en-GB', {
        style: 'currency',
        currency: 'GBP',
        maximumFractionDigits: 0,
      }).format(n)

export const fmtPct = (n, digits = 0) =>
  n === null || n === undefined ? '—' : `${(n * 100).toFixed(digits)}%`

export const fmtSigned = (n) => (n > 0 ? `+${fmtInt(n)}` : fmtInt(n))

export const fmtDate = (iso) =>
  !iso
    ? '—'
    : new Date(iso).toLocaleDateString('en-GB', {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
      })

export const fmtDateTime = (iso) =>
  !iso
    ? '—'
    : new Date(iso).toLocaleString('en-GB', {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })

export const fmtBps = (bps) => (bps === null || bps === undefined ? '—' : `${(bps / 100).toFixed(2)}%`)

// Grade colour. Banded rather than continuous so a letter always reads the same colour on every
// panel — a borrower who is BBB in the list must not look like a different risk in the header.
export function gradeTone(grade) {
  if (!grade) return 'neutral'
  if (['AAA', 'AA', 'A+', 'A'].includes(grade)) return 'strong'
  if (['A-', 'BBB', 'BBB-'].includes(grade)) return 'good'
  if (['BB', 'BB-', 'B'].includes(grade)) return 'watch'
  if (['B-', 'CCC'].includes(grade)) return 'weak'
  return 'bad'
}

// §7's trigger_reason enum, rendered for a human. The engine publishes the enum; burying it
// behind a label would undo the point of publishing it, so the raw value stays in the tooltip.
export const TRIGGER_LABELS = {
  scheduled_daily: 'Scheduled daily',
  event_anomaly: 'Event — anomaly',
  event_new_invoice: 'Event — new invoice',
  event_repayment: 'Event — repayment',
  event_dispute: 'Event — dispute',
  event_document: 'Event — document',
  event_data_quality_drop: 'Event — data quality',
  manual_rescore: 'Manual re-score',
  dispute_resolution: 'Dispute resolution',
}

export const triggerLabel = (r) => TRIGGER_LABELS[r] ?? r

export const FEED_LABELS = {
  invoice_feed: 'Invoices',
  bank_feed: 'Bank',
  accounting_feed: 'Accounting',
  document_feed: 'Documents',
  onchain_feed: 'On-chain',
}
