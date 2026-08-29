// Deploy ContinuumScoreRegistry using ethers and the Foundry-compiled artifact.
//
// WHY THIS EXISTS ALONGSIDE contracts/script/Deploy.s.sol:
//
// `forge script ... --broadcast` is the primary, documented path and remains so — it is what the
// README tells a reader to run, and it is what most environments will use. This is a fallback for
// machines where the forge binary cannot execute (Windows Application Control policy blocked it on
// the machine this was first run on, after the contract had already compiled and its tests had
// passed). Rather than fight a host security policy, this sends the same deployment transaction
// with the same constructor arguments using the same compiled bytecode.
//
// It deploys `contracts/out/ContinuumScoreRegistry.sol/ContinuumScoreRegistry.json` — the artifact
// Foundry produced — so the deployed bytecode is byte-identical to what `forge test` verified. It
// does NOT recompile, and it refuses to run if the artifact is missing: silently compiling with a
// different toolchain would break the property that makes the artifact worth trusting.
//
//   node og-bridge/deploy.mjs                 # dry run — prints cost, spends nothing
//   node og-bridge/deploy.mjs --yes
//
// Constructor arguments mirror config.py and Deploy.s.sol exactly:
//   cooldownSeconds  = RESCORE_COOLDOWN_HOURS (6h) * 3600 = 21600
//   maxRateChangeBps = MAX_RATE_CHANGE_BPS_PER_UPDATE      = 50

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ethers } from 'ethers'
import { network, wallet } from './lib.mjs'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const ARTIFACT = join(
  ROOT,
  'contracts',
  'out',
  'ContinuumScoreRegistry.sol',
  'ContinuumScoreRegistry.json'
)

const COOLDOWN_SECONDS = 6 * 60 * 60
const MAX_RATE_CHANGE_BPS = 50

function arg(name, fallback = undefined) {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) return fallback
  const next = process.argv[i + 1]
  return next && !next.startsWith('--') ? next : true
}

async function main() {
  const confirmed = Boolean(arg('yes', false))
  const net = network(process.env.CONTINUUM_OG_NETWORK ?? 'testnet')

  if (!existsSync(ARTIFACT)) {
    console.error(
      `No compiled artifact at ${ARTIFACT}.\n` +
        `Build it first:  cd contracts && forge build\n` +
        `This script deliberately does not compile — the whole point is to deploy the exact\n` +
        `bytecode that forge test verified.`
    )
    process.exit(1)
  }

  const artifact = JSON.parse(readFileSync(ARTIFACT, 'utf-8'))
  const signer = wallet(net)

  const balance = await signer.provider.getBalance(signer.address)
  const feeData = await signer.provider.getFeeData()
  const gasPrice = feeData.gasPrice ?? feeData.maxFeePerGas

  const factory = new ethers.ContractFactory(artifact.abi, artifact.bytecode.object, signer)
  const deployTx = await factory.getDeployTransaction(COOLDOWN_SECONDS, MAX_RATE_CHANGE_BPS)
  const gas = await signer.provider.estimateGas({ ...deployTx, from: signer.address })
  const cost = gas * gasPrice

  console.log(`ContinuumScoreRegistry — ${net.name} (chain ${net.chainId})`)
  console.log(`  deployer         ${signer.address}`)
  console.log(`  balance          ${ethers.formatEther(balance)} 0G`)
  console.log(`  cooldown         ${COOLDOWN_SECONDS}s (6h)`)
  console.log(`  rate cap         ${MAX_RATE_CHANGE_BPS} bps`)
  console.log(`  estimated gas    ${gas}`)
  console.log(`  estimated cost   ${ethers.formatEther(cost)} 0G`)

  if (net.name.includes('mainnet')) {
    console.log('\n  *** MAINNET — this spends real 0G and is irreversible. ***')
  }

  if (balance < cost) {
    console.error(`\nInsufficient balance.${net.faucet ? ` Faucet: ${net.faucet}` : ''}`)
    process.exit(1)
  }

  if (!confirmed) {
    console.log('\nDry run — nothing was deployed. Re-run with --yes.')
    process.exit(0)
  }

  console.log('\n  deploying...')
  const contract = await factory.deploy(COOLDOWN_SECONDS, MAX_RATE_CHANGE_BPS)
  const receipt = await contract.deploymentTransaction().wait()
  const address = await contract.getAddress()

  console.log(`  address          ${address}`)
  console.log(`  tx               ${receipt.hash}`)
  console.log(`  block            ${receipt.blockNumber}`)
  console.log(`  gas used         ${receipt.gasUsed}`)
  console.log(`  explorer         ${net.explorer}/address/${address}`)

  // Record it where continuum/og/chain.py looks, so the engine finds the registry without an
  // environment variable in every shell.
  const dir = join(ROOT, 'deployments')
  mkdirSync(dir, { recursive: true })
  const record = {
    network: net.name,
    chain_id: net.chainId,
    address,
    tx_hash: receipt.hash,
    block_number: receipt.blockNumber,
    explorer_url: `${net.explorer}/address/${address}`,
    contract: 'ContinuumScoreRegistry',
    deployer: signer.address,
    cooldown_seconds: COOLDOWN_SECONDS,
    max_rate_change_bps: MAX_RATE_CHANGE_BPS,
    deployed_at: new Date().toISOString(),
    // The deployer is authorised as a scorer by the constructor, so no follow-up transaction is
    // needed for a single-operator setup.
    authorized_scorer: signer.address,
  }
  const out = join(dir, `${process.env.CONTINUUM_OG_NETWORK ?? 'testnet'}.json`)
  writeFileSync(out, JSON.stringify(record, null, 2), 'utf-8')
  console.log(`  recorded         ${out}`)
}

main().catch((err) => {
  console.error(err?.shortMessage ?? err?.message ?? err)
  process.exit(1)
})
