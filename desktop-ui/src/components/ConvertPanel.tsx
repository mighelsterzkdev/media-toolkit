import { useState } from 'react'
import { ArrowRightLeft, CircleAlert, FileImage, FileVideo, Files, Images, LoaderCircle, Music2, Plus, X } from 'lucide-react'
import { Button } from './ui/button'
import { Label } from './ui/label'
import { RadioGroup, RadioGroupItem } from './ui/radio-group'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from './ui/select'
import { SaveDestination } from './SaveDestination'
import { bytes } from '../format'
import type { Conversion, ConversionFormat, LocalFile } from '../types'

interface Props {
  conversion: Conversion; output: string; disabled: boolean; ffmpegReady?: boolean;
  onAdd(): void; onRemove(id: string): void; onClear(): void; onBrowse(): void;
  onConvert(ids: string[], target: ConversionFormat, background: '#FFFFFF' | '#000000'): Promise<unknown>
}

const outputGroups = [
  { label: 'Images', formats: ['png', 'jpg', 'webp', 'avif', 'bmp', 'tiff', 'ico', 'tga', 'gif', 'jp2'] },
  { label: 'Audio', formats: ['mp3', 'wav', 'flac', 'm4a', 'aac', 'opus', 'ogg', 'aiff'] },
  { label: 'Video', formats: ['mp4', 'mkv', 'mov', 'webm'] },
  { label: 'Animation', formats: ['gif', 'webp', 'apng'] },
]
const knownFormats = new Set(outputGroups.flatMap(group => group.formats))
const ffmpegFormats = new Set(['mp3', 'wav', 'flac', 'm4a', 'aac', 'opus', 'ogg', 'aiff', 'mp4', 'mkv', 'mov', 'webm'])
const flattenFormats = new Set(['jpg', 'bmp', 'gif', 'mp4'])
const preferredFormats = {
  image: ['png', 'webp', 'tiff', 'jp2', 'bmp', 'tga', 'avif', 'jpg', 'gif', 'ico'],
  audio: ['mp3', 'm4a', 'opus', 'aac', 'ogg', 'flac', 'wav', 'aiff'],
  video: ['mp4', 'mkv', 'mov', 'webm'],
  animation: ['apng', 'webp', 'gif'],
} as const
const formatNames: Record<string, string> = { webp: 'WebP', webm: 'WebM' }
function formatName(value: string) { return formatNames[value] ?? value.toUpperCase() }
function mediaType(file: LocalFile) { return file.media_type ?? (file.animated ? 'animation' : 'image') }
function targetLabel(target: string, files: LocalFile[]) {
  const compatible = files.filter(file => file.targets.includes(target))
  const nativeMedia = compatible.length > 0 && compatible.every(file => ['audio', 'video'].includes(mediaType(file)) && file.format?.toLowerCase() === target)
  return `${formatName(target)}${nativeMedia ? ' compatibility' : ''}`
}
function preservesMedia(file: LocalFile, target: string) { return (preferredFormats[mediaType(file)] as readonly string[]).includes(target) }
function targetNeedsTools(file: LocalFile, target: string) { return Boolean(file.requires_tools || ['audio', 'video'].includes(mediaType(file)) || ffmpegFormats.has(target)) }
function defaultTarget(files: LocalFile[], targets: string[], ffmpegReady: boolean) {
  // Defaults preserve a single media category. Mixed sources or extraction
  // require an explicit choice, even when another format would cover more files.
  if (new Set(files.map(mediaType)).size !== 1) return undefined
  const sameCategory = targets.filter(target => files.some(file => file.targets.includes(target) && preservesMedia(file, target)))
  const categoryCandidates = sameCategory
  const readyCandidates = ffmpegReady ? categoryCandidates : categoryCandidates.filter(target => files.filter(file => file.targets.includes(target)).every(file => !targetNeedsTools(file, target)))
  const candidates = readyCandidates.length > 0 ? readyCandidates : categoryCandidates
  return candidates.reduce<string | undefined>((best, candidate) => {
    const score = (target: string) => {
      const compatible = files.filter(file => file.targets.includes(target))
      const preserving = compatible.filter(file => preservesMedia(file, target))
      const quality = preserving.reduce((total, file) => total + 50 - (preferredFormats[mediaType(file)] as readonly string[]).indexOf(target), 0)
      return [compatible.length, preserving.length, quality]
    }
    if (!best) return candidate
    const current = score(candidate), previous = score(best)
    const different = current.findIndex((value, index) => value !== previous[index])
    return different >= 0 && current[different] > previous[different] ? candidate : best
  }, undefined)
}
function mediaIcon(file: LocalFile) { const type = mediaType(file); return type === 'audio' ? Music2 : type === 'video' ? FileVideo : type === 'animation' ? Images : FileImage }
function playbackTime(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value < 0) return ''
  const seconds = Math.round(value)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
