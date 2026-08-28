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

// bytes32 fields must be exactly 32 bytes. Refs coming from 0G are already hex; anything else is
// hashed rather than truncated, because a silently truncated hash is a reference that looks valid
// and resolves to nothing.
function toBytes32(value) {
  if (!value) return ethers.ZeroHash
  const hex = String(value)
  if (/^0x[0-9a-fA-F]{64}$/.test(hex)) return hex
  if (/^0x[0-9a-fA-F]+$/.test(hex) && hex.length < 66) return ethers.zeroPadValue(hex, 32)
  return ethers.keccak256(ethers.toUtf8Bytes(hex))
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
    toBytes32(attestation.proof_ref),
    toBytes32(storageRef.root_hash),
    Boolean(attestation.verified),
  ]

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

  const receipt = await withTimeout(tx.wait(), TIMEOUT_MS, 'confirmation')
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
