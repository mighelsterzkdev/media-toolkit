import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { initialState, type DesktopApi, type DesktopState } from '../types'

const url = 'https://example.com/media'
function fixture() {
  let state: DesktopState = structuredClone(initialState)
  state.version = '0.7.0'
  state.settings.output = 'C:\\Downloads'
  const success = () => Promise.resolve({ ok: true as const, state: structuredClone(state) })
  const api: DesktopApi = {
    get_state: vi.fn(success),
    check_url: vi.fn(async checked => {
      state.inspection = { status: 'ready', id: 'inspection-1', url: checked, error: null, assets: [{ id: 'asset-1', title: 'A real checked title', description: '1920 Ãƒâ€” 1080', thumbnail: null, default_kind: 'video', kinds: [
        { id: 'video', label: 'Video', options: [{ id: 'original', label: 'Original', description: 'Best available quality', recommended: true }, { id: 'mp4', label: 'MP4', description: 'Convert to MP4', recommended: false }] },
        { id: 'audio', label: 'Audio', options: [{ id: 'original', label: 'Original', description: 'Original audio', recommended: true }, { id: 'mp3', label: 'MP3', description: 'Convert to MP3', recommended: false }] },
      ] }] }
      return success()
    }),
    cancel_check: vi.fn(success), enqueue: vi.fn(success), enqueue_links: vi.fn(success), cancel_task: vi.fn(success), retry_task: vi.fn(success), remove_finished: vi.fn(success),
    save_settings: vi.fn(async settings => { state.settings = settings; return success() }), browse_folder: vi.fn(success), open_folder: vi.fn(success), clear_resume: vi.fn(success), install_tools: vi.fn(success),
  }
  window.pywebview = { api }
  return { api, update: (next: Partial<DesktopState>) => { state = { ...state, ...next } } }
}
async function checkedApp() {
  const mock = fixture()
  const user = userEvent.setup()
  render(<App />)
  await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
  await user.type(screen.getByLabelText('Media URL'), url)
  await user.click(screen.getByRole('button', { name: 'Check' }))
  await screen.findByText('A real checked title')
  return { ...mock, user }
}

