import { isValidBidAmount } from './bid-amount'

function cents(value: string): bigint | null {
  if (!/^\d+(?:\.\d{1,2})?$/.test(value)) return null
  const [whole, fraction = ''] = value.split('.')
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
}

function money(value: bigint): string {
  return `${value / 100n}.${(value % 100n).toString().padStart(2, '0')}`
}

export function normalizeBid(value: string): string | null {
  const trimmed = value.trim()
  if (!isValidBidAmount(trimmed)) return null
  const amount = cents(trimmed)
  return amount === null ? null : money(amount)
}

export function validMargin(value: string): boolean {
  return /^\d{1,2}(?:\.\d{1,2})?$/.test(value) && Number(value) >= 0 && Number(value) < 100
}

export function marginForBid(totalCost: string | null, bidValue: string): string | null {
  const normalized = normalizeBid(bidValue)
  if (totalCost === null || normalized === null) return null
  const cost = cents(totalCost)
  const bid = cents(normalized)
  if (cost === null || bid === null) return null
  const difference = bid - cost
  const magnitude = difference < 0n ? -difference : difference
  const rounded = (magnitude * 10_000n + bid / 2n) / bid
  return `${difference < 0n && rounded > 0n ? '-' : ''}${money(rounded)}`
}

// Revenue-based margin: bid = cost / (1 - margin / 100), rounded UP to cents.
export function bidForMargin(totalCost: string | null, targetMargin: string): string | null {
  if (totalCost === null || !validMargin(targetMargin)) return null
  const cost = cents(totalCost)
  if (cost === null) return null
  const [whole, fraction = ''] = targetMargin.split('.')
  const basisPoints = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
  const denominator = 10_000n - basisPoints
  const calculated = (cost * 10_000n + denominator - 1n) / denominator
  const bid = money(calculated > 0n ? calculated : 1n)
  return isValidBidAmount(bid) ? bid : null
}
