import { Check, ArrowRightLeft, List, FolderOpen, LoaderCircle, RotateCcw, X, AlertCircle, Clock3 } from 'lucide-react'
import { Button } from './ui/button'
import { Progress } from './ui/progress'
import { bytes, duration, sourceHost } from '../format'
import type { DownloadTask, TaskStatus } from '../types'
import { t } from '../i18n'

const thumbnailPattern = /^data:image\/(?:png|jpeg|webp);base64,[a-z\d+/=\s]+$/i
const completed = new Set<TaskStatus>(['completed', 'existing'])
const active = new Set<TaskStatus>(['queued', 'checking', 'downloading', 'converting', 'finalizing'])
const labels: Record<TaskStatus, string> = {
  queued: 'Queued', checking: 'Checking link', downloading: 'Downloading', converting: 'Converting', finalizing: 'Finalizing',
  completed: 'Completed', existing: 'Already downloaded', failed: 'Failed', cancelled: 'Cancelled', cancelling: 'Cancelling…',
}

interface Props {
  tasks: DownloadTask[]; disabled: boolean; retryDisabled?: boolean;
  onCancel(id: string): void; onRetry(id: string): void; onOpen(id: string): void; onClear(): void; onConvert(id: string): void
}

export function TaskList({ tasks, disabled, retryDisabled, onCancel, onRetry, onOpen, onClear, onConvert }: Props) {
  const activeCount = tasks.filter(task => active.has(task.status) || task.status === 'cancelling').length
  const finishedCount = tasks.length - activeCount
  return <section className="downloads-section" aria-labelledby="downloads-heading">
    <div className="section-heading">
      <div className="flex items-center gap-2"><h2 id="downloads-heading">{t('Queue')}</h2>
        {tasks.length > 0 ? <span className="count-badge">{tasks.length}</span> : null}
      </div>
      {finishedCount > 0 ? <Button variant="ghost" size="sm" onClick={onClear} disabled={disabled}>{t('Clear finished')}</Button> : null}
    </div>
    {tasks.length === 0 ? <div className="empty-downloads">
      <List className="size-5" aria-hidden="true" />
      <div><p>{t('No tasks yet')}</p><span>{t('Downloads and conversions will appear here.')}</span></div>
    </div> : <ul className="task-list" aria-label={t('Task queue')}>
      {tasks.map(task => {
        const done = completed.has(task.status)
        const failed = task.status === 'failed'
        const isActive = active.has(task.status)
        const progress = task.progress == null || !Number.isFinite(task.progress) ? null : Math.max(0, Math.min(100, task.progress))
        const converting = task.status === 'converting'
        const operation = t(task.operation === 'preparing' ? 'Preparing file…' : task.operation === 'copying' ? 'Copying streams…' : 'Encoding file…')
        const label = t(labels[task.status])
        const title = task.title || sourceHost(task.url) || t(task.action === 'convert' ? 'Conversion' : 'Download')
        const thumbnail = task.thumbnail && thumbnailPattern.test(task.thumbnail) ? task.thumbnail : null
        const Icon = done ? Check : failed ? AlertCircle : task.status === 'queued' ? Clock3 : task.status === 'cancelled' ? X : LoaderCircle
        const spinning = isActive && task.status !== 'queued' || task.status === 'cancelling'
        return <li key={task.id} className="task-row" aria-label={`${task.title || sourceHost(task.url)}: ${label}`}>
          {thumbnail ? <div className={`task-icon task-thumbnail ${done ? 'task-icon-done' : ''} ${failed ? 'task-icon-failed' : ''}`}>
            <img src={thumbnail} alt="" />
            <span className="task-status-badge"><Icon className={`size-3 ${spinning ? 'animate-spin' : ''}`} aria-hidden="true" /></span>
          </div> : <div className={`task-icon ${done ? 'task-icon-done' : ''} ${failed ? 'task-icon-failed' : ''}`}>
            <Icon className={`size-[18px] ${spinning ? 'animate-spin' : ''}`} aria-hidden="true" />
          </div>}
          <div className="task-content">
            <p className="task-title" title={task.title || task.url}>{title}</p>
            <div className="task-meta"><span className={failed ? 'text-danger' : ''}>{label}</span>
              <span className="dot">·</span><span>{t(task.action === 'convert' ? 'task::Conversion' : 'task::Download')}</span>
              {task.format ? <><span className="dot">·</span><span>{task.action === 'convert' && task.source_format ? `${task.source_format.toUpperCase()} → ` : ''}{task.format === 'original' ? t('Original') : task.format.toUpperCase()}</span></> : null}
              {!done && task.downloaded_bytes > 0 ? <><span className="dot">·</span><span>{bytes(task.downloaded_bytes)}{task.total_bytes ? ` / ${bytes(task.total_bytes)}` : ''}</span></> : null}
              {task.speed && task.status === 'downloading' ? <><span className="dot">·</span><span>{bytes(task.speed)}/s</span></> : null}
              {converting && progress != null ? <><span className="dot">·</span><span>{Math.floor(progress)}%</span></> : null}
              {task.eta != null && (task.status === 'downloading' || converting && progress != null) ? <span className="task-eta">{duration(task.eta)}</span> : null}
            </div>
            {task.status === 'downloading' ? <Progress value={progress} aria-label={t('Download progress for {title}', { title: task.title })} className={progress == null ? 'progress-indeterminate' : ''} /> : null}
            {converting && progress != null ? <Progress value={progress} aria-label={t('Conversion progress for {title}', { title: task.title })} /> : null}
            {converting ? <p className="task-operation" role="status">{operation}</p> : null}
            {task.error ? <p className="task-error">{t(task.error)}</p> : null}
          </div>
          <div className="task-actions">
            {done && task.can_convert ? <Button variant="ghost" size="icon-sm" title={t('Convert file')} aria-label={t('Convert file {title}', { title: task.title })} disabled={disabled} onClick={() => onConvert(task.id)}><ArrowRightLeft /></Button> : null}
            {done && task.path ? <Button variant="ghost" size="icon-sm" title={t('Open containing folder')} aria-label={t('Open folder for {title}', { title: task.title })} disabled={disabled} onClick={() => onOpen(task.id)}><FolderOpen /></Button> : null}
            {failed || task.status === 'cancelled' ? <Button variant="ghost" size="icon-sm" title={t('Retry task')} aria-label={t('Retry {title}', { title: task.title })} disabled={disabled || (retryDisabled && task.action !== 'convert')} onClick={() => onRetry(task.id)}><RotateCcw /></Button> : null}
            {isActive ? <Button variant="ghost" size="icon-sm" title={t('Cancel task')} aria-label={t('Cancel {title}', { title: task.title || t('task') })} disabled={disabled} onClick={() => onCancel(task.id)}><X /></Button> : null}
          </div>
        </li>
      })}
    </ul>}
  </section>
}
