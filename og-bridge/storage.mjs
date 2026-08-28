// §5.3 — 0G Storage as the feature store.
//
//   "Each Borrower Feature Record (Section 6) and any synthetic evidence documents get written to
//    0G Storage; the on-chain payload carries only the resulting content hash/URI, not the raw
//    record."
//
// The hash-not-the-record split is a privacy property as much as a gas one. Borrower financials —
// counterparty names, revenue, disputes — do not belong in a public registry, and a merkle root is
// a commitment to them without being a disclosure. §11 lists data privacy as a live concern for
// this product; this is the shape that answers it.
//
// §5.3 also notes the staleness consequence: "a borrower whose feed goes quiet is visible as an
// absence of new 0G Storage writes, not just a database flag". That falls out of doing the writes
// per re-score rather than batching them.
//
// Usage:
//   echo '{"action":"upload","path":"data/features/brw_x.json"}' | node storage.mjs
//   echo '{"action":"download","root_hash":"0x...","path":"out.json"}' | node storage.mjs

import { statSync } from 'node:fs'
import { Indexer, ZgFile } from '@0gfoundation/0g-storage-ts-sdk'
import { fail, log, network, ok, readStdin, wallet, withTimeout } from './lib.mjs'

const TIMEOUT_MS = Number(process.env.CONTINUUM_OG_BRIDGE_TIMEOUT ?? 180) * 1000

async function upload(input, net) {
  const { path } = input
  if (!path) fail('no path supplied')

  const signer = wallet(net)
  const indexer = new Indexer(process.env.CONTINUUM_OG_STORAGE_INDEXER || net.indexer)

  const file = await ZgFile.fromFilePath(path)
  try {
    const [tree, treeErr] = await file.merkleTree()
    if (treeErr) fail(`merkle tree failed: ${treeErr}`)
    const rootHash = tree?.rootHash()
    if (!rootHash) fail('merkle tree produced no root hash')

    log(`0G Storage — uploading ${path} (root ${rootHash}) to ${net.name}`)

    // The root hash is deterministic in the file's content, so an identical record uploaded twice
    // yields the same root. A duplicate upload is reported rather than treated as an error: the
    // content is already stored under that root and re-scoring an unchanged record is normal.
    const [tx, err] = await withTimeout(
      indexer.upload(file, net.rpc, signer),
      TIMEOUT_MS,
      'storage upload'
    )

    if (err && !/already exist|duplicate/i.test(String(err))) {
      fail(`upload failed: ${err}`)
    }

    // The upload result carries its own rootHash (or rootHashes for a fragmented upload). Prefer
    // it over the locally computed tree when present: they must agree, and if they ever do not,
    // the authoritative one is what the network actually stored under.
    const storedRoot = tx?.rootHash ?? tx?.rootHashes?.[0] ?? rootHash
    if (storedRoot !== rootHash) {
      log(`  NOTE local merkle root ${rootHash} differs from stored ${storedRoot}; using stored`)
    }

    ok({
      provider: '0g-storage',
      root_hash: storedRoot,
      local_root_hash: rootHash,
      uri: `0g://${storedRoot}`,
      tx_hash: tx?.txHash ?? tx?.txHashes?.[0] ?? '',
      tx_seq: tx?.txSeq ?? tx?.txSeqs?.[0] ?? null,
      already_stored: Boolean(err),
      size_bytes: statSync(path).size,
      network: net.name,
      explorer_url: `${net.storageExplorer}/tx/${storedRoot}`,
      signer: signer.address,
    })
  } finally {
    await file.close()
  }
}

async function download(input, net) {
  const { root_hash: rootHash, path } = input
  if (!rootHash || !path) fail('download needs root_hash and path')

  const indexer = new Indexer(process.env.CONTINUUM_OG_STORAGE_INDEXER || net.indexer)
  // withProof=true: verifying the merkle proof on the way back is the entire reason the root hash
  // is the identifier. Downloading without it would make this an ordinary CDN fetch.
  const err = await withTimeout(indexer.download(rootHash, path, true), TIMEOUT_MS, 'download')
  if (err) fail(`download failed: ${err}`)
  ok({ root_hash: rootHash, path, verified: true, network: net.name })
}

async function main() {
  const input = readStdin()
  const net = network(input.network)
  const action = input.action ?? 'upload'

  if (action === 'upload') return upload(input, net)
  if (action === 'download') return download(input, net)
  fail(`unknown action ${action}`)
}

main().catch((err) => fail(err?.stack ?? err))
