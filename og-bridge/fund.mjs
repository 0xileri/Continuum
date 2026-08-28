// Fund the 0G Compute ledger. **This spends real 0G.**
//
// Kept as its own explicit command, and deliberately NOT called from the scoring path. An engine
// that silently tops up a ledger whenever a call is short is a wallet-draining loop with a friendly
// name — and on mainnet that is somebody's money. `compute.mjs` detects an unfunded sub-account and
// prints this command rather than running it.
//
// Every invocation prints what it is about to spend and requires --yes to proceed.
//
//   node og-bridge/fund.mjs --amount 3
//   node og-bridge/fund.mjs --amount 3 --provider 0xabc... --yes

import { ethers } from 'ethers'
import { createZGComputeNetworkBroker } from '@0gfoundation/0g-compute-ts-sdk'
import { network } from './lib.mjs'

function arg(name, fallback = undefined) {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) return fallback
  const next = process.argv[i + 1]
  return next && !next.startsWith('--') ? next : true
}

async function main() {
  const amount = Number(arg('amount', 3))
  const provider = arg('provider', null)
  const confirmed = Boolean(arg('yes', false))

  const net = network(process.env.CONTINUUM_OG_NETWORK ?? 'testnet')
  const pk = process.env.OG_PRIVATE_KEY || process.env.PRIVATE_KEY
  if (!pk) {
    console.error('OG_PRIVATE_KEY is not set.')
    process.exit(1)
  }

  const rpc = new ethers.JsonRpcProvider(net.rpc, net.chainId)
  const wallet = new ethers.Wallet(pk, rpc)
  const balance = Number(ethers.formatEther(await rpc.getBalance(wallet.address)))

  console.log(`0G Compute ledger funding — ${net.name}`)
  console.log(`  wallet        ${wallet.address}`)
  console.log(`  balance       ${balance.toFixed(4)} 0G`)
  console.log(`  depositing    ${amount} 0G`)
  if (provider) console.log(`  transferring  ${amount / 3} 0G to sub-account ${provider}`)

  if (net.name.includes('mainnet')) {
    console.log('\n  *** MAINNET — this spends real 0G. ***')
  }

  if (!confirmed) {
    console.log('\nNothing was spent. Re-run with --yes to proceed.')
    process.exit(0)
  }

  if (balance < amount) {
    console.error(
      `\nInsufficient balance: have ${balance.toFixed(4)} 0G, need ${amount}.` +
        (net.faucet ? `\nFaucet: ${net.faucet}` : '')
    )
    process.exit(1)
  }

  const broker = await createZGComputeNetworkBroker(wallet)

  let hasLedger = true
  try {
    await broker.ledger.getLedger()
  } catch {
    hasLedger = false
  }

  if (!hasLedger) {
    console.log('\n  creating ledger account...')
    await broker.ledger.addLedger(amount)
  } else {
    console.log('\n  depositing into existing ledger...')
    await broker.ledger.depositFund(amount)
  }

  if (provider) {
    // A sub-account is what actually pays a specific provider for inference. A third of the
    // deposit is a starting split, not a rule — top it up per provider as usage dictates.
    const share = BigInt(Math.floor((amount / 3) * 1e18))
    console.log(`  transferring to ${provider}...`)
    await broker.ledger.transferFund(provider, 'inference', share)
  }

  const ledger = await broker.ledger.getLedger()
  console.log(`\n  ledger balance now ${ethers.formatEther(ledger?.availableBalance ?? 0n)} 0G`)
  console.log('  verify with: node og-bridge/doctor.mjs')
}

main().catch((err) => {
  console.error(err?.shortMessage ?? err)
  process.exit(1)
})
