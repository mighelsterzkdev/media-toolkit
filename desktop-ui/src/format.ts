export function bytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return '—'
  if (value < 1024) return `${Math.round(value)} B`
  const power = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4)
  return `${(value / 1024 ** power).toFixed(1)} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][power]}`
}
export function duration(value: number | null): string {
  if (value == null || !Number.isFinite(value) || value < 0) return ''
  if (value < 60) return `${Math.ceil(value)}s left`
  return `${Math.floor(value / 60)}m ${Math.ceil(value % 60)}s left`
}
export function sourceHost(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return '' }
}
export function normalizeUrlInput(value: string): string {
  const url = value.trim()
  if (url.length >= 2 && (url[0] === '"' || url[0] === "'") && url.at(-1) === url[0]) return url.slice(1, -1).trim()
  return url
}
