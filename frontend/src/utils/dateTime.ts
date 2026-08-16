const TIMEZONE_SUFFIX = /(?:Z|[+-]\d{2}:?\d{2})$/i

export function serverDate(value: string) {
  // MySQL DATETIME drops timezone metadata. Backend timestamps are UTC, so an
  // ISO value without a suffix must be restored to UTC before local formatting.
  return new Date(TIMEZONE_SUFFIX.test(value) ? value : `${value}Z`)
}
