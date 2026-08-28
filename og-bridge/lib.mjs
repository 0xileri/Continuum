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

import { readFileSync } from 'node:fs'
import { ethers } from 'ethers'

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
