import { useEffect } from 'react'
import { Monitor, Moon, Sun } from 'lucide-react'
import { Button } from './ui/button'
import type { Theme } from '../types'
import { t } from '../i18n'

const next: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }
const labels: Record<Theme, string> = { system: 'Theme: match Windows', light: 'Theme: light', dark: 'Theme: dark' }
const icons = { system: Monitor, light: Sun, dark: Moon }

export function useThemeClass(theme: Theme) {
  useEffect(() => {
    const media = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null
    const apply = () => {
      const dark = theme === 'dark' || (theme === 'system' && (media?.matches ?? true))
      document.documentElement.classList.toggle('dark', dark)
      document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
    }
    apply()
    media?.addEventListener('change', apply)
    return () => media?.removeEventListener('change', apply)
  }, [theme])
}

export function ThemeToggle({ theme, disabled, onChange }: { theme: Theme; disabled?: boolean; onChange(theme: Theme): void }) {
  const Icon = icons[theme]
  return <Button variant="ghost" size="icon" title={t(labels[theme])} aria-label={t('{theme}. Change theme', { theme: t(labels[theme]) })} disabled={disabled} onClick={() => onChange(next[theme])}><Icon /></Button>
}