function fileDescription(file: LocalFile) {
  const type = mediaType(file)
  return [type[0].toUpperCase() + type.slice(1), file.format ? formatName(file.format) : '',
    file.width && file.height ? `${file.width} × ${file.height}` : '', playbackTime(file.duration),
    file.pages && file.pages > 1 ? `${file.pages} ${type === 'animation' ? 'frames' : 'pages'}` : '', file.size != null ? bytes(file.size) : '',
    file.transparent ? 'Transparent' : '', type === 'video' && file.has_audio === false ? 'No audio' : '',
  ].filter(Boolean).join(' · ')
}

export function ConvertPanel({ conversion, output, disabled, ffmpegReady = true, onAdd, onRemove, onClear, onBrowse, onConvert }: Props) {
  // New valid files start selected; user exclusions survive polling and tab changes.
  const [excluded, setExcluded] = useState<Set<string>>(() => new Set())
  const [preference, setPreference] = useState<{ target: ConversionFormat; selection: string } | null>(null)
  const [background, setBackground] = useState<'#FFFFFF' | '#000000'>('#FFFFFF')
  const [submitting, setSubmitting] = useState(false)
  const checking = conversion.status === 'checking'
  const busy = disabled || checking || submitting
  const validFiles = conversion.files.filter(file => !file.error && file.targets.length > 0)
  const selected = validFiles.filter(file => !excluded.has(file.id))
  const selectionKey = JSON.stringify(selected.map(file => [file.id, mediaType(file), file.format]).sort((a, b) => String(a[0]).localeCompare(String(b[0]))))
  const availableTargets = new Set(selected.flatMap(file => file.targets))
  const animationTargets = new Set(selected
    .filter(file => ['video', 'animation'].includes(mediaType(file)))
    .flatMap(file => file.targets.filter(format => ['gif', 'webp', 'apng'].includes(format))))
  const groups = [...outputGroups, { label: 'Other', formats: [...availableTargets].filter(format => !knownFormats.has(format)) }]
    .map(group => ({ ...group, formats: group.formats.filter(format => availableTargets.has(format)
      && (group.label === 'Images' ? !animationTargets.has(format)
        : group.label === 'Animation' ? format === 'apng' || animationTargets.has(format) : true)) }))
    .filter(group => group.formats.length > 0)
  const targets = groups.flatMap(group => group.formats)
  const target = preference?.selection === selectionKey && targets.includes(preference.target) ? preference.target : defaultTarget(selected, targets, ffmpegReady)
  const compatible = target ? selected.filter(file => file.targets.includes(target)) : []
  const skipped = selected.length - compatible.length
  const needsTools = Boolean(target && !ffmpegReady && compatible.some(file => targetNeedsTools(file, target)))
  const flattensAlpha = Boolean(target && compatible.some(file => file.transparent
    && (mediaType(file) === 'video' ? file.background_targets?.includes(target) : flattenFormats.has(target))))
  const targetNotes = target ? [...new Set(compatible.map(file => file.target_notes?.[target]).filter((note): note is string => Boolean(note)))] : []

  function selectFile(id: string, checked: boolean) {
    setExcluded(previous => { const next = new Set(previous); if (checked) next.delete(id); else next.add(id); return next })
  }
  function toggleAll() {
    setExcluded(previous => {
      const next = new Set(previous)
      for (const file of validFiles) { if (selected.length === validFiles.length) next.add(file.id); else next.delete(file.id) }
      return next
    })
  }
  async function convert() {
    if (busy || needsTools || !target || compatible.length === 0) return
    setSubmitting(true)
    try { await onConvert(compatible.map(file => file.id), target, background) }
    finally { setSubmitting(false) }
  }

  return <section className="convert-card" aria-labelledby="convert-heading">
    <div className="section-heading new-download-heading">
      <h2 id="convert-heading">Convert files</h2>
      <Button variant="outline" onClick={onAdd} disabled={busy}><Plus className="size-4" />Add files</Button>
    </div>
    {conversion.files.length === 0 ? <div className="conversion-empty">
      <Files className="size-6" aria-hidden="true" />
      <div><p>Choose files from your computer</p><span>Images, audio, video and animations. Original files are kept.</span></div>
    </div> : <>
      <div className="conversion-selection">
        <span>{selected.length} of {validFiles.length} valid {validFiles.length === 1 ? 'file' : 'files'} selected</span>
        <div><Button variant="ghost" size="sm" onClick={toggleAll} disabled={busy || validFiles.length === 0}>{selected.length === validFiles.length ? 'Deselect all' : 'Select all'}</Button><Button variant="ghost" size="sm" onClick={onClear} disabled={disabled || submitting}>Clear files</Button></div>
      </div>
      <ul className="conversion-files" aria-label="Selected files">
        {conversion.files.map(file => {
          const valid = !file.error && file.targets.length > 0
          const willSkip = valid && !excluded.has(file.id) && target && !file.targets.includes(target)
          const FileIcon = mediaIcon(file)
          return <li key={file.id} className={`conversion-file ${file.error ? 'conversion-file-invalid' : ''}`}>
            <input type="checkbox" className="file-checkbox" aria-label={`Select ${file.name}`} checked={valid && !excluded.has(file.id)} disabled={busy || !valid} onChange={event => selectFile(file.id, event.target.checked)} />
            <FileIcon className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div className="conversion-file-copy"><p title={file.name}>{file.name}</p>
              {file.error ? <span className="inline-error">{file.error}</span> : <span>{fileDescription(file)}</span>}
              {!checking && !file.error && file.targets.length === 0 ? <span>No compatible output formats are available with the installed tools.</span> : null}
              {willSkip ? <span className="conversion-file-skip">{file.format?.toLowerCase() === target ? `Already ${formatName(target)}` : `Incompatible with ${formatName(target)}`} · skipped</span> : null}
            </div>
            <Button variant="ghost" size="icon" aria-label={`Remove ${file.name}`} title="Remove from selection" disabled={busy} onClick={() => onRemove(file.id)}><X className="size-4" /></Button>
          </li>
        })}
      </ul>
    </>}
    {checking ? <div className="checking-state" role="status"><LoaderCircle className="size-4 animate-spin" />Checking files…</div> : null}
    {conversion.error ? <div className="inspection-error" role="alert"><CircleAlert className="size-4 shrink-0" /><span>{conversion.error}</span></div> : null}
    {selected.length > 0 && !checking ? <div className="conversion-options">
      <div className="conversion-target-field"><Label htmlFor="conversion-format">Convert to</Label>
        <Select value={target ?? ''} onValueChange={value => setPreference({ target: value, selection: selectionKey })} disabled={busy}>
          <SelectTrigger id="conversion-format" aria-describedby="conversion-compatibility"><SelectValue placeholder="Choose a format" /></SelectTrigger>
          <SelectContent>{groups.map(group => <SelectGroup key={group.label}><SelectLabel>{group.label}</SelectLabel>{group.formats.map(format => <SelectItem key={format} value={format} textValue={targetLabel(format, selected)}>{targetLabel(format, selected)}<span className="conversion-format-count"> · {selected.filter(file => file.targets.includes(format)).length} / {selected.length} {selected.length === 1 ? 'file' : 'files'}</span></SelectItem>)}</SelectGroup>)}</SelectContent>
        </Select>
      </div>
      <p id="conversion-compatibility" className="field-help" role="status">{target
        ? <>{compatible.length} of {selected.length} selected {selected.length === 1 ? 'file' : 'files'} can convert to {formatName(target)}.{skipped > 0 ? ` The other ${skipped === 1 ? 'file is' : `${skipped} files are`} already that format or incompatible and will be skipped.` : ''}</>
        : 'Choose an output format before converting this selection.'}</p>
      {targetNotes.map(note => <p key={note} className="field-help">{note}</p>)}
      {needsTools ? <p className="field-help">Install FFmpeg to convert this selection.</p> : null}
      {flattensAlpha && target ? <div className="alpha-background"><Label>Background</Label>
        <RadioGroup value={background} onValueChange={value => setBackground(value as '#FFFFFF' | '#000000')} aria-label={`${formatName(target)} background`} orientation="horizontal" className="flex flex-wrap gap-6">
          <Label htmlFor="background-white" className="background-choice"><RadioGroupItem id="background-white" value="#FFFFFF" disabled={busy} />White</Label>
          <Label htmlFor="background-black" className="background-choice"><RadioGroupItem id="background-black" value="#000000" disabled={busy} />Black</Label>
        </RadioGroup><p>{target === 'gif' ? 'GIF keeps fully transparent pixels. Partially transparent areas will use this background.' : 'Transparent areas will use this background for this output.'}</p>
      </div> : null}
    </div> : null}
    <div className="download-footer">
      <SaveDestination output={output} disabled={disabled} onBrowse={onBrowse} />
      <Button className="download-button" onClick={() => void convert()} disabled={busy || needsTools || !target || compatible.length === 0}>{submitting ? <LoaderCircle className="animate-spin" /> : <ArrowRightLeft />}Convert</Button>
    </div>
  </section>
}
