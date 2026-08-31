// Fund the 0G Compute ledger. **This spends real 0G.**
//
// Kept as its own explicit command, and deliberately NOT called from the scoring path. An engine
// that silently tops up a ledger whenever a call is short is a wallet-draining loop with a friendly
// name — and on mainnet that is somebody's money. `compute.mjs` detects an unfunded sub-account and
// prints this command rather than running it.
//
// Every invocation prints what it is about to spend and requires --yes to proceed.
//
//   node og-bridge/fund.mjs --amount 0.2
//   node og-bridge/fund.mjs --amount 0.2 --provider 0xabc... --yes
//
// The ledger's real floor is the LedgerManager's on-chain MIN_ACCOUNT_BALANCE, and it DIFFERS BY
// NETWORK: 0.1 0G on Galileo testnet, 3.0 0G on mainnet. The SDK hardcodes 3 and reports it as
// "the contract requires" on both, which is wrong on testnet and right on mainnet. This reads the
// constant from the chain instead of hardcoding either number, so the check is correct on both and
// a change on 0G's side surfaces as a clear error rather than a mystifying revert.

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
  const amount = Number(arg('amount', 0.2))
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

  // Check the real floor before spending anything.
  const LEDGERS = {
    '0g-galileo-testnet': '0xE70830508dAc0A97e6c087c75f402f9Be669E406',
    '0g-chain-mainnet': '0x2dE54c845Cd948B72D2e32e39586fe89607074E3',
  }
  const ledgerContract = new ethers.Contract(
    LEDGERS[net.name],
    ['function MIN_ACCOUNT_BALANCE() view returns (uint256)'],
    rpc
  )
  const minAccount = Number(ethers.formatEther(await ledgerContract.MIN_ACCOUNT_BALANCE()))
  if (amount < minAccount) {
    console.error(
      `
--amount ${amount} is below the ledger's on-chain MIN_ACCOUNT_BALANCE of ${minAccount} 0G.`
    )
    process.exit(1)
  }
  console.log(`  ledger minimum ${minAccount} 0G (read from chain)`)

  const broker = await createZGComputeNetworkBroker(wallet)

  let hasLedger = true
  try {
    await broker.ledger.getLedger()
  } catch {
    hasLedger = false
  }

  if (!hasLedger) {
    // The SDK refuses to CREATE a ledger below 3 0G, in both addLedger and depositFund, and its
    // error text says "the contract requires a minimum of 3 0G". That justification is incorrect:
    // the LedgerManager's own MIN_ACCOUNT_BALANCE is 0.1 0G, read from the chain a few lines above.
    // The 3 is a client-side comfort default.
    //
    // --direct calls the same contract function the SDK calls — addLedger(string) payable, with
    // the amount as msg.value, exactly as LedgerProcessor does — skipping only that guard. It is
    // not a bypass of any on-chain rule: a value below the real MIN_ACCOUNT_BALANCE still reverts,
    // and this script refuses it before sending. Useful on testnet, where the SDK's 3 0G is wrong;
    // on mainnet the contract genuinely requires 3, so this flag changes nothing there.
    if (amount < 3 && arg('direct', false)) {
      console.log(
        `\n  creating ledger by calling addLedger() directly.\n` +
          `  The SDK's 3 0G floor is client-side; the contract's MIN_ACCOUNT_BALANCE is ` +
          `${minAccount} 0G.`
      )
      const ledgerWrite = new ethers.Contract(
        LEDGERS[net.name],
        ['function addLedger(string additionalInfo) payable'],
        wallet
      )
      const tx = await ledgerWrite.addLedger('', { value: ethers.parseEther(String(amount)) })
      const receipt = await tx.wait()
      console.log(`  tx ${receipt.hash} in block ${receipt.blockNumber}`)
    } else {
      console.log('\n  creating ledger account...')
      await broker.ledger.addLedger(amount)
    }
  } else {
    console.log('\n  depositing into existing ledger...')
    await broker.ledger.depositFund(amount)
  }

  if (provider) {
    // A sub-account is what actually pays a specific provider for inference. A third of the
    // deposit is a starting split, not a rule — top it up per provider as usage dictates.
    const share = BigInt(Math.floor((amount / 2) * 1e18))
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
