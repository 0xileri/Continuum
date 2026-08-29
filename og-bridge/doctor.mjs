// Preflight for the whole 0G integration. Run this BEFORE a scoring run that is meant to produce
// Integration Proof, not after.
//
// §12 asks for the 0G unknowns to be settled early rather than discovered on Day 3, and §9 budgets
// explicit time for the testnet→mainnet promotion. Both are the same lesson: the expensive failure
// is finding out at submission time that the ledger was never funded or the scorer was never
// authorised. This checks every precondition and prints the exact command to fix each one.
//
//   node og-bridge/doctor.mjs
//   CONTINUUM_OG_NETWORK=mainnet node og-bridge/doctor.mjs

import { ethers } from 'ethers'
import { createZGComputeNetworkBroker } from '@0gfoundation/0g-compute-ts-sdk'
import { Indexer } from '@0gfoundation/0g-storage-ts-sdk'
import { NETWORKS, network } from './lib.mjs'

const PASS = 'PASS'
const WARN = 'WARN'
const FAIL = 'FAIL'

const results = []
function check(name, status, detail = '', fix = '') {
  results.push({ name, status, detail, fix })
  const mark = status === PASS ? '  ok  ' : status === WARN ? ' warn ' : ' FAIL '
  console.log(`[${mark}] ${name}${detail ? ' — ' + detail : ''}`)
  if (fix && status !== PASS) console.log(`         fix: ${fix}`)
}

