import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ConvertPanel } from '../components/ConvertPanel'
import type { LocalFile } from '../types'

const image: LocalFile = { id: 'image', name: 'Icon.png', format: 'png', width: 640, height: 480, size: 1024, transparent: true, targets: ['jpg', 'webp', 'tiff'], error: null, media_type: 'image' }
const video: LocalFile = { ...image, id: 'video', name: 'Clip.mov', format: 'mov', transparent: false, media_type: 'video', has_audio: true, duration: 30, targets: ['mp3', 'flac', 'mp4', 'mkv', 'webm'] }
const audio: LocalFile = { ...image, id: 'audio', name: 'Recording.aac', format: 'aac', width: null, height: null, transparent: false, media_type: 'audio', duration: 30, targets: ['mp3', 'flac', 'wav', 'opus'] }
const animation: LocalFile = { ...image, id: 'animation', name: 'Sticker.gif', format: 'gif', media_type: 'animation', animated: true, duration: 2, targets: ['webp', 'apng', 'mp4'] }

function props(files: LocalFile[]) {
  return { conversion: { status: 'ready' as const, id: 'inspection', files, error: null }, output: 'C:\\Downloads', disabled: false, onAdd: vi.fn(), onRemove: vi.fn(), onClear: vi.fn(), onBrowse: vi.fn(), onConvert: vi.fn(async () => undefined) }
}
function formatGroup(name = 'Convert to') { return screen.getByRole('radiogroup', { name }) }
function checkedFormat(name = 'Convert to') { return within(formatGroup(name)).queryByRole('radio', { checked: true }) }
async function choose(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(within(formatGroup()).getByRole('radio', { name: new RegExp(`^${name}\\b`) }))
}

