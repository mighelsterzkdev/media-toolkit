import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TaskList } from '../components/TaskList'
import type { DownloadTask } from '../types'

const converting: DownloadTask = { id: 'convert', url: '', title: 'Clip', kind: 'video', action: 'convert', source_format: 'mov', format: 'mp4', status: 'converting', progress: null, downloaded_bytes: 0, total_bytes: null, speed: null, eta: null, path: null, error: null }
function props(task: DownloadTask) {
  return { tasks: [task], disabled: false, onCancel: vi.fn(), onRetry: vi.fn(), onRetryFailed: vi.fn(), onRemove: vi.fn(), onOpen: vi.fn(), onClear: vi.fn(), onConvert: vi.fn() }
}

describe('queue conversion feedback', () => {
  it('shows measured conversion progress and time remaining', () => {
    render(<TaskList {...props({ ...converting, progress: 42.8, eta: 83, operation: 'encoding' })} />)
    expect(screen.getByText('42%')).toBeInTheDocument()
    expect(screen.getByText('1m 23s left')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Conversion progress for Clip' })).toHaveAttribute('aria-valuenow', '42.8')
    expect(screen.getByText('Encoding file…')).toBeInTheDocument()
  })

  it('does not invent progress or an ETA before measurements arrive', () => {
    render(<TaskList {...props({ ...converting, eta: 90, operation: 'preparing' })} />)
    expect(screen.getByText('Preparing file…')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText(/left$/)).not.toBeInTheDocument()
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument()
  })

  it('describes a measured stream copy without calling it encoding', () => {
    render(<TaskList {...props({ ...converting, progress: 99.9, eta: 0, operation: 'copying' })} />)
    expect(screen.getByText('Copying streams…')).toBeInTheDocument()
    expect(screen.queryByText('Encoding file…')).not.toBeInTheDocument()
    expect(screen.getByText('99%')).toBeInTheDocument()
  })

  it('rejects non-finite progress values from rendering a percentage', () => {
    render(<TaskList {...props({ ...converting, progress: Number.NaN })} />)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
  })

  it('lets a restored interrupted local task retry through the existing queue action', async () => {
    const callbacks = props({ ...converting, status: 'cancelled', error: 'Interrupted when the app closed. Retry to continue.' })
    render(<TaskList {...callbacks} retryDisabled />)
    expect(screen.getByText('Interrupted when the app closed. Retry to continue.')).toBeInTheDocument()
    const retry = screen.getByRole('button', { name: 'Retry Clip' })
    expect(retry).toBeEnabled()
    await userEvent.click(retry)
    expect(callbacks.onRetry).toHaveBeenCalledWith('convert')
  })
})

const reviewQueue: DownloadTask[] = [
  { ...converting, id: 'encoding', title: 'Holiday clip', status: 'converting' },
  { ...converting, id: 'stopping', title: 'Stopping task', status: 'cancelling' },
  { ...converting, id: 'failed-download', action: 'download', title: 'Ocean recording', url: 'https://media.example.com/ocean', format: 'original', source_format: null, status: 'failed' },
  { ...converting, id: 'failed-conversion', title: 'Portrait export', format: 'webp', source_format: 'png', status: 'failed' },
  { ...converting, id: 'done', title: 'Saved music', format: 'mp3', status: 'completed', path: 'C:\\Downloads\\music.mp3' },
  { ...converting, id: 'existing', title: 'Cached video', status: 'existing', path: 'C:\\Downloads\\video.mp4' },
  { ...converting, id: 'cancelled', title: 'Cancelled export', status: 'cancelled' },
]
function reviewProps() { return { ...props(converting), tasks: structuredClone(reviewQueue) } }
function filterButton(name: string) { return within(screen.getByRole('group', { name: 'Filter queue' })).getByRole('button', { name }) }
function taskNames() {
  const list = screen.queryByRole('list', { name: 'Task queue' })
  return list ? within(list).getAllByRole('listitem').map(item => item.getAttribute('aria-label')) : []
}

describe('queue review and bulk actions', () => {
  it('filters active, failed, completed and cancelled work without losing the full queue count', async () => {
    const user = userEvent.setup()
    render(<TaskList {...reviewProps()} />)
    expect(taskNames()).toHaveLength(7)

    await user.click(filterButton('In progress'))
    expect(taskNames()).toEqual(['Holiday clip: Converting', 'Stopping task: Cancelling…'])
    expect(filterButton('In progress')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('2 of 7 tasks')).toBeInTheDocument()

    await user.click(filterButton('Failed'))
    expect(taskNames()).toEqual(['Ocean recording: Failed', 'Portrait export: Failed'])

    await user.click(filterButton('Completed'))
    expect(taskNames()).toEqual(['Saved music: Completed', 'Cached video: Already downloaded'])

    await user.click(filterButton('Cancelled'))
    expect(taskNames()).toEqual(['Cancelled export: Cancelled'])
    await user.click(filterButton('All tasks'))
    expect(taskNames()).toHaveLength(7)
  })

  it('combines the selected status with case-insensitive searches by title, URL and format', async () => {
    const user = userEvent.setup()
    render(<TaskList {...reviewProps()} />)
    const search = screen.getByRole('searchbox', { name: 'Search queue' })
    await user.click(filterButton('Failed'))
    await user.type(search, '  PORTRAIT  ')
    expect(taskNames()).toEqual(['Portrait export: Failed'])
    await user.clear(search)
    await user.type(search, 'media.example.com')
    expect(taskNames()).toEqual(['Ocean recording: Failed'])
    await user.clear(search)
    await user.type(search, 'webp')
    expect(taskNames()).toEqual(['Portrait export: Failed'])
    await user.clear(search)
    await user.type(search, 'png')
    expect(taskNames()).toEqual(['Portrait export: Failed'])
    expect(screen.getByText('1 of 7 tasks')).toBeInTheDocument()
  })

  it('recovers from an empty search by clearing both the query and status filter', async () => {
    const user = userEvent.setup()
    render(<TaskList {...reviewProps()} />)
    await user.click(filterButton('Failed'))
    await user.type(screen.getByRole('searchbox', { name: 'Search queue' }), 'missing task')
    expect(screen.getByText('No tasks match this filter.')).toBeInTheDocument()
    expect(taskNames()).toEqual([])
    await user.click(screen.getByRole('button', { name: 'Show all tasks' }))
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(filterButton('All tasks')).toHaveAttribute('aria-pressed', 'true')
    expect(taskNames()).toHaveLength(7)
  })

  it('retries failures in one explicit action and keeps per-item retry for cancelled work', async () => {
    const user = userEvent.setup()
    const callbacks = reviewProps()
    render(<TaskList {...callbacks} />)
    expect(callbacks.onRetryFailed).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Retry failed (2)' }))
    expect(callbacks.onRetryFailed).toHaveBeenCalledOnce()
    expect(callbacks.onRetry).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Retry Cancelled export' }))
    expect(callbacks.onRetry).toHaveBeenCalledWith('cancelled')
  })

  it('keeps offline conversion retries available while download tools are missing', async () => {
    const user = userEvent.setup()
    const callbacks = reviewProps()
    const { rerender } = render(<TaskList {...callbacks} retryDisabled />)
    expect(screen.getByRole('button', { name: 'Retry Ocean recording' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Retry Portrait export' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Retry failed (1)' }))
    expect(callbacks.onRetryFailed).toHaveBeenCalledOnce()

    rerender(<TaskList {...callbacks} tasks={[reviewQueue[2]]} retryDisabled />)
    expect(screen.queryByRole('button', { name: /Retry failed/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry Ocean recording' })).toBeDisabled()
  })

  it('offers clearing only when successful items exist and disables mutations while busy', async () => {
    const user = userEvent.setup()
    const callbacks = reviewProps()
    const { rerender } = render(<TaskList {...callbacks} />)
    await user.click(screen.getByRole('button', { name: 'Clear finished' }))
    expect(callbacks.onClear).toHaveBeenCalledOnce()
    rerender(<TaskList {...callbacks} disabled />)
    expect(screen.getByRole('button', { name: 'Clear finished' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Retry failed (2)' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Retry Portrait export' })).toBeDisabled()
    rerender(<TaskList {...callbacks} tasks={reviewQueue.filter(task => ['failed', 'cancelled'].includes(task.status))} />)
    expect(screen.queryByRole('button', { name: 'Clear finished' })).not.toBeInTheDocument()
  })

  it('lets users explicitly forget terminal records without offering removal for active jobs', async () => {
    const user = userEvent.setup()
    const callbacks = reviewProps()
    const { rerender } = render(<TaskList {...callbacks} />)
    expect(screen.queryByRole('button', { name: 'Remove Holiday clip from queue' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove Stopping task from queue' })).not.toBeInTheDocument()
    for (const [title, id] of [['Ocean recording', 'failed-download'], ['Cancelled export', 'cancelled'], ['Saved music', 'done'], ['Cached video', 'existing']]) {
      const remove = screen.getByRole('button', { name: `Remove ${title} from queue` })
      expect(remove).toHaveAttribute('title', 'Remove from queue. Saved files are kept.')
      await user.click(remove)
      expect(callbacks.onRemove).toHaveBeenLastCalledWith(id)
    }
    expect(callbacks.onCancel).not.toHaveBeenCalled()
    expect(callbacks.onClear).not.toHaveBeenCalled()
    rerender(<TaskList {...callbacks} disabled />)
    expect(screen.getByRole('button', { name: 'Remove Ocean recording from queue' })).toBeDisabled()
  })

  it('updates the current view when polling changes task states without resetting the filter', async () => {
    const user = userEvent.setup()
    const callbacks = reviewProps()
    const { rerender } = render(<TaskList {...callbacks} />)
    await user.click(filterButton('Failed'))
    await user.type(screen.getByRole('searchbox'), 'export')
    expect(taskNames()).toEqual(['Portrait export: Failed'])
    const next = callbacks.tasks.map(task => task.id === 'failed-conversion' ? { ...task, status: 'queued' as const } : task)
    rerender(<TaskList {...callbacks} tasks={next} />)
    expect(filterButton('Failed')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('searchbox')).toHaveValue('export')
    expect(screen.getByText('0 of 7 tasks')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry failed (1)' })).toBeInTheDocument()
    expect(taskNames()).toEqual([])
  })
})
