export function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export function shortId(value: string) {
  return value.length > 12 ? `${value.slice(0, 8)}...` : value
}
