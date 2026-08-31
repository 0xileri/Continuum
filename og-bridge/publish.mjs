// §5.4 — publish one Score Publication Payload to ContinuumScoreRegistry on 0G Chain.
//
// This is the transaction that turns a computed score into an on-chain fact, and it is the one
// Wave 3's Integration Proof is measured on (§3, §10): a mainnet contract address, an 0G Explorer
// link showing publish transactions, and a payload whose attestation and storage_ref actually
// point at the 0G artifacts they claim to.
//
// The cooldown and circuit-breaker rules live in the contract (§7), not here. This deliberately
// does NOT pre-check them and skip: a client-side skip would mean the guarantee is the client's
// again, and the whole reason §7 puts the rules on-chain is that the operator and the publisher
// are the same party in Wave 3. A cooldown revert is surfaced as a structured result so the Python
// caller can record "held by the on-chain gate", which is a real outcome worth logging.

import { ethers } from 'ethers'
import { fail, log, network, ok, readStdin, wallet, withTimeout } from './lib.mjs'

const TIMEOUT_MS = Number(process.env.CONTINUUM_OG_BRIDGE_TIMEOUT ?? 180) * 1000

const ABI = [
  'function publishScore(string borrowerId,int16 scoreNumeric,int16 confidenceLow,int16 confidenceHigh,string triggerReason,bytes32 computeAttestationRef,bytes32 storageRootHash,bool attested) external returns (bytes32)',
  'function cooldownRemaining(string borrowerId) external view returns (uint64)',
  'function latestScore(string borrowerId) external view returns (tuple(string borrowerId,int16 scoreNumeric,int16 confidenceLow,int16 confidenceHigh,uint64 timestamp,bytes32 computeAttestationRef,bytes32 storageRootHash,string triggerReason,uint8 gradeIndex,uint16 effectiveRateBps,bool attested,uint32 publishCount))',
  'function borrowerCount() external view returns (uint256)',
  'function authorizedScorer(address) external view returns (bool)',
  'error CooldownActive(uint64 secondsRemaining)',
  'error NotAuthorizedScorer()',
  'error InvalidScore(int16 scoreNumeric)',
  'error InvalidInterval(int16 low,int16 high)',
  'error EmptyBorrowerId()',
]

// bytes32 fields must be canonical 32-byte refs. Anything else is rejected rather than silently
// hashed so malformed or non-canonical references cannot look valid on-chain.
// Strict validation: a present reference must be canonical 0x + 64 hex. A malformed or
// non-canonical ref is rejected rather than silently hashed or truncated, because a truncated hash
// is a reference that looks valid on-chain and resolves to nothing.
//
// `required` distinguishes the two cases the caller has:
//
//   storage_root_hash  — always required. A score whose feature record was not stored has nothing
//                        for a reader to check the published number against, and §5.3's whole
//                        design is that the payload carries the hash rather than the record.
//
//   proof_ref          — required only when the payload claims `attested: true`. An unattested
//                        score legitimately has no attestation, and ZeroHash is the honest
//                        encoding of "there is none" — not a malformed value. Rejecting it would
//                        make unattested publishing impossible, which is exactly the state a run
//                        without a funded 0G Compute ledger is in. The contract stores `attested`
//                        alongside the ref, so absence is already visible and cannot be mistaken
//                        for a verified attestation.
function toBytes32(value, { required = true, field = 'reference' } = {}) {
  if (!value) {
    if (required) {
      throw new Error(`empty or missing ${field}: refusing to publish`)
    }
    return ethers.ZeroHash
  }
  const hex = String(value)
  if (!/^0x[0-9a-fA-F]{64}$/.test(hex)) {
    throw new Error(`invalid canonical 32-byte ${field} (must be 0x + 64 hex chars): ${value}`)
  }
  return hex
}

