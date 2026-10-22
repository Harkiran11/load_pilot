export function isValidBidAmount(value: string): boolean {
  // Match PositiveBid: 18 total digits and at most two decimal places.
  return /^\d+(?:\.\d{1,2})?$/.test(value)
    && value.split('.')[0].replace(/^0+/, '').length <= 16
    && value.replace('.', '').replace(/^0+/, '').length <= 18
    && Number(value) > 0
}
