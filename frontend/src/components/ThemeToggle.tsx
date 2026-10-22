import { useEffect, useState } from 'react'

type Theme = 'light' | 'dark'

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  function toggleTheme() {
    const nextTheme = theme === 'light' ? 'dark' : 'light'
    setTheme(nextTheme)
    try {
      localStorage.setItem('loadpilot-theme', nextTheme)
    } catch {
      // Switching still works when browser storage is unavailable.
    }
  }

  return (
    <button
      type="button"
      className="btn btn-outline btn-primary btn-sm shrink-0 gap-2 max-sm:btn-square"
      onClick={toggleTheme}
      aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
      title={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
    >
      {theme === 'light' ? (
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className="size-4">
          <path strokeLinecap="round" strokeLinejoin="round" d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z" />
        </svg>
      ) : (
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className="size-4">
          <circle cx="12" cy="12" r="4" />
          <path strokeLinecap="round" d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
        </svg>
      )}
      <span className="hidden sm:inline">{theme === 'light' ? 'Dark mode' : 'Light mode'}</span>
    </button>
  )
}
