export const env = {
  apiBaseUrl: import.meta.env.DEV
    ? ''
    : (import.meta.env.VITE_API_BASE_URL?.trim() || '').replace(/\/+$/, ''),
} as const
