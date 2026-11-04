import { CircleAlert, Download, ExternalLink, LoaderCircle, RefreshCw, Sparkles, X } from 'lucide-react'
import { Button } from './ui/button'
import { t } from '../i18n'
import type { UpdateState } from '../types'

interface Props {
  update: UpdateState | null | undefined; name: string; busy: boolean; pending: boolean
  onDownload(): void; onInstall(): void; onDismiss(): void; onOpenPage(): void
}

// One quiet card under the title bar; nothing downloads or restarts without a click.
export function showsUpdate(update: UpdateState | null | undefined) {
  return Boolean(update?.version && ['available', 'downloading', 'ready', 'failed'].includes(update.status))
}

export function UpdateNotice({ update, name, busy, pending, onDownload, onInstall, onDismiss, onOpenPage }: Props) {
  if (!update?.version || !showsUpdate(update)) return null
  const values = { name, version: update.version }
  const dismiss = <Button variant="ghost" size="icon" aria-label={t('Not now')} title={t('Not now')} onClick={onDismiss}><X /></Button>
  if (update.status === 'downloading') {
    const percent = Math.round((update.progress ?? 0) * 100)
    return <div className="notice update-notice" role="status"><span className="update-icon"><LoaderCircle className="animate-spin" aria-hidden="true" /></span>
      <span className="update-copy"><span>{t('Downloading {name} {version}…', values)}</span>
        <span className="update-progress" aria-hidden="true"><span style={{ width: `${percent}%` }} /></span></span>
      <span className="update-percent tabular-nums">{percent}%</span></div>
  }
  if (update.status === 'ready') {
    return <div className="notice update-notice" role="status"><span className="update-icon update-icon-ready"><Sparkles aria-hidden="true" /></span>
      <span className="update-copy"><span>{busy ? t('Finish the current work to install {name} {version}.', values) : t('{name} {version} installs when you close the app.', values)}</span>
        <span>{t('Or restart now to use it right away.')}</span></span>
      <Button size="sm" disabled={busy || pending} onClick={onInstall}>{pending ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}{t('Restart to update')}</Button></div>
  }
  if (update.status === 'failed') {
    return <div className="notice update-notice notice-error" role="alert"><span className="update-icon"><CircleAlert aria-hidden="true" /></span>
      <span>{t(update.error ?? 'The update could not be downloaded. Try again.')}</span>
      <Button variant="outline" size="sm" onClick={onOpenPage}><ExternalLink />{t('Release page')}</Button>
      <Button variant="outline" size="sm" disabled={pending} onClick={onDownload}>{t('Try again')}</Button>{dismiss}</div>
  }
  return <div className="notice update-notice" role="status"><span className="update-icon"><Sparkles aria-hidden="true" /></span>
    <span className="update-copy"><span>{t('{name} {version} is available.', values)}</span>
      <span>{t('It downloads in the background and installs when you close the app.')}</span></span>
    <Button variant="ghost" size="sm" onClick={onOpenPage}><ExternalLink />{t("What's new")}</Button>
    <Button size="sm" disabled={pending} onClick={onDownload}>{pending ? <LoaderCircle className="animate-spin" /> : <Download />}{t('Update')}</Button>{dismiss}</div>
}
