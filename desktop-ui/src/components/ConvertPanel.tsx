import { useState } from 'react'
import { ArrowRightLeft, CircleAlert, FileImage, FileVideo, Files, Images, LoaderCircle, Music2, Plus, X } from 'lucide-react'
import { Button } from './ui/button'
import { Label } from './ui/label'
import { RadioGroup, RadioGroupItem } from './ui/radio-group'
import { FormatPicker } from './FormatPicker'
import { SaveDestination } from './SaveDestination'
import { bytes } from '../format'
import { formatHint } from '../formats'
import { t } from '../i18n'
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
  return nativeMedia ? t('{format} compatibility', { format: formatName(target) }) : formatName(target)
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
  return [t(type[0].toUpperCase() + type.slice(1)), file.format ? formatName(file.format) : '',
    file.width && file.height ? `${file.width} × ${file.height}` : '', playbackTime(file.duration),
    file.pages && file.pages > 1 ? t(type === 'animation' ? '{count} frames' : '{count} pages', { count: file.pages }) : '', file.size != null ? bytes(file.size) : '',
    file.transparent ? t('Transparent') : '', type === 'video' && file.has_audio === false ? t('No audio') : '',
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
      <h2 id="convert-heading">{t('Convert files')}</h2>
      <Button variant="outline" onClick={onAdd} disabled={busy}><Plus className="size-4" />{t('Add files')}</Button>
    </div>
    {conversion.files.length === 0 ? <div className="conversion-empty">
      <Files className="size-5" aria-hidden="true" />
      <div><p>{t('Add files or drop them here')}</p><span>{t('Images, audio, video and animations. Original files are kept.')}</span></div>
    </div> : <>
      <div className="conversion-selection">
        <span>{t(validFiles.length === 1 ? '{selected} of {count} valid file selected' : '{selected} of {count} valid files selected', { selected: selected.length, count: validFiles.length })}</span>
        <div><Button variant="ghost" size="sm" onClick={toggleAll} disabled={busy || validFiles.length === 0}>{t(selected.length === validFiles.length ? 'Deselect all' : 'Select all')}</Button><Button variant="ghost" size="sm" onClick={onClear} disabled={disabled || submitting}>{t('Clear files')}</Button></div>
      </div>
      <ul className="conversion-files" aria-label={t('Selected files')}>
        {conversion.files.map(file => {
          const valid = !file.error && file.targets.length > 0
          const willSkip = valid && !excluded.has(file.id) && target && !file.targets.includes(target)
          const FileIcon = mediaIcon(file)
          return <li key={file.id} className={`conversion-file ${file.error ? 'conversion-file-invalid' : ''}`}>
            <input type="checkbox" className="file-checkbox" aria-label={t('Select {name}', { name: file.name })} checked={valid && !excluded.has(file.id)} disabled={busy || !valid} onChange={event => selectFile(file.id, event.target.checked)} />
            <FileIcon className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div className="conversion-file-copy"><p title={file.name}>{file.name}</p>
              {file.error ? <span className="inline-error">{t(file.error)}</span> : <span>{fileDescription(file)}</span>}
              {!checking && !file.error && file.targets.length === 0 ? <span>{t('No compatible output formats are available with the installed tools.')}</span> : null}
              {willSkip ? <span className="conversion-file-skip">{t(file.format?.toLowerCase() === target ? 'Already {format} · skipped' : 'Incompatible with {format} · skipped', { format: formatName(target) })}</span> : null}
            </div>
            <Button variant="ghost" size="icon" aria-label={t('Remove {name}', { name: file.name })} title={t('Remove from selection')} disabled={busy} onClick={() => onRemove(file.id)}><X className="size-4" /></Button>
          </li>
        })}
      </ul>
    </>}
    {checking ? <div className="checking-state" role="status"><LoaderCircle className="size-4 animate-spin" />{t('Checking files…')}</div> : null}
    {conversion.error ? <div className="inspection-error" role="alert"><CircleAlert className="size-4 shrink-0" /><span>{t(conversion.error)}</span></div> : null}
    {selected.length > 0 && !checking ? <div className="conversion-options">
      <FormatPicker id="conversion-format" label={t('Convert to')} value={target} disabled={busy} describedBy="conversion-compatibility"
        onChange={value => setPreference({ target: value, selection: selectionKey })}
        groups={groups.map(group => ({ label: t(group.label), choices: group.formats.map(format => {
          const count = selected.filter(file => file.targets.includes(format)).length
          return { id: format, label: targetLabel(format, selected), detail: count < selected.length ? `${count}/${selected.length}` : undefined }
        }) }))} />
      <p id="conversion-compatibility" className="field-help" role="status">{target
        ? [formatHint(target, animationTargets.has(target)), selected.length > 1
          ? t('{count} of {total} selected files can convert to {format}.', { count: compatible.length, total: selected.length, format: formatName(target) }) : '',
          skipped > 0 ? t(skipped === 1 ? 'The other file is already that format or incompatible and will be skipped.' : 'The other {skipped} files are already that format or incompatible and will be skipped.', { skipped }) : '',
        ].filter(Boolean).join(' ')
        : t('Choose an output format before converting this selection.')}</p>
      {targetNotes.map(note => <p key={note} className="field-help">{t(note)}</p>)}
      {needsTools ? <p className="field-help">{t('Install FFmpeg to convert this selection.')}</p> : null}
      {flattensAlpha && target ? <div className="alpha-background"><Label>{t('Background')}</Label>
        <RadioGroup value={background} onValueChange={value => setBackground(value as '#FFFFFF' | '#000000')} aria-label={t('{format} background', { format: formatName(target) })} orientation="horizontal" className="flex flex-wrap gap-6">
          <Label htmlFor="background-white" className="background-choice"><RadioGroupItem id="background-white" value="#FFFFFF" disabled={busy} />{t('White')}</Label>
          <Label htmlFor="background-black" className="background-choice"><RadioGroupItem id="background-black" value="#000000" disabled={busy} />{t('Black')}</Label>
        </RadioGroup><p>{t(target === 'gif' ? 'GIF keeps fully transparent pixels. Partially transparent areas will use this background.' : 'Transparent areas will use this background for this output.')}</p>
      </div> : null}
    </div> : null}
    <div className="download-footer">
      <SaveDestination output={output} disabled={disabled} onBrowse={onBrowse} />
      <Button className="download-button" onClick={() => void convert()} disabled={busy || needsTools || !target || compatible.length === 0}>{submitting ? <LoaderCircle className="animate-spin" /> : <ArrowRightLeft />}{t('Convert')}</Button>
    </div>
  </section>
}