describe('conversion defaults and explicit choices', () => {
  it.each([
    ['video with audio', video, 'MP4'],
    ['audio', audio, 'MP3'],
    ['animation', animation, 'APNG'],
    ['transparent image', image, 'WebP'],
  ])('keeps the media type for a fresh %s selection', (_name, file, expected) => {
    render(<ConvertPanel {...props([file])} />)
    expect(checkedFormat('Convert to')).toHaveTextContent(expected)
  })

  it('keeps an all-video batch as video when extracting audio could cover more files', () => {
    const second: LocalFile = { ...video, id: 'second-video', name: 'Other.mp4', format: 'mp4', targets: ['mp3', 'flac', 'mkv', 'webm'] }
    render(<ConvertPanel {...props([video, second])} />)
    expect(checkedFormat('Convert to')).toHaveTextContent('MKV')
    expect(screen.getByText(/2 of 2 selected files can convert to MKV/)).toBeInTheDocument()
  })

  it('chooses another common audio format when the source is already MP3', () => {
    render(<ConvertPanel {...props([{ ...audio, format: 'mp3', targets: ['wav', 'flac', 'm4a', 'opus'] }])} />)
    expect(checkedFormat('Convert to')).toHaveTextContent('M4A')
  })

  it.each([
    ['m4a', audio],
    ['mkv', video],
  ])('identifies native %s codec conversion as compatibility and submits its format', async (target, source) => {
    const user = userEvent.setup()
    const callbacks = props([{ ...source, format: target, targets: [target] }])
    render(<ConvertPanel {...callbacks} />)
    expect(checkedFormat('Convert to')).toHaveTextContent(`${target.toUpperCase()} compatibility`)
    await choose(user, `${target.toUpperCase()} compatibility`)
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    expect(callbacks.onConvert).toHaveBeenCalledWith([source.id], target, '#FFFFFF')
  })

  it('keeps the generic format name when any compatible file has another source format', () => {
    render(<ConvertPanel {...props([
      { ...video, format: 'mkv', targets: ['mkv'] },
      { ...video, id: 'second-video', format: 'mov', targets: ['mkv'] },
    ])} />)
    expect(checkedFormat('Convert to')).not.toHaveTextContent('compatibility')
    expect(within(formatGroup()).getByRole('radio', { name: 'MKV' })).toBeChecked()
  })

  it('labels compatibility using only files eligible for that target', () => {
    render(<ConvertPanel {...props([
      { ...video, format: 'mkv', targets: ['mkv'] },
      { ...video, id: 'second-video', format: 'mov', targets: ['mp3'] },
    ])} />)
    expect(checkedFormat('Convert to')).toHaveTextContent('MKV compatibility')
  })

  it('explains when a checked file has no available output formats', () => {
    render(<ConvertPanel {...props([{ ...image, targets: [] }])} />)
    expect(screen.getByText('No compatible output formats are available with the installed tools.')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select Icon.png' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Convert' })).toBeDisabled()
    expect(screen.queryByRole('radiogroup', { name: 'Convert to' })).not.toBeInTheDocument()
  })

  it('retains an explicit inspection error without adding a no-output explanation', () => {
    render(<ConvertPanel {...props([{ ...image, targets: [], error: 'The source file could not be read.' }])} />)
    expect(screen.getByText('The source file could not be read.')).toBeInTheDocument()
    expect(screen.queryByText('No compatible output formats are available with the installed tools.')).not.toBeInTheDocument()
  })

  it('waits for file checking to finish before explaining unavailable formats', () => {
    const callbacks = props([{ ...image, targets: [] }])
    render(<ConvertPanel {...callbacks} conversion={{ ...callbacks.conversion, status: 'checking' }} />)
    expect(screen.queryByText('No compatible output formats are available with the installed tools.')).not.toBeInTheDocument()
  })

  it.each(['MKV', 'MOV', 'WebM'])('submits the selected %s background when its checked video plan flattens transparency', async target => {
    const user = userEvent.setup()
    const callbacks = props([{ ...video, transparent: true, targets: ['mkv', 'mov', 'webm'], background_targets: ['mkv', 'mov', 'webm'] }])
    render(<ConvertPanel {...callbacks} />)
    await choose(user, target)
    expect(screen.getByRole('radiogroup', { name: `${target} background` })).toBeInTheDocument()
    expect(screen.getByText('Transparent areas will use this background for this output.')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Black' }))
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    expect(callbacks.onConvert).toHaveBeenCalledWith(['video'], target.toLowerCase(), '#000000')
  })

  it('does not offer a background for a checked alpha-preserving video stream copy', () => {
    render(<ConvertPanel {...props([{ ...video, transparent: true, targets: ['webm'], background_targets: [] }])} />)
    expect(checkedFormat('Convert to')).toHaveTextContent('WebM')
    expect(screen.queryByRole('radiogroup', { name: /background/ })).not.toBeInTheDocument()
  })

  it('requires an explicit format for mixed media instead of defaulting to audio extraction', async () => {
    const user = userEvent.setup()
    const callbacks = props([video, audio, image])
    const view = render(<ConvertPanel {...callbacks} />)
    expect(checkedFormat()).toBeNull()
    expect(screen.getByRole('button', { name: 'Convert' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    expect(callbacks.onConvert).not.toHaveBeenCalled()
    await choose(user, 'MP3')
    expect(screen.getByText(/2 of 3 selected files can convert to MP3/)).toBeInTheDocument()
    view.rerender(<ConvertPanel {...callbacks} conversion={{ ...callbacks.conversion, files: structuredClone([video, audio, image]) }} />)
    expect(checkedFormat('Convert to')).toHaveTextContent('MP3')
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    expect(callbacks.onConvert).toHaveBeenCalledWith(['video', 'audio'], 'mp3', '#FFFFFF')
  })

  it('requires an explicit format when no video-preserving encoder is available', async () => {
    const user = userEvent.setup()
    const callbacks = props([{ ...video, targets: ['mp3', 'gif'] }])
    render(<ConvertPanel {...callbacks} />)
    expect(checkedFormat()).toBeNull()
    expect(screen.getByRole('button', { name: 'Convert' })).toBeDisabled()
    await choose(user, 'GIF')
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    expect(callbacks.onConvert).toHaveBeenCalledWith(['video'], 'gif', '#FFFFFF')
  })

  it('preserves an explicit audio extraction across refreshed state but resets it for a new video selection', async () => {
    const user = userEvent.setup()
    const callbacks = props([video])
    const view = render(<ConvertPanel {...callbacks} />)
    await choose(user, 'MP3')
    view.rerender(<ConvertPanel {...callbacks} conversion={{ ...callbacks.conversion, files: structuredClone([video]) }} />)
    expect(checkedFormat('Convert to')).toHaveTextContent('MP3')
    view.rerender(<ConvertPanel {...props([{ ...video, id: 'new-video', name: 'New.mov' }])} />)
    expect(checkedFormat('Convert to')).toHaveTextContent('MP4')
  })

  it('resets a previous extraction when the inspected content changes category under the same file ID', async () => {
    const user = userEvent.setup()
    const view = render(<ConvertPanel {...props([video])} />)
    await choose(user, 'FLAC')
    view.rerender(<ConvertPanel {...props([{ ...audio, id: video.id }])} />)
    expect(checkedFormat('Convert to')).toHaveTextContent('MP3')
  })

  it('keeps offline image conversion available after an explicit choice in a mixed batch', async () => {
    const user = userEvent.setup()
    render(<ConvertPanel {...props([image, audio])} ffmpegReady={false} />)
    expect(screen.getByRole('button', { name: 'Convert' })).toBeDisabled()
    await choose(user, 'WebP')
    expect(checkedFormat('Convert to')).toHaveTextContent('WebP')
    expect(screen.getByRole('button', { name: 'Convert' })).toBeEnabled()
  })

  it.each(['GIF', 'WebP', 'APNG'])('offers %s animation for a silent MP4 and submits the explicit selection', async target => {
    const user = userEvent.setup()
    const file: LocalFile = { ...video, format: 'mp4', name: 'Silent.mp4', has_audio: false,
      targets: ['mov', 'mkv', 'webm', 'gif', 'webp', 'apng'],
      target_notes: { gif: 'Loops without audio.', webp: 'Lossless animation without audio.', apng: 'Animated PNG without audio.' } }
    const callbacks = props([file])
    const view = render(<ConvertPanel {...callbacks} />)
    expect(checkedFormat('Convert to')).toHaveTextContent('MKV')
    expect(within(formatGroup()).getAllByRole('radio', { name: new RegExp(`^${target}\\b`) })).toHaveLength(1)
    expect(screen.getByText('Animation')).toBeInTheDocument()
    expect(within(formatGroup()).queryByRole('radio', { name: /^MP3\b/ })).not.toBeInTheDocument()
    await choose(user, target)
    view.rerender(<ConvertPanel {...callbacks} conversion={{ ...callbacks.conversion, files: structuredClone([file]) }} />)
    expect(checkedFormat('Convert to')).toHaveTextContent(target)
    await user.click(screen.getByRole('button', { name: 'Convert' }))
    expect(callbacks.onConvert).toHaveBeenCalledWith(['video'], target.toLowerCase(), '#FFFFFF')
  })
})
