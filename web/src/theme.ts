// Viewer preferences kept per browser (localStorage), never in the URL: the theme and the basemap.
// Storage can be missing or blocked (private windows); reads then fall back to the defaults.
import { createContext, useContext } from 'react'
import type { Ground } from './symbols'

export type Theme = Ground

/** Also read by the inline script in index.html, so the saved theme applies before the first paint. */
export const THEME_KEY = 'eww.theme'
export const BASEMAP_KEY = 'eww.basemap'

export function readPreference(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function savePreference(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Not saved: the choice still holds until the page is closed.
  }
}

export const readTheme = (): Theme => (readPreference(THEME_KEY) === 'light' ? 'light' : 'dark')

/** The page theme; symbols drawn on the page's surfaces (legend, list, panel) read it. */
export const ThemeContext = createContext<Theme>('dark')
export const useTheme = () => useContext(ThemeContext)
