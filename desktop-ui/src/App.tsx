import { useEffect, useState } from 'react'
import * as RadioGroupPrimitive from '@radix-ui/react-radio-group'
import { ArrowDownToLine, ArrowRight, ArrowRightLeft, CircleAlert, FileImage, FileVideo, Link2, LoaderCircle, Music2, Upload, X } from 'lucide-react'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Label } from './components/ui/label'
import { RadioGroup, RadioGroupItem } from './components/ui/radio-group'
import { BatchDialog } from './components/BatchDialog'
import { SettingsDialog } from './components/SettingsDialog'
import { ThemeToggle, useThemeClass } from './components/ThemeToggle'
import { LanguageToggle } from './components/LanguageToggle'
import { WindowControls, dragFromHeader, maximizeFromHeader } from './components/TitleBar'
import { TaskList } from './components/TaskList'
import { ConvertPanel } from './components/ConvertPanel'
import { FormatPicker, type FormatChoice, type FormatGroup } from './components/FormatPicker'
import { SaveDestination } from './components/SaveDestination'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './components/ui/tabs'
import { useDesktop } from './use-desktop'
import { normalizeUrlInput, sourceHost } from './format'
import { formatHint, formatName } from './formats'
import { resolveLanguage, setLanguage, t } from './i18n'
import type { MediaAsset, MediaKind } from './types'

interface Selection { inspection: string | null; asset: string; kind: string; option: string }
const thumbnailPattern = /^data:image\/(?:png|jpeg|webp);base64,[a-z\d+/=\s]+$/i
const categoryLabels: Record<string, string> = { video: 'Video', audio: 'Audio', image: 'Image', animation: 'Animation' }
function mediaIcon(kind: string) { return kind === 'audio' ? Music2 : kind === 'image' ? FileImage : FileVideo }
function defaultSelection(inspection: string | null, asset: MediaAsset): Selection {
  const kind = asset.kinds.find(item => item.id === asset.default_kind) ?? asset.kinds[0]
  return { inspection, asset: asset.id, kind: kind?.id ?? '', option: kind?.options.find(option => option.recommended)?.id ?? kind?.options[0]?.id ?? '' }
}
// The service labels each output with its category ("MP4 video", "GIF animation").
function optionCategory(kind: MediaKind, label: string) {
  const category = label.split(' ').at(-1) ?? ''
  return category in categoryLabels ? category : kind.id
}
function optionGroups(kind: MediaKind): FormatGroup[] {
  const groups = new Map<string, FormatChoice[]>()
  for (const option of kind.options) {
    const category = option.id === 'original' ? kind.id : optionCategory(kind, option.label)
    const choice = option.id === 'original'
      ? { id: option.id, label: t('Original'), detail: /\(([A-Z\d]+)\)/.exec(option.description)?.[1] }
      : { id: option.id, label: formatName(option.id) }
    groups.set(category, [...groups.get(category) ?? [], choice])
  }
  return [...groups].map(([category, choices], index) => ({ label: index === 0 ? undefined : t(categoryLabels[category] ?? category), choices }))
}
function hasThumbnail(asset: MediaAsset) { return Boolean(asset.thumbnail && thumbnailPattern.test(asset.thumbnail)) }

