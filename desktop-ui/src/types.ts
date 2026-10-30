import type { Language } from './i18n'
export type Theme = 'system' | 'dark' | 'light'
export interface Settings { output: string; max_height: number | null; max_size: number | null }
export interface MediaOption { id: string; label: string; description: string; recommended: boolean }
export interface MediaKind { id: string; label: string; options: MediaOption[] }
export interface MediaAsset { id: string; title: string; description: string; thumbnail: string | null; kinds: MediaKind[]; default_kind: string | null }
export interface Inspection {
  status: 'idle' | 'checking' | 'ready' | 'failed'; id: string | null; url: string; error: string | null; assets: MediaAsset[]
}
export type ConversionFormat = string
export type ImageFormat = ConversionFormat
export interface LocalFile {
  id: string; name: string; format: string | null; width: number | null; height: number | null;
  size: number | null; transparent: boolean; targets: ConversionFormat[]; error: string | null;
  media_type?: 'image' | 'audio' | 'video' | 'animation'; duration?: number | null; animated?: boolean;
  pages?: number; has_audio?: boolean; target_notes?: Record<string, string>; requires_tools?: boolean;
  background_targets?: ConversionFormat[]
}
export type LocalImage = LocalFile
export interface Conversion {
  status: 'idle' | 'checking' | 'ready' | 'failed'; id: string | null; files: LocalFile[]; error: string | null
}
export type TaskStatus = 'queued' | 'checking' | 'downloading' | 'converting' | 'finalizing' | 'completed' | 'existing' | 'failed' | 'cancelled' | 'cancelling'
export interface DownloadTask {
  id: string; url: string; title: string; kind: string | null; format: string; status: TaskStatus;
  progress: number | null; downloaded_bytes: number; total_bytes: number | null; speed: number | null;
  eta: number | null; path: string | null; error: string | null;
  action?: 'download' | 'convert'; source_format?: string | null; can_convert?: boolean;
  operation?: 'preparing' | 'encoding' | 'copying' | null; thumbnail?: string | null
}
export interface UpdateState {
  status: 'unsupported' | 'idle' | 'checking' | 'current' | 'available' | 'downloading' | 'ready' | 'failed'
  version: string | null; progress: number | null; error: string | null
}
export interface DesktopState {
  version: string; settings: Settings; inspection: Inspection; conversion: Conversion; tasks: DownloadTask[]; notice: string | null;
  resume_count: number; update?: UpdateState | null; theme?: Theme; language?: Language; tools: { status: 'ready' | 'missing' | 'installing' | 'failed'; message?: string; error?: string | null; ffmpeg?: boolean; javascript?: boolean }
}
export type ApiResult = { ok: true; state: DesktopState } | { ok: false; error: string }
export interface DesktopApi {
  get_state(): Promise<ApiResult>;
  check_url(url: string): Promise<ApiResult>;
  cancel_check(): Promise<ApiResult>;
  enqueue(inspectionId: string, assetId: string, kind: string, format: string): Promise<ApiResult>;
  enqueue_links(text: string): Promise<ApiResult>;
  cancel_task(id: string): Promise<ApiResult>;
  retry_task(id: string): Promise<ApiResult>;
  remove_finished(): Promise<ApiResult>;
  save_settings(settings: Settings): Promise<ApiResult>;
  browse_folder(): Promise<ApiResult>;
  open_folder(taskId?: string | null): Promise<ApiResult>;
  clear_resume(): Promise<ApiResult>;
  install_tools(): Promise<ApiResult>;
  select_conversion_files(): Promise<ApiResult>;
  remove_conversion_file(id: string): Promise<ApiResult>;
  clear_conversion_files(): Promise<ApiResult>;
  enqueue_conversions(ids: string[], target: ConversionFormat, background: '#FFFFFF' | '#000000'): Promise<ApiResult>;
  prepare_conversion(taskId: string): Promise<ApiResult>;
  set_theme(theme: Theme): Promise<ApiResult>;
  set_language(language: Language): Promise<ApiResult>;
  check_for_updates(): Promise<ApiResult>;
  download_update(): Promise<ApiResult>;
  install_update(): Promise<ApiResult>;
  dismiss_update(): Promise<ApiResult>;
  open_update_page(): Promise<ApiResult>;
  window_action?(action: WindowAction): Promise<{ ok: true } | { ok: false; error: string }>;
}
export type ResizeEdge = 'top' | 'bottom' | 'left' | 'right' | 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right'
export type WindowAction = 'drag' | `resize-${ResizeEdge}` | 'minimize' | 'maximize' | 'close'
declare global { interface Window { pywebview?: { api: DesktopApi } } }
export const initialState: DesktopState = {
  version: '', settings: { output: '', max_height: null, max_size: null },
  inspection: { status: 'idle', id: '', url: '', error: null, assets: [] }, tasks: [], notice: null,
  conversion: { status: 'idle', id: null, files: [], error: null },
  resume_count: 0, tools: { status: 'ready', message: '' },
}
