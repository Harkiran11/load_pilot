import { useId } from 'react'
import { fixedPriceHistoryRange, isFixedPriceAnalysis, layoutPriceCallouts, priceAnalysisLines, priceMarginCurve, type PriceAnalysisReport } from '../utils/price-analysis'
import { amount, quantity } from '../utils/load-format'

export default function PriceAnalysisChart({ result, bid }: { result: PriceAnalysisReport; bid: string | null }) {
  const id = useId()
  const fixed = isFixedPriceAnalysis(result)
  const payoutLabel = fixed ? 'Offered rate' : 'Your bid'
  const lines = priceAnalysisLines(result)
  const curve = priceMarginCurve(result, bid)
  const historyRange = fixedPriceHistoryRange(result)
  const prices = [
    ...(historyRange ? [historyRange.minimum, historyRange.maximum] : []),
    ...lines.flatMap((line) => line.value === null ? [] : [line.value]),
    ...curve.points.map((point) => point.bid),
    ...(curve.currentBid === null ? [] : [curve.currentBid]),
  ]
  const minimum = prices.length ? Math.min(...prices) : 0
  const highest = prices.length ? Math.max(...prices) : 1
  const maximum = highest + Math.max(1, highest - minimum) * 0.1
  const left = 52, right = 282, top = 24, bottom = 266
  const boxX = 306, boxWidth = 166, boxHeight = 38
  const x = (margin: number) => left + (margin - curve.minMargin) / (curve.maxMargin - curve.minMargin) * (right - left)
  const y = (price: number) => bottom - (price - minimum) / (maximum - minimum) * (bottom - top)
  const baseline = curve.points.length ? y(curve.points[0].bid) : bottom
  const axis = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 })
  const path = curve.points.map((point, index) => `${index ? 'L' : 'M'}${x(point.margin).toFixed(2)},${y(point.bid).toFixed(2)}`).join(' ')
  const valueLabel = (value: number | null) => value === null ? 'Unavailable' : amount(String(value))
  const currentPoint = curve.currentMargin !== null && curve.currentBid !== null
    ? { x: x(curve.currentMargin), y: y(curve.currentBid) } : null
  const callouts = layoutPriceCallouts([
    ...lines.map((line) => ({
      label: line.label, value: valueLabel(line.value), color: line.value === null ? 'stroke-base-300' : line.color,
      anchorX: line.value === null ? null : right, anchorY: line.value === null ? null : y(line.value), current: false,
    })),
    ...(currentPoint ? [{
      label: `${payoutLabel} · ${quantity(curve.currentMargin, '%')}`, value: valueLabel(curve.currentBid), color: 'stroke-primary',
      anchorX: currentPoint.x, anchorY: currentPoint.y, current: true,
    }] : []),
  ], top, bottom, boxHeight)

  return <figure className="rounded-lg border border-base-300 bg-base-100 p-3">
    <figcaption className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs font-semibold">{fixed ? 'Offer vs profit margin' : 'Bid vs profit margin'} <span className="text-[10px] font-normal text-base-content/55">USD · % of revenue</span></figcaption>
    <div className="overflow-x-auto">
      <svg viewBox="0 0 480 316" className="block w-full min-w-[440px]" role="img" aria-labelledby={`${id}-title ${id}-description`}>
        <title id={`${id}-title`}>{fixed ? 'Fixed offer by profit margin' : 'Bid price by profit margin'}</title>
        <desc id={`${id}-description`}>{`The curve shows the payout needed at each margin using the backend estimated cost. Connected callout boxes show the payout, margin and reference values. ${payoutLabel}: ${valueLabel(curve.currentBid)}. Current margin: ${quantity(curve.currentMargin, '%')}. ${lines.map((line) => `${line.label}: ${valueLabel(line.value)}`).join('. ')}.`}</desc>
        <text x={left} y={13} className="fill-base-content/60" fontSize={9}>{fixed ? 'Payout (USD)' : 'Bid price (USD)'}</text>
        {historyRange && <rect data-chart-history="payout-range" x={left} y={y(historyRange.maximum)} width={right - left} height={Math.max(1, y(historyRange.minimum) - y(historyRange.maximum))} className="fill-success/10">
          <title>{`Historical payout range: ${valueLabel(historyRange.minimum)}–${valueLabel(historyRange.maximum)}. Observed payouts, not a prediction interval.`}</title>
        </rect>}
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => <g key={fraction} aria-hidden="true">
          <line x1={left} x2={right} y1={y(minimum + (maximum - minimum) * fraction)} y2={y(minimum + (maximum - minimum) * fraction)} className="stroke-base-300/60" strokeDasharray="3 4" />
          <text x={left - 7} y={y(minimum + (maximum - minimum) * fraction) + 3} textAnchor="end" className="fill-base-content/55" fontSize={9}>{axis.format(minimum + (maximum - minimum) * fraction)}</text>
          <text x={left + fraction * (right - left)} y={bottom + 17} textAnchor="middle" className="fill-base-content/55" fontSize={9}>{Number((curve.minMargin + fraction * (curve.maxMargin - curve.minMargin)).toFixed(1))}%</text>
        </g>)}
        <line x1={left} x2={left} y1={top} y2={bottom} className="stroke-base-content/25" strokeWidth={1} />
        <line data-chart-axis="baseline" x1={left} x2={right} y1={baseline} y2={baseline} className="stroke-base-content/40" strokeWidth={1.25} />
        <text x={(left + right) / 2} y={bottom + 38} textAnchor="middle" className="fill-base-content/60" fontSize={10}>Profit margin (% of revenue)</text>
        {lines.filter((line) => line.value !== null).map((line) => <line key={line.label} x1={left} x2={right} y1={y(line.value!)} y2={y(line.value!)} className={line.color} strokeWidth={1.25} strokeDasharray="4 4" />)}
        {path && <path data-chart-curve="bid" d={path} fill="none" className="stroke-primary" strokeWidth={2.25} strokeLinejoin="round"><title>Payout required for each margin at the analyzed cost</title></path>}
        {currentPoint && <line x1={currentPoint.x} x2={currentPoint.x} y1={currentPoint.y} y2={bottom} className="stroke-primary/25" strokeDasharray="3 4" />}
        {callouts.map((callout) => <g key={callout.current ? 'current-bid' : callout.label} data-chart-callout={callout.current ? 'current-bid' : 'reference'}>
          <title>{`${callout.label}: ${callout.value}`}</title>
          {callout.anchorX !== null && callout.anchorY !== null && <>
            <path d={`M${callout.anchorX},${callout.anchorY} H${right + 10} L${boxX - 8},${callout.boxY + boxHeight / 2} H${boxX}`} fill="none" className={callout.color} strokeWidth={0.8} opacity={0.65} />
            <circle cx={callout.anchorX} cy={callout.anchorY} r={callout.current ? 4 : 2.5} className={callout.current ? 'fill-primary stroke-base-100' : `fill-base-100 ${callout.color}`} strokeWidth={1.5} />
          </>}
          <rect x={boxX} y={callout.boxY} width={boxWidth} height={boxHeight} rx={5} className={`fill-base-100 ${callout.color}`} strokeWidth={callout.current ? 1.25 : 0.65} />
          <text x={boxX + 8} y={callout.boxY + 13} className="fill-base-content/60" fontSize={10.5}>{callout.label}</text>
          <text x={boxX + 8} y={callout.boxY + 28} className={callout.current ? 'fill-primary' : 'fill-base-content'} fontSize={12} fontWeight={600} textLength={callout.value.length > 22 ? boxWidth - 16 : undefined} lengthAdjust="spacingAndGlyphs">{callout.value}</text>
        </g>)}
      </svg>
    </div>
    {!curve.points.length && <p className="mt-2 text-xs text-base-content/60">Cost is unavailable, so the margin curve cannot be calculated.</p>}
    {fixed && <p className="mt-2 text-[10px] text-base-content/60">Model advisory unavailable: the backend currently supports open bids only.</p>}
    {historyRange && <p className="mt-1 text-[10px] text-success">Shaded range: historical payouts {valueLabel(historyRange.minimum)}–{valueLabel(historyRange.maximum)}.</p>}
    <p className="mt-2 text-[10px] leading-relaxed text-base-content/50">{fixed ? 'The offered payout is fixed. Curve and minimum rate use backend cost estimates.' : 'Baseline starts at the curve’s initial bid. Price axis fits the analyzed values. Reference prices are advisory.'}</p>
  </figure>
}