async function main() {
  const netName = process.env.CONTINUUM_OG_NETWORK ?? 'testnet'
  const net = network(netName)

  console.log(`0G preflight — ${net.name} (chain ${net.chainId})`)
  console.log(`  rpc      ${net.rpc}`)
  console.log(`  indexer  ${net.indexer}`)
  console.log(`  explorer ${net.explorer}\n`)

  if (netName === 'mainnet') {
    console.log('  *** MAINNET. Every transaction from here spends real 0G. ***\n')
  }

  // ---- key ------------------------------------------------------------------------
  const pk = process.env.OG_PRIVATE_KEY || process.env.PRIVATE_KEY
  if (!pk) {
    check('signing key', FAIL, 'OG_PRIVATE_KEY not set', 'export OG_PRIVATE_KEY=0x...  (never commit it)')
    summarise()
    return
  }

  let wallet
  try {
    const provider = new ethers.JsonRpcProvider(net.rpc, net.chainId)
    wallet = new ethers.Wallet(pk, provider)
    check('signing key', PASS, wallet.address)
  } catch (err) {
    check('signing key', FAIL, String(err.message))
    summarise()
    return
  }

  // ---- rpc + balance ----------------------------------------------------------------
  let balance = 0n
  try {
    balance = await wallet.provider.getBalance(wallet.address)
    const og = Number(ethers.formatEther(balance))
    if (og === 0) {
      check(
        'native balance',
        FAIL,
        '0 0G',
        net.name.includes('mainnet')
          ? 'fund this address with 0G before deploying or publishing'
          : `get testnet 0G: https://faucet.0g.ai (address ${wallet.address})`
      )
    } else {
      // 0.35 covers a ledger at twice the on-chain minimum plus a deploy and a handful of
      // publishes, with room to spare. Below that a run can start and strand halfway.
      check('native balance', og < 0.35 ? WARN : PASS, `${og.toFixed(4)} 0G`)
    }
  } catch (err) {
    check('rpc reachable', FAIL, String(err.shortMessage ?? err.message), `check ${net.rpc}`)
  }

  // ---- storage indexer --------------------------------------------------------------
  try {
    const indexer = new Indexer(net.indexer)
    const nodes = await indexer.getShardedNodes()
    const count = nodes?.trusted?.length ?? nodes?.length ?? 0
    check('0G Storage indexer', count > 0 ? PASS : WARN, `${count} trusted nodes`)
  } catch (err) {
    check('0G Storage indexer', FAIL, String(err.message).slice(0, 120), `check ${net.indexer}`)
  }

  // ---- compute marketplace + ledger --------------------------------------------------
  try {
    const broker = await createZGComputeNetworkBroker(wallet)
    const services = await broker.inference.listService()
    const acknowledged = services.filter((s) => s.teeSignerAcknowledged)
    check(
      '0G Compute marketplace',
      services.length ? PASS : FAIL,
      `${services.length} services, ${acknowledged.length} with an acknowledged TEE signer`
    )

    if (acknowledged.length) {
      console.log('\n  providers with a verifiable TEE signer:')
      for (const s of acknowledged.slice(0, 8)) {
        console.log(`    ${s.provider}  ${(s.models ?? []).join(', ') || '(models unlisted)'}`)
      }
      console.log(
        '\n  Pin one for reproducibility — the provider identity is part of what the\n' +
          '  attestation attests to:\n' +
          `    export CONTINUUM_OG_COMPUTE_PROVIDER=${acknowledged[0].provider}\n`
      )
    } else if (services.length) {
      check(
        'verifiable provider',
        FAIL,
        'no provider has an acknowledged TEE signer',
        'without one, processResponse cannot verify anything and every score publishes unattested'
      )
    }

    try {
      const ledger = await broker.ledger.getLedger()
      const available = Number(ethers.formatEther(ledger?.availableBalance ?? ledger?.[1] ?? 0n))
      check(
        '0G Compute ledger',
        available > 0 ? PASS : FAIL,
        `${available.toFixed(4)} 0G available`,
        available > 0 ? '' : 'node og-bridge/fund.mjs --amount 0.2 --yes'
      )
    } catch {
      check(
        '0G Compute ledger',
        FAIL,
        'no ledger account',
        'node og-bridge/fund.mjs --amount 0.2 --yes   (on-chain minimum is 0.1 0G)'
      )
    }
  } catch (err) {
    check('0G Compute marketplace', FAIL, String(err.message).slice(0, 160))
  }

  // ---- registry ----------------------------------------------------------------------
  const registry = process.env.CONTINUUM_REGISTRY_ADDRESS
  if (!registry) {
    check(
      'ContinuumScoreRegistry',
      FAIL,
      'CONTINUUM_REGISTRY_ADDRESS not set',
      `cd contracts && forge script script/Deploy.s.sol:Deploy --rpc-url ${net.rpc} --broadcast`
    )
  } else {
    try {
      const code = await wallet.provider.getCode(registry)
      if (code === '0x') {
        check('ContinuumScoreRegistry', FAIL, `no contract at ${registry} on ${net.name}`,
          'deployed to a different network? check CONTINUUM_OG_NETWORK')
      } else {
        const c = new ethers.Contract(
          registry,
          ['function authorizedScorer(address) view returns (bool)', 'function borrowerCount() view returns (uint256)'],
          wallet
        )
        const authorized = await c.authorizedScorer(wallet.address)
        const count = await c.borrowerCount()
        check('ContinuumScoreRegistry', PASS, `${registry} — ${count} borrowers`)
        check(
          'scorer authorised',
          authorized ? PASS : FAIL,
          authorized ? wallet.address : `${wallet.address} is not authorised`,
          `CONTINUUM_SCORER_ADDRESS=${wallet.address} forge script script/Deploy.s.sol:AuthorizeScorer --rpc-url ${net.rpc} --broadcast`
        )
        console.log(`         explorer: ${net.explorer}/address/${registry}`)
      }
    } catch (err) {
      check('ContinuumScoreRegistry', FAIL, String(err.shortMessage ?? err.message).slice(0, 140))
    }
  }

  summarise()
}

function summarise() {
  const failed = results.filter((r) => r.status === FAIL)
  const warned = results.filter((r) => r.status === WARN)
  console.log(
    `\n${results.length - failed.length - warned.length} ok, ${warned.length} warnings, ` +
      `${failed.length} blocking`
  )
  if (failed.length) {
    console.log('\nBlocking:')
    for (const r of failed) console.log(`  - ${r.name}: ${r.detail}`)
    process.exit(1)
  }
  console.log('\nReady. A scoring run can produce attested, on-chain scores.')
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