export default function App() {
  const { state, connected, error, pending, invoke, dismissError } = useDesktop()
  const theme = state.theme ?? 'system'
  useThemeClass(theme)
  setLanguage(resolveLanguage(state.language))
  const [tab, setTab] = useState('download')
  const [url, setUrl] = useState('')
  const [checkedDraft, setCheckedDraft] = useState<string | null>(null)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [downloadBusy, setDownloadBusy] = useState(false)
  const [dragging, setDragging] = useState<'files' | 'link' | null>(null)
  const normalizedUrl = normalizeUrlInput(url)
  const currentInspection = checkedDraft === normalizedUrl && state.inspection.url === normalizedUrl
  const checking = currentInspection && state.inspection.status === 'checking'
  const ready = currentInspection && state.inspection.status === 'ready'
  const inspectionError = currentInspection && state.inspection.status === 'failed' ? state.inspection.error : null
  const assets = ready ? state.inspection.assets : []
  const selectedAsset = selection?.inspection === state.inspection.id ? assets.find(asset => asset.id === selection.asset) : null
  // A page with alternatives requires an explicit asset choice; a single item needs no extra step.
  const asset = selectedAsset ?? (assets.length === 1 ? assets[0] : null)
  const chosen = asset ? selection?.inspection === state.inspection.id && selection.asset === asset.id ? selection : defaultSelection(state.inspection.id, asset) : null
  const kind = asset?.kinds.find(item => item.id === chosen?.kind) ?? asset?.kinds[0]
  const option = kind?.options.find(item => item.id === chosen?.option) ?? kind?.options.find(item => item.recommended) ?? kind?.options[0]
  const disconnected = !connected
  const busy = pending != null
  const installing = state.tools.status === 'installing'
  const toolsReady = state.tools.status === 'ready'
  const showToolsNotice = tab === 'download' ? !toolsReady : state.tools.ffmpeg === false
  const toolsMessage = tab === 'convert' && !installing && state.tools.status !== 'failed'
    ? t('Install FFmpeg for audio and video conversion. Images can still be converted offline.')
    : state.tools.message ? t(state.tools.message) : state.tools.error ? t(state.tools.error) : t(installing ? 'Installing media tools…' : 'Install media tools to enable downloads. Local image conversion is ready to use.')
  const canDownload = connected && toolsReady && ready && asset && kind && option && !downloadBusy && !busy
  const downloadable = assets.filter(item => item.kinds.some(entry => entry.options.length > 0))
  const Icon = mediaIcon(kind?.id ?? '')
  const animation = Boolean(kind && option && option.id !== 'original' && optionCategory(kind, option.label) === 'animation')
  const hint = option ? option.id === 'original' ? t('Keeps the file exactly as published.') : formatHint(option.id, animation) : ''

  async function checkUrl(value: string) {
    setCheckedDraft(value)
    setSelection(null)
    await invoke('check_url', value)
  }
  async function check(event: React.FormEvent) {
    event.preventDefault()
    if (!normalizedUrl || !connected || busy || !toolsReady) return
    await checkUrl(normalizedUrl)
  }
  function editUrl(value: string) {
    setUrl(value)
    setCheckedDraft(null)
    setSelection(null)
    dismissError()
  }
  async function download() {
    if (!canDownload || !asset || !kind || !option || !state.inspection.id) return
    setDownloadBusy(true)
    await invoke('enqueue', state.inspection.id, asset.id, kind.id, option.id)
    setDownloadBusy(false)
  }
  async function downloadAll() {
    if (!connected || !toolsReady || !ready || downloadBusy || busy || !state.inspection.id) return
    setDownloadBusy(true)
    for (const item of downloadable) {
      const choice = defaultSelection(state.inspection.id, item)
      if (!await invoke('enqueue', state.inspection.id, item.id, choice.kind, choice.option)) break
    }
    setDownloadBusy(false)
  }
  function changeKind(value: string) {
    if (!asset) return
    const nextKind = asset.kinds.find(item => item.id === value)
    if (nextKind) setSelection({ inspection: state.inspection.id, asset: asset.id, kind: value, option: nextKind.options.find(item => item.recommended)?.id ?? nextKind.options[0]?.id ?? '' })
  }
  const activeCount = state.tasks.filter(task => ['queued', 'checking', 'downloading', 'converting', 'finalizing', 'cancelling'].includes(task.status)).length

  async function prepareConversion(id: string) {
    const result = await invoke('prepare_conversion', id)
    if (result) setTab('convert')
  }

  // Links dropped anywhere are checked; files go to the converter (the desktop host reads their paths).
  const dropLink = (value: string) => {
    const link = normalizeUrlInput(value.split(/\r?\n/).find(line => line.trim() && !line.startsWith('#')) ?? '')
    if (!/^https?:\/\//i.test(link) || !connected || !toolsReady) return
    setTab('download')
    setUrl(link)
    void checkUrl(link)
  }
  useEffect(() => {
    const kindOf = (event: DragEvent) => {
      const types = [...event.dataTransfer?.types ?? []]
      return types.includes('Files') ? 'files' : types.includes('text/uri-list') || types.includes('text/plain') ? 'link' : null
    }
    const over = (event: DragEvent) => {
      const kind = kindOf(event)
      if (!kind) return
      event.preventDefault()
      setDragging(kind)
    }
    const leave = (event: DragEvent) => { if (!event.relatedTarget) setDragging(null) }
    const drop = (event: DragEvent) => {
      const kind = kindOf(event)
      setDragging(null)
      if (!kind) return
      event.preventDefault()
      if (kind === 'files') setTab('convert')
      else dropLink(event.dataTransfer?.getData('text/uri-list') || event.dataTransfer?.getData('text/plain') || '')
    }
    window.addEventListener('dragover', over)
    window.addEventListener('dragleave', leave)
    window.addEventListener('drop', drop)
    return () => { window.removeEventListener('dragover', over); window.removeEventListener('dragleave', leave); window.removeEventListener('drop', drop) }
  })

  return <Tabs value={tab} onValueChange={setTab} className="app-shell">
    <header className={connected ? 'app-header with-window-controls' : 'app-header'} onMouseDown={dragFromHeader} onDoubleClick={maximizeFromHeader}>{connected ? <WindowControls /> : null}
      <div className="app-header-main"><h1>Media Toolkit</h1>
        <TabsList aria-label={t('Media tools')}><TabsTrigger value="download"><ArrowDownToLine className="size-4" />{t('Download')}</TabsTrigger><TabsTrigger value="convert"><ArrowRightLeft className="size-4" />{t('Convert')}</TabsTrigger></TabsList>
      </div>
      <div className="app-header-actions">
        <LanguageToggle language={state.language ?? 'system'} disabled={disconnected} onChange={value => void invoke('set_language', value)} />
        <ThemeToggle theme={theme} disabled={disconnected} onChange={value => void invoke('set_theme', value)} />
        <SettingsDialog state={state} disabled={disconnected} busy={busy} onSave={value => invoke('save_settings', value)} onClearResume={() => invoke('clear_resume')} />
      </div>
    </header>

    {!connected || error || state.notice || showToolsNotice ? <div className="app-notices">
    {!connected ? <div className="notice" role="status"><CircleAlert className="size-4 shrink-0" /><span>{t('Waiting for the desktop connection. Open this app with Media Toolkit.')}</span></div> : null}
    {error ? <div className="notice notice-error" role="alert"><CircleAlert className="size-4 shrink-0" /><span>{error}</span><Button variant="ghost" size="icon" aria-label={t('Dismiss error')} onClick={dismissError}><X /></Button></div> : null}
    {state.notice ? <div className="notice" role="status"><span>{t(state.notice)}</span></div> : null}
    {connected && showToolsNotice ? <div className="notice tools-notice" role="status"><CircleAlert className="size-4 shrink-0" /><span>{toolsMessage}</span><Button variant="outline" size="sm" disabled={installing || busy} onClick={() => void invoke('install_tools')}>{installing ? <LoaderCircle className="animate-spin" /> : null}{t(state.tools.status === 'failed' ? 'Retry installation' : 'Install tools')}</Button></div> : null}
    </div> : null}

    <main className="app-content">
      <TabsContent value="download" forceMount hidden={tab !== 'download'} className="tool-panel">
      <section className="download-card" aria-labelledby="new-download-heading">
        <div className="section-heading new-download-heading"><h2 id="new-download-heading">{t('New download')}</h2><BatchDialog disabled={disconnected || !toolsReady} busy={busy || installing} onEnqueue={text => invoke('enqueue_links', text)} /></div>
        <form onSubmit={check} className="url-form">
          <Label htmlFor="media-url" className="sr-only">{t('Media URL')}</Label>
          <div className="url-input"><Link2 className="size-[18px]" aria-hidden="true" /><Input id="media-url" value={url} onChange={event => editUrl(event.target.value)} placeholder={t('Paste a video, audio or image link')} autoComplete="off" spellCheck={false} disabled={disconnected} maxLength={16384} /></div>
          {checking ? <Button type="button" variant="outline" onClick={() => void invoke('cancel_check')} disabled={busy}><LoaderCircle className="animate-spin" /><span>{t('Cancel')}</span></Button> : <Button type="submit" variant="secondary" disabled={!normalizedUrl || disconnected || busy || !toolsReady}>{t('Check')}<ArrowRight /></Button>}
        </form>
        {checking ? <div className="checking-state" role="status"><LoaderCircle className="size-4 animate-spin" />{t('Checking the link and available media…')}</div> : null}
        {inspectionError ? <div className="inspection-error" role="alert"><CircleAlert className="size-[18px] shrink-0" /><div><p>{t('Could not check this link')}</p><span>{t(inspectionError)}</span></div></div> : null}
        {ready && assets.length > 1 ? <div className="asset-picker">
          <div className="asset-picker-heading"><span id="asset-picker-label">{t('{count} items at this link. Choose one, or download all.', { count: assets.length })}</span>
            <Button variant="outline" size="sm" disabled={!connected || !toolsReady || downloadBusy || busy || downloadable.length === 0} onClick={() => void downloadAll()}><ArrowDownToLine />{t('Download all ({count})', { count: downloadable.length })}</Button></div>
          <RadioGroupPrimitive.Root value={asset?.id ?? ''} onValueChange={value => { const next = assets.find(item => item.id === value); if (next) setSelection(defaultSelection(state.inspection.id, next)) }} aria-labelledby="asset-picker-label" className="asset-grid" loop>
            {assets.map((item, index) => { const ItemIcon = mediaIcon(item.default_kind ?? ''); return <RadioGroupPrimitive.Item key={item.id} value={item.id} className="asset-card" aria-label={`${index + 1}. ${item.title}${item.description ? ` · ${t(item.description)}` : ''}`}>
              {hasThumbnail(item) ? <img src={item.thumbnail ?? ''} alt="" /> : <span className="asset-card-placeholder"><ItemIcon className="size-6" aria-hidden="true" /></span>}
              <span className="asset-card-index" aria-hidden="true">{index + 1}</span>
              {item.description ? <small aria-hidden="true">{t(item.description)}</small> : null}
            </RadioGroupPrimitive.Item> })}
          </RadioGroupPrimitive.Root>
        </div> : null}
        {ready && asset ? <div className="inspected-result">
          <div className="media-summary">
            {hasThumbnail(asset) ? <img className="media-thumbnail" src={asset.thumbnail ?? ''} alt="" /> : <div className="media-thumbnail media-placeholder"><Icon className="size-7" aria-hidden="true" /></div>}
            <div className="media-description"><span className="source-name" title={sourceHost(normalizedUrl)}>{sourceHost(normalizedUrl)}</span><h3 title={asset.title}>{asset.title}</h3>{asset.description ? <p>{t(asset.description)}</p> : null}</div>
          </div>
          {kind && option ? <div className="download-options">
            {asset.kinds.length > 1 ? <RadioGroup value={kind.id} onValueChange={changeKind} aria-label={t('Media type')} orientation="horizontal" className="kind-options flex flex-wrap gap-2">
              {asset.kinds.map(item => { const KindIcon = mediaIcon(item.id); return <Label key={item.id} className={`kind-option ${kind.id === item.id ? 'selected' : ''}`} htmlFor={`kind-${item.id}`}><RadioGroupItem id={`kind-${item.id}`} value={item.id} className="sr-only" /><KindIcon className="size-4" />{t(item.label)}</Label> })}
            </RadioGroup> : null}
            <FormatPicker id="download-format" label={t('Save as')} groups={optionGroups(kind)} value={option.id} disabled={disconnected || busy || downloadBusy}
              describedBy={hint ? 'download-format-description' : undefined} onChange={value => setSelection({ inspection: state.inspection.id, asset: asset.id, kind: kind.id, option: value })} />
            {hint ? <p id="download-format-description" className="field-help">{hint}</p> : null}
          </div> : <p className="url-hint px-0">{t('No format fits current limits. Change settings and check again.')}</p>}
        </div> : null}
        {!checking && !ready && !inspectionError ? <div className="drop-hint"><Upload className="size-5" aria-hidden="true" /><div><p>{t('Paste a link, or drop a link or file here')}</p><span>{t('Videos, audio and images from most sites. Dropped files open in Convert.')}</span></div></div> : null}
        {ready && assets.length === 0 ? <p className="url-hint">{t('No downloadable media was found at this link.')}</p> : null}
        <div className="download-footer">
          <SaveDestination output={state.settings.output} disabled={disconnected || busy} onBrowse={() => void invoke('browse_folder')} />
          <Button className="download-button" onClick={() => void download()} disabled={!canDownload}>{downloadBusy ? <LoaderCircle className="animate-spin" /> : <ArrowDownToLine />}{t('Download')}</Button>
        </div>
      </section>
      </TabsContent>
      <TabsContent value="convert" forceMount hidden={tab !== 'convert'} className="tool-panel">
        <ConvertPanel conversion={state.conversion} output={state.settings.output} disabled={disconnected || busy} ffmpegReady={state.tools.ffmpeg !== false} onAdd={() => void invoke('select_conversion_files')} onRemove={id => void invoke('remove_conversion_file', id)} onClear={() => void invoke('clear_conversion_files')} onBrowse={() => void invoke('browse_folder')} onConvert={(ids, target, background) => invoke('enqueue_conversions', ids, target, background)} />
      </TabsContent>
      <TaskList tasks={state.tasks} disabled={disconnected || busy} retryDisabled={!toolsReady} onCancel={id => void invoke('cancel_task', id)} onRetry={id => void invoke('retry_task', id)} onOpen={id => void invoke('open_folder', id)} onClear={() => void invoke('remove_finished')} onConvert={id => void prepareConversion(id)} />
    </main>
    <footer className="app-footer"><span>{activeCount > 0 ? t(activeCount === 1 ? '{count} active task' : '{count} active tasks', { count: activeCount }) : t('Ready when you are')}</span><span>{state.version ? `v${state.version}` : t('Desktop')}</span></footer>
    {dragging ? <div className="drop-overlay" aria-hidden="true"><div><Upload className="size-6" /><p>{t(dragging === 'files' ? 'Drop files to convert them' : 'Drop the link to check it')}</p></div></div> : null}
  </Tabs>
}
