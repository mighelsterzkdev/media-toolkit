import { pt, ptPatterns } from './locales/pt'

export type Language = 'system' | 'pt' | 'en'
type Resolved = Exclude<Language, 'system'>

// English text is the key, so untranslated or backend-provided text falls back to itself.
// A 'context::' prefix separates identical English words that translate differently.
let current: Resolved = 'en'

export function resolveLanguage(preference: Language | undefined): Resolved {
  if (preference === 'pt' || preference === 'en') return preference
  return typeof navigator !== 'undefined' && navigator.language?.toLowerCase().startsWith('pt') ? 'pt' : 'en'
}
export function setLanguage(language: Resolved) { current = language }
export function language(): Resolved { return current }
export function locale() { return current === 'pt' ? 'pt-BR' : 'en-US' }
function translate(text: string): string {
  const plain = text.replace(/^\w+::/, '')
  if (current === 'en') return plain
  if (Object.hasOwn(pt, text)) return pt[text]
  if (plain !== text) return translate(plain)
  for (const [pattern, render] of ptPatterns) {
    const match = pattern.exec(text)
    if (match) return render(match.slice(1), translate)
  }
  return text
}
export function t(text: string, values?: Record<string, string | number>) {
  const template = translate(text)
  return values ? template.replace(/\{(\w+)\}/g, (match, key: string) => key in values ? String(values[key]) : match) : template
}
