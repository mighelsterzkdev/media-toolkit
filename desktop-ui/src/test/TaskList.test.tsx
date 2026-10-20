import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TaskList } from '../components/TaskList'
import type { DownloadTask } from '../types'

const converting: DownloadTask = { id: 'convert', url: '', title: 'Clip', kind: 'video', action: 'convert', source_format: 'mov', format: 'mp4', status: 'converting', progress: null, downloaded_bytes: 0, total_bytes: null, speed: null, eta: null, path: null, error: null }
function props(task: DownloadTask) {
  return { tasks: [task], disabled: false, onCancel: vi.fn(), onRetry: vi.fn(), onOpen: vi.fn(), onClear: vi.fn(), onConvert: vi.fn() }
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
