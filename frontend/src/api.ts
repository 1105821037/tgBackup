export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

function cookie(name: string): string | undefined {
  const prefix = `${encodeURIComponent(name)}=`
  return document.cookie
    .split('; ')
    .find((entry) => entry.startsWith(prefix))
    ?.slice(prefix.length)
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  const csrf = cookie('tg_backup_csrf')
  if (csrf && init.method && init.method !== 'GET') {
    headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  }

  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 60_000)
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers,
      credentials: 'include',
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(504, '请求超时，请检查网络或代理后重试')
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
  if (!response.ok) {
    if (response.status === 401) {
      window.dispatchEvent(new Event('tg-auth-expired'))
    }
    const body = await response.json().catch(() => ({ detail: '请求失败' }))
    const detail = body.detail
    const message = Array.isArray(detail)
      ? detail.map((item) => {
          const key = item.loc?.at?.(-1)
          const label = key === 'username'
            ? '用户名'
            : key === 'password'
              ? '密码'
              : key === 'current_password'
                ? '当前密码'
                : key === 'new_password'
                  ? '新密码'
                  : '字段'
          if (item.type === 'string_too_short') return `${label}至少需要 ${item.ctx?.min_length} 个字符`
          if (item.type === 'string_too_long') return `${label}不能超过 ${item.ctx?.max_length} 个字符`
          if (item.type === 'string_pattern_mismatch') return `${label}格式不正确`
          return item.msg || `${label}格式不正确`
        }).join('；')
      : typeof detail === 'string'
        ? detail
        : '请求失败'
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export interface TelegramStatus {
  state: 'unbound' | 'active' | 'login_required' | 'identity_mismatch'
  immutable_binding: boolean
  telegram_user_id?: number
  entity_id?: number | null
  photo_id?: number | null
  avatar_url?: string | null
  username?: string | null
  display_name?: string
  phone_masked?: string | null
  connection?: 'unbound' | 'connecting' | 'connected' | 'reconnecting' | 'login_required' | 'identity_mismatch' | 'stopped'
  connection_error?: string | null
  connected_at?: string | null
  last_dialog_refresh_at?: string | null
  error?: string
}

export interface UserInfo {
  id: number
  username: string
  is_owner: boolean
  telegram: TelegramStatus | null
}

export interface ManagedUser {
  id: number
  username: string
  is_owner: boolean
  has_telegram: boolean
  created_at: string
}

export interface OverviewActivity {
  id: string
  kind: 'backup' | 'history'
  chat_title: string
  status: string
  message_count: number
  media_count: number
  changed_count: number
  deleted_count: number
  started_at: string
  finished_at?: string | null
}

export interface OverviewSummary {
  account_bound: boolean
  message_count: number
  media_count: number
  media_size_bytes: number
  archive_chat_count: number
  rule_count: number
  active_rule_count: number
  paused_rule_count: number
  running_task_count: number
  attention_task_count: number
  last_completed_at?: string | null
  activities: OverviewActivity[]
}

export interface ChatBackupRule {
  enabled: boolean
  schedule_kind: 'weekly' | 'cron'
  backup_time: string
  weekdays: number[]
  cron_expression: string | null
  media_types: string[]
  history_enabled: boolean
  history_available: boolean
  history_schedule_kind: 'weekly' | 'cron'
  history_time: string
  history_weekdays: number[]
  history_cron_expression: string | null
  history_max_updates: number
  history_start_kind: 'earliest' | 'days_ago'
  history_start_days_ago: number | null
  history_end_kind: 'latest' | 'days_ago'
  history_end_days_ago: number | null
  updated_at?: string
}

export interface TelegramChat {
  peer_id: number
  is_self: boolean
  entity_id?: number | null
  entity_version?: number | null
  photo_id?: number | null
  avatar_url?: string | null
  title: string
  username?: string | null
  kind: 'private' | 'bot' | 'group' | 'supergroup' | 'channel' | 'unknown'
  archived: boolean
  unread_count: number
  unread_mentions_count: number
  last_message_date?: string | null
  rule: ChatBackupRule | null
}

export interface TelegramEntityVersion {
  version: number
  source: string
  observed_at: string
  snapshot: Record<string, unknown>
}

export interface TelegramEntityMetric {
  date: string
  participants_count?: number | null
  online_count?: number | null
  observed_at: string
}

export interface TelegramEntityDetail {
  id: number
  peer_id: number
  telegram_id: number
  kind: 'user' | 'bot' | 'group' | 'supergroup' | 'channel' | 'unknown'
  display_name: string
  username?: string | null
  first_name?: string | null
  last_name?: string | null
  phone_masked?: string | null
  about?: string | null
  is_contact: boolean
  is_verified: boolean
  is_deleted: boolean
  is_scam: boolean
  is_fake: boolean
  access_state: string
  current_version: number
  photo_id?: number | null
  avatars: Partial<Record<'small' | 'big', string>>
  first_observed_at: string
  last_observed_at: string
  last_full_refreshed_at?: string | null
  versions: TelegramEntityVersion[]
  metrics: TelegramEntityMetric[]
}

export interface BackupRunStatus {
  id: number
  trigger: string
  status: string
  start_cursor: number
  end_cursor: number
  fetched_count: number
  stored_count: number
  skipped_count: number
  media_count: number
  error_code?: string | null
  error_message?: string | null
  started_at: string
  finished_at?: string | null
}

export interface BackupRuleItem {
  id: number
  peer_id: number
  is_self: boolean
  chat_title: string
  chat_kind: TelegramChat['kind']
  entity_id?: number | null
  photo_id?: number | null
  avatar_url?: string | null
  message_count: number
  media_count: number
  media_size_bytes: number
  rule: ChatBackupRule
  state: {
    status: string
    last_message_id: number
    last_error_code?: string | null
    last_error?: string | null
    last_started_at?: string | null
    last_completed_at?: string | null
  }
  latest_run?: BackupRunStatus | null
  history_update: {
    enabled: boolean
    status: string
    last_completed_at?: string | null
    latest_run?: {
      id: number
      status: string
      candidate_count: number
      checked_count: number
      changed_count: number
      deleted_count: number
      media_completed_count: number
      error_count: number
    } | null
  }
}

export interface ArchiveChat {
  peer_id: number
  is_self: boolean
  title: string
  kind: TelegramChat['kind']
  shows_sender_profiles: boolean
  username?: string | null
  entity_id?: number | null
  avatar_url?: string | null
  message_count: number
  media_count: number
  last_message: string
  last_message_at?: string | null
  rule_status: 'active' | 'paused' | 'removed' | 'none'
  backup_status: string
  last_backup_at?: string | null
}

export interface ArchiveMessageMedia {
  id: number
  type: string
  mime_type?: string | null
  name?: string | null
  size_bytes: number
  url: string
  download_url: string
}

export interface ArchiveSharedMediaItem extends ArchiveMessageMedia {
  message_id: number
  sent_at?: string | null
  text?: string | null
  content: Record<string, any>
}

export interface ArchiveSharedMediaPage {
  items: ArchiveSharedMediaItem[]
  has_more: boolean
  next_before_id?: number | null
}

export interface ArchiveMessage {
  id: number
  message_id: number
  sender_id?: number | null
  sender?: {
    entity_id: number
    peer_id: number
    kind: string
    name: string
    username?: string | null
    avatar_url?: string | null
  } | null
  origin_sender?: {
    entity_id?: number | null
    peer_id?: number | null
    kind?: string | null
    name?: string | null
    username?: string | null
    avatar_url?: string | null
  } | null
  sent_at?: string | null
  text?: string | null
  content_kind: string
  content: Record<string, any>
  out: boolean
  post: boolean
  post_author?: string | null
  via_bot?: {
    entity_id?: number | null
    peer_id: number
    kind?: string | null
    name?: string | null
    username?: string | null
  } | null
  reply_to_msg_id?: number | null
  reply_preview?: {
    message_id: number
    sender_name?: string | null
    text: string
    is_deleted: boolean
  } | null
  grouped_id?: number | null
  forward_info?: {
    name?: string | null
    date?: string | null
    post_author?: string | null
    from_id?: Record<string, unknown> | null
    saved_from_peer?: Record<string, unknown> | null
    saved_from_id?: Record<string, unknown> | null
    saved_from_name?: string | null
  } | null
  webpage_info?: {
    url: string
    display_url?: string | null
    site_name?: string | null
    title?: string | null
    description?: string | null
    type?: string | null
    duration?: number | null
  } | null
  buttons: Array<Array<{
    text: string
    url?: string | null
    kind?: string | null
  }>>
  entities: Record<string, unknown>[]
  is_deleted: boolean
  is_edited: boolean
  current_version: number
  edit_date?: string | null
  observed_at: string
  metrics: {
    views?: number | null
    forwards?: number | null
    replies?: number | null
    pinned?: boolean
    reactions?: Array<{ reaction?: unknown; count?: number }>
  }
  media: ArchiveMessageMedia[]
}

export interface ArchiveMessagePage {
  items: ArchiveMessage[]
  has_more: boolean
  has_older?: boolean
  has_newer?: boolean
  next_before_id?: number | null
  next_after_id?: number | null
  requested_anchor_id?: number | null
  anchor_id?: number | null
  anchor_found?: boolean | null
}

export interface ArchiveMessageVersion {
  version: number
  text?: string | null
  content_kind: string
  content: Record<string, any>
  is_deleted: boolean
  edit_date?: string | null
  observed_at: string
  entities: Record<string, unknown>[]
  post_author?: string | null
  media: ArchiveMessageMedia[]
}

export interface ArchiveMessageVersions {
  id: number
  message_id: number
  current_version: number
  is_deleted: boolean
  items: ArchiveMessageVersion[]
}
