import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { initialState, type DesktopApi, type DesktopState, type LocalImage } from '../types'

const url = 'https://example.com/media'
function fixture() {
  let state: DesktopState = structuredClone(initialState)
  state.version = '0.8.0'
  state.settings.output = 'C:\\Downloads'
  const success = () => Promise.resolve({ ok: true as const, state: structuredClone(state) })
  const api: DesktopApi = {
    get_state: vi.fn(success),
    check_url: vi.fn(async checked => {
      state.inspection = { status: 'ready', id: 'inspection-1', url: checked, error: null, assets: [{ id: 'asset-1', title: 'A real checked title', description: '1920 × 1080', thumbnail: null, default_kind: 'video', kinds: [
        { id: 'video', label: 'Video', options: [{ id: 'original', label: 'Original', description: 'Best available quality', recommended: true }, { id: 'mp4', label: 'MP4', description: 'Convert to MP4', recommended: false }] },
        { id: 'audio', label: 'Audio', options: [{ id: 'original', label: 'Original', description: 'Original audio', recommended: true }, { id: 'mp3', label: 'MP3', description: 'Convert to MP3', recommended: false }] },
      ] }] }
      return success()
    }),
    cancel_check: vi.fn(success), enqueue: vi.fn(success), enqueue_links: vi.fn(success), cancel_task: vi.fn(success), retry_task: vi.fn(success), remove_finished: vi.fn(success),
    save_settings: vi.fn(async settings => { state.settings = settings; return success() }), browse_folder: vi.fn(success), open_folder: vi.fn(success), clear_resume: vi.fn(success), install_tools: vi.fn(success),
    select_conversion_files: vi.fn(success), remove_conversion_file: vi.fn(success), clear_conversion_files: vi.fn(success), enqueue_conversions: vi.fn(success), prepare_conversion: vi.fn(success),
    set_theme: vi.fn(async theme => { state.theme = theme; return success() }),
  }
  window.pywebview = { api }
  return {
    api,
    update: (next: Partial<DesktopState>) => { state = { ...state, ...next } },
    reply: (next: Partial<DesktopState>) => { state = { ...state, ...next }; return success() },
  }
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

describe('appearance', () => {
  it('cycles the saved theme and applies it to the document', async () => {
    const { api } = fixture()
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Theme: match Windows/ })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: /Theme: match Windows/ }))
    await waitFor(() => expect(api.set_theme).toHaveBeenCalledWith('light'))
    await waitFor(() => expect(document.documentElement).not.toHaveClass('dark'))
    await user.click(screen.getByRole('button', { name: /Theme: light/ }))
    await waitFor(() => expect(api.set_theme).toHaveBeenCalledWith('dark'))
    await waitFor(() => expect(document.documentElement).toHaveClass('dark'))
  })
})

