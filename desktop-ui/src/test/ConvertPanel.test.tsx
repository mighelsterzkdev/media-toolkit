import { render, screen } from '@testing-library/react'
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
async function choose(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole('combobox', { name: 'Convert to' }))
  await user.click(await screen.findByRole('option', { name: new RegExp(`^${name}\\b`) }))
}

describe('conversion defaults and explicit choices', () => {
  it.each([
    ['video with audio', video, 'MP4'],
    ['audio', audio, 'MP3'],
    ['animation', animation, 'APNG'],
    ['transparent image', image, 'WebP'],
  ])('keeps the media type for a fresh %s selection', (_name, file, expected) => {
    render(<ConvertPanel {...props([file])} />)
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent(expected)
  })

  it('keeps an all-video batch as video when extracting audio could cover more files', () => {
    const second: LocalFile = { ...video, id: 'second-video', name: 'Other.mp4', format: 'mp4', targets: ['mp3', 'flac', 'mkv', 'webm'] }
    render(<ConvertPanel {...props([video, second])} />)
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('MKV')
    expect(screen.getByText(/2 of 2 selected files can convert to MKV/)).toBeInTheDocument()
  })

  it('chooses another common audio format when the source is already MP3', () => {
    render(<ConvertPanel {...props([{ ...audio, format: 'mp3', targets: ['wav', 'flac', 'm4a', 'opus'] }])} />)
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('M4A')
  })

  it('defaults a mixed media batch to the output with the most compatible selected files', async () => {
    const onConvert = vi.fn(async () => undefined)
    render(<ConvertPanel {...props([video, audio, image])} onConvert={onConvert} />)
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('MP3')
    expect(screen.getByText(/2 of 3 selected files can convert to MP3/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Convert' }))
    expect(onConvert).toHaveBeenCalledWith(['video', 'audio'], 'mp3', '#FFFFFF')
  })

  it('preserves an explicit audio extraction across refreshed state but resets it for a new video selection', async () => {
    const user = userEvent.setup()
    const callbacks = props([video])
    const view = render(<ConvertPanel {...callbacks} />)
    await choose(user, 'MP3')
    view.rerender(<ConvertPanel {...callbacks} conversion={{ ...callbacks.conversion, files: structuredClone([video]) }} />)
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('MP3')
    view.rerender(<ConvertPanel {...props([{ ...video, id: 'new-video', name: 'New.mov' }])} />)
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('MP4')
  })

  it('resets a previous extraction when the inspected content changes category under the same file ID', async () => {
    const user = userEvent.setup()
    const view = render(<ConvertPanel {...props([video])} />)
    await choose(user, 'FLAC')
    view.rerender(<ConvertPanel {...props([{ ...audio, id: video.id }])} />)
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('MP3')
  })

  it('prefers an available image conversion in a tied mixed batch while FFmpeg is missing', () => {
    render(<ConvertPanel {...props([image, audio])} ffmpegReady={false} />)
    expect(screen.getByRole('combobox', { name: 'Convert to' })).toHaveTextContent('WebP')
    expect(screen.getByRole('button', { name: 'Convert' })).toBeEnabled()
  })
})
