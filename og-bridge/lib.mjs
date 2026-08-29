// Shared plumbing for the three 0G CLIs.
//
// Why a Node bridge exists at all: 0G ships TypeScript and Go SDKs. Continuum's scoring engine is
// Python (§8's stack table), and there is no Python SDK for Compute or Storage. The options were
// to reimplement the broker's signing and settlement against the contracts by hand, or to shell
// out to the vendor SDK. Reimplementing an authentication and micropayment protocol to avoid a
// subprocess would be the wrong trade — the bridge is thin, and the SDK is the thing that will
// track 0G's changes.
//
// Contract with the Python side: read one JSON object from stdin, write exactly one JSON object to
// stdout, put everything human-readable on stderr. Any failure exits non-zero with
// {"ok": false, "error": ...} on stdout, so the caller never has to parse a traceback.

import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ethers } from 'ethers'

// Load the repo-root .env, so the signing key can live in one gitignored file rather than being
// exported into a shell. Python's side already reads it via python-dotenv; without this the Node
// bridge would be the one component that needed the secret in the environment, which is the one
// place it is most likely to end up in a shell history or a captured log.
//
// Deliberately does NOT overwrite an already-set variable: an explicit `export` in the calling
// shell should win over a file, so a one-off run against a different key does not silently pick up
// the checked-out .env instead.
function loadDotEnv() {
  const path = join(dirname(dirname(fileURLToPath(import.meta.url))), '.env')
  if (!existsSync(path)) return
  for (const line of readFileSync(path, 'utf-8').split(/\r?\n/)) {
    const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line)
    if (!match) continue
    const [, key] = match
    let value = match[2].trim()
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1)
    }
    if (value && process.env[key] === undefined) process.env[key] = value
  }
}
loadDotEnv()

// Keep stdout clean for the JSON result — the contract with the Python side is exactly one JSON
// object on stdout and everything human-readable on stderr.
//
// The 0G Storage SDK does not honour that: it console.logs upload options and node status during
// `indexer.upload`, which lands on stdout and makes the result unparseable. Rather than have every
// caller strip vendor chatter — a fragile guess about what a future SDK version happens to print —
// this redirects console.log/info/debug to stderr process-wide. `ok()` and `fail()` write to
// process.stdout directly, so they are unaffected, and the SDK's diagnostics stay visible in the
// bridge log where they belong.
console.log = (...args) => console.error(...args)
console.info = (...args) => console.error(...args)
console.debug = (...args) => console.error(...args)

export const NETWORKS = {
  mainnet: {
    name: '0g-chain-mainnet',
    chainId: 16661,
    rpc: 'https://evmrpc.0g.ai',
    explorer: 'https://chainscan.0g.ai',
    indexer: 'https://indexer-storage-turbo.0g.ai',
    storageExplorer: 'https://storagescan.0g.ai',
  },
  testnet: {
    name: '0g-galileo-testnet',
    chainId: 16602,
    rpc: 'https://evmrpc-testnet.0g.ai',
    explorer: 'https://chainscan-galileo.0g.ai',
    indexer: 'https://indexer-storage-testnet-turbo.0g.ai',
    storageExplorer: 'https://storagescan-galileo.0g.ai',
  },
}

export function readStdin() {
  try {
    return JSON.parse(readFileSync(0, 'utf-8') || '{}')
  } catch (err) {
    fail(`could not parse stdin as JSON: ${err.message}`)
  }
}

export function network(name) {
  const net = NETWORKS[name ?? process.env.CONTINUUM_OG_NETWORK ?? 'testnet']
  if (!net) fail(`unknown 0G network ${name}`)
  return { ...net, rpc: process.env.CONTINUUM_OG_RPC_URL || net.rpc }
}

// The private key is read from the environment and never echoed, never written to a file, and
// never included in the JSON returned to Python. The address it derives is returned instead —
// that is what an operator needs to fund and what a reader needs to check on the explorer.
export function wallet(net) {
  const pk = process.env.OG_PRIVATE_KEY || process.env.PRIVATE_KEY
  if (!pk) {
    fail(
      'OG_PRIVATE_KEY is not set. The 0G bridge signs transactions with it; set it in your ' +
        'shell (never in the repo) and fund the address on ' +
        (net.name.includes('mainnet') ? '0G mainnet' : 'the Galileo faucet: https://faucet.0g.ai')
    )
  }
  const provider = new ethers.JsonRpcProvider(net.rpc, net.chainId)
  return new ethers.Wallet(pk, provider)
}

export function ok(payload) {
  process.stdout.write(JSON.stringify({ ok: true, ...payload }))
  process.exit(0)
}

export function fail(error, extra = {}) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(error), ...extra }))
  process.exit(1)
}

export function log(...args) {
  // stderr, so it can never contaminate the JSON channel.
  console.error(...args)
}

export async function withTimeout(promise, ms, label) {
  let timer
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms)
  })
  try {
    return await Promise.race([promise, timeout])
  } finally {
    clearTimeout(timer)
  }
}
