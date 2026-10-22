import type { ExpectedEconomics, HistoricalMarketContext, LoadView, ModelPriceAdvice, PriceAnalysisResult } from '../api/types'
import { marginForBid, normalizeBid } from './profit-margin'

export function priceAnalysisKey(load: LoadView, bid: string | null, unitId: string | null) {
  // Sync timestamps/countdowns change on heartbeats; invalidate on actual input edits.
  return JSON.stringify([
    load.external_id, load.order_type, load.status, load.board_missing_at,
    load.origin_terminal_code, load.destination_terminal_code, load.origin_state, load.destination_state,
    load.origin_latitude, load.origin_longitude, load.destination_latitude, load.destination_longitude,
    load.distance_miles, load.weight_lbs, load.cost_per_mile, load.estimated_cost, load.offered_rate,
    load.pickup_at, load.delivery_at, load.expires_at, bid, unitId,
  ])
}

export interface FixedPriceAnalysis {
  order_type: 'fixed_rate'
  economics: ExpectedEconomics | null
  offered_rate: string | null
  distance_miles: string | null
  market_context?: HistoricalMarketContext | null
}

export type PriceAnalysisReport = PriceAnalysisResult | FixedPriceAnalysis

export function isFixedPriceAnalysis(result: PriceAnalysisReport): result is FixedPriceAnalysis {
  return 'order_type' in result && result.order_type === 'fixed_rate'
}

export function priceEconomics(result: PriceAnalysisReport) {
  return isFixedPriceAnalysis(result) ? result.economics : result.deterministic_evaluation.economics
}

export function priceAdvice(result: PriceAnalysisReport): ModelPriceAdvice | null {
  if (isFixedPriceAnalysis(result)) return null
  const advice = result.model_response?.advisory
  return result.advisory_status !== 'unavailable' && advice && 'pricing_source' in advice
    && advice.pricing_source === result.advisory_status ? advice : null
}

