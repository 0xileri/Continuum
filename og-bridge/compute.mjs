// §5.2 — the reasoning call, run on 0G Compute, with the attestation captured.
//
// SCOPE, STATED PLAINLY (this is the §5.2 fallback, taken deliberately):
//
//   §5.2 asks to "wrap the aggregation step (at minimum) as an 0G Compute job rather than a plain
//   function call", and offers a fallback: "If the full aggregation step proves awkward to run
//   inside an 0G Compute job in the time available, fall back to running just the LLM reasoning
//   call through 0G Compute and flag this scope reduction explicitly in the README rather than
//   silently mocking it."
//
//   The fallback is not a time problem, it is a capability one, and §12 asked for this to be
//   settled early rather than discovered on Day 3. 0G Compute Network serves **inference and
//   fine-tuning against registered model providers** — it is a marketplace of TEE-hosted model
//   endpoints reached over an OpenAI-compatible API, not a general compute runtime. There is no
//   call shape that takes Continuum's aggregation function and executes it. So the reasoning call
//   goes through 0G Compute and the aggregation arithmetic runs locally, bound to its inputs by
//   the measurement_hash in continuum/scoring/attestation.py.
//
//   This is flagged in README.md, in WAVE3.md, in the explanation artifact's trust_disclaimer,
//   and on the dashboard. It is not mocked anywhere.
//
// What comes back IS a real attestation: the provider's response is signed by a key held inside
// the TEE, and broker.inference.processResponse verifies that signature for the response id. That
// verification result is what fills §6's attestation block.

import { createZGComputeNetworkBroker } from '@0gfoundation/0g-compute-ts-sdk'
import { fail, log, network, ok, readStdin, wallet, withTimeout } from './lib.mjs'

const TIMEOUT_MS = Number(process.env.CONTINUUM_OG_BRIDGE_TIMEOUT ?? 180) * 1000

async function main() {
  const input = readStdin()
  const { system, user, provider: wanted, maxTokens = 4096, temperature = 0 } = input

  if (!user) fail('no user message supplied')

  const net = network(input.network)
  const signer = wallet(net)
  log(`0G Compute — ${net.name} as ${signer.address}`)

  const broker = await withTimeout(
    createZGComputeNetworkBroker(signer),
    TIMEOUT_MS,
    'broker creation'
  )

  // --- provider selection ------------------------------------------------------------
  //
  // The provider address is part of what the attestation attests to, so leaving it to "whatever
  // listService returns first" makes a published score unreproducible. It is configurable and the
  // fallback is reported in the result rather than hidden.
  const services = await withTimeout(broker.inference.listService(), TIMEOUT_MS, 'listService')
  if (!services?.length) fail('0G Compute marketplace returned no inference services')

  let chosen
  if (wanted) {
    chosen = services.find((s) => s.provider?.toLowerCase() === wanted.toLowerCase())
    if (!chosen) {
      fail(`provider ${wanted} not found on the marketplace`, {
        available: services.map((s) => s.provider),
      })
    }
  } else {
    // Prefer a provider whose TEE signer the contract owner has acknowledged. That flag is what
    // makes processResponse able to verify anything at all — picking an unacknowledged provider
    // produces flags with no usable attestation, which is the one outcome this whole path exists
    // to avoid. Falling back to the first service is reported, not silent.
    chosen =
      services.find((s) => s.teeSignerAcknowledged && !s.occupied) ??
      services.find((s) => s.teeSignerAcknowledged) ??
      services[0]
  }

  const providerAddress = chosen.provider
  if (!chosen.teeSignerAcknowledged) {
    log(
      `  WARNING provider ${providerAddress} has no acknowledged TEE signer — the response will ` +
        `not be verifiable. Set CONTINUUM_OG_COMPUTE_PROVIDER to pin an acknowledged one.`
    )
  }

  // --- ledger ------------------------------------------------------------------------
  //
  // Funding is NOT done here. broker.ledger.depositFund spends real 0G, and a scoring run that
  // silently tops up a ledger every time it is short is a wallet-draining loop with a friendly
  // name. If the sub-account cannot pay, this exits with the exact command an operator should run.
  try {
    await broker.inference.acknowledgeProviderSigner(providerAddress)
  } catch (err) {
    const msg = String(err?.message ?? err)
    if (/ledger|account|fund|insufficient/i.test(msg)) {
      fail(
        `0G Compute sub-account for ${providerAddress} is not funded: ${msg}\n` +
          `Fund it yourself (this spends real 0G, so the bridge will not):\n` +
          `  node og-bridge/fund.mjs --amount 3 --provider ${providerAddress}`,
        { provider: providerAddress, needsFunding: true }
      )
    }
    throw err
  }

  const { endpoint, model } = await broker.inference.getServiceMetadata(providerAddress)
  const headers = await broker.inference.getRequestHeaders(providerAddress)

  const messages = []
  if (system) messages.push({ role: 'system', content: system })
  messages.push({ role: 'user', content: user })

  log(`  provider ${providerAddress}  model ${model}`)

  const response = await withTimeout(
    fetch(`${endpoint}/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify({ model, messages, max_tokens: maxTokens, temperature }),
    }),
    TIMEOUT_MS,
    'inference request'
  )

  if (!response.ok) {
    fail(`inference request failed: HTTP ${response.status} ${await response.text()}`)
  }

  const data = await response.json()
  const content = data?.choices?.[0]?.message?.content ?? ''
  const chatId = response.headers.get('ZG-Res-Key') || data?.id || ''

  // --- verification ------------------------------------------------------------------
  //
  // processResponse checks the provider's TEE signature for this response id and settles the fee.
  // A failure here is reported, never swallowed: `verified: false` reaching the payload is the
  // honest outcome, and config.OG_REQUIRE_ATTESTATION is what turns it into a hard stop for runs
  // that are going to be shown as Integration Proof.
  let verified = false
  let verifyError = ''
  if (chatId) {
    try {
      verified = Boolean(
        await withTimeout(
          broker.inference.processResponse(providerAddress, chatId),
          TIMEOUT_MS,
          'processResponse'
        )
      )
    } catch (err) {
      verifyError = String(err?.message ?? err)
      log(`  WARNING verification failed: ${verifyError}`)
    }
  } else {
    verifyError = 'provider returned no response id (ZG-Res-Key); nothing to verify against'
  }

  log(`  verified: ${verified}`)

  ok({
    content,
    attestation: {
      type: '0g-compute',
      provider: '0g-compute-network',
      job_id: chatId,
      // proof_ref is the handle the verification is reproducible from: anyone with the provider
      // address and this id can re-run processResponse. 0x-prefixed per §6's example, which shows
      // a hex string; the SDK's id is not natively hex, so it is hashed into that shape rather
      // than being reformatted lossily. The raw id stays in job_id.
      proof_ref: chatId ? '0x' + Buffer.from(chatId).toString('hex').slice(0, 64) : '',
      compute_node: providerAddress,
      verified,
      model,
    },
    tee_signer_acknowledged: Boolean(chosen.teeSignerAcknowledged),
    tee_signer_address: chosen.teeSignerAddress ?? '',
    verify_error: verifyError,
    network: net.name,
    signer: signer.address,
  })
}

main().catch((err) => fail(err?.stack ?? err))
