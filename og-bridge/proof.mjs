// Build the §10 Integration Proof artifact from ON-CHAIN STATE, not from the publish script's log.
//
// Why this exists as a separate step: the publish script records what it *believes* happened, and
// that belief can be wrong in both directions. A transaction can land while the receipt lookup
// fails (0G's RPC returns "no matching receipts found" for a few seconds after inclusion — this
// cost a real mainnet transaction to discover), and a transaction can be reported as sent while
// actually reverting. Either way the artifact would misstate what a judge can verify.
//
// The registry's own ScorePublished events are the authoritative record. This reads them back and
// writes the artifact from what the chain will actually show someone, so the file and the explorer
// can never disagree.
//
//   node og-bridge/proof.mjs
//   CONTINUUM_OG_NETWORK=mainnet node og-bridge/proof.mjs

import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ethers } from 'ethers'
import { network } from './lib.mjs'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))

const ABI = [
  'event ScorePublished(bytes32 indexed borrowerKey,string borrowerId,int16 scoreNumeric,int16 confidenceLow,int16 confidenceHigh,uint8 gradeIndex,string triggerReason,bytes32 computeAttestationRef,bytes32 storageRootHash,uint16 effectiveRateBps,bool attested,uint64 timestamp)',
  'function borrowerCount() view returns (uint256)',
  'function owner() view returns (address)',
  'function cooldownSeconds() view returns (uint64)',
  'function maxRateChangeBps() view returns (uint16)',
]

const GRADES = [
  'AAA', 'AA', 'A+', 'A', 'A-', 'BBB', 'BBB-', 'BB', 'BB-', 'B', 'B-', 'CCC', 'CC', 'C', 'D',
]

function borrowerNames() {
  const path = join(ROOT, 'data', 'raw', 'borrowers.json')
  if (!existsSync(path)) return {}
  return Object.fromEntries(
    JSON.parse(readFileSync(path, 'utf-8')).map((b) => [b.borrower_id, b.name])
  )
}

async function main() {
  const net = network(process.env.CONTINUUM_OG_NETWORK ?? 'testnet')
  const deploymentPath = join(ROOT, 'deployments', `${process.env.CONTINUUM_OG_NETWORK ?? 'testnet'}.json`)
  if (!existsSync(deploymentPath)) {
    console.error(`no deployment recorded at ${deploymentPath}`)
    process.exit(1)
  }
  const deployment = JSON.parse(readFileSync(deploymentPath, 'utf-8'))
  const address = process.env.CONTINUUM_REGISTRY_ADDRESS || deployment.address

  const provider = new ethers.JsonRpcProvider(net.rpc, net.chainId)
  const registry = new ethers.Contract(address, ABI, provider)
  const names = borrowerNames()

  const head = await provider.getBlockNumber()
  const from = deployment.block_number ?? 0

  // 0G's RPC caps eth_getLogs ranges, so the scan is chunked from the deployment block rather
  // than issued as one open-ended query.
  const CHUNK = 5000
  const events = []
  for (let start = from; start <= head; start += CHUNK) {
    const end = Math.min(start + CHUNK - 1, head)
    try {
      events.push(...(await registry.queryFilter(registry.filters.ScorePublished(), start, end)))
    } catch (err) {
      console.error(`  warning: log scan ${start}-${end} failed: ${err.shortMessage ?? err.message}`)
    }
  }

  const publications = events.map((e) => {
    const a = e.args
    return {
      borrower_id: a.borrowerId,
      name: names[a.borrowerId] ?? '',
      score: GRADES[Number(a.gradeIndex)] ?? '?',
      score_numeric: Number(a.scoreNumeric),
      confidence_interval: [Number(a.confidenceLow), Number(a.confidenceHigh)],
      trigger_reason: a.triggerReason,
      effective_rate_bps: Number(a.effectiveRateBps),
      attested: a.attested,
      compute_attestation_ref: a.computeAttestationRef,
      storage_root_hash: a.storageRootHash,
      published_at: new Date(Number(a.timestamp) * 1000).toISOString(),
      tx_hash: e.transactionHash,
      block_number: e.blockNumber,
      explorer_url: `${net.explorer}/tx/${e.transactionHash}`,
      storage_explorer_url: `${net.storageExplorer}/tx/${a.storageRootHash}`,
    }
  })

  const proof = {
    project: 'Continuum',
    generated_at: new Date().toISOString(),
    source: 'derived from on-chain ScorePublished events, not from the publish script log',
    network: net.name,
    chain_id: net.chainId,
    contract: 'ContinuumScoreRegistry',
    contract_address: address,
    contract_explorer_url: `${net.explorer}/address/${address}`,
    deploy_tx: deployment.tx_hash,
    deploy_tx_explorer_url: `${net.explorer}/tx/${deployment.tx_hash}`,
    deploy_block: deployment.block_number,
    storage_explorer: net.storageExplorer,
    on_chain_parameters: {
      owner: await registry.owner(),
      cooldown_seconds: Number(await registry.cooldownSeconds()),
      max_rate_change_bps: Number(await registry.maxRateChangeBps()),
      borrower_count: Number(await registry.borrowerCount()),
    },
    publication_count: publications.length,
    attested_count: publications.filter((p) => p.attested).length,
    publications,
    og_components: {
      '0g-chain':
        'ContinuumScoreRegistry — score registry enforcing the §4 cooldown (with its ' +
        'boundary-crossing-downgrade override) and the §5.4 ±50bps circuit breaker in bytecode',
      '0g-storage':
        'Borrower Feature Records, referenced on-chain by merkle root only — the payload never ' +
        'carries the raw record (§5.3)',
      '0g-compute':
        'document-reasoning call, TEE-signed and broker-verified (§5.2). See scope_note.',
    },
    scope_note:
      '0G Compute serves inference against registered providers and does not execute arbitrary ' +
      "jobs, so §5.2's stated fallback applies: the reasoning call runs on 0G Compute and the " +
      'aggregation arithmetic runs off-chain, bound to its inputs by measurement_hash. Flagged, ' +
      'not mocked. Publications with attested=false were produced without a funded Compute ' +
      'ledger and say so in their own payload rather than implying an attestation they lack.',
  }

  mkdirSync(join(ROOT, 'deployments'), { recursive: true })
  const out = join(ROOT, 'deployments', `integration_proof_${process.env.CONTINUUM_OG_NETWORK ?? 'testnet'}.json`)
  writeFileSync(out, JSON.stringify(proof, null, 2))

  console.log(`Integration proof — ${net.name}`)
  console.log(`  contract        ${address}`)
  console.log(`  explorer        ${net.explorer}/address/${address}`)
  console.log(`  publications    ${publications.length} (${proof.attested_count} attested)`)
  console.log(`  borrowerCount   ${proof.on_chain_parameters.borrower_count}`)
  console.log('')
  for (const p of publications) {
    console.log(
      `  ${(p.name || p.borrower_id).slice(0, 26).padEnd(27)}${p.score.padStart(4)} ` +
        `${String(p.score_numeric).padStart(4)}  ${p.attested ? 'attested' : '  —     '}  ` +
        `${p.tx_hash.slice(0, 20)}…`
    )
  }
  console.log(`\n  written to ${out}`)
}

main().catch((err) => {
  console.error(err?.shortMessage ?? err)
  process.exit(1)
})