function metricValue(value: unknown): number | null {
  if (value === null || value === undefined || (typeof value === 'string' && !value.trim()) || !['string', 'number'].includes(typeof value)) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function chartValue(value: unknown): number | null {
  const number = metricValue(value)
  return number !== null && number >= 0 ? number : null
}

export interface PriceMetricGroup {
  title: string
  unit: 'USD' | '%' | 'USD/mi'
  metrics: Array<{ label: string; value: number | null; color: string; signed?: boolean }>
}

export function priceAnalysisMetricGroups(result: PriceAnalysisReport): PriceMetricGroup[] {
  const economics = priceEconomics(result)
  const profit = metricValue(economics?.gross_profit)
  const margin = metricValue(economics?.margin_pct)
  if (isFixedPriceAnalysis(result)) {
    const miles = chartValue(result.distance_miles)
    const payout = chartValue(economics?.payout ?? result.offered_rate)
    const cost = chartValue(economics?.total_cost)
    return [
      { title: 'Offer economics', unit: 'USD', metrics: [
        { label: 'Estimated total cost', value: cost, color: 'fill-primary' },
        { label: 'Estimated gross profit', value: profit, color: profit !== null && profit < 0 ? 'fill-error' : 'fill-success' },
      ] },
      { title: 'Profit margin', unit: '%', metrics: [
        { label: 'Estimated margin', value: margin, color: margin !== null && margin < 0 ? 'fill-error' : 'fill-primary' },
        { label: 'Configured minimum', value: metricValue(economics?.bid_floor.minimum_margin_pct), color: 'fill-warning' },
      ] },
      { title: 'Rate per loaded mile', unit: 'USD/mi', metrics: [
        { label: 'Offer / mile', value: payout !== null && miles ? payout / miles : null, color: 'fill-primary' },
        { label: 'Cost / mile', value: cost !== null && miles ? cost / miles : null, color: 'fill-info' },
      ] },
    ]
  }
  const comparison = result.model_response?.comparison
  const advice = priceAdvice(result)
  return [
    { title: 'Profit & price difference', unit: 'USD', metrics: [
      { label: 'Estimated gross profit', value: profit, color: profit !== null && profit < 0 ? 'fill-error' : 'fill-success' },
      { label: 'Bid − advisory', value: metricValue(comparison?.manual_minus_advisory_dollars), color: 'fill-primary', signed: true },
    ] },
    { title: 'Margin & deviation', unit: '%', metrics: [
      { label: 'Estimated margin', value: margin, color: margin !== null && margin < 0 ? 'fill-error' : 'fill-primary' },
      { label: 'Deviation from advice', value: metricValue(comparison?.absolute_reference_deviation_percent), color: 'fill-warning' },
    ] },
    { title: 'Rate per loaded mile', unit: 'USD/mi', metrics: [
      { label: 'Your bid / mile', value: metricValue(comparison?.manual_bid_rpm), color: 'fill-primary' },
      { label: 'Advisory / mile', value: metricValue(advice?.predicted_bid_rpm), color: 'fill-info' },
    ] },
  ]
}

export function priceAnalysisLines(result: PriceAnalysisReport) {
  const advice = priceAdvice(result)
  const economics = priceEconomics(result)
  const local = [
    { label: 'Estimated cost', value: chartValue(economics?.total_cost), color: 'stroke-base-content/40', swatch: 'bg-base-content/40' },
    { label: isFixedPriceAnalysis(result) ? 'Minimum profitable rate' : 'Minimum profitable bid', value: chartValue(economics?.minimum_profitable_bid), color: 'stroke-warning', swatch: 'bg-warning' },
  ]
  if (isFixedPriceAnalysis(result)) {
    const history = fixedPriceHistory(result)
    return history ? [...local,
      { label: 'Historical median', value: chartValue(history.payout.median), color: 'stroke-success', swatch: 'bg-success' },
      { label: 'Historical average', value: chartValue(history.payout.mean), color: 'stroke-info', swatch: 'bg-info' },
    ] : local
  }
  return [...local,
    { label: advice?.pricing_source === 'baseline' ? 'Baseline fallback advice' : 'Model advisory', value: chartValue(advice?.predicted_bid_dollars), color: 'stroke-info', swatch: 'bg-info' },
    { label: 'Historical baseline', value: chartValue(advice?.baseline_reference.predicted_bid_dollars), color: 'stroke-success', swatch: 'bg-success' },
  ]
}

export function fixedPriceHistory(result: FixedPriceAnalysis) {
  const history = result.market_context
  return history?.order_type === 'fixed_rate' && history.payout?.sample_count > 0 ? history : null
}

export function fixedPriceHistoryRange(result: PriceAnalysisReport) {
  if (!isFixedPriceAnalysis(result)) return null
  const history = fixedPriceHistory(result)
  if (!history) return null
  const minimum = chartValue(history.payout.minimum), maximum = chartValue(history.payout.maximum)
  return minimum !== null && maximum !== null && minimum <= maximum ? { minimum, maximum } : null
}

export function priceMarginCurve(result: PriceAnalysisReport, bid: string | null) {
  const economics = priceEconomics(result)
  const cost = chartValue(economics?.total_cost)
  const currentBid = chartValue(bid)
  const margin = bid ? marginForBid(economics?.total_cost ?? null, bid) : null
  const currentMargin = margin === null ? null : Number(margin)
  const minMargin = Math.min(0, currentMargin === null ? 0 : Math.floor(currentMargin / 10) * 10)
  const maxMargin = Math.min(99.99, Math.max(50, currentMargin === null ? 50 : Math.ceil(currentMargin / 10) * 10))
  // This is the deterministic revenue/margin relationship, not a model forecast.
  const points = cost === null ? [] : Array.from({ length: 81 }, (_, index) => {
    const margin = minMargin + (maxMargin - minMargin) * index / 80
    return { margin, bid: cost / (1 - margin / 100) }
  })
  return { points, minMargin, maxMargin, currentMargin, currentBid }
}

/** Keep chart callouts apart without changing their data-point coordinates. */
export function layoutPriceCallouts<T extends { anchorY: number | null }>(
  items: T[], top: number, bottom: number, height = 38, gap = 6,
): Array<T & { boxY: number }> {
  const placed = items.map((item, index) => ({ item, index }))
    .sort((a, b) => (a.item.anchorY ?? Infinity) - (b.item.anchorY ?? Infinity) || a.index - b.index)
    .map(({ item }) => ({ ...item, boxY: Math.max(top, Math.min(bottom - height, (item.anchorY ?? bottom) - height / 2)) }))
  for (let index = 1; index < placed.length; index++) {
    placed[index].boxY = Math.max(placed[index].boxY, placed[index - 1].boxY + height + gap)
  }
  if (placed.length && placed.at(-1)!.boxY + height > bottom) {
    placed.at(-1)!.boxY = bottom - height
    for (let index = placed.length - 2; index >= 0; index--) {
      placed[index].boxY = Math.min(placed[index].boxY, placed[index + 1].boxY - height - gap)
    }
  }
  return placed
}

export function verifyPriceAnalysis(result: PriceAnalysisResult, requestId: string, externalId: string, bid: string, unitId: string | null) {
  if (!result || result.request_id !== requestId || result.external_id !== externalId
    || normalizeBid(String(result.bid_rate)) !== bid || result.requested_unit_id !== unitId
    || !result.deterministic_evaluation?.economics
    || !['model', 'baseline', 'unavailable'].includes(result.advisory_status)
    || result.advisory_only !== true || result.submission_enabled !== false || result.requires_guardrail_checks !== true) {
    throw new Error('The price analysis did not match this order and bid. Analyze the current price again.')
  }
  return result
}