// ---- Commit-Reveal Pattern for Front-running Mitigation ----
//
// SECURITY: Rate-changing publishes should not be visible in the mempool before commitment.
// This implements a commit-reveal delay pattern:
//
//   1. Commit phase: Hash the score data and submit a transaction that records the hash.
//   2. Delay: Wait for a fixed number of blocks (e.g., 3-5 blocks).
//   3. Reveal phase: Publish the actual score, which can only succeed if it matches the
//      committed hash.
//
// This forces anyone monitoring the mempool to wait out the delay before they can be certain
// what score is coming, preventing sandwich attacks on rate-changing updates.
//
// TODO: Integrate a private relay provider (e.g., MEV-Blocker, Flashbots Protect, Threshold)
// to submit the commit transaction without exposing it to the public mempool.
// Do not hardcode a specific provider; make it configurable via environment variable.
//
// For now, this is a TODO marker. Production mainnet must use a private relay.
const COMMIT_REVEAL_ENABLED = false  // Set to true once a private relay is configured
const PRIVATE_RELAY_URL = process.env.CONTINUUM_PRIVATE_RELAY || null

// Wait for a receipt, tolerating 0G's receipt-propagation window.
//
// `tx.wait()` alone is not enough here. 0G's RPC answers eth_getTransactionReceipt for a freshly
// mined transaction with a non-standard error — {code: -32000, "no matching receipts found: this
// may indicate potential data corruption"} — for a few seconds after inclusion. ethers surfaces
// that as a thrown UNKNOWN_ERROR rather than a null receipt, so the caller sees a *successful*
// on-chain write as a failure.
//
// That is the worst possible failure mode for this script: the score is in the registry, the gas
// is spent, and the proof artifact says it never happened. It cost a real mainnet transaction to
// discover, so it is worth handling precisely rather than by widening a try/catch.
//
// The alarming wording is a red herring — nothing is corrupt; the node simply has not indexed the
// receipt yet. So the error is treated as "not ready", and only a genuinely absent receipt after
// the full window is a failure.
async function waitForReceipt(provider, tx, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  let lastError

  while (Date.now() < deadline) {
    try {
      const receipt = await provider.getTransactionReceipt(tx.hash)
      if (receipt) {
        if (receipt.status === 0) {
          fail(`transaction ${tx.hash} reverted on chain`, { tx_hash: tx.hash })
        }
        return receipt
      }
    } catch (err) {
      const message = String(err?.message ?? err)
      // Anything else — a dead RPC, a malformed request — should surface immediately rather than
      // be retried silently until the deadline.
      if (!/no matching receipts|not found|missing/i.test(message)) throw err
      lastError = message
    }
    await new Promise((r) => setTimeout(r, 2000))
  }

  fail(
    `no receipt for ${tx.hash} within ${timeoutMs}ms. The transaction may still be pending or may ` +
      `already have succeeded — CHECK THE EXPLORER BEFORE RE-PUBLISHING, because a re-publish ` +
      `would spend gas again and, if the first one landed, hit the cooldown.` +
      (lastError ? ` Last RPC error: ${lastError}` : ''),
    { tx_hash: tx.hash, indeterminate: true }
  )
}