describe('desktop flow', () => {
  it('requires a check and an explicit Download action', async () => {
    const { api } = fixture()
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
    expect(screen.queryByRole('radiogroup', { name: 'Download format' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled()
    await user.type(screen.getByLabelText('Media URL'), url)
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await screen.findByText('A real checked title')
    expect(api.enqueue).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledWith('inspection-1', 'asset-1', 'video', 'original'))
  })

  it('supports keyboard radio choices and keeps the choice across polling', async () => {
    const { api, user } = await checkedApp()
    const video = screen.getByRole('radio', { name: 'Video' })
    act(() => video.focus())
    await user.keyboard('{ArrowRight>}')
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Audio' })).toBeChecked())
    await user.keyboard('{/ArrowRight}')
    const mp3 = screen.getByRole('radio', { name: /MP3/ })
    await user.click(mp3)
    await act(() => new Promise(resolve => setTimeout(resolve, 500)))
    expect(mp3).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledWith('inspection-1', 'asset-1', 'audio', 'mp3'))
  })

  it('hides stale media and disables Download immediately when the URL changes', async () => {
    const { api, user } = await checkedApp()
    await user.type(screen.getByLabelText('Media URL'), '/changed')
    expect(screen.queryByText('A real checked title')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled()
    expect(api.enqueue).not.toHaveBeenCalled()
  })

  it('shows checking errors without exposing a download choice', async () => {
    const { api } = fixture()
    api.check_url = vi.fn(async (checked: string) => ({ ok: true as const, state: { ...structuredClone(initialState), settings: { output: 'C:\\Downloads', max_height: null, max_size: null }, inspection: { status: 'failed' as const, id: '', url: checked, error: 'No supported media at this address.', assets: [] } } }))
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
    await user.type(screen.getByLabelText('Media URL'), url)
    await user.click(screen.getByRole('button', { name: 'Check' }))
    expect(await screen.findByText('No supported media at this address.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled()
  })

  it('allows cancellation of a queued task and hides folder actions for unfinished tasks', async () => {
    const { api, update } = fixture()
    update({ tasks: [{ id: 'task-1', url, title: 'Pending item', kind: 'video', format: 'original', status: 'queued', progress: null, downloaded_bytes: 0, total_bytes: null, speed: null, eta: null, path: null, error: null }] })
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Cancel Pending item' }))
    await waitFor(() => expect(api.cancel_task).toHaveBeenCalledWith('task-1'))
    expect(screen.queryByRole('button', { name: /Open folder/ })).not.toBeInTheDocument()
  })

  it('is truthful and disabled when opened outside the desktop host', () => {
    render(<App />)
    expect(screen.getByText(/Waiting for the desktop connection/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled()
    expect(screen.getByText('No downloads yet')).toBeInTheDocument()
    expect(screen.queryByText('A real checked title')).not.toBeInTheDocument()
  })

  it('submits multiple links only after the dialog confirmation', async () => {
    const { api } = fixture()
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add multiple links' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Add multiple links' }))
    await user.type(screen.getByLabelText('Links'), `${url}\nhttps://example.com/second`)
    expect(api.enqueue_links).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Download originals' }))
    await waitFor(() => expect(api.enqueue_links).toHaveBeenCalledWith(`${url}\nhttps://example.com/second`))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('saves explicit limits with MiB converted to bytes', async () => {
    const { api } = fixture()
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Settings' }))
    await user.type(screen.getByLabelText('Max video height'), '1080')
    await user.type(screen.getByLabelText('Max file size'), '12.5')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(api.save_settings).toHaveBeenCalledWith({ output: 'C:\\Downloads', max_height: 1080, max_size: 13107200 }))
  })

  it('reconnects when pywebviewready arrives after the page loads', async () => {
    render(<App />)
    const { api } = fixture()
    fireEvent(window, new Event('pywebviewready'))
    await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
    expect(api.get_state).toHaveBeenCalled()
  })

  it('keeps settings changes local until Save and leaves the folder picker in the main window', async () => {
    const { api } = fixture()
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Settings' }))
    await user.type(screen.getByLabelText('Max video height'), '720')
    expect(within(screen.getByRole('dialog')).queryByRole('button', { name: /Browse/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(api.save_settings).not.toHaveBeenCalled()
    expect(api.browse_folder).not.toHaveBeenCalled()
  })

  it('shows an inline settings failure and preserves the draft', async () => {
    const { api } = fixture()
    api.save_settings = vi.fn(async () => ({ ok: false as const, error: 'Cannot write preferences.' }))
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Settings' }))
    await user.type(screen.getByLabelText('Max video height'), '1080')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    expect(await within(screen.getByRole('dialog')).findByText(/Settings could not be saved/)).toBeInTheDocument()
    expect(screen.getByLabelText('Max video height')).toHaveValue(1080)
  })

  it('shows an inline bulk failure and keeps the links available to correct', async () => {
    const { api } = fixture()
    api.enqueue_links = vi.fn(async () => ({ ok: false as const, error: 'Invalid URL.' }))
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add multiple links' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Add multiple links' }))
    await user.type(screen.getByLabelText('Links'), 'incorrect-url')
    await user.click(screen.getByRole('button', { name: 'Download originals' }))
    expect(await within(screen.getByRole('dialog')).findByText(/These links could not be queued/)).toBeInTheDocument()
    expect(screen.getByLabelText('Links')).toHaveValue('incorrect-url')
  })

  it('explains inspected media with no format fitting the current limits', async () => {
    const { api } = fixture()
    api.check_url = vi.fn(async (checked: string) => ({ ok: true as const, state: { ...structuredClone(initialState), inspection: { status: 'ready' as const, id: 'limited', url: checked, error: null, assets: [{ id: 'asset-limit', title: 'A very large video', description: 'Exceeds the maximum file size', thumbnail: null, default_kind: '', kinds: [] }] } } }))
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
    await user.type(screen.getByLabelText('Media URL'), url)
    await user.click(screen.getByRole('button', { name: 'Check' }))
    expect(await screen.findByText('A very large video')).toBeInTheDocument()
    expect(screen.getByText('No format fits current limits. Change settings and check again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled()
  })

  it('accepts quoted clipboard URLs while retaining the successful inspection', async () => {
    const { api } = fixture()
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
    await user.type(screen.getByLabelText('Media URL'), `"${url}"`)
    await user.click(screen.getByRole('button', { name: 'Check' }))
    expect(await screen.findByText('A real checked title')).toBeInTheDocument()
    expect(api.check_url).toHaveBeenCalledWith(url)
    expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled()
  })

  it.each(['missing', 'installing', 'failed'] as const)('holds new work while tools are %s without blocking queue cancellation', async status => {
    const { update } = fixture()
    update({ tools: { status, ffmpeg: false, javascript: false, error: null }, tasks: [{ id: 'pending', url, title: 'Waited item', kind: null, format: 'original', status: 'queued', progress: null, downloaded_bytes: 0, total_bytes: null, speed: null, eta: null, path: null, error: null }] })
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
    await user.type(screen.getByLabelText('Media URL'), url)
    expect(screen.getByRole('button', { name: 'Check' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Add multiple links' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel Waited item' })).toBeEnabled()
  })
})
