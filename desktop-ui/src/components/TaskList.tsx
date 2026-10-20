import { Check, ArrowRightLeft, List, FolderOpen, LoaderCircle, RotateCcw, X, AlertCircle, Clock3 } from 'lucide-react'
import { Button } from './ui/button'
import { Progress } from './ui/progress'
import { bytes, duration, sourceHost } from '../format'
import type { DownloadTask, TaskStatus } from '../types'

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
      <div className="flex items-center gap-2"><h2 id="downloads-heading">Queue</h2>
        {tasks.length > 0 ? <span className="count-badge">{tasks.length}</span> : null}
      </div>
      {finishedCount > 0 ? <Button variant="ghost" size="sm" onClick={onClear} disabled={disabled}>Clear finished</Button> : null}
    </div>
    {tasks.length === 0 ? <div className="empty-downloads">
      <List className="size-5" aria-hidden="true" />
      <div><p>No tasks yet</p><span>Downloads and conversions will appear here.</span></div>
    </div> : <ul className="task-list" aria-label="Task queue">
      {tasks.map(task => {
        const done = completed.has(task.status)
        const failed = task.status === 'failed'
        const isActive = active.has(task.status)
        const progress = task.progress == null ? null : Math.max(0, Math.min(100, task.progress))
        const Icon = done ? Check : failed ? AlertCircle : task.status === 'queued' ? Clock3 : task.status === 'cancelled' ? X : LoaderCircle
        return <li key={task.id} className="task-row" aria-label={`${task.title || sourceHost(task.url)}: ${labels[task.status]}`}>
          <div className={`task-icon ${done ? 'task-icon-done' : ''} ${failed ? 'task-icon-failed' : ''}`}>
            <Icon className={`size-[18px] ${isActive && task.status !== 'queued' || task.status === 'cancelling' ? 'animate-spin' : ''}`} aria-hidden="true" />
          </div>
          <div className="task-content">
            <p className="task-title" title={task.title || task.url}>{task.title || sourceHost(task.url) || (task.action === 'convert' ? 'Conversion' : 'Download')}</p>
            <div className="task-meta"><span className={failed ? 'text-red-300' : ''}>{labels[task.status]}</span>
              <span className="dot">·</span><span>{task.action === 'convert' ? 'Convert' : 'Download'}</span>
              {task.format ? <><span className="dot">·</span><span>{task.action === 'convert' && task.source_format ? `${task.source_format.toUpperCase()} → ` : ''}{task.format === 'original' ? 'Original' : task.format.toUpperCase()}</span></> : null}
              {!done && task.downloaded_bytes > 0 ? <><span className="dot">·</span><span>{bytes(task.downloaded_bytes)}{task.total_bytes ? ` / ${bytes(task.total_bytes)}` : ''}</span></> : null}
              {task.speed && task.status === 'downloading' ? <><span className="dot">·</span><span>{bytes(task.speed)}/s</span></> : null}
              {task.eta != null && task.status === 'downloading' ? <span className="task-eta">{duration(task.eta)}</span> : null}
            </div>
            {task.status === 'downloading' ? <Progress value={progress} aria-label={`Download progress for ${task.title}`} className={progress == null ? 'progress-indeterminate' : ''} /> : null}
            {task.status === 'converting' ? <p className="task-operation" role="status">Encoding file…</p> : null}
            {task.error ? <p className="task-error">{task.error}</p> : null}
          </div>
          <div className="task-actions">
            {done && task.can_convert ? <Button variant="ghost" size="icon" title="Convert file" aria-label={`Convert file ${task.title}`} disabled={disabled} onClick={() => onConvert(task.id)}><ArrowRightLeft /></Button> : null}
            {done && task.path ? <Button variant="ghost" size="icon" title="Open containing folder" aria-label={`Open folder for ${task.title}`} disabled={disabled} onClick={() => onOpen(task.id)}><FolderOpen /></Button> : null}
            {failed || task.status === 'cancelled' ? <Button variant="ghost" size="icon" title="Retry task" aria-label={`Retry ${task.title}`} disabled={disabled || (retryDisabled && task.action !== 'convert')} onClick={() => onRetry(task.id)}><RotateCcw /></Button> : null}
            {isActive ? <Button variant="ghost" size="icon" title="Cancel task" aria-label={`Cancel ${task.title || 'task'}`} disabled={disabled} onClick={() => onCancel(task.id)}><X /></Button> : null}
          </div>
        </li>
      })}
    </ul>}
  </section>
}