async function main() {
  const input = readStdin()
  const net = network(input.network)
  const address = input.contract || process.env.CONTINUUM_REGISTRY_ADDRESS
  if (!address) {
    fail(
      'no registry address. Deploy first:\n' +
        '  cd contracts && forge script script/Deploy.s.sol:Deploy --rpc-url ' +
        net.rpc +
        ' --broadcast\n' +
        'then export CONTINUUM_REGISTRY_ADDRESS=0x...'
    )
  }

  const signer = wallet(net)
  const registry = new ethers.Contract(address, ABI, signer)

  const authorized = await registry.authorizedScorer(signer.address)
  if (!authorized) {
    fail(
      `${signer.address} is not an authorized scorer on ${address}. The registry owner must run:\n` +
        `  CONTINUUM_SCORER_ADDRESS=${signer.address} forge script ` +
        `script/Deploy.s.sol:AuthorizeScorer --rpc-url ${net.rpc} --broadcast`
    )
  }

  const {
    borrower_id: borrowerId,
    score_numeric: scoreNumeric,
    confidence_interval: ci,
    trigger_reason: triggerReason,
    attestation = {},
    storage_ref: storageRef = {},
  } = input

  if (!borrowerId) fail('no borrower_id')
  if (scoreNumeric === undefined) fail('no score_numeric')

  const [lo, hi] = ci ?? [scoreNumeric, scoreNumeric]

  log(`0G Chain — publishing ${borrowerId} ${scoreNumeric} [${lo}-${hi}] to ${address}`)

  const args = [
    borrowerId,
    scoreNumeric,
    lo,
    hi,
    triggerReason ?? 'scheduled_daily',
    toBytes32(attestation.proof_ref, {
      required: Boolean(attestation.verified),
      field: 'attestation proof_ref',
    }),
    toBytes32(storageRef.root_hash, { field: 'storage root_hash' }),
    Boolean(attestation.verified),
  ]

  // COMMIT-REVEAL: If enabled and a private relay is configured, use it for the commit phase.
  // Otherwise, publish directly (current behavior, vulnerable to front-running on public mempool).
  if (COMMIT_REVEAL_ENABLED && PRIVATE_RELAY_URL) {
    log(`0G Chain — using commit-reveal pattern via private relay ${PRIVATE_RELAY_URL}`)
    // TODO: Implement commit phase: hash args, send via private relay
    // TODO: Implement reveal phase: wait for block confirmation, then publish
    fail(
      'commit-reveal via private relay: not yet implemented; configure CONTINUUM_PRIVATE_RELAY to enable'
    )
  } else if (COMMIT_REVEAL_ENABLED && !PRIVATE_RELAY_URL) {
    fail(
      'COMMIT_REVEAL_ENABLED=true but no CONTINUUM_PRIVATE_RELAY configured. ' +
        'Set CONTINUUM_PRIVATE_RELAY to a private relay URL (e.g., https://...).'
    )
  } else {
    log('  WARNING: Publishing to public mempool — score will be visible before finalization.')
    log('  For mainnet production, configure a private relay and set COMMIT_REVEAL_ENABLED=true')
  }

  let tx
  try {
    tx = await withTimeout(registry.publishScore(...args), TIMEOUT_MS, 'publishScore')
  } catch (err) {
    // Decode the custom error so the caller learns which on-chain rule refused, not just that
    // "execution reverted".
    const data = err?.data ?? err?.info?.error?.data ?? err?.error?.data
    if (data) {
      try {
        const parsed = registry.interface.parseError(data)
        if (parsed?.name === 'CooldownActive') {
          return ok({
            published: false,
            rejected_by: 'cooldown',
            seconds_remaining: Number(parsed.args[0]),
            note:
              '§4 cooldown enforced on-chain. Not an error: a boundary-crossing downgrade would ' +
              'have gone through, so the registry is telling you this move was not one.',
            network: net.name,
            contract: address,
          })
        }
        fail(`registry rejected the publish: ${parsed?.name}(${parsed?.args?.join(', ')})`)
      } catch {
        /* not a decodable custom error; fall through */
      }
    }
    fail(err?.shortMessage ?? err?.message ?? String(err))
  }

  const receipt = await waitForReceipt(registry.runner.provider, tx, TIMEOUT_MS)
  log(`  tx ${receipt.hash} in block ${receipt.blockNumber}`)

  ok({
    published: true,
    network: net.name,
    chain_id: net.chainId,
    contract: address,
    tx_hash: receipt.hash,
    block_number: receipt.blockNumber,
    gas_used: String(receipt.gasUsed ?? ''),
    explorer_url: `${net.explorer}/tx/${receipt.hash}`,
    contract_explorer_url: `${net.explorer}/address/${address}`,
    signer: signer.address,
  })
}

main().catch((err) => fail(err?.stack ?? err))
