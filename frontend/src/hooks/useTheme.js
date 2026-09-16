import { useCallback, useEffect, useState } from 'react'

const KEY = 'voiceops-theme'

/** Theme is 'light', 'dark' or 'system'; only an explicit choice is stamped. */
export function useTheme() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem(KEY) || 'system'
    } catch {
      return 'system'
    }
  })

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      if (theme === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, theme)
    } catch {
      // Storage can be unavailable (private mode); the theme still applies.
    }
  }, [theme])

  const cycle = useCallback(() => {
    setTheme((current) => (current === 'light' ? 'dark' : current === 'dark' ? 'system' : 'light'))
  }, [])

  return { theme, setTheme, cycle }
}
