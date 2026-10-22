import { env } from '../config/env'

const apiUrl = env.apiBaseUrl

export async function apiFetch(
  path: string,
  options: RequestInit = {},
) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  new Headers(options.headers).forEach((value, name) => {
    headers.set(name, value)
  })

  return fetch(`${apiUrl}${path}`, {
    ...options,
    headers,
  })
}

export function withQuery(path: string, params: object = {}): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) query.set(key, String(value))
  }
  return query.size ? `${path}?${query}` : path
}