describe('desktop flow', () => {
  it('requires a check and an explicit Download action', async () => {
    const { api } = fixture()
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
    expect(screen.queryByRole('combobox', { name: 'Save as' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled()
    await user.type(screen.getByLabelText('Media URL'), url)
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await screen.findByText('A real checked title')
    expect(api.enqueue).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledWith('inspection-1', 'asset-1', 'video', 'original'))
  })

  it('supports keyboard media and format choices and keeps the choice across polling', async () => {
    const { api, user } = await checkedApp()
    const video = screen.getByRole('radio', { name: 'Video' })
    act(() => video.focus())
    await user.keyboard('{ArrowRight>}')
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Audio' })).toBeChecked())
    await user.keyboard('{/ArrowRight}')
    const format = screen.getByRole('combobox', { name: 'Save as' })
    expect(format).toHaveTextContent('Original · Recommended')
    expect(format).toHaveAccessibleDescription('Original audio')
    act(() => format.focus())
    await user.keyboard('{Enter}')
    await user.keyboard('{Escape}')
    expect(format).toHaveTextContent('Original · Recommended')
    await user.keyboard('{Enter}')
    await user.keyboard('{ArrowDown}{Enter}')
    await act(() => new Promise(resolve => setTimeout(resolve, 500)))
    expect(format).toHaveTextContent('MP3')
    expect(format).toHaveAccessibleDescription('Convert to MP3')
    expect(api.enqueue).not.toHaveBeenCalled()
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
    const { api, reply } = fixture()
    api.check_url = vi.fn((checked: string) => reply({ inspection: { status: 'failed', id: '', url: checked, error: 'No supported media at this address.', assets: [] } }))
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
    expect(screen.getByText('No tasks yet')).toBeInTheDocument()
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
    const { api, reply } = fixture()
    api.check_url = vi.fn((checked: string) => reply({ inspection: { status: 'ready', id: 'limited', url: checked, error: null, assets: [{ id: 'asset-limit', title: 'A very large video', description: 'Exceeds the maximum file size', thumbnail: null, default_kind: '', kinds: [] }] } }))
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

const png: LocalImage = { id: 'image-1', name: 'Transparent icon.png', format: 'png', width: 640, height: 480, size: 1024, transparent: true, targets: ['jpg', 'webp'], error: null }
const jpeg: LocalImage = { id: 'image-2', name: 'Photo.jpg', format: 'jpg', width: 1200, height: 800, size: 2048, transparent: false, targets: ['png', 'webp'], error: null }
const invalidImage: LocalImage = { id: 'image-bad', name: 'Animated.webp', format: 'webp', width: null, height: null, size: 123, transparent: false, targets: [], error: 'Animated images are not supported.' }

async function conversionApp(files: LocalImage[], tools: DesktopState['tools'] = { status: 'ready' }) {
  const mock = fixture()
  mock.update({ conversion: { status: 'ready', id: 'local-inspection', files, error: null }, tools })
  const user = userEvent.setup()
  render(<App />)
  await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
  await user.click(screen.getByRole('tab', { name: 'Convert' }))
  return { ...mock, user }
}
async function chooseConversionFormat(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole('combobox', { name: 'Convert to' }))
  await user.click(await screen.findByRole('option', { name: new RegExp(`^${name}\\b`) }))
}

describe('local image conversion', () => {
  it('allows clearing files to cancel a running local inspection', async () => {
    const { api, update, reply } = fixture()
    update({ conversion: { status: 'checking', id: null, files: [{ ...png, format: null, targets: [] }], error: null } })
    api.clear_conversion_files = vi.fn(() => reply({ conversion: structuredClone(initialState.conversion) }))
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
    await user.click(screen.getByRole('tab', { name: 'Convert' }))
    expect(screen.getByText('Checking files…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add files' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Clear files' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Clear files' }))
    await waitFor(() => expect(api.clear_conversion_files).toHaveBeenCalledOnce())
    expect(await screen.findByText('Choose files from your computer')).toBeInTheDocument()
    expect(screen.queryByText('Checking files…')).not.toBeInTheDocument()
    expect(api.enqueue_conversions).not.toHaveBeenCalled()
  })

  it('preserves the checked Download choice and queue across both tabs', async () => {
    const { user, update } = await checkedApp()
    await user.click(screen.getByRole('radio', { name: 'Audio' }))
    await user.click(screen.getByRole('combobox', { name: 'Save as' }))
    await user.click(await screen.findByRole('option', { name: 'MP3' }))
    update({ tasks: [{ id: 'pending', url, title: 'Still running', kind: 'video', format: 'original', status: 'downloading', progress: 45, downloaded_bytes: 1024, total_bytes: 4096, speed: null, eta: null, path: null, error: null }] })
    await user.click(screen.getByRole('tab', { name: 'Convert' }))
    expect(screen.getByRole('tabpanel', { name: 'Convert' })).toBeVisible()
    expect(screen.queryByRole('textbox', { name: 'Media URL' })).not.toBeInTheDocument()
    expect(await screen.findByText('Still running')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Download' }))
    expect(screen.getByLabelText('Media URL')).toHaveValue(url)
    expect(screen.getByRole('radio', { name: 'Audio' })).toBeChecked()
    expect(screen.getByRole('combobox', { name: 'Save as' })).toHaveTextContent('MP3')
    expect(screen.getByText('Still running')).toBeInTheDocument()
  })

  it('preserves an explicit conversion choice across polling and tab changes', async () => {
    const video: LocalImage = { ...png, id: 'video-1', name: 'Clip.mov', format: 'mov', media_type: 'video', has_audio: true, transparent: false, targets: ['mp3', 'mp4', 'mkv'] }
    const { user } = await conversionApp([video])
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('MP4')
    await chooseConversionFormat(user, 'MP3')
    await act(() => new Promise(resolve => setTimeout(resolve, 450)))
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('MP3')
    await user.click(screen.getByRole('tab', { name: 'Download' }))
    await user.click(screen.getByRole('tab', { name: 'Convert' }))
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('MP3')
  })

  it('uses a native file picker and only queues files after explicit Convert', async () => {
    const { api, update } = fixture()
    api.select_conversion_files = vi.fn(async () => {
      update({ conversion: { status: 'ready', id: 'picked', files: [png, jpeg], error: null } })
      return api.get_state()
    })
    const user = userEvent.setup()
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Media URL')).toBeEnabled())
    await user.click(screen.getByRole('tab', { name: 'Convert' }))
    expect(screen.getByRole('button', { name: 'Convert' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Add files' }))
    expect(await screen.findByText(png.name)).toBeInTheDocument()
    expect(api.select_conversion_files).toHaveBeenCalledOnce()
    expect(api.enqueue_conversions).not.toHaveBeenCalled()
    expect(screen.getByText(/2 of 2 selected files can convert to WebP/)).toBeInTheDocument()
    await chooseConversionFormat(user, 'WebP')
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    await waitFor(() => expect(api.enqueue_conversions).toHaveBeenCalledWith(['image-1', 'image-2'], 'webp', '#FFFFFF'))
  })

  it('excludes invalid files and makes JPEG transparency loss explicit', async () => {
    const { api, user } = await conversionApp([png, invalidImage])
    expect(screen.getByText('Animated images are not supported.')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: `Select ${invalidImage.name}` })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: `Select ${invalidImage.name}` })).not.toBeChecked()
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('WebP')
    await chooseConversionFormat(user, 'JPG')
    expect(screen.getByText('Transparent areas will use this background for this output.')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Black' }))
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    await waitFor(() => expect(api.enqueue_conversions).toHaveBeenCalledWith(['image-1'], 'jpg', '#000000'))
    await chooseConversionFormat(user, 'WebP')
    expect(screen.queryByRole('radiogroup', { name: 'JPG background' })).not.toBeInTheDocument()
  })

  it('recomputes compatible counts from the selected subset and keeps selection across polling', async () => {
    const { api, user } = await conversionApp([png, jpeg])
    await chooseConversionFormat(user, 'JPG')
    expect(screen.getByText(/1 of 2 selected files can convert to JPG/)).toBeInTheDocument()
    expect(screen.getByText('Already JPG · skipped')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: `Select ${jpeg.name}` }))
    expect(screen.getByText(/1 of 1 selected file can convert to WebP/)).toBeInTheDocument()
    await chooseConversionFormat(user, 'JPG')
    await act(() => new Promise(resolve => setTimeout(resolve, 450)))
    expect(screen.getByRole('checkbox', { name: `Select ${jpeg.name}` })).not.toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    await waitFor(() => expect(api.enqueue_conversions).toHaveBeenCalledWith(['image-1'], 'jpg', '#FFFFFF'))
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    await user.click(screen.getByRole('button', { name: 'Deselect all' }))
    expect(screen.getByRole('button', { name: 'Convert' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('WebP')
  })

  it('offers the union for a mixed batch and only queues files compatible with the chosen output', async () => {
    const webp: LocalImage = { ...png, id: 'webp', name: 'Picture.webp', format: 'webp', targets: ['png', 'jpg'] }
    const { api, user } = await conversionApp([png, jpeg, webp])
    expect(screen.getByText(/2 of 3 selected files can convert to PNG/)).toHaveTextContent('The other file is already that format or incompatible and will be skipped.')
    expect(screen.getByRole('button', { name: 'Convert' })).toBeEnabled()
    expect(screen.getByText(/2 of 3 selected files can convert to PNG/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    await waitFor(() => expect(api.enqueue_conversions).toHaveBeenCalledWith(['image-2', 'webp'], 'png', '#FFFFFF'))
  })

  it('allows local conversion and retries while download tools are missing', async () => {
    const { api, user, update } = await conversionApp([png], { status: 'missing' })
    expect(screen.queryByRole('button', { name: 'Install tools' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add files' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    await waitFor(() => expect(api.enqueue_conversions).toHaveBeenCalledOnce())
    update({ tasks: [{ id: 'local-failed', url: '', title: 'Image conversion', action: 'convert', source_format: 'png', kind: 'image', format: 'jpg', status: 'failed', progress: null, downloaded_bytes: 0, total_bytes: null, speed: null, eta: null, path: null, error: 'Temporary output failure.' }] })
    await user.click(await screen.findByRole('button', { name: 'Retry Image conversion' }))
    await waitFor(() => expect(api.retry_task).toHaveBeenCalledWith('local-failed'))
  })

  it('opens completed static images in Convert without starting conversion automatically', async () => {
    const { api, update } = fixture()
    update({ tasks: [{ id: 'completed-image', url, title: 'Downloaded photo', action: 'download', can_convert: true, kind: 'image', format: 'original', status: 'completed', progress: 100, downloaded_bytes: 1024, total_bytes: 1024, speed: null, eta: null, path: 'C:\\Downloads\\photo.png', error: null }] })
    api.prepare_conversion = vi.fn(async () => {
      update({ conversion: { status: 'ready', id: 'prepared', files: [png], error: null } })
      return api.get_state()
    })
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Convert file Downloaded photo' }))
    expect(await screen.findByRole('tabpanel', { name: 'Convert' })).toBeVisible()
    expect(screen.getByText(png.name)).toBeVisible()
    expect(api.prepare_conversion).toHaveBeenCalledWith('completed-image')
    expect(api.enqueue_conversions).not.toHaveBeenCalled()
  })

  it('shows indeterminate conversion activity and allows cancellation', async () => {
    const { api, update } = fixture()
    update({ tasks: [{ id: 'local-active', url: '', title: 'Converting image', action: 'convert', source_format: 'png', kind: 'image', format: 'webp', status: 'converting', progress: null, downloaded_bytes: 0, total_bytes: null, speed: null, eta: null, path: null, error: null }] })
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByText('Encoding file…')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.getByText('PNG → WEBP')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel Converting image' }))
    await waitFor(() => expect(api.cancel_task).toHaveBeenCalledWith('local-active'))
  })

  it('shows expanded image destinations in a grouped selector and explains GIF transparency', async () => {
    const expanded: LocalImage = { ...png, targets: ['jpg', 'webp', 'avif', 'bmp', 'tiff', 'ico', 'tga', 'gif', 'jp2'], target_notes: { gif: 'GIF keeps fully transparent pixels; partial transparency uses the chosen background.' } }
    const { api, user } = await conversionApp([expanded])
    await user.click(screen.getByRole('combobox', { name: 'Convert to' }))
    expect(screen.getByRole('group', { name: 'Images' })).toBeInTheDocument()
    expect(screen.getAllByRole('option')).toHaveLength(9)
    expect(screen.queryByRole('option', { name: /^PNG\b/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: /^BMP\b/ }))
    expect(screen.getByRole('radiogroup', { name: 'BMP background' })).toBeInTheDocument()
    expect(screen.getByText('Transparent areas will use this background for this output.')).toBeInTheDocument()
    await chooseConversionFormat(user, 'GIF')
    expect(screen.getByText(expanded.target_notes!.gif)).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: 'GIF background' })).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Black' }))
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    await waitFor(() => expect(api.enqueue_conversions).toHaveBeenCalledWith(['image-1'], 'gif', '#000000'))
  })

  it('converts audio and video with FFmpeg even when the downloader JavaScript runtime is missing', async () => {
    const audio: LocalImage = { ...png, id: 'audio-1', name: 'Recording.wav', format: 'wav', media_type: 'audio', width: null, height: null, transparent: false, duration: 83, requires_tools: true, targets: ['mp3', 'flac', 'm4a', 'aac', 'opus', 'ogg', 'aiff'] }
    const video: LocalImage = { ...audio, id: 'video-1', name: 'Clip.mov', format: 'mov', media_type: 'video', width: 1920, height: 1080, has_audio: true, targets: ['mp3', 'mp4', 'mkv', 'webm'] }
    const { api, user } = await conversionApp([audio, video], { status: 'missing', ffmpeg: true, javascript: false })
    expect(screen.queryByRole('button', { name: 'Install tools' })).not.toBeInTheDocument()
    expect(screen.getByText(/Audio · WAV · 1:23/)).toBeInTheDocument()
    expect(screen.getByText(/Video · MOV · 1920 × 1080 · 1:23/)).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Convert to' }))
    expect(screen.getByRole('group', { name: 'Audio' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Video' })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: /^MP3\b/ }))
    expect(screen.getByText(/2 of 2 selected files can convert to MP3/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    await waitFor(() => expect(api.enqueue_conversions).toHaveBeenCalledWith(['audio-1', 'video-1'], 'mp3', '#FFFFFF'))
  })

  it('keeps image conversion usable with missing FFmpeg and blocks only a selected audio or video conversion', async () => {
    const audio: LocalImage = { ...png, id: 'audio-1', name: 'Recording.wav', format: 'wav', media_type: 'audio', width: null, height: null, transparent: false, requires_tools: true, targets: ['mp3', 'flac'] }
    const { api, user } = await conversionApp([png, audio], { status: 'missing', ffmpeg: false, javascript: false })
    expect(screen.getByText(/Install FFmpeg for audio and video conversion/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Convert' })).toBeDisabled()
    await chooseConversionFormat(user, 'WebP')
    expect(screen.getByRole('button', { name: 'Convert' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    await waitFor(() => expect(api.enqueue_conversions).toHaveBeenCalledWith(['image-1'], 'webp', '#FFFFFF'))
    await chooseConversionFormat(user, 'MP3')
    expect(screen.getByRole('button', { name: 'Convert' })).toBeDisabled()
    expect(screen.getByText('Install FFmpeg to convert this selection.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Install tools' }))
    await waitFor(() => expect(api.install_tools).toHaveBeenCalledOnce())
  })

  it('preserves animation output notes and requires FFmpeg only for video output', async () => {
    const animation: LocalImage = { ...png, id: 'animation-1', name: 'Sticker.gif', format: 'gif', media_type: 'animation', animated: true, duration: 2, pages: 2, requires_tools: false, targets: ['webp', 'apng', 'mp4'], target_notes: { apng: 'Animation and frame timing are preserved.', mp4: 'MP4 cannot keep transparency; transparent areas become black.' } }
    const { user } = await conversionApp([animation], { status: 'missing', ffmpeg: false, javascript: false })
    expect(screen.getByText(/Animation · GIF · 640 × 480 · 0:02/)).toBeInTheDocument()
    expect(screen.getByText(/2 frames/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Convert' })).toBeEnabled()
    await chooseConversionFormat(user, 'APNG')
    expect(screen.getByText(animation.target_notes!.apng)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Convert' })).toBeEnabled()
    await chooseConversionFormat(user, 'MP4')
    expect(screen.getByText(animation.target_notes!.mp4)).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: 'MP4 background' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Convert' })).toBeDisabled()
  })
})
