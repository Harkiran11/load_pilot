import { getAutopilotAttempts } from '../api/autopilot'
import { getLoad } from '../api/orders'
import type { AutopilotAttempt, LoadDetail, Page } from '../api/types'
import { readAutopilotResponse, verifyAutopilotPage } from './autopilot'

export type RunProfitStats = {
  submitted: RunSubmission[]
  submissions: number
  openBids: number
  estimatedProfit: string | null
  originalBid: string | null
  ratio: string | null
  missingPrices: number
  missingProfit: number
}

export type RunSubmission = { attemptId: string; shipmentId: string; externalId: string; orderType: string; rate: string | null; finishedAt: string | null }

function cents(value: unknown, signed = false): bigint | null {
  if (typeof value !== 'string') return null
  const text = value.trim()
  if (!(signed ? /^-?\d+(?:\.\d{1,2})?$/ : /^\d+(?:\.\d{1,2})?$/).test(text)) return null
  const negative = text.startsWith('-')
  const [whole, fraction = ''] = text.replace(/^-/, '').split('.')
  const amount = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
  return negative ? -amount : amount
}

function decimal(value: bigint) {
  const magnitude = value < 0n ? -value : value
  return `${value < 0n ? '-' : ''}${magnitude / 100n}.${String(magnitude % 100n).padStart(2, '0')}`
}

function submissions(attempts: AutopilotAttempt[]) {
  const seen = new Set<string>()
  return attempts.filter(attempt => {
    if (attempt.status !== 'submitted' || !['ACCEPT', 'BID'].includes(attempt.decision ?? '')) return false
    const key = attempt.action_id ?? attempt.attempt_id
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function calculateRunProfitStats(attempts: AutopilotAttempt[], originalRates: Map<string, string | null>): RunProfitStats {
  const submitted = submissions(attempts)
  let profit = 0n, price = 0n, missingPrices = 0, missingProfit = 0
  for (const attempt of submitted) {
    const economics = attempt.evaluation_snapshot.economics
    const original = attempt.order_type === 'fixed_rate'
      ? economics?.sources?.payout?.inputs?.['order.offered_rate'] ?? economics?.payout
      : originalRates.get(attempt.external_id)
    const amount = cents(original)
    const gain = cents(economics?.gross_profit, true)
    if (amount === null) missingPrices++; else price += amount
    if (gain === null) missingProfit++; else profit += gain
  }
  const magnitude = profit < 0n ? -profit : profit
  const percentage = price > 0n ? (magnitude * 10000n + price / 2n) / price : 0n
  return {
    submitted: submitted.map(attempt => ({ attemptId: attempt.attempt_id, shipmentId: attempt.shipment_id, externalId: attempt.external_id, orderType: attempt.order_type, rate: attempt.bid_rate ?? attempt.evaluation_snapshot.economics?.payout ?? null, finishedAt: attempt.finished_at_wall })),
    submissions: submitted.length,
    openBids: submitted.filter(attempt => attempt.order_type === 'open_bid').length,
    estimatedProfit: missingProfit ? null : decimal(profit), originalBid: missingPrices ? null : decimal(price),
    ratio: missingPrices || missingProfit || price === 0n ? null : decimal(profit < 0n ? -percentage : percentage),
    missingPrices, missingProfit,
  }
}

export async function getRunProfitStats(runId: string, originalRates: Map<string, string | null>, signal: AbortSignal) {
  const limit = 200
  const page = async (offset: number) => {
    const result = verifyAutopilotPage(await readAutopilotResponse<Page<AutopilotAttempt>>(await getAutopilotAttempts(runId, { limit, offset }, { signal })))
    if (result.offset !== offset || result.items.some(item => item.run_id !== runId || typeof item.attempt_id !== 'string' || typeof item.shipment_id !== 'string' || typeof item.external_id !== 'string' || typeof item.status !== 'string' || !item.evaluation_snapshot || typeof item.evaluation_snapshot !== 'object' || Array.isArray(item.evaluation_snapshot))) throw new Error('Run statistics did not match the current run.')
    return result
  }
  const first = await page(0)
  const attempts = [...first.items]
  for (let offset = limit; offset < first.total; offset += limit * 4) {
    const offsets = Array.from({ length: Math.min(4, Math.ceil((first.total - offset) / limit)) }, (_, index) => offset + index * limit)
    const pages = await Promise.all(offsets.map(page))
    if (pages.some(result => result.total !== first.total)) throw new Error('Run decisions changed during refresh. Statistics will retry.')
    attempts.push(...pages.flatMap(result => result.items))
  }
  if (new Set(attempts.map(item => item.attempt_id)).size !== first.total) throw new Error('Run decisions are incomplete. Statistics will retry.')
  const ids = [...new Set(submissions(attempts).filter(item => item.order_type === 'open_bid' && !originalRates.has(item.external_id)).map(item => item.external_id))]
  for (let offset = 0; offset < ids.length; offset += 4) {
    await Promise.all(ids.slice(offset, offset + 4).map(async externalId => {
      try {
        const order = await readAutopilotResponse<LoadDetail>(await getLoad(externalId, { signal }))
        if (order.external_id === externalId && order.order_type === 'open_bid' && cents(order.offered_rate) !== null) originalRates.set(externalId, order.offered_rate)
      } catch { /* An unavailable original price stays missing and retries on refresh. */ }
    }))
  }
  return calculateRunProfitStats(attempts, originalRates)
}
