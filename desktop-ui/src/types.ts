export interface Settings { output: string; max_height: number | null; max_size: number | null }
export interface MediaOption { id: string; label: string; description: string; recommended: boolean }
export interface MediaKind { id: string; label: string; options: MediaOption[] }
export interface MediaAsset { id: string; title: string; description: string; thumbnail: string | null; kinds: MediaKind[]; default_kind: string | null }
export interface Inspection {
  status: 'idle' | 'checking' | 'ready' | 'failed'; id: string | null; url: string; error: string | null; assets: MediaAsset[]
}
export type TaskStatus = 'queued' | 'checking' | 'downloading' | 'finalizing' | 'completed' | 'existing' | 'failed' | 'cancelled' | 'cancelling'
export interface DownloadTask {
  id: string; url: string; title: string; kind: string | null; format: string; status: TaskStatus;
  progress: number | null; downloaded_bytes: number; total_bytes: number | null; speed: number | null;
  eta: number | null; path: string | null; error: string | null
}
export interface DesktopState {
  version: string; settings: Settings; inspection: Inspection; tasks: DownloadTask[]; notice: string | null;
  resume_count: number; tools: { status: 'ready' | 'missing' | 'installing' | 'failed'; message?: string; error?: string | null; ffmpeg?: boolean; javascript?: boolean }
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
}
declare global { interface Window { pywebview?: { api: DesktopApi } } }
export const initialState: DesktopState = {
  version: '', settings: { output: '', max_height: null, max_size: null },
  inspection: { status: 'idle', id: '', url: '', error: null, assets: [] }, tasks: [], notice: null,
  resume_count: 0, tools: { status: 'ready', message: '' },
}
