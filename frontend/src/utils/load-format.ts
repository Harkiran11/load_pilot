const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })

export function amount(value: string | null) {
  return value === null || !Number.isFinite(Number(value)) ? '—' : currency.format(Number(value))
}

export function quantity(value: string | number | null, unit: string) {
  return value === null || !Number.isFinite(Number(value)) ? '—' : `${number.format(Number(value))} ${unit}`
}

export function timestamp(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

export function label(value: string) {
  return value.replaceAll('_', ' ')
}
