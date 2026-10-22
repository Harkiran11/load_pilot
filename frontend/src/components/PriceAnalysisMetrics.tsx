import { priceAnalysisMetricGroups, type PriceAnalysisReport, type PriceMetricGroup } from '../utils/price-analysis'
import { amount, quantity } from '../utils/load-format'

const tones: Record<string, string> = {
  'fill-primary': 'text-primary', 'fill-success': 'text-success', 'fill-error': 'text-error',
  'fill-info': 'text-info', 'fill-warning': 'text-warning',
}
const shortLabels: Record<string, string> = {
  'Estimated gross profit': 'Gross profit', 'Estimated margin': 'Margin',
  'Deviation from advice': 'Deviation', 'Your bid / mile': 'Your bid', 'Advisory / mile': 'Advisory',
  'Estimated total cost': 'Total cost', 'Configured minimum': 'Minimum', 'Offer / mile': 'Offer', 'Cost / mile': 'Cost',
}

function MetricChart({ group }: { group: PriceMetricGroup }) {
  const values = group.metrics.flatMap((metric) => metric.value === null ? [] : [metric.value])
  const minimum = Math.min(0, ...values)
  const maximum = Math.max(0, ...values) || (minimum === 0 ? 1 : 0)
  const y = (value: number) => 100 - (value - minimum) / (maximum - minimum) * 100
  const zero = y(0)
  const valueLabel = (value: number | null, signed = false) => {
    if (value === null) return 'Unavailable'
    const formatted = group.unit === 'USD' ? amount(String(value)) : quantity(value, group.unit)
    return signed && value > 0 ? `+${formatted}` : formatted
  }

  return <figure className="min-w-0 rounded-lg border border-base-300 bg-base-100 p-2.5">
    <figcaption className="mb-2 flex min-h-8 items-start justify-between gap-2 text-[10px] font-semibold text-base-content/55">{group.title}<span className="shrink-0 font-normal">{group.unit}</span></figcaption>
    <p className="sr-only">Bars share a scale in {group.unit}. Positive values rise above zero; negative values extend below zero.</p>
    <div className="grid grid-cols-2 gap-2">
      {group.metrics.map((metric) => <div key={metric.label} className="flex min-w-0 flex-col items-center gap-1">
        <span className="block w-full truncate text-center text-[10px] font-medium tabular-nums" title={valueLabel(metric.value, metric.signed)}>{valueLabel(metric.value, metric.signed)}</span>
        <svg viewBox="0 0 32 100" preserveAspectRatio="none" aria-hidden="true" className={`block h-24 w-8 overflow-visible ${tones[metric.color] ?? 'text-primary'}`}>
          <rect x={8} y={0} width={16} height={100} rx={3} className="fill-base-200" />
          {metric.value !== null && <>
            <line x1={0} x2={32} y1={zero} y2={zero} className="stroke-base-content/30" strokeWidth={1} vectorEffect="non-scaling-stroke" />
            {metric.value === 0
              ? <line x1={8} x2={24} y1={zero} y2={zero} stroke="currentColor" strokeWidth={3} vectorEffect="non-scaling-stroke" />
              : <rect x={8} y={Math.min(zero, y(metric.value))} width={16} height={Math.abs(y(metric.value) - zero)} rx={3} fill="currentColor" />}
          </>}
        </svg>
        <span className="block w-full truncate text-center text-[10px] text-base-content/65" title={metric.label}>{shortLabels[metric.label] ?? metric.label}</span>
      </div>)}
    </div>
  </figure>
}

export default function PriceAnalysisMetrics({ result }: { result: PriceAnalysisReport }) {
  return <section aria-label="Price analysis metric charts" className="@container">
    <div className="grid grid-cols-1 gap-2 @[400px]:grid-cols-3">
      {priceAnalysisMetricGroups(result).map((group) => <MetricChart key={group.unit} group={group} />)}
    </div>
  </section>
}
