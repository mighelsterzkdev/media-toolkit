import { Button } from './ui/button'
import { resolveLanguage, t, type Language } from '../i18n'

export function LanguageToggle({ language, disabled, onChange }: { language: Language; disabled?: boolean; onChange(language: Language): void }) {
  const current = resolveLanguage(language)
  return <Button variant="ghost" size="icon" className="language-toggle" title={t(current === 'pt' ? 'Language: Portuguese' : 'Language: English')}
    aria-label={t('Change language')} disabled={disabled} onClick={() => onChange(current === 'pt' ? 'en' : 'pt')}>{current.toUpperCase()}</Button>
}
