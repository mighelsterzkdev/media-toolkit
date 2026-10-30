import { CircleAlert, Download, ExternalLink, LoaderCircle, RefreshCw, Sparkles, X } from 'lucide-react'
import { Button } from './ui/button'
import { t } from '../i18n'
import type { UpdateState } from '../types'

interface Props {
  update: UpdateState | null | undefined; name: string; busy: boolean; pending: boolean
  onDownload(): void; onInstall(): void; onDismiss(): void; onOpenPage(): void
}

// One quiet row under the title bar; nothing downloads or restarts without a click.
export function showsUpdate(update: UpdateState | null | undefined) {
  return Boolean(update?.version && ['available', 'downloading', 'ready', 'failed'].includes(update.status))
}

export function UpdateNotice({ update, name, busy, pending, onDownload, onInstall, onDismiss, onOpenPage }: Props) {
  if (!update?.version || !showsUpdate(update)) return null
  const values = { name, version: update.version }
  const dismiss = <Button variant="ghost" size="icon" aria-label={t('Not now')} title={t('Not now')} onClick={onDismiss}><X /></Button>
  if (update.status === 'downloading') {
    const percent = Math.round((update.progress ?? 0) * 100)
    return <div className="notice update-notice" role="status"><LoaderCircle className="size-4 shrink-0 animate-spin text-muted-foreground" aria-hidden="true" />
      <span>{t('Downloading {name} {version}…', values)}</span>
      <span className="update-percent tabular-nums">{percent}%</span></div>
  }
  if (update.status === 'ready') {
    return <div className="notice update-notice" role="status"><Sparkles className="size-4 shrink-0 text-success" aria-hidden="true" />
      <span>{busy ? t('Finish the current work to install {name} {version}.', values) : t('{name} {version} is ready to install.', values)}</span>
      <Button size="sm" disabled={busy || pending} onClick={onInstall}>{pending ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}{t('Restart to update')}</Button></div>
  }
  if (update.status === 'failed') {
    return <div className="notice update-notice notice-error" role="alert"><CircleAlert className="size-4 shrink-0" aria-hidden="true" />
      <span>{t(update.error ?? 'The update could not be downloaded. Try again.')}</span>
      <Button variant="outline" size="sm" onClick={onOpenPage}><ExternalLink />{t('Release page')}</Button>
      <Button variant="outline" size="sm" disabled={pending} onClick={onDownload}>{t('Try again')}</Button>{dismiss}</div>
  }
  return <div className="notice update-notice" role="status"><Sparkles className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    <span>{t('{name} {version} is available.', values)}</span>
    <Button variant="ghost" size="sm" onClick={onOpenPage}><ExternalLink />{t("What's new")}</Button>
    <Button size="sm" disabled={pending} onClick={onDownload}>{pending ? <LoaderCircle className="animate-spin" /> : <Download />}{t('Update')}</Button>{dismiss}</div>
}
