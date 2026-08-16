<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  api,
  type ArchiveChat,
  type ArchiveMessage,
  type ArchiveMessageMedia,
  type ArchiveMessagePage,
  type ArchiveMessageSearchItem,
  type ArchiveMessageSearchPage,
  type ArchiveSharedMediaItem,
  type ArchiveSharedMediaPage,
  type ArchiveMessageVersions,
  type TelegramEntityDetail,
} from '../api'
import type { RealtimeEvent } from '../realtime'
import { calculateAlbumLayout, type AlbumLayout } from '../utils/albumLayout'
import { serverDate } from '../utils/dateTime'
import {
  buildArchiveMessagePresentation,
  isAudioMedia,
  isImageMedia,
  isVideoMedia,
  type ArchiveMessagePresentation,
} from '../utils/archiveMessagePresentation'
import SavedMessagesIcon from './SavedMessagesIcon.vue'
import ArchiveSpecialContent from './ArchiveSpecialContent.vue'
import TelegramAudioPlayer from './TelegramAudioPlayer.vue'
import TgsSticker from './TgsSticker.vue'
import TelegramCustomEmoji from './TelegramCustomEmoji.vue'

const emit = defineEmits<{ 'mobile-detail-change': [open: boolean] }>()

const chats = ref<ArchiveChat[]>([])
const selected = ref<ArchiveChat | null>(null)
const messages = ref<ArchiveMessage[]>([])
const query = ref('')
const loadingChats = ref(true)
const loadingMessages = ref(false)
const loadingOlder = ref(false)
const loadingNewer = ref(false)
const error = ref('')
const hasOlder = ref(false)
const hasNewer = ref(false)
const nextBeforeId = ref<number | null>(null)
const nextAfterId = ref<number | null>(null)
const infoOpen = ref(true)
type SharedMediaTab = 'media' | 'documents' | 'links' | 'audio' | 'voice' | 'gif'
const sharedMediaTab = ref<SharedMediaTab>('media')
const sharedMediaScrollTops: Partial<Record<SharedMediaTab, number>> = {}
const sharedMediaByIndex = ref<Record<number, ArchiveSharedMediaItem>>({})
const sharedMediaItems = computed(() => Object.entries(sharedMediaByIndex.value)
  .sort(([left], [right]) => Number(left) - Number(right))
  .map(([, item]) => item))
const sharedMediaTotal = ref(0)
const sharedMediaLoading = ref(false)
const sharedMediaLoadingMore = ref(false)
const sharedMediaHasMore = ref(false)
const sharedMediaError = ref('')
const infoScroll = ref<HTMLElement | null>(null)
const chatList = ref<HTMLElement | null>(null)
const sharedMediaVirtual = ref<HTMLElement | null>(null)
const sharedMediaViewportWidth = ref(300)
const sharedMediaWindow = ref({ start: 0, end: -1 })
const sharedScrollbar = ref<HTMLElement | null>(null)
const sharedScrollbarThumb = ref<HTMLElement | null>(null)
const sharedScrollbarVisible = ref(false)
const sharedScrollbarDragging = ref(false)
const sharedScrollbarBubbleVisible = ref(false)
const sharedScrollbarThumbTop = ref(0)
const sharedScrollbarBubbleTop = ref(0)
const sharedScrollbarLabel = ref('')
const sharedMediaKinds = ref({ photo: true, video: true })
const sharedContextMenu = ref<{ item: ArchiveSharedMediaItem; x: number; y: number } | null>(null)
const sharedContextMenuButton = ref<HTMLButtonElement | null>(null)
const mobileThreadOpen = ref(false)
const mobileNavigationDirection = ref<'forward' | 'back' | null>(null)
const mobileTransitionDirection = ref<'forward' | 'back'>('forward')
const mobileTransitionFrom = ref<ArchiveMobileLayer>('list')
const newMessageCount = ref(0)
const messageViewport = ref<HTMLElement | null>(null)
const messagePositionReady = ref(false)
const messageSearchOpen = ref(false)
const messageSearchQuery = ref('')
const messageSearchResults = ref<ArchiveMessageSearchItem[]>([])
const messageSearchTotal = ref(0)
const messageSearchActiveIndex = ref(-1)
const messageSearchLoading = ref(false)
const messageSearchLoadingMore = ref(false)
const messageSearchError = ref('')
const messageSearchInput = ref<HTMLInputElement | null>(null)
let olderLoadNeedsFreshGesture = false
let lastUpwardWheelAt = 0
let messageSearchTimer = 0
let messageSearchRequestId = 0
const WHEEL_GESTURE_IDLE_MS = 320
const messageViewportWidth = ref(480)
const mediaDimensions = ref<Record<number, { width: number; height: number }>>({})
const mediaDurations = ref<Record<number, number>>({})
const mediaRemaining = ref<Record<number, number>>({})
const mediaPosters = ref<Record<number, string>>({})
const sharedMediaPosters = ref<Record<number, string>>({})
const failedPreviewIds = ref<Set<number>>(new Set())
const stickyDateLabel = ref('')
const viewerIndex = ref<number | null>(null)
const viewerZoom = ref(1)
const viewerPan = ref({ x: 0, y: 0 })
const viewerSwipeX = ref(0)
const viewerDragging = ref(false)
const viewerStage = ref<HTMLElement | null>(null)
const viewerMedia = ref<HTMLImageElement | HTMLVideoElement | null>(null)
const revealedMediaSpoilers = ref<Set<number>>(new Set())
const revealedTextSpoilers = ref<Set<string>>(new Set())
const alwaysShowSpoilers = ref(window.localStorage.getItem('tg-backup-always-show-spoilers') === 'true')
const historyMessage = ref<ArchiveMessage | null>(null)
const historyData = ref<ArchiveMessageVersions | null>(null)
const historyLoading = ref(false)
const historyError = ref('')
const senderProfile = ref<{
  entity_id?: number | null
  peer_id?: number | null
  kind?: string | null
  name: string
  username?: string | null
  avatar_url?: string | null
} | null>(null)
const senderProfileDetail = ref<TelegramEntityDetail | null>(null)
const senderProfileLoading = ref(false)
const senderProfileError = ref('')
const senderProfileCloseButton = ref<HTMLButtonElement | null>(null)
const jumpNotice = ref('')
let senderProfileRequestId = 0
let sharedMediaRequestId = 0
let senderProfileTrigger: HTMLElement | null = null
let viewerDragOrigin: { pointerId: number; clientX: number; clientY: number; startedAt: number; x: number; y: number; mode: 'pan' | 'swipe' } | null = null
let suppressViewerClick = false
let viewportObserver: ResizeObserver | null = null
let infoScrollObserver: ResizeObserver | null = null
let inlineVideoObserver: IntersectionObserver | null = null
let sharedScrollbarFrame = 0
let archiveSnapshotTimer = 0
let archiveNavigationSequence = 0
let lastAppliedArchiveEntry = ''
let activeArchiveStackIndex = 0
let sharedScrollbarBubbleTimer = 0
let sharedScrollbarDrag: { pointerId: number; offsetY: number } | null = null
let sharedLongPressTimer = 0
let sharedLongPress: {
  item: ArchiveSharedMediaItem
  pointerId: number
  startX: number
  startY: number
  viewer: boolean
} | null = null
let sharedLongPressSuppression: { mediaId: number; expiresAt: number } | null = null
let sharedMediaLoadedPages = new Set<number>()
let sharedMediaPendingPages = new Set<number>()
let sharedMediaFailedPages = new Set<number>()
const visibleInlineVideos = new Set<HTMLVideoElement>()
const MESSAGE_WINDOW_LIMIT = 240
const SHARED_MEDIA_PAGE_SIZE = 60
const SHARED_MEDIA_BUFFER_VIEWPORTS = 2

interface ArchiveEntry {
  key: string
  items: ArchiveMessage[]
  first: ArchiveMessage
  last: ArchiveMessage
  media: ArchiveMessageMedia[]
  text: string
}

type ArchiveMobileLayer = 'list' | 'thread' | 'info'

interface ArchiveMobileRoute {
  layer: ArchiveMobileLayer
  peerId: number | null
}

interface ArchiveInfoSnapshot {
  tab: SharedMediaTab
  photo: boolean
  video: boolean
  scrollTop: number
  anchorIndex: number | null
  anchorOffset: number
  tabScrollTops: Partial<Record<SharedMediaTab, number>>
}

interface ArchiveThreadSnapshot {
  messageId: number | null
  offset: number
  atBottom: boolean
}

interface ArchiveListSnapshot {
  scrollTop: number
  query: string
}

type ArchiveOverlaySnapshot =
  | { type: 'viewer'; mediaId: number; origin: 'messages' | 'shared' }
  | { type: 'history'; messageId: number }
  | { type: 'profile'; messageId: number }
  | { type: 'search'; query: string }

interface ArchiveHistoryFrame {
  entryId: string
  stackIndex: number
  layer: ArchiveMobileLayer
  peerId: number | null
  info?: ArchiveInfoSnapshot
  thread?: ArchiveThreadSnapshot
  list?: ArchiveListSnapshot
  overlay?: ArchiveOverlaySnapshot
}

function isMobileArchive() {
  return window.matchMedia('(max-width: 760px)').matches
}

function archiveRouteFromLocation(): ArchiveMobileRoute {
  const match = window.location.hash.match(/^#\/archive(?:\/chat\/(-?\d+)(?:\/(info))?)?\/?$/)
  if (!match?.[1]) return { layer: 'list', peerId: null }
  const peerId = Number(match[1])
  if (!Number.isSafeInteger(peerId)) return { layer: 'list', peerId: null }
  return { layer: match[2] === 'info' ? 'info' : 'thread', peerId }
}

function archiveRouteHash(layer: ArchiveMobileLayer, peerId = selected.value?.peer_id) {
  if (layer === 'list' || !peerId) return '#/archive'
  return `#/archive/chat/${peerId}${layer === 'info' ? '/info' : ''}`
}

function currentArchiveFrame(): ArchiveHistoryFrame | null {
  const frame = window.history.state?.tgArchive
  return frame && typeof frame === 'object' ? frame as ArchiveHistoryFrame : null
}

function archiveHistoryState(frame: ArchiveHistoryFrame) {
  const current = window.history.state
  return {
    ...(current && typeof current === 'object' ? current : {}),
    tgArchiveDepth: frame.stackIndex,
    tgArchive: frame,
  }
}

function makeArchiveEntryId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function writeArchiveRoute(
  layer: ArchiveMobileLayer,
  replace = false,
  snapshot: Pick<ArchiveHistoryFrame, 'info' | 'thread' | 'list' | 'overlay'> = {},
) {
  if (!isMobileArchive()) return
  const current = currentArchiveFrame()
  const stackIndex = replace ? current?.stackIndex ?? 0 : (current?.stackIndex ?? -1) + 1
  const frame: ArchiveHistoryFrame = {
    entryId: makeArchiveEntryId(),
    stackIndex,
    layer,
    peerId: layer === 'list' ? null : selected.value?.peer_id ?? null,
    ...snapshot,
  }
  const method = replace ? 'replaceState' : 'pushState'
  window.history[method](archiveHistoryState(frame), '', archiveRouteHash(layer))
  lastAppliedArchiveEntry = frame.entryId
  activeArchiveStackIndex = frame.stackIndex
}

function ensureArchiveHistoryFrame(layer: ArchiveMobileLayer, peerId: number | null) {
  if (!isMobileArchive()) return
  const current = currentArchiveFrame()
  if (current?.layer === layer && current.peerId === peerId) return
  const frame: ArchiveHistoryFrame = {
    entryId: makeArchiveEntryId(),
    stackIndex: current?.stackIndex ?? 0,
    layer,
    peerId,
  }
  window.history.replaceState(archiveHistoryState(frame), '', archiveRouteHash(layer, peerId ?? undefined))
  activeArchiveStackIndex = frame.stackIndex
}

function pushArchiveOverlay(overlay: ArchiveOverlaySnapshot) {
  if (!isMobileArchive()) return
  flushArchiveSnapshot()
  const current = currentArchiveFrame()
  if (!current || current.overlay) return
  const frame: ArchiveHistoryFrame = {
    ...current,
    entryId: makeArchiveEntryId(),
    stackIndex: current.stackIndex + 1,
    overlay,
  }
  window.history.pushState(archiveHistoryState(frame), '', window.location.href)
  lastAppliedArchiveEntry = frame.entryId
  activeArchiveStackIndex = frame.stackIndex
}

function popArchiveOverlay(type: ArchiveOverlaySnapshot['type']) {
  const frame = currentArchiveFrame()
  if (!isMobileArchive() || frame?.overlay?.type !== type || frame.stackIndex <= 0) return false
  flushArchiveSnapshot()
  window.history.back()
  return true
}

const filteredChats = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  if (!needle) return chats.value
  return chats.value.filter((chat) => (
    `${chat.title} ${chat.username || ''} ${chat.last_message}`
      .toLocaleLowerCase()
      .includes(needle)
  ))
})

const displayEntries = computed<ArchiveEntry[]>(() => {
  const entries: ArchiveEntry[] = []
  for (let index = 0; index < messages.value.length;) {
    const first = messages.value[index]
    const items = [first]
    if (first.grouped_id) {
      let cursor = index + 1
      while (messages.value[cursor]?.grouped_id === first.grouped_id) {
        items.push(messages.value[cursor])
        cursor += 1
      }
      index = cursor
    } else {
      index += 1
    }
    const text = [...new Set(items.map((item) => item.text?.trim()).filter(Boolean) as string[])]
      .join('\n')
    entries.push({
      key: `${first.grouped_id ? 'album' : 'message'}-${first.id}`,
      items,
      first,
      last: items.at(-1) || first,
      media: items.flatMap((item) => item.media),
      text,
    })
  }
  return entries
})

interface ViewerItem {
  media: ArchiveMessageMedia
  message: ArchiveMessage
  entry: ArchiveEntry
}
const viewerOrigin = ref<'messages' | 'shared'>('messages')

const viewerItems = computed<ViewerItem[]>(() => displayEntries.value.flatMap((entry) => (
  entry.items.flatMap((message) => message.media
    .filter((media) => ['photo', 'video', 'animation'].includes(media.type))
    .map((media) => ({ media, message, entry })))
)))
const sharedViewerItems = computed<ViewerItem[]>(() => sharedMediaItems.value
  .filter((media) => ['photo', 'video', 'animation'].includes(media.type))
  .map((media) => {
    const message: ArchiveMessage = {
      id: media.message_id, message_id: media.message_id, sent_at: media.sent_at, text: media.text,
      content_kind: media.type, content: media.content, out: false, post: false, buttons: [], entities: [],
      is_deleted: false, is_edited: Boolean(media.is_history_version), current_version: media.current_version || 1,
      displayed_version: media.version || media.current_version || 1, is_restored: false, observed_at: media.sent_at || '', metrics: {}, media: [media],
    }
    const entry: ArchiveEntry = { key: `shared-${media.id}`, items: [message], first: message, last: message, media: [media], text: media.text || '' }
    return { media, message, entry }
  }))
const activeViewerItems = computed(() => viewerOrigin.value === 'shared' ? sharedViewerItems.value : viewerItems.value)
const activeViewerItem = computed(() => (
  viewerIndex.value === null ? null : activeViewerItems.value[viewerIndex.value] || null
))
const activeAlbumItems = computed(() => {
  const active = activeViewerItem.value
  if (!active || viewerOrigin.value === 'shared' || active.entry.media.length < 2) return []
  return viewerItems.value.filter((item) => item.entry.key === active.entry.key)
})

const sharedMediaTabs: Array<{ type: SharedMediaTab; label: string }> = [
  { type: 'media', label: '媒体' },
  { type: 'documents', label: '文件' },
  { type: 'links', label: '链接' },
  { type: 'audio', label: '音乐' },
  { type: 'voice', label: '语音' },
  { type: 'gif', label: 'GIF' },
]

const presentationByKey = computed<Record<string, ArchiveMessagePresentation>>(() => {
  const result: Record<string, ArchiveMessagePresentation> = {}
  displayEntries.value.forEach((entry, index) => {
    result[entry.key] = buildArchiveMessagePresentation({
      media: entry.media,
      text: entry.text,
      entities: entryEntities(entry),
      isDeleted: entryIsDeleted(entry),
      hasReply: Boolean(entryReply(entry)),
      hasForward: showsForwardHeader(entry),
      hasSender: showEntrySender(index, entry),
      hasWebPage: Boolean(entryWebPage(entry)),
      hasReactions: Boolean(entryMetrics(entry).reactions?.length),
      hasInlineButtons: Boolean(entryButtons(entry).length),
      contentKind: entry.first.content_kind,
    })
  })
  return result
})

function entryPresentation(entry: ArchiveEntry) {
  return presentationByKey.value[entry.key]
}

function entryContent(entry: ArchiveEntry): Record<string, any> {
  return entry.first.content || {}
}

function entryContentKind(entry: ArchiveEntry) {
  return entry.first.content_kind || 'unsupported'
}

function mediaContent(entry: ArchiveEntry, media: ArchiveMessageMedia): Record<string, any> {
  return entry.items.find((item) => item.media.some((candidate) => candidate.id === media.id))?.content || entryContent(entry)
}

function isVideoSticker(media: ArchiveMessageMedia) {
  return media.type === 'sticker' && (
    media.mime_type === 'video/webm' || Boolean(media.name?.toLocaleLowerCase().endsWith('.webm'))
  )
}

function isTgsSticker(media: ArchiveMessageMedia) {
  return media.type === 'sticker' && (
    media.mime_type === 'application/x-tgsticker'
    || Boolean(media.name?.toLocaleLowerCase().endsWith('.tgs'))
  )
}

function mediaHasSpoiler(entry: ArchiveEntry, media: ArchiveMessageMedia) {
  return !alwaysShowSpoilers.value
    && Boolean(mediaContent(entry, media).spoiler)
    && !revealedMediaSpoilers.value.has(media.id)
}

function revealMediaSpoiler(media: ArchiveMessageMedia) {
  revealedMediaSpoilers.value = new Set([...revealedMediaSpoilers.value, media.id])
}

function textSpoilerKey(scope: string | number, partIndex: number) {
  return `${scope}:${partIndex}`
}

function isTextSpoilerRevealed(scope: string | number, partIndex: number) {
  return alwaysShowSpoilers.value || revealedTextSpoilers.value.has(textSpoilerKey(scope, partIndex))
}

function revealTextSpoiler(scope: string | number, partIndex: number, event: Event) {
  const key = textSpoilerKey(scope, partIndex)
  if (revealedTextSpoilers.value.has(key)) return
  event.preventDefault()
  event.stopPropagation()
  revealedTextSpoilers.value = new Set([...revealedTextSpoilers.value, key])
}

function activateMedia(entry: ArchiveEntry, media: ArchiveMessageMedia) {
  if (mediaHasSpoiler(entry, media)) {
    revealMediaSpoiler(media)
    return
  }
  openMedia(media)
}

function mediaTtlSeconds(entry: ArchiveEntry, media: ArchiveMessageMedia) {
  const value = Number(mediaContent(entry, media).ttl_seconds || 0)
  return Number.isFinite(value) && value > 0 ? value : 0
}

function locationUrl(entry: ArchiveEntry) {
  const geo = entryContent(entry).geo as Record<string, unknown> | undefined
  const latitude = Number(geo?.latitude)
  const longitude = Number(geo?.longitude)
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return undefined
  return `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=16/${latitude}/${longitude}`
}

function locationCoordinates(entry: ArchiveEntry) {
  const geo = entryContent(entry).geo as Record<string, unknown> | undefined
  const latitude = Number(geo?.latitude)
  const longitude = Number(geo?.longitude)
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return '坐标不可用'
  return `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`
}

function pollPercent(answer: Record<string, any>, entry: ArchiveEntry) {
  const total = Number(entryContent(entry).total_voters || 0)
  const voters = Number(answer.voters || 0)
  return total > 0 ? Math.round(voters / total * 100) : 0
}

const ruleStatusLabel: Record<string, string> = {
  active: '持续备份中', paused: '备份已暂停', removed: '规则已移除', none: '仅保留归档',
}

function avatarText(value: { title?: string; name?: string }) {
  return (value.title || value.name || '#').trim().slice(0, 1).toUpperCase() || '#'
}

function avatarHue(id: number) {
  return { '--avatar-hue': String(Math.abs(id) % 360) }
}

function formatListTime(value?: string | null) {
  if (!value) return ''
  const date = serverDate(value)
  const today = new Date()
  if (date.toDateString() === today.toDateString()) {
    return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(date)
  }
  if (date.getFullYear() === today.getFullYear()) {
    return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(date)
  }
  return new Intl.DateTimeFormat('zh-CN', { year: '2-digit', month: 'numeric', day: 'numeric' }).format(date)
}

function formatTime(value?: string | null) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(serverDate(value))
}

function formatFullTime(value?: string | null) {
  if (!value) return '尚未完成备份'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(serverDate(value))
}

function dateKey(value?: string | null) {
  if (!value) return ''
  const date = serverDate(value)
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
}

function dateLabel(value?: string | null) {
  if (!value) return '时间未知'
  const date = serverDate(value)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (date.toDateString() === today.toDateString()) return '今天'
  if (date.toDateString() === yesterday.toDateString()) return '昨天'
  return new Intl.DateTimeFormat('zh-CN', {
    year: date.getFullYear() === today.getFullYear() ? undefined : 'numeric',
    month: 'long', day: 'numeric',
  }).format(date)
}

function showDate(index: number) {
  if (index === 0) return true
  return dateKey(displayEntries.value[index - 1]?.first.sent_at)
    !== dateKey(displayEntries.value[index]?.first.sent_at)
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}

function isImageDocument(media: ArchiveMessageMedia) {
  return media.type === 'document' && Boolean(media.mime_type?.startsWith('image/'))
}

function mediaLabel(media: ArchiveMessageMedia) {
  return ({
    photo: '图片', video: '视频', audio: '音频', voice: '语音', document: '文件',
    animation: '动图', sticker: '贴纸',
  } as Record<string, string>)[media.type] || '媒体文件'
}

function entryEntities(entry: ArchiveEntry) {
  const message = entry.items.find((item) => item.text?.trim())
  return entry.items.filter((item) => item.text?.trim()).length === 1 ? message?.entities || [] : []
}

function richTextParts(value: string, entities: Record<string, unknown>[]) {
  const normalized = entities.map((entity) => ({
    type: String(entity._ || ''),
    offset: Number(entity.offset || 0),
    end: Number(entity.offset || 0) + Number(entity.length || 0),
    url: typeof entity.url === 'string' ? entity.url : undefined,
    customEmojiId: entity.document_id !== undefined
      ? String(entity.document_id)
      : entity.documentId !== undefined
        ? String(entity.documentId)
        : undefined,
  })).filter((entity) => entity.end > entity.offset && entity.offset >= 0 && entity.end <= value.length)
  const boundaries = [...new Set([0, value.length, ...normalized.flatMap((entity) => [entity.offset, entity.end])])]
    .sort((left, right) => left - right)
  const parts: Array<{ text: string; href?: string; customEmojiId?: string; classes: string[] }> = []
  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const start = boundaries[index]
    const end = boundaries[index + 1]
    const text = value.slice(start, end)
    if (!text) continue
    const active = normalized.filter((entity) => entity.offset <= start && entity.end >= end)
    const classes = active.map((entity) => ({
      MessageEntityBold: 'rich-bold', MessageEntityItalic: 'rich-italic',
      MessageEntityUnderline: 'rich-underline', MessageEntityStrike: 'rich-strike',
      MessageEntityCode: 'rich-code', MessageEntityPre: 'rich-code',
      MessageEntitySpoiler: 'rich-spoiler',
    }[entity.type])).filter(Boolean) as string[]
    const link = active.find((entity) => [
      'MessageEntityTextUrl', 'MessageEntityUrl', 'MessageEntityMention',
      'MessageEntityEmail', 'MessageEntityPhone',
    ].includes(entity.type))
    let href = link?.url
    if (link?.type === 'MessageEntityUrl') href = text
    if (link?.type === 'MessageEntityMention') href = `https://t.me/${text.replace(/^@/, '')}`
    if (link?.type === 'MessageEntityEmail') href = `mailto:${text}`
    if (link?.type === 'MessageEntityPhone') href = `tel:${text}`
    const customEmojiId = active.find((entity) => entity.type === 'MessageEntityCustomEmoji')?.customEmojiId
    parts.push({ text, href, customEmojiId, classes })
  }
  if (!entities.length) {
    const pattern = /((?:https?:\/\/|tg:\/\/)[^\s<]+|@[A-Za-z0-9_]{5,})/g
    const fallback: Array<{ text: string; href?: string; customEmojiId?: string; classes: string[] }> = []
    let cursor = 0
    for (const match of value.matchAll(pattern)) {
      const start = match.index || 0
      if (start > cursor) fallback.push({ text: value.slice(cursor, start), classes: [] })
      const text = match[0]
      fallback.push({ text, href: text.startsWith('@') ? `https://t.me/${text.slice(1)}` : text, classes: [] })
      cursor = start + text.length
    }
    if (cursor < value.length) fallback.push({ text: value.slice(cursor), classes: [] })
    return fallback
  }
  return parts
}

function entryForward(entry: ArchiveEntry) {
  return entry.items.find((item) => item.forward_info)?.forward_info || null
}

function entrySender(entry: ArchiveEntry) {
  if (selected.value?.is_self && entry.first.forward_info) {
    return entry.first.origin_sender || null
  }
  return entry.first.sender || null
}

function entrySenderName(entry: ArchiveEntry) {
  const sender = entrySender(entry)
  return sender?.name
    || (selected.value?.is_self ? entry.first.forward_info?.name : null)
    || entry.first.post_author
    || '未知发送者'
}

function entrySenderId(entry: ArchiveEntry) {
  const sender = entrySender(entry)
  return sender?.peer_id ?? entry.first.sender_id ?? 0
}

function senderKindLabel(kind?: string | null) {
  return ({
    user: '用户', bot: '机器人', group: '群组', supergroup: '超级群组', channel: '频道',
  } as Record<string, string>)[kind || ''] || 'Telegram 资料'
}

function senderProfileAvatar() {
  return senderProfileDetail.value?.avatars.big
    || senderProfileDetail.value?.avatars.small
    || senderProfile.value?.avatar_url
    || null
}

async function openSenderProfile(entry: ArchiveEntry, event?: Event, recordHistory = true) {
  const sender = entrySender(entry)
  if (recordHistory) pushArchiveOverlay({ type: 'profile', messageId: entry.first.message_id })
  const requestId = ++senderProfileRequestId
  senderProfileTrigger = event?.currentTarget instanceof HTMLElement ? event.currentTarget : null
  senderProfile.value = {
    entity_id: sender?.entity_id,
    peer_id: sender?.peer_id ?? entry.first.sender_id,
    kind: sender?.kind,
    name: entrySenderName(entry),
    username: sender?.username,
    avatar_url: sender?.avatar_url,
  }
  senderProfileDetail.value = null
  senderProfileError.value = ''
  document.body.classList.add('archive-viewer-open')
  await nextTick()
  senderProfileCloseButton.value?.focus()
  if (!sender?.entity_id) return
  senderProfileLoading.value = true
  try {
    const detail = await api<TelegramEntityDetail>(`/api/entities/${sender.entity_id}`)
    if (requestId === senderProfileRequestId) senderProfileDetail.value = detail
  } catch (loadError) {
    if (requestId === senderProfileRequestId) {
      senderProfileError.value = loadError instanceof Error ? loadError.message : '无法读取发送者资料'
    }
  } finally {
    if (requestId === senderProfileRequestId) senderProfileLoading.value = false
  }
}

function closeSenderProfile(fromHistory = false) {
  if (!fromHistory && popArchiveOverlay('profile')) return
  const trigger = senderProfileTrigger
  senderProfileRequestId += 1
  senderProfile.value = null
  senderProfileDetail.value = null
  senderProfileLoading.value = false
  senderProfileError.value = ''
  senderProfileTrigger = null
  if (viewerIndex.value === null && !historyMessage.value) {
    document.body.classList.remove('archive-viewer-open')
  }
  void nextTick(() => trigger?.focus())
}

function showsForwardHeader(entry: ArchiveEntry) {
  return Boolean(entryForward(entry) && !selected.value?.is_self)
}

function entryWebPage(entry: ArchiveEntry) {
  const message = entry.items.find((item) => item.webpage_info)
  const webpage = message?.webpage_info
  if (!webpage) return null

  // Telegram may attach MessageMediaWebPage containing WebPageEmpty. It only
  // reserves a URL for a possible preview and official clients render it as a
  // plain link. A card is useful only when Telegram supplied presentable data.
  const hasPreviewContent = Boolean(
    webpage.title
    || webpage.description
    || webpage.site_name
    || webpage.display_url
    || webpage.type
    || webpage.duration
    || message?.media.length,
  )
  return hasPreviewContent ? webpage : null
}

function entryButtons(entry: ArchiveEntry) {
  return entry.items.find((item) => item.buttons?.length)?.buttons || []
}

function entryViaBot(entry: ArchiveEntry) {
  return entry.items.find((item) => item.via_bot)?.via_bot || null
}

function viaBotLabel(entry: ArchiveEntry) {
  const bot = entryViaBot(entry)
  if (!bot) return ''
  return bot.username ? `@${bot.username}` : (bot.name || `机器人 ${bot.peer_id}`)
}

function reactionLabel(value: unknown) {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    if (typeof record.emoticon === 'string') return record.emoticon
  }
  return '◇'
}

function groupShowsSender() {
  return Boolean(selected.value && (
    selected.value.is_self
    || ['group', 'supergroup'].includes(selected.value.kind)
    || (selected.value.kind === 'channel' && selected.value.shows_sender_profiles)
  ))
}

function entryReply(entry: ArchiveEntry) {
  return entry.items.find((item) => item.reply_to_msg_id) || null
}

function entryMetrics(entry: ArchiveEntry) {
  for (let index = entry.items.length - 1; index >= 0; index -= 1) {
    if (entry.items[index].metrics.reactions?.length) return entry.items[index].metrics
  }
  return entry.last.metrics
}

function entryIsEdited(entry: ArchiveEntry) {
  return entry.items.some((item) => item.is_edited)
}

function entryIsRestored(entry: ArchiveEntry) {
  return entry.items.some((item) => item.is_restored)
}

function entryIsDeleted(entry: ArchiveEntry) {
  return entry.items.every((item) => item.is_deleted)
}

function entryVersions(entry: ArchiveEntry) {
  return Math.max(...entry.items.map((item) => item.current_version))
}

function entryHistoryMessage(entry: ArchiveEntry) {
  return entry.items.find((item) => item.is_restored)
    || entry.items.find((item) => item.current_version > 1 || item.is_deleted)
    || entry.first
}

async function openMessageHistory(entry: ArchiveEntry, recordHistory = true) {
  const message = entryHistoryMessage(entry)
  if (recordHistory) pushArchiveOverlay({ type: 'history', messageId: message.message_id })
  historyMessage.value = message
  historyData.value = null
  historyError.value = ''
  historyLoading.value = true
  document.body.classList.add('archive-viewer-open')
  try {
    historyData.value = await api<ArchiveMessageVersions>(`/api/archive/messages/${message.id}/versions`)
  } catch (loadError) {
    historyError.value = loadError instanceof Error ? loadError.message : '无法读取消息历史'
  } finally {
    historyLoading.value = false
  }
}

function closeMessageHistory(fromHistory = false) {
  if (!fromHistory && popArchiveOverlay('history')) return
  historyMessage.value = null
  historyData.value = null
  historyError.value = ''
  if (viewerIndex.value === null) document.body.classList.remove('archive-viewer-open')
}

function versionTime(value?: string | null) {
  if (!value) return '记录时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(serverDate(value))
}

function versionKindLabel(kind: string) {
  return ({
    text: '文本', photo: '图片', video: '视频', round_video: '圆形视频', animation: '动图',
    sticker: '贴纸', audio: '音乐', voice: '语音', document: '文件', contact: '联系人',
    location: '位置', venue: '地点', poll: '投票', todo: '待办', service: '服务消息',
  } as Record<string, string>)[kind] || '消息'
}

function rememberMediaDimensions(media: ArchiveMessageMedia, event: Event) {
  const target = event.currentTarget as HTMLImageElement | HTMLVideoElement
  if (target instanceof HTMLVideoElement) {
    // At this point both the scrolling root and the media element are mounted.
    // Rebinding here avoids a race when the asynchronous message list renders
    // before its template ref is available.
    void nextTick(bindInlineVideoObserver)
  }
  const width = 'naturalWidth' in target ? target.naturalWidth : target.videoWidth
  const height = 'naturalHeight' in target ? target.naturalHeight : target.videoHeight
  if (!width || !height) return
  const current = mediaDimensions.value[media.id]
  if (current?.width === width && current.height === height) return
  mediaDimensions.value = { ...mediaDimensions.value, [media.id]: { width, height } }
}

function rememberVideoMetadata(media: ArchiveMessageMedia, event: Event) {
  rememberMediaDimensions(media, event)
  const video = event.currentTarget as HTMLVideoElement
  if (Number.isFinite(video.duration) && video.duration > 0) {
    mediaDurations.value = { ...mediaDurations.value, [media.id]: video.duration }
    if (!(media.id in mediaRemaining.value)) {
      mediaRemaining.value = { ...mediaRemaining.value, [media.id]: video.duration }
    }
  }
  captureInlineVideoPoster(media, video)
}

function mediaPreviewUrl(media: ArchiveMessageMedia) {
  return media.preview_url && !failedPreviewIds.value.has(media.id)
    ? media.preview_url
    : media.url
}

function mediaPosterUrl(media: ArchiveMessageMedia) {
  if (media.preview_url && !failedPreviewIds.value.has(media.id)) return media.preview_url
  return mediaPosters.value[media.id] || sharedMediaPosters.value[media.id] || ''
}

async function handleMediaPreviewError(media: ArchiveMessageMedia, event: Event) {
  if (!media.preview_url || failedPreviewIds.value.has(media.id)) return
  failedPreviewIds.value = new Set(failedPreviewIds.value).add(media.id)
  await nextTick()
  const container = (event.currentTarget as HTMLElement).parentElement
  const video = container?.querySelector('video')
  if (video instanceof HTMLVideoElement && video.readyState >= 2) {
    captureInlineVideoPoster(media, video)
  }
}

function captureInlineVideoPoster(media: ArchiveMessageMedia, source: Event | HTMLVideoElement) {
  const video = source instanceof HTMLVideoElement
    ? source
    : source.currentTarget as HTMLVideoElement
  if ((media.preview_url && !failedPreviewIds.value.has(media.id)) || mediaPosters.value[media.id] || !video.videoWidth || !video.videoHeight || video.readyState < 2) return

  const capture = () => {
    if (mediaPosters.value[media.id] || !video.videoWidth || !video.videoHeight) return
    const width = Math.min(640, video.videoWidth)
    const height = Math.max(1, Math.round(width * video.videoHeight / video.videoWidth))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) return
    try {
      context.drawImage(video, 0, 0, width, height)
      const poster = canvas.toDataURL('image/jpeg', .78)
      mediaPosters.value = { ...mediaPosters.value, [media.id]: poster }
    } catch {
      // The video itself remains usable when a browser blocks frame extraction.
    }
  }

  // A loaded, paused video can lose its compositor surface after the browser
  // tab is restored. Capture synchronously first so shared-media thumbnails do
  // not depend on that surface remaining alive. requestVideoFrameCallback is a
  // second chance for browsers that have not painted the first frame yet.
  capture()
  if (!mediaPosters.value[media.id] && 'requestVideoFrameCallback' in video) {
    video.requestVideoFrameCallback(capture)
  }
  if (!mediaPosters.value[media.id]) window.setTimeout(capture, 120)
}

function startSharedVideoPoster(media: ArchiveSharedMediaItem, event: Event) {
  const video = event.currentTarget as HTMLVideoElement
  if (sharedMediaPosters.value[media.id] || video.dataset.posterStarted === 'true') return
  if (!Number.isFinite(video.duration) || video.duration <= 0) return
  video.dataset.posterStarted = 'true'
  video.dataset.posterAttempt = '0'
  seekSharedVideoPoster(video)
}

function seekSharedVideoPoster(video: HTMLVideoElement) {
  const duration = video.duration
  if (!Number.isFinite(duration) || duration <= 0) return
  const attempt = Number(video.dataset.posterAttempt || 0)
  const fractions = [.12, .32, .58]
  const target = Math.min(Math.max(.04, duration * fractions[Math.min(attempt, fractions.length - 1)]), Math.max(0, duration - .02))
  if (Math.abs(video.currentTime - target) < .01) {
    video.dispatchEvent(new Event('seeked'))
  } else {
    video.currentTime = target
  }
}

function captureSharedVideoPoster(media: ArchiveSharedMediaItem, event: Event) {
  const video = event.currentTarget as HTMLVideoElement
  if (sharedMediaPosters.value[media.id] || !video.videoWidth || !video.videoHeight || video.readyState < 2) return

  const capture = () => {
    if (sharedMediaPosters.value[media.id] || !video.videoWidth || !video.videoHeight) return
    const width = Math.min(640, video.videoWidth)
    const height = Math.max(1, Math.round(width * video.videoHeight / video.videoWidth))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d', { willReadFrequently: true })
    if (!context) return
    try {
      context.drawImage(video, 0, 0, width, height)

      // Some MP4 files expose a decoded frame before Chromium has painted it;
      // drawing at that point produces an all-black JPEG. Try a later keyframe
      // before accepting an effectively empty preview.
      const sampleWidth = Math.min(80, width)
      const sampleHeight = Math.max(1, Math.round(sampleWidth * height / width))
      const sample = document.createElement('canvas')
      sample.width = sampleWidth
      sample.height = sampleHeight
      const sampleContext = sample.getContext('2d', { willReadFrequently: true })
      sampleContext?.drawImage(canvas, 0, 0, sampleWidth, sampleHeight)
      const pixels = sampleContext?.getImageData(0, 0, sampleWidth, sampleHeight).data
      let visiblePixels = 0
      if (pixels) {
        for (let index = 0; index < pixels.length; index += 4) {
          if (pixels[index] > 12 || pixels[index + 1] > 12 || pixels[index + 2] > 12) visiblePixels += 1
        }
      }
      const attempt = Number(video.dataset.posterAttempt || 0)
      if (pixels && visiblePixels / (pixels.length / 4) < .008 && attempt < 2) {
        video.dataset.posterAttempt = String(attempt + 1)
        seekSharedVideoPoster(video)
        return
      }

      sharedMediaPosters.value = { ...sharedMediaPosters.value, [media.id]: canvas.toDataURL('image/jpeg', .8) }
    } catch {
      // Keep the video loader visible when frame extraction is unavailable.
    }
  }

  // Paused videos do not consistently deliver requestVideoFrameCallback after
  // a programmatic seek. A short timer is the reliable path; the frame callback
  // merely lets capable browsers finish sooner.
  let captured = false
  const captureOnce = () => {
    if (captured || sharedMediaPosters.value[media.id]) return
    captured = true
    capture()
  }
  if ('requestVideoFrameCallback' in video) video.requestVideoFrameCallback(captureOnce)
  window.setTimeout(captureOnce, 100)
}

function handleInlineVideoTimeUpdate(media: ArchiveMessageMedia, event: Event) {
  const video = event.currentTarget as HTMLVideoElement
  if (!Number.isFinite(video.duration)) return
  const remaining = Math.max(0, video.duration - video.currentTime)
  if (Math.ceil(mediaRemaining.value[media.id] ?? -1) === Math.ceil(remaining)) return
  mediaRemaining.value = { ...mediaRemaining.value, [media.id]: remaining }
}

function formatMediaDuration(value?: number) {
  if (!Number.isFinite(value) || value === undefined) return ''
  const seconds = Math.max(0, Math.ceil(value))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
    : `${minutes}:${String(rest).padStart(2, '0')}`
}

function mediaRatio(media: ArchiveMessageMedia) {
  const dimensions = mediaDimensions.value[media.id]
  if (dimensions) return dimensions.width / dimensions.height
  if (media.width && media.height) return media.width / media.height
  return media.type === 'video' ? 16 / 9 : 1
}

function singleMediaStyle(media: ArchiveMessageMedia) {
  return { '--media-ratio': String(Math.min(4, Math.max(.28, mediaRatio(media)))) }
}

function setInlineVideoState(video: HTMLVideoElement) {
  // Telegram Web A does not connect videos inside an album to its playback
  // intersection observer. Album tiles stay as previews until the user opens
  // the media viewer, otherwise every visible video in one bubble plays at once.
  if (video.classList.contains('archive-album-video')) {
    video.pause()
    return
  }

  const shouldPlay = visibleInlineVideos.has(video)
    && document.visibilityState === 'visible'
    && viewerIndex.value === null

  if (!shouldPlay) {
    video.pause()
    return
  }

  video.muted = true
  video.loop = true
  void video.play().then(() => {
    video.closest('.archive-album-tile, .archive-video-preview')?.classList.remove('playback-unavailable')
  }).catch(() => {
    video.closest('.archive-album-tile, .archive-video-preview')?.classList.add('playback-unavailable')
  })
}

function refreshInlineVideoPlayback() {
  messageViewport.value?.querySelectorAll<HTMLVideoElement>('video.archive-inline-video')
    .forEach(setInlineVideoState)
}

function bindInlineVideoObserver() {
  inlineVideoObserver?.disconnect()
  inlineVideoObserver = null
  visibleInlineVideos.clear()
  const root = messageViewport.value
  if (!root) return

  inlineVideoObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const video = entry.target as HTMLVideoElement
      if (entry.isIntersecting) visibleInlineVideos.add(video)
      else visibleInlineVideos.delete(video)
      setInlineVideoState(video)
    }
  }, { root })

  root.querySelectorAll<HTMLVideoElement>('video.archive-inline-video').forEach((video) => {
    inlineVideoObserver?.observe(video)
  })
}

function handleInlineVideoPlayback(event: Event, isPlaying: boolean) {
  const media = (event.currentTarget as HTMLVideoElement).closest('.archive-album-tile, .archive-video-preview')
  media?.classList.toggle('is-playing', isPlaying)
}

function handleDocumentVisibility() {
  if (document.visibilityState === 'visible') {
    messageViewport.value?.querySelectorAll<HTMLVideoElement>('video.archive-inline-video').forEach((video) => {
      video.classList.add('is-repainting')
      const reveal = () => video.classList.remove('is-repainting')
      if ('requestVideoFrameCallback' in video) video.requestVideoFrameCallback(reveal)
      else window.setTimeout(reveal, 80)
    })
  }
  if (document.visibilityState === 'hidden') flushArchiveSnapshot()
  refreshInlineVideoPlayback()
}

const albumLayouts = computed<Record<string, AlbumLayout>>(() => {
  const layouts: Record<string, AlbumLayout> = {}
  for (const entry of displayEntries.value) {
    if (!entryPresentation(entry).isVisualAlbum) continue
    const hasAvatarColumn = groupShowsSender() && !entry.first.out
    const maximum = entry.first.out ? 480 : 464
    const reservedWidth = hasAvatarColumn ? 112 : 72
    // Albums with captions have a 2px inset on both sides inside the bubble.
    // Reserve that space before calculating absolute tile coordinates, so the
    // child grid cannot make the border-box wider than the mobile viewport.
    const bubbleInset = entryPresentation(entry).isMediaOnly ? 0 : 4
    const available = Math.min(maximum, Math.max(160, messageViewportWidth.value - reservedWidth - bubbleInset))
    layouts[entry.key] = calculateAlbumLayout(entry.media.map(mediaRatio), available)
  }
  return layouts
})

function entryAlbumLayout(entry: ArchiveEntry): AlbumLayout {
  return albumLayouts.value[entry.key]
}

function albumContainerStyle(entry: ArchiveEntry) {
  const layout = entryAlbumLayout(entry)
  return { width: `${layout.width}px`, height: `${layout.height}px` }
}

function visualMediaBubbleStyle(entry: ArchiveEntry) {
  const presentation = entryPresentation(entry)
  if (presentation.isVisualAlbum) {
    const bubbleInset = presentation.isMediaOnly ? 0 : 4
    return { '--archive-album-width': `${entryAlbumLayout(entry).width + bubbleInset}px` }
  }
  if (presentation.hasVisualMedia && entry.media.length === 1) {
    return { '--media-ratio': String(Math.min(4, Math.max(.28, mediaRatio(entry.media[0])))) }
  }
  return undefined
}

function albumTileStyle(entry: ArchiveEntry, index: number) {
  const tile = entryAlbumLayout(entry).tiles[index]
  return tile ? {
    left: `${tile.x}px`, top: `${tile.y}px`, width: `${tile.width}px`, height: `${tile.height}px`,
  } : undefined
}

function openMedia(media: ArchiveMessageMedia, recordHistory = true) {
  const index = viewerItems.value.findIndex((item) => item.media.id === media.id)
  if (index < 0) return
  viewerOrigin.value = 'messages'
  if (recordHistory) pushArchiveOverlay({ type: 'viewer', mediaId: media.id, origin: 'messages' })
  viewerIndex.value = index
  resetViewerTransform()
  document.body.classList.add('archive-viewer-open')
}

function openSharedMedia(media: ArchiveSharedMediaItem, recordHistory = true) {
  const index = sharedViewerItems.value.findIndex((item) => item.media.id === media.id)
  if (index < 0) return
  viewerOrigin.value = 'shared'
  if (recordHistory) pushArchiveOverlay({ type: 'viewer', mediaId: media.id, origin: 'shared' })
  viewerIndex.value = index
  resetViewerTransform()
  document.body.classList.add('archive-viewer-open')
}

function sharedMediaLayout() {
  const visual = ['media', 'gif'].includes(sharedMediaTab.value)
  const columns = visual ? 3 : 1
  const gap = visual ? 1 : 0
  const padding = visual ? 0 : 4
  const width = Math.max(1, sharedMediaViewportWidth.value - padding * 2)
  const itemWidth = visual ? (width - gap * (columns - 1)) / columns : width
  return {
    columns,
    gap,
    padding,
    itemWidth,
    rowHeight: visual ? itemWidth + gap : 68,
  }
}

function sharedMediaVirtualHeight() {
  const layout = sharedMediaLayout()
  return Math.ceil(sharedMediaTotal.value / layout.columns) * layout.rowHeight + layout.padding * 2
}

const sharedMediaVisibleSlots = computed(() => {
  const layout = sharedMediaLayout()
  const slots: Array<{ index: number; item?: ArchiveSharedMediaItem; style: Record<string, string> }> = []
  for (let index = sharedMediaWindow.value.start; index <= sharedMediaWindow.value.end; index += 1) {
    const row = Math.floor(index / layout.columns)
    const column = index % layout.columns
    slots.push({
      index,
      item: sharedMediaByIndex.value[index],
      style: {
        left: `${layout.padding + column * (layout.itemWidth + layout.gap)}px`,
        top: `${layout.padding + row * layout.rowHeight}px`,
        width: `${layout.itemWidth}px`,
        height: `${layout.rowHeight - layout.gap}px`,
      },
    })
  }
  return slots
})

function sharedMediaScrollMetrics() {
  const content = infoScroll.value
  const virtual = sharedMediaVirtual.value
  if (!content || !virtual) return null
  if (virtual.clientWidth > 0 && virtual.clientWidth !== sharedMediaViewportWidth.value) {
    sharedMediaViewportWidth.value = virtual.clientWidth
  }
  const maxScroll = Math.max(0, content.scrollHeight - content.clientHeight)
  const contentRect = content.getBoundingClientRect()
  const virtualTop = virtual.getBoundingClientRect().top - contentRect.top + content.scrollTop
  const mediaSection = virtual.closest<HTMLElement>('.archive-shared-media')
  const tabs = mediaSection?.querySelector<HTMLElement>('.archive-shared-tabs')
  // This directly mirrors `position: sticky; top: 0`: once the tab bar is
  // clamped to the scroll viewport's top edge, the scrubber becomes available.
  const tabsAreSticky = Boolean(tabs && tabs.getBoundingClientRect().top <= contentRect.top + 1)
  const mediaStart = Math.min(virtualTop, maxScroll)
  const mediaRange = Math.max(0, maxScroll - mediaStart)
  return {
    content,
    maxScroll,
    mediaStart,
    mediaRange: Math.max(1, mediaRange),
    mediaScrollable: mediaRange > 1,
    virtualTop,
    tabsAreSticky,
  }
}

function cloneInfoSnapshot(snapshot: ArchiveInfoSnapshot): ArchiveInfoSnapshot {
  return { ...snapshot, tabScrollTops: { ...snapshot.tabScrollTops } }
}

function freshInfoSnapshot(): ArchiveInfoSnapshot {
  return {
    tab: 'media',
    photo: true,
    video: true,
    scrollTop: 0,
    anchorIndex: null,
    anchorOffset: 0,
    tabScrollTops: {},
  }
}

function captureInfoSnapshot(): ArchiveInfoSnapshot | null {
  const content = infoScroll.value
  if (!content) return null
  const metrics = sharedMediaScrollMetrics()
  let anchorIndex: number | null = null
  let anchorOffset = 0
  if (metrics && content.scrollTop >= metrics.virtualTop) {
    const layout = sharedMediaLayout()
    const row = Math.max(0, Math.floor((content.scrollTop - metrics.virtualTop) / layout.rowHeight))
    anchorIndex = Math.min(Math.max(0, sharedMediaTotal.value - 1), row * layout.columns)
    anchorOffset = content.scrollTop - (metrics.virtualTop + row * layout.rowHeight)
  }
  return {
    tab: sharedMediaTab.value,
    photo: sharedMediaKinds.value.photo,
    video: sharedMediaKinds.value.video,
    scrollTop: content.scrollTop,
    anchorIndex,
    anchorOffset,
    tabScrollTops: { ...sharedMediaScrollTops, [sharedMediaTab.value]: content.scrollTop },
  }
}

function captureThreadSnapshot(): ArchiveThreadSnapshot | null {
  const viewport = messageViewport.value
  if (!viewport) return null
  const atBottom = nearBottom()
  const viewportTop = viewport.getBoundingClientRect().top
  const line = [...viewport.querySelectorAll<HTMLElement>('.archive-message-line')]
    .find((candidate) => candidate.getBoundingClientRect().bottom >= viewportTop)
  if (!line) return { messageId: null, offset: 0, atBottom }
  const messageId = Number(line.dataset.messageIds?.split(' ')[0])
  return {
    messageId: Number.isSafeInteger(messageId) ? messageId : null,
    offset: line.getBoundingClientRect().top - viewportTop,
    atBottom,
  }
}

function captureListSnapshot(): ArchiveListSnapshot {
  return { scrollTop: chatList.value?.scrollTop || 0, query: query.value }
}

function persistCurrentArchiveSnapshot() {
  if (!isMobileArchive()) return
  const frame = currentArchiveFrame()
  if (!frame || (frame.peerId !== null && frame.peerId !== selected.value?.peer_id)) return
  const next: ArchiveHistoryFrame = { ...frame }
  if (frame.layer === 'info') {
    const snapshot = captureInfoSnapshot()
    if (snapshot) next.info = snapshot
  } else if (frame.layer === 'thread') {
    const snapshot = captureThreadSnapshot()
    if (snapshot && (snapshot.messageId || snapshot.atBottom || !next.thread?.messageId)) next.thread = snapshot
  } else {
    next.list = captureListSnapshot()
  }
  window.history.replaceState(archiveHistoryState(next), '', window.location.href)
}

function scheduleArchiveSnapshot() {
  if (!isMobileArchive() || archiveSnapshotTimer) return
  archiveSnapshotTimer = window.setTimeout(() => {
    archiveSnapshotTimer = 0
    persistCurrentArchiveSnapshot()
  }, 120)
}

function flushArchiveSnapshot() {
  if (archiveSnapshotTimer) window.clearTimeout(archiveSnapshotTimer)
  archiveSnapshotTimer = 0
  persistCurrentArchiveSnapshot()
}

async function restoreInfoSnapshot(snapshot: ArchiveInfoSnapshot | undefined, navigationId = archiveNavigationSequence) {
  if (!snapshot) return
  const configurationChanged = sharedMediaTab.value !== snapshot.tab
    || sharedMediaKinds.value.photo !== snapshot.photo
    || sharedMediaKinds.value.video !== snapshot.video
  sharedMediaTab.value = snapshot.tab
  sharedMediaKinds.value = { photo: snapshot.photo, video: snapshot.video }
  for (const tab of sharedMediaTabs) delete sharedMediaScrollTops[tab.type]
  Object.assign(sharedMediaScrollTops, snapshot.tabScrollTops)
  if (configurationChanged || !sharedMediaTotal.value) await loadSharedMedia(false)
  if (navigationId !== archiveNavigationSequence) return
  await nextTick()
  if (navigationId !== archiveNavigationSequence || !infoOpen.value) return
  const content = infoScroll.value
  if (!content) return
  if (snapshot.anchorIndex === null) {
    content.scrollTop = snapshot.scrollTop
  } else {
    const metrics = sharedMediaScrollMetrics()
    if (metrics) {
      const layout = sharedMediaLayout()
      const row = Math.floor(snapshot.anchorIndex / layout.columns)
      content.scrollTop = metrics.virtualTop + row * layout.rowHeight + snapshot.anchorOffset
      const page = Math.floor(snapshot.anchorIndex / SHARED_MEDIA_PAGE_SIZE)
      if (!sharedMediaLoadedPages.has(page)) await fetchSharedMediaPage(page)
      if (navigationId !== archiveNavigationSequence) return
    }
  }
  await nextTick()
  updateSharedMediaWindow()
  updateSharedScrollbar()
}

async function restoreThreadSnapshot(snapshot: ArchiveThreadSnapshot | undefined, navigationId = archiveNavigationSequence) {
  if (!snapshot) return
  if (snapshot.atBottom) {
    if (hasNewer.value) await loadMessages(false)
    if (navigationId !== archiveNavigationSequence) return
    await scrollToBottom()
    return
  }
  if (!snapshot.messageId) return
  await jumpToMessage(snapshot.messageId, false)
  if (navigationId !== archiveNavigationSequence) return
  await nextTick()
  if (navigationId !== archiveNavigationSequence) return
  const viewport = messageViewport.value
  const target = viewport?.querySelector<HTMLElement>(`[data-message-ids~="${snapshot.messageId}"]`)
  if (!viewport || !target) return
  viewport.scrollTop += target.getBoundingClientRect().top - viewport.getBoundingClientRect().top - snapshot.offset
  updateStickyDate()
}

async function restoreListSnapshot(snapshot: ArchiveListSnapshot | undefined, navigationId = archiveNavigationSequence) {
  if (!snapshot) return
  query.value = snapshot.query
  await nextTick()
  if (navigationId !== archiveNavigationSequence) return
  if (chatList.value) chatList.value.scrollTop = snapshot.scrollTop
}

function closeActiveArchiveOverlay() {
  if (viewerIndex.value !== null) closeViewer(true)
  if (historyMessage.value) closeMessageHistory(true)
  if (senderProfile.value) closeSenderProfile(true)
  if (messageSearchOpen.value) closeMessageSearch(true)
}

async function restoreArchiveOverlay(overlay: ArchiveOverlaySnapshot | undefined, navigationId: number) {
  if (!overlay || navigationId !== archiveNavigationSequence) return
  if (overlay.type === 'search') {
    messageSearchQuery.value = overlay.query
    await openMessageSearch(false)
    return
  }
  if (overlay.type === 'viewer') {
    const items = overlay.origin === 'shared' ? sharedViewerItems.value : viewerItems.value
    const item = items.find((candidate) => candidate.media.id === overlay.mediaId)
    if (!item || navigationId !== archiveNavigationSequence) return
    if (overlay.origin === 'shared') openSharedMedia(item.media as ArchiveSharedMediaItem, false)
    else openMedia(item.media, false)
    return
  }
  const entry = displayEntries.value.find((candidate) => candidate.items.some((item) => item.message_id === overlay.messageId))
  if (!entry || navigationId !== archiveNavigationSequence) return
  if (overlay.type === 'history') await openMessageHistory(entry, false)
  else await openSenderProfile(entry, undefined, false)
}

function requestSharedMediaRange(start: number, end: number) {
  if (end < start || sharedMediaTotal.value <= 0 || sharedMediaError.value) return
  const firstPage = Math.floor(start / SHARED_MEDIA_PAGE_SIZE)
  const lastPage = Math.floor(end / SHARED_MEDIA_PAGE_SIZE)
  for (let page = firstPage; page <= lastPage; page += 1) {
    if (!sharedMediaLoadedPages.has(page) && !sharedMediaPendingPages.has(page) && !sharedMediaFailedPages.has(page)) {
      void fetchSharedMediaPage(page)
    }
  }
}

function updateSharedMediaWindow() {
  const metrics = sharedMediaScrollMetrics()
  if (!metrics || sharedMediaTotal.value <= 0) {
    sharedMediaWindow.value = { start: 0, end: -1 }
    return
  }
  const layout = sharedMediaLayout()
  const localTop = Math.max(0, metrics.content.scrollTop - metrics.virtualTop)
  const buffer = metrics.content.clientHeight * SHARED_MEDIA_BUFFER_VIEWPORTS
  const firstRow = Math.max(0, Math.floor((localTop - buffer) / layout.rowHeight))
  const lastRow = Math.min(
    Math.ceil(sharedMediaTotal.value / layout.columns) - 1,
    Math.ceil((localTop + metrics.content.clientHeight + buffer) / layout.rowHeight),
  )
  const start = firstRow * layout.columns
  const end = Math.min(sharedMediaTotal.value - 1, (lastRow + 1) * layout.columns - 1)
  sharedMediaWindow.value = { start, end }
  requestSharedMediaRange(start, end)
}

function updateSharedScrollbar() {
  const metrics = sharedMediaScrollMetrics()
  const scrollbar = sharedScrollbar.value
  const thumb = sharedScrollbarThumb.value
  if (!metrics || !scrollbar || !thumb || !metrics.mediaScrollable || sharedMediaTotal.value <= 0) {
    sharedScrollbarVisible.value = false
    sharedScrollbarBubbleVisible.value = false
    return
  }
  sharedScrollbarVisible.value = metrics.tabsAreSticky || sharedScrollbarDragging.value
  if (!sharedScrollbarVisible.value) {
    sharedScrollbarBubbleVisible.value = false
    return
  }
  const thumbHeight = thumb.offsetHeight || 42
  const maxThumbTop = Math.max(1, scrollbar.clientHeight - thumbHeight)
  const ratio = Math.max(0, Math.min(1, (metrics.content.scrollTop - metrics.mediaStart) / metrics.mediaRange))
  const thumbTop = Math.round(ratio * maxThumbTop)
  sharedScrollbarThumbTop.value = thumbTop
  sharedScrollbarBubbleTop.value = thumbTop + thumbHeight / 2
  const layout = sharedMediaLayout()
  const current = Math.min(
    sharedMediaTotal.value,
    Math.max(1, Math.floor(Math.max(0, metrics.content.scrollTop - metrics.virtualTop) / layout.rowHeight) * layout.columns + 1),
  )
  sharedScrollbarLabel.value = `${current} / ${sharedMediaTotal.value}`
}

function scheduleSharedScrollbarUpdate() {
  if (sharedScrollbarFrame) return
  sharedScrollbarFrame = requestAnimationFrame(() => {
    sharedScrollbarFrame = 0
    updateSharedMediaWindow()
    updateSharedScrollbar()
  })
}

function showSharedScrollbarBubble(sticky = false) {
  if (!sharedScrollbarVisible.value) return
  sharedScrollbarBubbleVisible.value = true
  if (sharedScrollbarBubbleTimer) window.clearTimeout(sharedScrollbarBubbleTimer)
  if (!sticky) {
    sharedScrollbarBubbleTimer = window.setTimeout(() => {
      sharedScrollbarBubbleVisible.value = false
    }, 850)
  }
}

function handleSharedMediaScroll() {
  scheduleSharedScrollbarUpdate()
  scheduleArchiveSnapshot()
  showSharedScrollbarBubble()
}

function setSharedScrollFromPointer(clientY: number) {
  const content = infoScroll.value
  const scrollbar = sharedScrollbar.value
  const thumb = sharedScrollbarThumb.value
  if (!content || !scrollbar || !thumb || !sharedScrollbarDrag) return
  const metrics = sharedMediaScrollMetrics()
  if (!metrics || !metrics.mediaScrollable) return
  const rect = scrollbar.getBoundingClientRect()
  const thumbHeight = thumb.offsetHeight || 42
  const maxThumbTop = Math.max(1, rect.height - thumbHeight)
  const thumbTop = Math.max(0, Math.min(maxThumbTop, clientY - rect.top - sharedScrollbarDrag.offsetY))
  content.scrollTop = metrics.mediaStart + thumbTop / maxThumbTop * metrics.mediaRange
  updateSharedMediaWindow()
  updateSharedScrollbar()
  showSharedScrollbarBubble(true)
}

function handleSharedScrollbarPointerDown(event: PointerEvent) {
  const scrollbar = sharedScrollbar.value
  const thumb = sharedScrollbarThumb.value
  if (!scrollbar || !thumb) return
  const thumbRect = thumb.getBoundingClientRect()
  const offsetY = Math.max(0, Math.min(thumbRect.height, event.clientY - thumbRect.top))
  sharedScrollbarDrag = { pointerId: event.pointerId, offsetY }
  sharedScrollbarDragging.value = true
  sharedScrollbarBubbleVisible.value = true
  if (sharedScrollbarBubbleTimer) window.clearTimeout(sharedScrollbarBubbleTimer)
  thumb.setPointerCapture(event.pointerId)
  setSharedScrollFromPointer(event.clientY)
  event.preventDefault()
}

function handleSharedScrollbarPointerMove(event: PointerEvent) {
  if (!sharedScrollbarDrag || sharedScrollbarDrag.pointerId !== event.pointerId) return
  setSharedScrollFromPointer(event.clientY)
  event.preventDefault()
}

function endSharedScrollbarDrag(event?: PointerEvent) {
  const thumb = sharedScrollbarThumb.value
  if (!sharedScrollbarDrag || !thumb) return
  const pointerId = sharedScrollbarDrag.pointerId
  sharedScrollbarDrag = null
  sharedScrollbarDragging.value = false
  if (thumb.hasPointerCapture(pointerId)) thumb.releasePointerCapture(pointerId)
  if (sharedScrollbarBubbleTimer) window.clearTimeout(sharedScrollbarBubbleTimer)
  sharedScrollbarBubbleTimer = window.setTimeout(() => {
    sharedScrollbarBubbleVisible.value = false
  }, 650)
  event?.preventDefault()
}

async function fetchSharedMediaPage(page: number) {
  const chat = selected.value
  if (!chat || page < 0 || sharedMediaLoadedPages.has(page) || sharedMediaPendingPages.has(page)) return
  const requestId = sharedMediaRequestId
  const pendingPages = sharedMediaPendingPages
  pendingPages.add(page)
  if (page === 0) sharedMediaLoading.value = true
  else sharedMediaLoadingMore.value = true
  try {
    const kind = sharedMediaKinds.value.photo && sharedMediaKinds.value.video
      ? 'all'
      : sharedMediaKinds.value.photo ? 'photo' : 'video'
    const filter = sharedMediaTab.value === 'media' ? `&media_filter=${kind}` : ''
    const offset = page * SHARED_MEDIA_PAGE_SIZE
    const result = await api<ArchiveSharedMediaPage>(`/api/archive/chats/${chat.peer_id}/shared-media?type=${sharedMediaTab.value}&limit=${SHARED_MEDIA_PAGE_SIZE}&offset=${offset}${filter}`)
    if (requestId !== sharedMediaRequestId || selected.value?.peer_id !== chat.peer_id) return
    const indexed = { ...sharedMediaByIndex.value }
    result.items.forEach((item, index) => { indexed[offset + index] = item })
    sharedMediaByIndex.value = indexed
    sharedMediaTotal.value = result.total_count ?? Math.max(sharedMediaTotal.value, offset + result.items.length)
    sharedMediaLoadedPages.add(page)
    sharedMediaHasMore.value = Object.keys(indexed).length < sharedMediaTotal.value
  } catch (value) {
    if (requestId !== sharedMediaRequestId) return
    sharedMediaFailedPages.add(page)
    sharedMediaError.value = value instanceof Error ? value.message : '无法读取共享媒体'
  } finally {
    pendingPages.delete(page)
    if (requestId === sharedMediaRequestId) {
      if (page === 0) sharedMediaLoading.value = false
      sharedMediaLoadingMore.value = [...sharedMediaPendingPages].some((pendingPage) => pendingPage > 0)
      await nextTick()
      updateSharedMediaWindow()
      updateSharedScrollbar()
    }
  }
}

async function loadSharedMedia(append = false) {
  if (append) {
    const nextPage = Math.floor(sharedMediaItems.value.length / SHARED_MEDIA_PAGE_SIZE)
    await fetchSharedMediaPage(nextPage)
    return
  }
  sharedMediaRequestId += 1
  sharedMediaLoadedPages = new Set<number>()
  sharedMediaPendingPages = new Set<number>()
  sharedMediaFailedPages = new Set<number>()
  sharedMediaByIndex.value = {}
  sharedMediaTotal.value = 0
  sharedMediaWindow.value = { start: 0, end: -1 }
  sharedMediaPosters.value = {}
  sharedMediaError.value = ''
  sharedMediaHasMore.value = false
  await fetchSharedMediaPage(0)
}

async function switchSharedMediaTab(tab: SharedMediaTab) {
  if (sharedMediaTab.value === tab) return
  sharedMediaScrollTops[sharedMediaTab.value] = infoScroll.value?.scrollTop || 0
  sharedMediaTab.value = tab
  await loadSharedMedia(false)
  await nextTick()
  const mediaTop = infoScroll.value?.querySelector<HTMLElement>('.archive-shared-media')?.offsetTop || 0
  if (infoScroll.value) infoScroll.value.scrollTop = sharedMediaScrollTops[tab] ?? mediaTop
  updateSharedScrollbar()
  scheduleArchiveSnapshot()
}

function toggleSharedMediaKind(kind: 'photo' | 'video') {
  const current = sharedMediaKinds.value
  if (current[kind] && !current[kind === 'photo' ? 'video' : 'photo']) return
  sharedMediaKinds.value = { ...current, [kind]: !current[kind] }
  void loadSharedMedia(false).then(() => scheduleArchiveSnapshot())
}

async function openSharedContextMenu(
  item: ArchiveSharedMediaItem,
  event: { clientX: number; clientY: number; preventDefault?: () => void },
) {
  event.preventDefault?.()
  const padding = 8
  const menuWidth = 176
  const menuHeight = 52
  sharedContextMenu.value = {
    item,
    x: Math.max(padding, Math.min(event.clientX, window.innerWidth - menuWidth - padding)),
    y: Math.max(padding, Math.min(event.clientY, window.innerHeight - menuHeight - padding)),
  }
  await nextTick()
  sharedContextMenuButton.value?.focus({ preventScroll: true })
}

function cancelSharedLongPress() {
  if (sharedLongPressTimer) window.clearTimeout(sharedLongPressTimer)
  sharedLongPressTimer = 0
  sharedLongPress = null
}

function startSharedLongPress(item: ArchiveSharedMediaItem, event: PointerEvent, viewer = false) {
  if (event.pointerType === 'mouse' || !event.isPrimary || event.button !== 0) return
  cancelSharedLongPress()
  sharedLongPress = {
    item,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    viewer,
  }
  sharedLongPressTimer = window.setTimeout(() => {
    const press = sharedLongPress
    if (!press) return
    sharedLongPressTimer = 0
    sharedLongPressSuppression = { mediaId: press.item.id, expiresAt: performance.now() + 900 }
    if (press.viewer) suppressViewerClick = true
    void openSharedContextMenu(press.item, { clientX: press.startX, clientY: press.startY })
  }, 520)
}

function moveSharedLongPress(event: PointerEvent) {
  const press = sharedLongPress
  if (!press || press.pointerId !== event.pointerId) return
  if (Math.hypot(event.clientX - press.startX, event.clientY - press.startY) > 10) {
    cancelSharedLongPress()
  }
}

function endSharedLongPress(event: PointerEvent) {
  if (sharedLongPress?.pointerId !== event.pointerId) return
  cancelSharedLongPress()
}

function releaseViewerPointer(pointerId?: number) {
  const origin = viewerDragOrigin
  if (!origin || (pointerId !== undefined && origin.pointerId !== pointerId)) return
  const stage = viewerStage.value
  if (stage?.hasPointerCapture(origin.pointerId)) {
    try { stage.releasePointerCapture(origin.pointerId) } catch { /* capture already ended */ }
  }
  if (origin.mode === 'swipe') {
    const elapsed = Math.max(16, performance.now() - origin.startedAt)
    const velocity = viewerSwipeX.value / elapsed * 1000
    const threshold = Math.min(110, Math.max(54, window.innerWidth * .16))
    const direction = viewerSwipeX.value < 0 ? 1 : -1
    const canMove = viewerIndex.value !== null
      && viewerIndex.value + direction >= 0
      && viewerIndex.value + direction < activeViewerItems.value.length
    if (canMove && (Math.abs(viewerSwipeX.value) >= threshold || Math.abs(velocity) >= 520)) {
      moveViewer(direction)
    } else {
      viewerSwipeX.value = 0
    }
  } else {
    viewerPan.value = clampViewerPan(viewerPan.value)
  }
  viewerDragOrigin = null
  viewerDragging.value = false
}

function handleGlobalPointerEnd(event: PointerEvent) {
  endSharedLongPress(event)
  releaseViewerPointer(event.pointerId)
  if (sharedScrollbarDrag?.pointerId === event.pointerId) endSharedScrollbarDrag()
}

function resetPointerInteractions() {
  cancelSharedLongPress()
  sharedLongPressSuppression = null
  releaseViewerPointer()
  endSharedScrollbarDrag()
}

function preventSharedNativeDrag(event: DragEvent) {
  const target = event.target as HTMLElement | null
  if (!target?.closest('.archive-shared-virtual-item, .archive-viewer-stage')) return
  event.preventDefault()
  resetPointerInteractions()
}

function sharedMediaItemAtEvent(event: Event) {
  const target = event.target as HTMLElement | null
  const item = target?.closest<HTMLElement>('[data-shared-index]')
  const index = Number(item?.dataset.sharedIndex)
  return Number.isInteger(index) ? sharedMediaByIndex.value[index] : undefined
}

function handleSharedLongPressPointerDown(event: PointerEvent) {
  const item = sharedMediaItemAtEvent(event)
  if (item) startSharedLongPress(item, event)
}

function consumeSharedLongPressActivation(event: MouseEvent) {
  const item = sharedMediaItemAtEvent(event)
  const suppression = sharedLongPressSuppression
  if (!item || !suppression || suppression.mediaId !== item.id || performance.now() > suppression.expiresAt) return
  sharedLongPressSuppression = null
  event.preventDefault()
  event.stopPropagation()
}

function closeSharedContextMenu() {
  sharedContextMenu.value = null
}

function handleSharedContextPointerDown(event: PointerEvent) {
  if (!(event.target as HTMLElement).closest('.archive-shared-context-menu')) closeSharedContextMenu()
}

async function goToSharedMessage() {
  const messageId = sharedContextMenu.value?.item.message_id
  if (!messageId) return
  closeSharedContextMenu()
  if (isMobileArchive()) {
    const navigationId = ++archiveNavigationSequence
    flushArchiveSnapshot()
    await jumpToMessage(messageId, true, false)
    if (navigationId !== archiveNavigationSequence) return
    mobileTransitionFrom.value = 'info'
    mobileTransitionDirection.value = 'forward'
    if (viewerIndex.value !== null) closeViewer(true)
    infoOpen.value = false
    writeArchiveRoute('thread', false, { thread: captureThreadSnapshot() || { messageId, offset: 0, atBottom: false } })
    flushArchiveSnapshot()
    return
  }
  await jumpToMessage(messageId)
}

function openViewerSharedContextMenu(event: MouseEvent) {
  if (viewerOrigin.value !== 'shared' || !activeViewerItem.value) return
  void openSharedContextMenu(activeViewerItem.value.media as ArchiveSharedMediaItem, event)
}

function sharedMediaDuration(item: ArchiveSharedMediaItem) {
  const value = Number(item.content?.duration || 0)
  return value > 0 ? formatMediaDuration(value) : ''
}

function sharedMediaDomain(item: ArchiveSharedMediaItem) {
  try { return new URL(item.url).hostname.replace(/^www\./, '') } catch { return item.content?.site_name || '链接' }
}

function closeViewer(fromHistory = false) {
  if (!fromHistory && popArchiveOverlay('viewer')) return
  viewerIndex.value = null
  resetViewerTransform()
  document.body.classList.remove('archive-viewer-open')
}

function moveViewer(direction: number) {
  if (viewerIndex.value === null) return
  const next = viewerIndex.value + direction
  if (next < 0 || next >= activeViewerItems.value.length) return
  viewerIndex.value = next
  resetViewerTransform()
}

function resetViewerTransform() {
  viewerZoom.value = 1
  viewerPan.value = { x: 0, y: 0 }
  viewerSwipeX.value = 0
  viewerDragging.value = false
  viewerDragOrigin = null
}

function viewerContentGeometry() {
  const stage = viewerStage.value
  if (!stage) return null
  const rect = stage.getBoundingClientRect()
  const styles = window.getComputedStyle(stage)
  const paddingTop = Number.parseFloat(styles.paddingTop) || 0
  const paddingBottom = Number.parseFloat(styles.paddingBottom) || 0
  const contentHeight = Math.max(1, stage.clientHeight - paddingTop - paddingBottom)
  return {
    width: stage.clientWidth,
    height: contentHeight,
    centerX: rect.left + stage.clientWidth / 2,
    centerY: rect.top + paddingTop + contentHeight / 2,
  }
}

function clampViewerPan(pan: { x: number; y: number }, scale = viewerZoom.value) {
  const geometry = viewerContentGeometry()
  const media = viewerMedia.value
  if (!geometry || !media || scale <= 1) return { x: 0, y: 0 }
  const maxX = Math.max(0, (media.offsetWidth * scale - geometry.width) / 2)
  const maxY = Math.max(0, (media.offsetHeight * scale - geometry.height) / 2)
  return {
    x: Math.min(maxX, Math.max(-maxX, pan.x)),
    y: Math.min(maxY, Math.max(-maxY, pan.y)),
  }
}

function setViewerZoom(value: number, clientX?: number, clientY?: number) {
  const previous = viewerZoom.value
  const next = Math.min(4, Math.max(1, Math.round(value * 100) / 100))
  if (next === previous) return
  const geometry = viewerContentGeometry()
  let pan = viewerPan.value
  if (geometry && clientX !== undefined && clientY !== undefined) {
    const focusX = clientX - geometry.centerX
    const focusY = clientY - geometry.centerY
    const ratio = next / previous
    pan = {
      x: focusX - (focusX - pan.x) * ratio,
      y: focusY - (focusY - pan.y) * ratio,
    }
  } else if (next === 1) {
    pan = { x: 0, y: 0 }
  }
  viewerZoom.value = next
  viewerPan.value = clampViewerPan(pan, next)
}

function handleViewerWheel(event: WheelEvent) {
  const magnitude = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? .18 : .0024 * Math.abs(event.deltaY)
  const direction = event.deltaY < 0 ? 1 : -1
  setViewerZoom(viewerZoom.value + direction * Math.min(.35, Math.max(.1, magnitude)), event.clientX, event.clientY)
}

function handleViewerPointerDown(event: PointerEvent) {
  if (viewerOrigin.value === 'shared' && activeViewerItem.value) {
    startSharedLongPress(activeViewerItem.value.media as ArchiveSharedMediaItem, event, true)
  }
  if (event.button !== 0 || !event.isPrimary) return
  const target = event.currentTarget as HTMLElement
  target.setPointerCapture(event.pointerId)
  viewerDragOrigin = {
    pointerId: event.pointerId,
    clientX: event.clientX,
    clientY: event.clientY,
    startedAt: performance.now(),
    x: viewerPan.value.x,
    y: viewerPan.value.y,
    mode: viewerZoom.value > 1 ? 'pan' : 'swipe',
  }
  suppressViewerClick = false
  event.preventDefault()
}

function handleViewerPointerMove(event: PointerEvent) {
  moveSharedLongPress(event)
  const origin = viewerDragOrigin
  if (!origin || origin.pointerId !== event.pointerId) return
  const deltaX = event.clientX - origin.clientX
  const deltaY = event.clientY - origin.clientY
  if (Math.hypot(deltaX, deltaY) > 4) {
    suppressViewerClick = true
    viewerDragging.value = true
  }
  if (origin.mode === 'swipe') {
    const atFirst = viewerIndex.value === 0 && deltaX > 0
    const atLast = viewerIndex.value === activeViewerItems.value.length - 1 && deltaX < 0
    viewerSwipeX.value = (atFirst || atLast) ? deltaX * .28 : deltaX
  } else {
    viewerPan.value = clampViewerPan({ x: origin.x + deltaX, y: origin.y + deltaY })
  }
}

function handleViewerPointerUp(event: PointerEvent) {
  endSharedLongPress(event)
  releaseViewerPointer(event.pointerId)
}

function handleViewerStageClick(event: MouseEvent) {
  if (suppressViewerClick) {
    suppressViewerClick = false
    return
  }
  if (viewerZoom.value !== 1) return
  if ((event.target as HTMLElement).closest('video')) return
  closeViewer()
}

function viewerMediaStyle() {
  const translateX = viewerZoom.value === 1 ? viewerSwipeX.value : viewerPan.value.x
  return {
    transform: `translate3d(${translateX.toFixed(2)}px, ${viewerPan.value.y.toFixed(2)}px, 0) scale(${viewerZoom.value})`,
  }
}

function handleViewerKeydown(event: KeyboardEvent) {
  if (sharedContextMenu.value && event.key === 'Escape') {
    closeSharedContextMenu()
    return
  }
  if (senderProfile.value && event.key === 'Escape') {
    closeSenderProfile()
    return
  }
  if (historyMessage.value && event.key === 'Escape') {
    closeMessageHistory()
    return
  }
  if (viewerIndex.value === null) return
  if (event.key === 'Escape') closeViewer()
  else if (event.key === 'ArrowLeft' && viewerZoom.value === 1) moveViewer(-1)
  else if (event.key === 'ArrowRight' && viewerZoom.value === 1) moveViewer(1)
  else if (event.key === '+' || event.key === '=') setViewerZoom(viewerZoom.value + 0.25)
  else if (event.key === '-') setViewerZoom(viewerZoom.value - 0.25)
}

function senderKey(message: ArchiveMessage) {
  const originKey = selected.value?.is_self && message.forward_info
    ? message.origin_sender?.peer_id ?? message.forward_info.name
    : null
  return `${message.out ? 'out' : 'in'}:${String(originKey ?? message.sender_id ?? message.post_author ?? 'incoming')}`
}

function entriesJoin(left: ArchiveEntry | undefined, right: ArchiveEntry | undefined) {
  if (!left || !right || left.first.out !== right.first.out) return false
  if (entryContentKind(left) === 'service' || entryContentKind(right) === 'service') return false
  if (senderKey(left.first) !== senderKey(right.first)) return false
  if (left.last.post_author !== right.first.post_author) return false
  if (entryButtons(left).length || entryButtons(right).length) return false
  if (entryIsDeleted(left) || entryIsDeleted(right)) return false
  if (dateKey(left.last.sent_at) !== dateKey(right.first.sent_at)) return false
  const leftAt = left.last.sent_at ? serverDate(left.last.sent_at).getTime() : Number.NaN
  const rightAt = right.first.sent_at ? serverDate(right.first.sent_at).getTime() : Number.NaN
  return Number.isFinite(leftAt) && Number.isFinite(rightAt)
    && rightAt >= leftAt && rightAt - leftAt <= 10 * 60 * 1000
}

function joinsPrevious(index: number) {
  return entriesJoin(displayEntries.value[index - 1], displayEntries.value[index])
}

function joinsNext(index: number) {
  return entriesJoin(displayEntries.value[index], displayEntries.value[index + 1])
}

function entryPosition(index: number) {
  const previous = joinsPrevious(index)
  const next = joinsNext(index)
  if (previous && next) return 'group-middle'
  if (previous) return 'group-last'
  if (next) return 'group-first'
  return 'standalone'
}

function messageAppendixPath(isOwn: boolean) {
  return isOwn
    ? 'M6 17H0V0c.193 2.84.876 5.767 2.05 8.782.904 2.325 2.446 4.485 4.625 6.48A1 1 0 016 17z'
    : 'M3 17h6V0c-.193 2.84-.876 5.767-2.05 8.782-.904 2.325-2.446 4.485-4.625 6.48A1 1 0 003 17z'
}

function showEntrySender(index: number, entry: ArchiveEntry) {
  if (entryContentKind(entry) === 'service') return false
  const anonymousOwn = !selected.value?.is_self
    && entry.first.out
    && Boolean(entry.first.sender?.peer_id && entry.first.sender.peer_id < 0)
  return (groupShowsSender() && !entry.first.out && !joinsPrevious(index))
    || (anonymousOwn && !joinsPrevious(index))
}

function showEntryAvatar(index: number, entry: ArchiveEntry) {
  if (entryContentKind(entry) === 'service') return false
  return groupShowsSender() && !entry.first.out && !joinsNext(index)
}

function messageSearchSnippet(item: ArchiveMessageSearchItem) {
  const text = (item.text || '').replace(/\s+/g, ' ').trim()
  const query = messageSearchQuery.value.trim()
  const index = text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase())
  if (!query || index < 0) return { before: text.slice(0, 150), match: '', after: text.length > 150 ? '…' : '' }
  const start = Math.max(0, index - 55)
  const end = Math.min(text.length, index + query.length + 80)
  return {
    before: `${start ? '…' : ''}${text.slice(start, index)}`,
    match: text.slice(index, index + query.length),
    after: `${text.slice(index + query.length, end)}${end < text.length ? '…' : ''}`,
  }
}

async function goToMessageSearchResult(index: number) {
  if (index < 0) return
  if (index >= messageSearchResults.value.length && messageSearchResults.value.length < messageSearchTotal.value) {
    await runMessageSearch(true)
  }
  const result = messageSearchResults.value[index]
  if (!result) return
  messageSearchActiveIndex.value = index
  await nextTick()
  document
    .querySelector<HTMLElement>('.archive-message-search-results > button.active')
    ?.scrollIntoView({ block: 'nearest' })
  await jumpToMessage(result.message_id)
}

async function runMessageSearch(append = false) {
  const chat = selected.value
  const query = messageSearchQuery.value.trim()
  if (!chat || !query) {
    messageSearchResults.value = []
    messageSearchTotal.value = 0
    messageSearchActiveIndex.value = -1
    messageSearchError.value = ''
    return
  }
  if (append && (messageSearchLoadingMore.value || messageSearchResults.value.length >= messageSearchTotal.value)) return
  const requestId = append ? messageSearchRequestId : ++messageSearchRequestId
  if (append) messageSearchLoadingMore.value = true
  else {
    messageSearchLoading.value = true
    messageSearchResults.value = []
    messageSearchTotal.value = 0
    messageSearchActiveIndex.value = -1
  }
  messageSearchError.value = ''
  try {
    const offset = append ? messageSearchResults.value.length : 0
    const result = await api<ArchiveMessageSearchPage>(
      `/api/archive/chats/${chat.peer_id}/search?q=${encodeURIComponent(query)}&offset=${offset}&limit=42`,
    )
    if (requestId !== messageSearchRequestId || selected.value?.peer_id !== chat.peer_id || messageSearchQuery.value.trim() !== query) return
    messageSearchResults.value = append ? [...messageSearchResults.value, ...result.items] : result.items
    messageSearchTotal.value = result.total_count
    if (!append && result.items.length) await goToMessageSearchResult(0)
  } catch (value) {
    if (requestId !== messageSearchRequestId) return
    messageSearchError.value = value instanceof Error ? value.message : '无法搜索消息'
  } finally {
    if (requestId === messageSearchRequestId) {
      messageSearchLoading.value = false
      messageSearchLoadingMore.value = false
    }
  }
}

function scheduleMessageSearch() {
  if (messageSearchTimer) window.clearTimeout(messageSearchTimer)
  if (!messageSearchQuery.value.trim()) {
    messageSearchRequestId += 1
    messageSearchResults.value = []
    messageSearchTotal.value = 0
    messageSearchActiveIndex.value = -1
    messageSearchLoading.value = false
    messageSearchError.value = ''
    return
  }
  messageSearchTimer = window.setTimeout(() => void runMessageSearch(false), 260)
}

async function openMessageSearch(recordHistory = true) {
  if (recordHistory) pushArchiveOverlay({ type: 'search', query: messageSearchQuery.value })
  messageSearchOpen.value = true
  infoOpen.value = false
  await nextTick()
  messageSearchInput.value?.focus()
}

function closeMessageSearch(fromHistory = false) {
  if (!fromHistory && popArchiveOverlay('search')) return
  if (messageSearchTimer) window.clearTimeout(messageSearchTimer)
  messageSearchRequestId += 1
  messageSearchOpen.value = false
  messageSearchQuery.value = ''
  messageSearchResults.value = []
  messageSearchTotal.value = 0
  messageSearchActiveIndex.value = -1
  messageSearchLoading.value = false
  messageSearchLoadingMore.value = false
  messageSearchError.value = ''
}

function handleMessageSearchKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeMessageSearch()
  } else if (event.key === 'Enter') {
    event.preventDefault()
    if (messageSearchActiveIndex.value >= 0) void goToMessageSearchResult(messageSearchActiveIndex.value)
    else void runMessageSearch(false)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    void goToMessageSearchResult(messageSearchActiveIndex.value + 1)
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    void goToMessageSearchResult(messageSearchActiveIndex.value - 1)
  }
}

function handleMessageSearchResultsScroll(event: Event) {
  const target = event.currentTarget as HTMLElement
  if (target.scrollHeight - target.scrollTop - target.clientHeight < 120
    && messageSearchResults.value.length < messageSearchTotal.value) {
    void runMessageSearch(true)
  }
}

async function jumpToMessage(messageId: number, highlight = true, smooth = highlight) {
  if (!selected.value || loadingMessages.value) return
  const existing = messages.value.some((message) => message.message_id === messageId)
  if (!smooth) messagePositionReady.value = false
  if (!existing) {
    loadingMessages.value = true
    messagePositionReady.value = false
    jumpNotice.value = ''
    try {
      const result = await api<ArchiveMessagePage>(
        `/api/archive/chats/${selected.value.peer_id}/messages?anchor_id=${messageId}&before=20&after=20`,
      )
      messages.value = result.items
      applyPageState(result)
      if (result.anchor_found === false) {
        jumpNotice.value = `消息 #${messageId} 未归档，已定位到附近的消息`
      }
      loadingMessages.value = false
      await nextTick()
      messageId = result.anchor_id || messageId
    } catch (value) {
      error.value = value instanceof Error ? value.message : '无法定位目标消息'
      messagePositionReady.value = true
      loadingMessages.value = false
      return
    }
  }
  await nextTick()
  const entry = displayEntries.value.find((item) => (
    item.items.some((message) => message.message_id === messageId)
  ))
  if (!entry) {
    messagePositionReady.value = true
    return
  }
  const target = document.getElementById(`archive-${entry.key}`)
  const viewport = messageViewport.value
  if (target && viewport) {
    const centerTarget = () => {
      const viewportRect = viewport.getBoundingClientRect()
      const targetRect = target.getBoundingClientRect()
      const centeredTop = viewport.scrollTop
        + targetRect.top - viewportRect.top
        - (viewport.clientHeight - targetRect.height) / 2
      viewport.scrollTo({
        top: Math.max(0, Math.min(centeredTop, viewport.scrollHeight - viewport.clientHeight)),
        behavior: smooth && existing ? 'smooth' : 'auto',
      })
    }
    centerTarget()
    if (!smooth) {
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
      centerTarget()
    }
  }
  messagePositionReady.value = true
  if (highlight) {
    target?.classList.add('is-highlighted')
    window.setTimeout(() => target?.classList.remove('is-highlighted'), 1400)
  }
}

function applyPageState(result: ArchiveMessagePage) {
  hasOlder.value = result.has_older
  hasNewer.value = result.has_newer ?? false
  nextBeforeId.value = result.next_before_id || null
  nextAfterId.value = result.next_after_id || null
}

function mergeMessages(items: ArchiveMessage[]) {
  const byId = new Map<number, ArchiveMessage>()
  for (const message of [...messages.value, ...items]) byId.set(message.message_id, message)
  return [...byId.values()].sort((left, right) => left.message_id - right.message_id)
}

interface MessageScrollAnchor {
  elementId: string
  messageId: number | null
  top: number
  scrollTop: number
  scrollHeight: number
}

function visibleScrollAnchor(): MessageScrollAnchor | null {
  const viewport = messageViewport.value
  if (!viewport) return null
  const elements = [...viewport.querySelectorAll<HTMLElement>('.archive-message-line')]
  const element = elements.find((candidate) => candidate.getBoundingClientRect().bottom >= viewport.getBoundingClientRect().top)
  if (!element) return null
  const firstMessageId = Number(element.dataset.messageIds?.split(' ')[0])
  return {
    elementId: element.id,
    messageId: Number.isFinite(firstMessageId) ? firstMessageId : null,
    top: element.getBoundingClientRect().top,
    scrollTop: viewport.scrollTop,
    scrollHeight: viewport.scrollHeight,
  }
}

function restoreScrollAnchor(anchor: MessageScrollAnchor | null) {
  const viewport = messageViewport.value
  if (!viewport || !anchor) return
  // Prepending can complete an album whose Vue key is based on its former
  // first message. Fall back to a stable Telegram message id when that makes
  // the old DOM id disappear.
  const element = document.getElementById(anchor.elementId)
    || (anchor.messageId === null
      ? null
      : viewport.querySelector<HTMLElement>(`[data-message-ids~="${anchor.messageId}"]`))
  if (element) {
    viewport.scrollTop += element.getBoundingClientRect().top - anchor.top
  } else {
    // A line may still be replaced by regrouping or filtering. Preserving the
    // inserted height is safer than leaving scrollTop at zero.
    viewport.scrollTop = anchor.scrollTop + viewport.scrollHeight - anchor.scrollHeight
  }
  updateStickyDate()
}

async function stabilizeScrollAnchor(anchor: MessageScrollAnchor | null) {
  restoreScrollAnchor(anchor)
  if (!anchor) return
  // Cached thumbnails and child components can settle one or two frames after
  // Vue's nextTick. Reapply the same stable anchor before unlocking loading.
  for (let frame = 0; frame < 2; frame += 1) {
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
    restoreScrollAnchor(anchor)
  }
}

function nearBottom() {
  const viewport = messageViewport.value
  if (!viewport) return true
  return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 90
}

function updateStickyDate() {
  const viewport = messageViewport.value
  if (!viewport) {
    stickyDateLabel.value = ''
    return
  }
  const viewportTop = viewport.getBoundingClientRect().top
  let active = ''
  viewport.querySelectorAll<HTMLElement>('.archive-date-separator').forEach((separator) => {
    if (separator.getBoundingClientRect().top <= viewportTop + 8) {
      active = separator.dataset.dateLabel || ''
    }
  })
  stickyDateLabel.value = active
}

async function scrollToBottom(smooth = false) {
  await nextTick()
  const viewport = messageViewport.value
  if (viewport) {
    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior: smooth ? 'smooth' : 'auto',
    })
  }
  newMessageCount.value = 0
  updateStickyDate()
}

async function loadChats() {
  loadingChats.value = true
  error.value = ''
  try {
    const result = await api<{ items: ArchiveChat[] }>('/api/archive/chats')
    chats.value = result.items
    const mobileRoute = archiveRouteFromLocation()
    if (selected.value) {
      selected.value = result.items.find((item) => item.peer_id === selected.value?.peer_id) || null
    }
    if (!selected.value && result.items.length) {
      selected.value = result.items.find((item) => item.peer_id === mobileRoute.peerId) || result.items[0]
      await Promise.all([loadMessages(false), loadSharedMedia(false)])
    }
    if (isMobileArchive()) {
      const routeMatchesSelection = mobileRoute.peerId !== null && mobileRoute.peerId === selected.value?.peer_id
      mobileThreadOpen.value = routeMatchesSelection && mobileRoute.layer !== 'list'
      infoOpen.value = routeMatchesSelection && mobileRoute.layer === 'info'
      if (mobileRoute.peerId !== null && !routeMatchesSelection) {
        writeArchiveRoute('list', true)
      } else {
        const layer = routeMatchesSelection ? mobileRoute.layer : 'list'
        ensureArchiveHistoryFrame(layer, routeMatchesSelection ? mobileRoute.peerId : null)
        const frame = currentArchiveFrame()
        lastAppliedArchiveEntry = frame?.entryId || ''
        activeArchiveStackIndex = frame?.stackIndex ?? 0
        if (layer === 'info') {
          await nextTick()
          await restoreInfoSnapshot(frame?.info)
        } else if (layer === 'thread') {
          await nextTick()
          await restoreThreadSnapshot(frame?.thread)
        } else {
          await restoreListSnapshot(frame?.list)
        }
        await restoreArchiveOverlay(frame?.overlay, archiveNavigationSequence)
      }
    }
  } catch (value) {
    error.value = value instanceof Error ? value.message : '无法读取聊天记录'
  } finally {
    loadingChats.value = false
  }
}

async function loadMessages(prepend: boolean) {
  const chat = selected.value
  if (!chat || (prepend ? loadingOlder.value : loadingMessages.value)) return
  const anchor = prepend ? visibleScrollAnchor() : null
  if (prepend) {
    // Keep this lock after the request finishes. Residual wheel events from
    // the same gesture must not immediately request another page when the
    // scroll anchor is restored.
    olderLoadNeedsFreshGesture = true
    loadingOlder.value = true
  }
  else {
    olderLoadNeedsFreshGesture = false
    loadingMessages.value = true
    messagePositionReady.value = false
  }
  error.value = ''
  try {
    const before = prepend && nextBeforeId.value ? `&before_id=${nextBeforeId.value}` : ''
    const result = await api<ArchiveMessagePage>(
      `/api/archive/chats/${chat.peer_id}/messages?limit=40${before}`,
    )
    if (selected.value?.peer_id !== chat.peer_id) return
    messages.value = prepend ? mergeMessages(result.items) : result.items
    if (prepend) {
      hasOlder.value = result.has_older
      nextBeforeId.value = result.next_before_id || null
      if (messages.value.length > MESSAGE_WINDOW_LIMIT) {
        messages.value = messages.value.slice(0, MESSAGE_WINDOW_LIMIT)
        hasNewer.value = true
        nextAfterId.value = messages.value.at(-1)?.message_id || null
      }
    } else {
      applyPageState(result)
    }
    // Render the new thread invisibly first, place it at the latest message,
    // then expose it. Otherwise the browser paints the top of the thread for
    // one frame before the initial scroll position is restored.
    if (!prepend) loadingMessages.value = false
    await nextTick()
    if (prepend) {
      await stabilizeScrollAnchor(anchor)
    } else if (!prepend) {
      await scrollToBottom()
      messagePositionReady.value = true
    }
  } catch (value) {
    error.value = value instanceof Error ? value.message : '无法读取消息'
    if (!prepend) messagePositionReady.value = true
  } finally {
    loadingMessages.value = false
    loadingOlder.value = false
  }
}

async function loadNewerMessages() {
  const chat = selected.value
  if (!chat || loadingNewer.value || !hasNewer.value || !nextAfterId.value) return
  const anchor = visibleScrollAnchor()
  loadingNewer.value = true
  error.value = ''
  try {
    const result = await api<ArchiveMessagePage>(
      `/api/archive/chats/${chat.peer_id}/messages?limit=40&after_id=${nextAfterId.value}`,
    )
    if (selected.value?.peer_id !== chat.peer_id) return
    messages.value = mergeMessages(result.items)
    hasNewer.value = result.has_newer ?? false
    nextAfterId.value = result.next_after_id || null
    if (messages.value.length > MESSAGE_WINDOW_LIMIT) {
      messages.value = messages.value.slice(-MESSAGE_WINDOW_LIMIT)
      hasOlder.value = true
      nextBeforeId.value = messages.value[0]?.message_id || null
    }
    await nextTick()
    restoreScrollAnchor(anchor)
  } catch (value) {
    error.value = value instanceof Error ? value.message : '无法读取更新的消息'
  } finally {
    loadingNewer.value = false
  }
}

async function returnToLatest() {
  if (!selected.value) return
  jumpNotice.value = ''
  newMessageCount.value = 0
  await loadMessages(false)
}

async function handleNewMessages() {
  if (hasNewer.value) await returnToLatest()
  else await scrollToBottom(true)
}

async function toggleInfoPane() {
  if (!isMobileArchive()) {
    infoOpen.value = !infoOpen.value
    return
  }
  if (infoOpen.value) {
    closeInfoPane()
    return
  }
  const navigationId = ++archiveNavigationSequence
  flushArchiveSnapshot()
  const snapshot = freshInfoSnapshot()
  await restoreInfoSnapshot(snapshot, navigationId)
  if (navigationId !== archiveNavigationSequence) return
  mobileTransitionFrom.value = 'thread'
  mobileTransitionDirection.value = 'forward'
  infoOpen.value = true
  writeArchiveRoute('info', false, { info: cloneInfoSnapshot(snapshot) })
  await restoreInfoSnapshot(snapshot, navigationId)
}

function closeInfoPane() {
  if (!isMobileArchive()) {
    infoOpen.value = false
    return
  }
  ++archiveNavigationSequence
  flushArchiveSnapshot()
  const frame = currentArchiveFrame()
  if (frame?.layer === 'info' && frame.stackIndex > 0) {
    window.history.back()
    return
  }
  mobileTransitionFrom.value = 'info'
  mobileTransitionDirection.value = 'back'
  infoOpen.value = false
  writeArchiveRoute('thread', true, { thread: captureThreadSnapshot() || undefined })
}

function closeMobileThread() {
  if (messageSearchOpen.value) {
    closeMessageSearch()
    return
  }
  if (!isMobileArchive()) {
    mobileThreadOpen.value = false
    return
  }
  ++archiveNavigationSequence
  flushArchiveSnapshot()
  const frame = currentArchiveFrame()
  if (frame?.layer === 'thread' && frame.stackIndex > 0) {
    window.history.back()
    return
  }
  infoOpen.value = false
  mobileTransitionFrom.value = 'thread'
  mobileTransitionDirection.value = 'back'
  mobileNavigationDirection.value = 'back'
  mobileThreadOpen.value = false
  writeArchiveRoute('list', true)
}

async function syncArchiveRouteFromLocation() {
  if (!isMobileArchive() || !window.location.hash.startsWith('#/archive')) return
  const route = archiveRouteFromLocation()
  const frame = currentArchiveFrame()
  const entryKey = frame?.entryId || `${window.location.hash}:${frame?.stackIndex ?? 'direct'}`
  if (entryKey === lastAppliedArchiveEntry) return
  const navigationId = ++archiveNavigationSequence
  const fromLayer: ArchiveMobileLayer = infoOpen.value ? 'info' : mobileThreadOpen.value ? 'thread' : 'list'
  const targetStackIndex = frame?.stackIndex ?? activeArchiveStackIndex
  const direction: 'forward' | 'back' = targetStackIndex < activeArchiveStackIndex ? 'back' : 'forward'
  mobileTransitionFrom.value = fromLayer
  mobileTransitionDirection.value = direction
  mobileNavigationDirection.value = direction
  activeArchiveStackIndex = targetStackIndex
  lastAppliedArchiveEntry = entryKey
  if (route.layer === 'list') {
    await restoreListSnapshot(frame?.list, navigationId)
    if (navigationId !== archiveNavigationSequence) return
    closeActiveArchiveOverlay()
    infoOpen.value = false
    if (mobileThreadOpen.value) mobileNavigationDirection.value = 'back'
    mobileThreadOpen.value = false
    return
  }
  const chat = chats.value.find((item) => item.peer_id === route.peerId)
  if (!chat) return
  if (selected.value?.peer_id !== chat.peer_id) await selectChat(chat, false)
  if (navigationId !== archiveNavigationSequence) return
  if (route.layer === 'info') {
    const snapshot = frame?.info
    await restoreInfoSnapshot(snapshot, navigationId)
    if (navigationId !== archiveNavigationSequence) return
    mobileThreadOpen.value = true
    infoOpen.value = true
    await nextTick()
    await restoreInfoSnapshot(snapshot, navigationId)
  } else {
    await restoreThreadSnapshot(frame?.thread, navigationId)
    if (navigationId !== archiveNavigationSequence) return
    mobileThreadOpen.value = true
    infoOpen.value = false
  }
  if (navigationId !== archiveNavigationSequence) return
  closeActiveArchiveOverlay()
  await restoreArchiveOverlay(frame?.overlay, navigationId)
}

async function selectChat(chat: ArchiveChat, updateMobileRoute = true) {
  if (updateMobileRoute) flushArchiveSnapshot()
  if (isMobileArchive() && updateMobileRoute) {
    ++archiveNavigationSequence
    mobileNavigationDirection.value = 'forward'
    mobileTransitionDirection.value = 'forward'
    mobileTransitionFrom.value = mobileThreadOpen.value ? 'thread' : 'list'
  }
  if (selected.value?.peer_id === chat.peer_id) {
    if (updateMobileRoute || !isMobileArchive()) {
      mobileThreadOpen.value = true
      if (window.innerWidth <= 760) infoOpen.value = false
    }
    if (updateMobileRoute && isMobileArchive()) writeArchiveRoute('thread')
    await scrollToBottom()
    if (updateMobileRoute) flushArchiveSnapshot()
    return
  }
  closeMessageSearch(true)
  selected.value = chat
  for (const tab of sharedMediaTabs) delete sharedMediaScrollTops[tab.type]
  closeSenderProfile(true)
  if (updateMobileRoute || !isMobileArchive()) {
    mobileThreadOpen.value = true
    infoOpen.value = window.innerWidth > 760
  }
  if (updateMobileRoute && isMobileArchive()) writeArchiveRoute('thread')
  messages.value = []
  hasOlder.value = false
  hasNewer.value = false
  nextBeforeId.value = null
  nextAfterId.value = null
  newMessageCount.value = 0
  stickyDateLabel.value = ''
  await Promise.all([loadMessages(false), loadSharedMedia(false)])
  if (updateMobileRoute) flushArchiveSnapshot()
}

function handleMessageScroll() {
  const viewport = messageViewport.value
  if (!viewport) return
  updateStickyDate()
  if (messagePositionReady.value) {
    if (viewport.scrollTop < 120 && hasOlder.value && !loadingOlder.value && !olderLoadNeedsFreshGesture) loadMessages(true)
    if (viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 120
      && hasNewer.value && !loadingNewer.value) loadNewerMessages()
  }
  if (nearBottom()) newMessageCount.value = 0
  scheduleArchiveSnapshot()
}

function handleMessageWheel(event: WheelEvent) {
  if (event.deltaY >= 0) return
  const now = performance.now()
  const startsFreshGesture = now - lastUpwardWheelAt > WHEEL_GESTURE_IDLE_MS
  lastUpwardWheelAt = now
  if (startsFreshGesture && olderLoadNeedsFreshGesture && !loadingOlder.value) {
    olderLoadNeedsFreshGesture = false
  }
}

function handleMessagePointerDown() {
  if (!loadingOlder.value) olderLoadNeedsFreshGesture = false
}

function handleMessageNavigationKey(event: KeyboardEvent) {
  if (!['ArrowUp', 'PageUp', 'Home'].includes(event.key)) return
  const target = event.target as HTMLElement | null
  if (target?.matches('input, textarea, [contenteditable="true"]')) return
  if (!loadingOlder.value) olderLoadNeedsFreshGesture = false
}

async function syncCurrentChat() {
  const chat = selected.value
  if (!chat) return
  const wasAtBottom = nearBottom()
  const oldMax = messages.value.at(-1)?.message_id || 0
  const result = await api<ArchiveMessagePage>(
    `/api/archive/chats/${chat.peer_id}/messages?limit=40`,
  )
  if (selected.value?.peer_id !== chat.peer_id) return
  if (hasNewer.value) {
    const unseen = result.items.filter((item) => item.message_id > oldMax).length
    if (unseen) newMessageCount.value = Math.max(newMessageCount.value, unseen)
    return
  }
  const latest = new Map(result.items.map((item) => [item.message_id, item]))
  const replaced = messages.value.map((item) => latest.get(item.message_id) || item)
  const appended = result.items.filter((item) => item.message_id > oldMax)
  messages.value = [...replaced, ...appended]
  if (appended.length && !wasAtBottom) newMessageCount.value += appended.length
  if (wasAtBottom) await scrollToBottom()
}

function handleRealtimeEvent(event: Event) {
  const message = (event as CustomEvent<RealtimeEvent>).detail
  if (!message || ![
    'telegram.backup.completed',
    'telegram.history.completed',
  ].includes(message.type)) return
  const peerId = Number(message.payload.peer_id)
  loadChats()
  if (selected.value?.peer_id === peerId) syncCurrentChat()
}

watch(displayEntries, () => {
  void nextTick(bindInlineVideoObserver)
}, { flush: 'post' })

watch(viewerIndex, () => {
  refreshInlineVideoPlayback()
})

watch(alwaysShowSpoilers, (value) => {
  window.localStorage.setItem('tg-backup-always-show-spoilers', String(value))
})

watch(query, scheduleArchiveSnapshot)

watch(infoOpen, (value) => {
  if (value && selected.value && !sharedMediaItems.value.length && !sharedMediaLoading.value) void loadSharedMedia(false)
})

watch(mobileThreadOpen, (value) => {
  emit('mobile-detail-change', value)
}, { immediate: true })

watch(() => [infoOpen.value, selected.value?.peer_id], async () => {
  await nextTick()
  infoScrollObserver?.disconnect()
  if (infoScroll.value) infoScrollObserver?.observe(infoScroll.value)
  updateSharedScrollbar()
})

watch(sharedMediaItems, () => {
  void nextTick(scheduleSharedScrollbarUpdate)
}, { flush: 'post' })

onMounted(() => {
  window.addEventListener('popstate', syncArchiveRouteFromLocation)
  window.addEventListener('hashchange', syncArchiveRouteFromLocation)
  window.addEventListener('tg-realtime-event', handleRealtimeEvent)
  window.addEventListener('keydown', handleViewerKeydown)
  window.addEventListener('keydown', handleMessageNavigationKey)
  document.addEventListener('visibilitychange', handleDocumentVisibility)
  document.addEventListener('pointerdown', handleSharedContextPointerDown)
  window.addEventListener('pointermove', moveSharedLongPress, true)
  window.addEventListener('pointerup', handleGlobalPointerEnd, true)
  window.addEventListener('pointercancel', handleGlobalPointerEnd, true)
  window.addEventListener('blur', resetPointerInteractions)
  document.addEventListener('dragstart', preventSharedNativeDrag, true)
  document.addEventListener('scroll', closeSharedContextMenu, true)
  window.addEventListener('resize', closeSharedContextMenu)
  viewportObserver = new ResizeObserver(([entry]) => {
    messageViewportWidth.value = entry.contentRect.width
  })
  infoScrollObserver = new ResizeObserver(scheduleSharedScrollbarUpdate)
  if (messageViewport.value) viewportObserver.observe(messageViewport.value)
  loadChats()
})
onBeforeUnmount(() => {
  flushArchiveSnapshot()
  emit('mobile-detail-change', false)
  window.removeEventListener('popstate', syncArchiveRouteFromLocation)
  window.removeEventListener('hashchange', syncArchiveRouteFromLocation)
  window.removeEventListener('tg-realtime-event', handleRealtimeEvent)
  window.removeEventListener('keydown', handleViewerKeydown)
  window.removeEventListener('keydown', handleMessageNavigationKey)
  document.removeEventListener('visibilitychange', handleDocumentVisibility)
  document.removeEventListener('pointerdown', handleSharedContextPointerDown)
  window.removeEventListener('pointermove', moveSharedLongPress, true)
  window.removeEventListener('pointerup', handleGlobalPointerEnd, true)
  window.removeEventListener('pointercancel', handleGlobalPointerEnd, true)
  window.removeEventListener('blur', resetPointerInteractions)
  document.removeEventListener('dragstart', preventSharedNativeDrag, true)
  document.removeEventListener('scroll', closeSharedContextMenu, true)
  window.removeEventListener('resize', closeSharedContextMenu)
  viewportObserver?.disconnect()
  infoScrollObserver?.disconnect()
  inlineVideoObserver?.disconnect()
  if (sharedScrollbarFrame) cancelAnimationFrame(sharedScrollbarFrame)
  if (archiveSnapshotTimer) window.clearTimeout(archiveSnapshotTimer)
  if (sharedScrollbarBubbleTimer) window.clearTimeout(sharedScrollbarBubbleTimer)
  cancelSharedLongPress()
  if (messageSearchTimer) window.clearTimeout(messageSearchTimer)
  visibleInlineVideos.forEach((video) => video.pause())
  visibleInlineVideos.clear()
  document.body.classList.remove('archive-viewer-open')
})
</script>

<template>
  <div :class="['archive-layout', `mobile-transition-${mobileTransitionDirection}`, `mobile-from-${mobileTransitionFrom}`, { 'mobile-thread-open': mobileThreadOpen, 'mobile-nav-forward': mobileNavigationDirection === 'forward', 'mobile-nav-back': mobileNavigationDirection === 'back', 'info-open': Boolean(selected && infoOpen) }]">
    <aside class="archive-chat-pane" aria-label="已备份会话">
      <header class="archive-list-header">
        <div><p class="eyebrow">聊天记录</p><h1>消息</h1></div>
        <span>{{ chats.length }}</span>
      </header>
      <label class="archive-search">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20.7 19.3-4.1-4.1a7.5 7.5 0 1 0-1.4 1.4l4.1 4.1 1.4-1.4ZM5 11a6 6 0 1 1 12 0 6 6 0 0 1-12 0Z" /></svg>
        <input v-model="query" aria-label="搜索已备份会话" placeholder="搜索" />
      </label>
      <div v-if="loadingChats" class="archive-loading"><span class="spinner small"></span>正在读取</div>
      <div v-else-if="error && !chats.length" class="archive-empty"><strong>无法读取聊天记录</strong><span>{{ error }}</span><button class="button secondary compact" @click="loadChats">重试</button></div>
      <div v-else-if="!chats.length" class="archive-empty"><strong>还没有已备份消息</strong><span>为会话创建规则并完成一次备份后，消息会出现在这里。</span></div>
      <div v-else ref="chatList" class="archive-chat-list" @scroll.passive="scheduleArchiveSnapshot">
        <button v-for="chat in filteredChats" :key="chat.peer_id" :class="['archive-chat-item', { active: selected?.peer_id === chat.peer_id }]" @click="selectChat(chat)">
          <span :class="['chat-avatar', { 'saved-messages-avatar': chat.is_self }]" :style="avatarHue(chat.peer_id)">
            <SavedMessagesIcon v-if="chat.is_self" />
            <img v-else-if="chat.avatar_url" :src="chat.avatar_url" :alt="`${chat.title}头像`" />
            <template v-else>{{ avatarText(chat) }}</template>
          </span>
          <span class="archive-chat-copy">
            <span><strong>{{ chat.title }}</strong><time>{{ formatListTime(chat.last_message_at) }}</time></span>
            <span><small>{{ chat.last_message }}</small><i v-if="chat.rule_status !== 'active'">{{ ruleStatusLabel[chat.rule_status] }}</i></span>
          </span>
        </button>
        <div v-if="!filteredChats.length" class="archive-empty compact">没有匹配的会话</div>
      </div>
    </aside>

    <main v-if="selected" class="archive-message-pane">
      <header class="archive-thread-header">
        <button class="archive-back" aria-label="返回上一层" @click="closeMobileThread"><svg viewBox="0 0 24 24"><path d="m15 5-7 7 7 7" /></svg></button>
        <span :class="['chat-avatar', 'thread-avatar', { 'saved-messages-avatar': selected.is_self }]" :style="avatarHue(selected.peer_id)">
          <SavedMessagesIcon v-if="selected.is_self" />
          <img v-else-if="selected.avatar_url" :src="selected.avatar_url" :alt="`${selected.title}头像`" />
          <template v-else>{{ avatarText(selected) }}</template>
        </span>
        <div class="archive-thread-title"><strong>{{ selected.title }}</strong><span>{{ selected.message_count }} 条已备份消息 · {{ ruleStatusLabel[selected.rule_status] }}</span></div>
        <button class="archive-icon-button" aria-label="搜索消息" title="搜索消息" @click="openMessageSearch()"><svg viewBox="0 0 24 24"><path d="m20.7 19.3-4.1-4.1a7.5 7.5 0 1 0-1.4 1.4l4.1 4.1 1.4-1.4ZM5 11a6 6 0 1 1 12 0 6 6 0 0 1-12 0Z" /></svg></button>
        <button :class="['archive-icon-button', { active: infoOpen }]" aria-label="会话信息" @click="toggleInfoPane"><svg viewBox="0 0 24 24"><path d="M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0-11v6m0-10v.1" /></svg></button>
      </header>

      <section v-if="messageSearchOpen" class="archive-message-search" aria-label="搜索消息">
        <header>
          <button aria-label="关闭搜索" title="关闭搜索" @click="closeMessageSearch()"><svg viewBox="0 0 24 24"><path d="m15 5-7 7 7 7" /></svg></button>
          <label>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20.7 19.3-4.1-4.1a7.5 7.5 0 1 0-1.4 1.4l4.1 4.1 1.4-1.4ZM5 11a6 6 0 1 1 12 0 6 6 0 0 1-12 0Z" /></svg>
            <input ref="messageSearchInput" v-model="messageSearchQuery" type="search" aria-label="搜索当前会话消息" placeholder="搜索" autocomplete="off" @input="scheduleMessageSearch" @keydown="handleMessageSearchKeydown" />
            <span v-if="messageSearchLoading" class="spinner small"></span>
            <button v-else-if="messageSearchQuery" type="button" aria-label="清除搜索" title="清除" @click="messageSearchQuery = ''; scheduleMessageSearch(); messageSearchInput?.focus()"><svg viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17" /></svg></button>
          </label>
          <span class="archive-message-search-count" aria-live="polite">{{ messageSearchTotal ? `${Math.max(1, messageSearchActiveIndex + 1)} / ${messageSearchTotal}` : messageSearchQuery && !messageSearchLoading ? '无结果' : '' }}</span>
          <button aria-label="较旧的结果" title="较旧的结果" :disabled="messageSearchActiveIndex < 0 || messageSearchActiveIndex + 1 >= messageSearchTotal" @click="goToMessageSearchResult(messageSearchActiveIndex + 1)"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" /></svg></button>
          <button aria-label="较新的结果" title="较新的结果" :disabled="messageSearchActiveIndex <= 0" @click="goToMessageSearchResult(messageSearchActiveIndex - 1)"><svg viewBox="0 0 24 24"><path d="m6 15 6-6 6 6" /></svg></button>
        </header>
        <div v-if="messageSearchQuery" class="archive-message-search-dropdown">
          <div v-if="messageSearchError" class="archive-message-search-state error"><span>{{ messageSearchError }}</span><button @click="runMessageSearch(false)">重试</button></div>
          <div v-else-if="messageSearchLoading" class="archive-message-search-state"><span class="spinner small"></span><span>正在搜索</span></div>
          <div v-else-if="!messageSearchResults.length" class="archive-message-search-state">没有找到匹配的消息</div>
          <div v-else class="archive-message-search-results" @scroll.passive="handleMessageSearchResultsScroll">
            <button v-for="(result, resultIndex) in messageSearchResults" :key="result.message_id" :class="{ active: resultIndex === messageSearchActiveIndex }" @click="goToMessageSearchResult(resultIndex)">
              <span><strong>{{ result.sender_name || (selected.is_self ? '我' : selected.title) }}</strong><time>{{ formatListTime(result.sent_at) }}</time></span>
              <small><template v-for="(part, partIndex) in [messageSearchSnippet(result)]" :key="partIndex">{{ part.before }}<mark v-if="part.match">{{ part.match }}</mark>{{ part.after }}</template></small>
              <em v-if="result.is_history_version">历史版本 {{ result.matched_version }}</em>
            </button>
            <div v-if="messageSearchLoadingMore" class="archive-message-search-more"><span class="spinner small"></span>正在加载更多</div>
          </div>
        </div>
      </section>

      <div ref="messageViewport" :class="['archive-message-viewport', { 'is-prepending': loadingOlder }]" @scroll.passive="handleMessageScroll" @wheel.passive="handleMessageWheel" @pointerdown="handleMessagePointerDown">
        <div v-if="loadingMessages" class="archive-message-loading"><span class="spinner"></span><span>正在读取消息</span></div>
        <div v-else-if="!messages.length" class="archive-message-loading"><span>这个会话暂时没有可显示的消息</span></div>
        <div v-else :class="['archive-message-flow', { 'is-positioning': !messagePositionReady }]">
          <div v-if="loadingOlder" class="archive-older"><span class="spinner small"></span>正在加载更早消息</div>
          <div v-else-if="hasOlder" class="archive-older">向上滚动加载更早消息</div>
          <template v-for="(entry, index) in displayEntries" :key="entry.key">
            <div v-if="showDate(index)" class="archive-date-separator" :data-date-label="dateLabel(entry.first.sent_at)"><span>{{ dateLabel(entry.first.sent_at) }}</span></div>
            <div
              :id="`archive-${entry.key}`"
              :data-message-ids="entry.items.map((item) => item.message_id).join(' ')"
              :class="['archive-message-line', entryPosition(index), {
                out: entry.first.out,
                deleted: entryIsDeleted(entry),
                'has-sender': groupShowsSender() && !entry.first.out && entryContentKind(entry) !== 'service',
                'service-message': entryPresentation(entry).primary === 'service',
              }]"
            >
              <button
                v-if="groupShowsSender() && !entry.first.out && entryContentKind(entry) !== 'service'"
                :class="['sender-avatar', { hidden: !showEntryAvatar(index, entry) }]"
                :style="avatarHue(entrySenderId(entry))"
                type="button"
                :aria-label="`查看 ${entrySenderName(entry)} 的资料`"
                :title="`查看 ${entrySenderName(entry)} 的资料`"
                :tabindex="showEntryAvatar(index, entry) ? 0 : -1"
                :disabled="!showEntryAvatar(index, entry)"
                @click="openSenderProfile(entry, $event)"
              >
                <img v-if="entrySender(entry)?.avatar_url" :src="entrySender(entry)!.avatar_url!" :alt="`${entrySenderName(entry)}头像`" />
                <template v-else>{{ avatarText({ name: entrySenderName(entry) }) }}</template>
              </button>
              <div :class="['archive-message-content', { 'has-inline-buttons': entryButtons(entry).length }]">
              <article :class="['archive-bubble', ...entryPresentation(entry).classes]" :style="visualMediaBubbleStyle(entry)">
                <div v-if="entryViaBot(entry)" class="archive-via-bot"><span>via</span><a v-if="entryViaBot(entry)!.username" :href="`https://t.me/${entryViaBot(entry)!.username}`" target="_blank" rel="noopener">{{ viaBotLabel(entry) }}</a><strong v-else>{{ viaBotLabel(entry) }}</strong></div>
                <strong v-if="showEntrySender(index, entry)" class="archive-sender">{{ entrySenderName(entry) }}</strong>
                <div v-if="showsForwardHeader(entry)" class="archive-forward">
                  <svg viewBox="0 0 24 24"><path d="m9 8-5 4 5 4v-3c5 0 8 1 11 5-1-6-4-9-11-9V8Z" /></svg>
                  <span><small>转发自</small><strong>{{ entryForward(entry)?.name || entryForward(entry)?.post_author || 'Telegram 用户或频道' }}</strong></span>
                </div>
                <button
                  v-if="entryReply(entry)"
                  class="archive-reply"
                  @click="jumpToMessage(entryReply(entry)!.reply_to_msg_id!)"
                >
                  <span>{{ entryReply(entry)!.reply_preview?.sender_name || '回复消息' }}</span>
                  <strong>{{ entryReply(entry)!.reply_preview?.text || `消息 #${entryReply(entry)!.reply_to_msg_id}` }}</strong>
                </button>
                <template v-if="entryIsDeleted(entry)">
                  <p class="archive-deleted-copy"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13" /></svg>此消息已从 Telegram 删除</p>
                  <span class="archive-preserved">已保留 {{ Math.max(0, entryVersions(entry) - 1) }} 个旧版本</span>
                </template>
                <template v-else>
                  <p v-if="entryPresentation(entry).primary === 'unsupported'" class="archive-unsupported">此消息类型暂不支持</p>
                  <div v-if="entryPresentation(entry).primary === 'service'" class="archive-service-content">
                    <span>{{ entry.first.sender?.name || entry.first.post_author || '' }}</span>{{ entryContent(entry).summary || '服务消息' }}
                  </div>
                  <div v-if="entryPresentation(entry).primary === 'contact'" class="archive-contact-card">
                    <span class="archive-contact-avatar">{{ avatarText({ name: entryContent(entry).name }) }}</span>
                    <span><strong>{{ entryContent(entry).name || '联系人' }}</strong><a v-if="entryContent(entry).phone_number" :href="`tel:${entryContent(entry).phone_number}`">{{ entryContent(entry).phone_number }}</a><small v-else>未保存电话号码</small></span>
                  </div>
                  <a v-if="['location', 'venue'].includes(entryPresentation(entry).primary)" class="archive-location-card" :href="locationUrl(entry)" target="_blank" rel="noopener">
                    <span class="archive-location-pin"><svg viewBox="0 0 24 24"><path d="M12 22s7-6.1 7-13a7 7 0 1 0-14 0c0 6.9 7 13 7 13Zm0-10a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" /></svg></span>
                    <span><strong>{{ entryContent(entry).title || (entryPresentation(entry).primary === 'venue' ? '地点' : '位置信息') }}</strong><small>{{ entryContent(entry).address || locationCoordinates(entry) }}</small></span>
                  </a>
                  <div v-if="entryPresentation(entry).primary === 'poll'" class="archive-poll-card">
                    <span class="archive-structured-eyebrow">{{ entryContent(entry).quiz ? '匿名测验' : '投票' }}</span>
                    <strong>{{ entryContent(entry).question || '投票' }}</strong>
                    <div class="archive-poll-options">
                      <div v-for="answer in entryContent(entry).answers || []" :key="answer.option" :class="{ chosen: answer.chosen, correct: answer.correct }">
                        <span><strong>{{ pollPercent(answer, entry) }}%</strong>{{ answer.text }}</span><i :style="{ width: `${pollPercent(answer, entry)}%` }"></i>
                      </div>
                    </div>
                    <small>{{ entryContent(entry).total_voters || 0 }} 人参与<span v-if="entryContent(entry).closed"> · 已结束</span></small>
                  </div>
                  <div v-if="entryPresentation(entry).primary === 'dice'" class="archive-dice-card"><span>{{ entryContent(entry).emoticon || '🎲' }}</span><small v-if="entryContent(entry).value">结果：{{ entryContent(entry).value }}</small></div>
                  <div v-if="entryPresentation(entry).primary === 'todo'" class="archive-todo-card">
                    <span class="archive-structured-eyebrow">待办清单</span>
                    <strong>{{ entryContent(entry).title || '待办事项' }}</strong>
                    <div class="archive-todo-items">
                      <div v-for="item in entryContent(entry).items || []" :key="item.id" :class="{ completed: item.completed }"><i><svg v-if="item.completed" viewBox="0 0 16 16"><path d="m3 8 3 3 7-7" /></svg></i><span>{{ item.title || '未命名事项' }}</span></div>
                    </div>
                    <small v-if="entryContent(entry).others_can_append || entryContent(entry).others_can_complete">{{ entryContent(entry).others_can_append ? '成员可添加事项' : '' }}{{ entryContent(entry).others_can_append && entryContent(entry).others_can_complete ? ' · ' : '' }}{{ entryContent(entry).others_can_complete ? '成员可完成事项' : '' }}</small>
                  </div>
                  <ArchiveSpecialContent
                    v-if="['game', 'invoice', 'story', 'paid_media', 'giveaway', 'giveaway_results'].includes(entryPresentation(entry).primary)"
                    :kind="entryPresentation(entry).primary as 'game' | 'invoice' | 'story' | 'paid_media' | 'giveaway' | 'giveaway_results'"
                    :content="entryContent(entry)"
                  />
                  <p v-if="entry.text && entryWebPage(entry)" class="archive-message-text"><template v-for="(part, partIndex) in richTextParts(entry.text, entryEntities(entry))" :key="partIndex"><TelegramCustomEmoji v-if="part.customEmojiId" :document-id="part.customEmojiId" :fallback="part.text" /><a v-else-if="part.href" :href="part.href" :class="[part.classes, { revealed: isTextSpoilerRevealed(entry.key, partIndex) }]" target="_blank" rel="noopener" @click="part.classes.includes('rich-spoiler') && revealTextSpoiler(entry.key, partIndex, $event)">{{ part.text }}</a><span v-else :class="[part.classes, { revealed: isTextSpoilerRevealed(entry.key, partIndex) }]" :role="part.classes.includes('rich-spoiler') ? 'button' : undefined" :tabindex="part.classes.includes('rich-spoiler') ? 0 : undefined" @click="part.classes.includes('rich-spoiler') && revealTextSpoiler(entry.key, partIndex, $event)" @keydown.enter="part.classes.includes('rich-spoiler') && revealTextSpoiler(entry.key, partIndex, $event)">{{ part.text }}</span></template></p>
                  <div v-if="!entryWebPage(entry) && entryPresentation(entry).isVisualAlbum" class="archive-media-grid album" :style="albumContainerStyle(entry)">
                    <button
                      v-for="(media, mediaIndex) in entry.media"
                      :key="media.id"
                      :class="['archive-album-tile', { 'media-spoiler-hidden': mediaHasSpoiler(entry, media) }]"
                      :style="albumTileStyle(entry, mediaIndex)"
                      :aria-label="`查看${mediaLabel(media)}`"
                      @click="activateMedia(entry, media)"
                    >
                      <img v-if="isImageMedia(media)" :src="mediaPreviewUrl(media)" :alt="media.name || mediaLabel(media)" loading="lazy" @load="rememberMediaDimensions(media, $event)" @error="handleMediaPreviewError(media, $event)" />
                      <img v-if="isVideoMedia(media) && mediaPosterUrl(media)" class="archive-video-poster" :src="mediaPosterUrl(media)" alt="" aria-hidden="true" @load="rememberMediaDimensions(media, $event)" @error="handleMediaPreviewError(media, $event)" />
                      <video v-if="isVideoMedia(media)" class="archive-inline-video archive-album-video" muted playsinline preload="metadata" disablepictureinpicture :poster="mediaPosterUrl(media)" :src="media.url" @loadedmetadata="rememberVideoMetadata(media, $event)" @loadeddata="captureInlineVideoPoster(media, $event)"></video>
                      <span v-if="isVideoMedia(media) && mediaDurations[media.id]" class="archive-video-duration">{{ media.type === 'animation' ? 'GIF' : formatMediaDuration(mediaRemaining[media.id] ?? mediaDurations[media.id]) }}</span>
                      <span v-if="isVideoMedia(media)" class="archive-video-badge"><svg viewBox="0 0 24 24"><path d="m9 7 8 5-8 5V7Z" /></svg></span>
                      <span v-if="mediaHasSpoiler(entry, media)" class="archive-spoiler-reveal"><svg viewBox="0 0 24 24"><path d="M2 12s3.7-6 10-6 10 6 10 6-3.7 6-10 6S2 12 2 12Zm10 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" /></svg>显示</span>
                      <span v-if="mediaTtlSeconds(entry, media)" class="archive-ttl-badge">◉ {{ mediaTtlSeconds(entry, media) }} 秒</span>
                    </button>
                  </div>
                  <div v-else-if="!entryWebPage(entry) && entry.media.length" class="archive-media-grid">
                    <div v-for="media in entry.media" :key="media.id" class="archive-media">
                      <button v-if="isImageMedia(media) && media.type !== 'sticker'" :class="['archive-visual', media.type, { 'media-spoiler-hidden': mediaHasSpoiler(entry, media) }]" :style="singleMediaStyle(media)" :aria-label="`查看${mediaLabel(media)}`" @click="activateMedia(entry, media)">
                        <img :src="mediaPreviewUrl(media)" :alt="media.name || mediaLabel(media)" loading="lazy" @load="rememberMediaDimensions(media, $event)" @error="handleMediaPreviewError(media, $event)" />
                        <span v-if="mediaHasSpoiler(entry, media)" class="archive-spoiler-reveal"><svg viewBox="0 0 24 24"><path d="M2 12s3.7-6 10-6 10 6 10 6-3.7 6-10 6S2 12 2 12Zm10 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" /></svg>显示</span>
                        <span v-if="mediaTtlSeconds(entry, media)" class="archive-ttl-badge">◉ {{ mediaTtlSeconds(entry, media) }} 秒</span>
                      </button>
                      <span v-else-if="media.type === 'sticker'" :class="['archive-visual', 'sticker', { 'media-spoiler-hidden': mediaHasSpoiler(entry, media) }]" role="img" :aria-label="media.name || '贴纸'" @click="mediaHasSpoiler(entry, media) && revealMediaSpoiler(media)">
                        <TgsSticker v-if="isTgsSticker(media)" :src="media.url" :alt="mediaContent(entry, media).emoji || '动态贴纸'" />
                        <video v-else-if="isVideoSticker(media)" class="archive-inline-video archive-sticker-video" autoplay muted loop playsinline preload="auto" :src="media.url"></video>
                        <img v-else :src="media.url" :alt="media.name || '贴纸'" loading="lazy" />
                        <span v-if="mediaHasSpoiler(entry, media)" class="archive-spoiler-reveal"><svg viewBox="0 0 24 24"><path d="M2 12s3.7-6 10-6 10 6 10 6-3.7 6-10 6S2 12 2 12Zm10 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" /></svg>显示</span>
                      </span>
                      <button v-else-if="isVideoMedia(media)" :class="['archive-video-preview', { 'media-spoiler-hidden': mediaHasSpoiler(entry, media) }]" :style="singleMediaStyle(media)" aria-label="查看视频" @click="activateMedia(entry, media)">
                        <img v-if="mediaPosterUrl(media)" class="archive-video-poster" :src="mediaPosterUrl(media)" alt="" aria-hidden="true" @load="rememberMediaDimensions(media, $event)" @error="handleMediaPreviewError(media, $event)" />
                        <video class="archive-inline-video" autoplay muted loop playsinline preload="metadata" disablepictureinpicture :poster="mediaPosterUrl(media)" :src="media.url" @loadedmetadata="rememberVideoMetadata(media, $event)" @loadeddata="captureInlineVideoPoster(media, $event)" @timeupdate="handleInlineVideoTimeUpdate(media, $event)" @playing="handleInlineVideoPlayback($event, true)" @pause="handleInlineVideoPlayback($event, false)"></video>
                        <span v-if="mediaDurations[media.id]" class="archive-video-duration">{{ media.type === 'animation' ? 'GIF' : formatMediaDuration(mediaRemaining[media.id] ?? mediaDurations[media.id]) }}</span>
                        <span class="archive-video-badge large"><svg viewBox="0 0 24 24"><path d="m9 7 8 5-8 5V7Z" /></svg></span>
                        <span v-if="mediaHasSpoiler(entry, media)" class="archive-spoiler-reveal"><svg viewBox="0 0 24 24"><path d="M2 12s3.7-6 10-6 10 6 10 6-3.7 6-10 6S2 12 2 12Zm10 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" /></svg>显示</span>
                        <span v-if="mediaTtlSeconds(entry, media)" class="archive-ttl-badge">◉ {{ mediaTtlSeconds(entry, media) }} 秒</span>
                      </button>
                      <TelegramAudioPlayer v-else-if="isAudioMedia(media)" :media="media" :content="mediaContent(entry, media)" :own="entry.first.out" />
                      <a v-else :class="['archive-document', { 'has-thumbnail': isImageDocument(media) }]" :href="media.download_url">
                        <span class="archive-document-icon"><img v-if="isImageDocument(media)" :src="mediaPreviewUrl(media)" alt="" loading="lazy" @error="handleMediaPreviewError(media, $event)" /><svg v-else viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7V3Zm7 0v5h5" /></svg></span>
                        <span><strong>{{ media.name || mediaLabel(media) }}</strong><small>{{ formatBytes(media.size_bytes) }}</small></span>
                      </a>
                      <span v-if="mediaTtlSeconds(entry, media) && isAudioMedia(media)" class="archive-ttl-note">Telegram 中为 {{ mediaTtlSeconds(entry, media) }} 秒限时媒体 · 归档副本不会自动销毁</span>
                    </div>
                  </div>
                  <p v-if="entry.text && !entryWebPage(entry)" class="archive-message-text">
                    <template v-for="(part, partIndex) in richTextParts(entry.text, entryEntities(entry))" :key="partIndex"><TelegramCustomEmoji v-if="part.customEmojiId" :document-id="part.customEmojiId" :fallback="part.text" /><a v-else-if="part.href" :href="part.href" :class="[part.classes, { revealed: isTextSpoilerRevealed(entry.key, partIndex) }]" target="_blank" rel="noopener" @click="part.classes.includes('rich-spoiler') && revealTextSpoiler(entry.key, partIndex, $event)">{{ part.text }}</a><span v-else :class="[part.classes, { revealed: isTextSpoilerRevealed(entry.key, partIndex) }]" :role="part.classes.includes('rich-spoiler') ? 'button' : undefined" :tabindex="part.classes.includes('rich-spoiler') ? 0 : undefined" @click="part.classes.includes('rich-spoiler') && revealTextSpoiler(entry.key, partIndex, $event)" @keydown.enter="part.classes.includes('rich-spoiler') && revealTextSpoiler(entry.key, partIndex, $event)">{{ part.text }}</span></template>
                    <footer v-if="entryPresentation(entry).metaPosition === 'in-text'" class="archive-message-meta inline">
                      <span v-if="entryMetrics(entry).views" class="archive-views"><svg viewBox="0 0 24 24"><path d="M2 12s3.7-6 10-6 10 6 10 6-3.7 6-10 6S2 12 2 12Zm10 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" /></svg>{{ entryMetrics(entry).views }}</span>
                      <span v-if="entryIsEdited(entry)">已编辑</span><time>{{ formatTime(entry.last.sent_at) }}</time><svg v-if="entry.first.out" class="archive-delivery" viewBox="0 0 18 12" aria-hidden="true"><path d="m1.5 6.5 3 3 6-7M7.5 8.5l1 1 7-7" /></svg>
                    </footer>
                  </p>
                  <a v-if="entryWebPage(entry)" class="archive-web-preview" :href="entryWebPage(entry)!.url" target="_blank" rel="noopener">
                    <span><small>{{ entryWebPage(entry)!.site_name || entryWebPage(entry)!.display_url }}</small><strong>{{ entryWebPage(entry)!.title || entryWebPage(entry)!.display_url }}</strong><p v-if="entryWebPage(entry)!.description">{{ entryWebPage(entry)!.description }}</p></span>
                    <img v-if="entry.media[0]" :src="entry.media[0].url" alt="链接预览" loading="lazy" />
                  </a>
                </template>
                <div v-if="entryMetrics(entry).reactions?.length" class="archive-reactions"><span v-for="(reaction, reactionIndex) in entryMetrics(entry).reactions" :key="reactionIndex">{{ reactionLabel(reaction.reaction) }} <small>{{ reaction.count || 0 }}</small></span></div>
                <footer v-if="entryPresentation(entry).metaPosition === 'standalone' && entryPresentation(entry).primary !== 'service'" class="archive-message-meta">
                  <span v-if="entryMetrics(entry).views" class="archive-views"><svg viewBox="0 0 24 24"><path d="M2 12s3.7-6 10-6 10 6 10 6-3.7 6-10 6S2 12 2 12Zm10 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" /></svg>{{ entryMetrics(entry).views }}</span>
                  <span v-if="entryIsEdited(entry)">已编辑</span><time>{{ formatTime(entry.last.sent_at) }}</time><svg v-if="entry.first.out" class="archive-delivery" viewBox="0 0 18 12" aria-hidden="true"><path d="m1.5 6.5 3 3 6-7M7.5 8.5l1 1 7-7" /></svg>
                </footer>
                <svg
                  v-if="entryPresentation(entry).classes.includes('has-appendix') && ['standalone', 'group-last'].includes(entryPosition(index))"
                  class="archive-appendix"
                  width="9"
                  height="20"
                  aria-hidden="true"
                >
                  <defs>
                    <filter :id="`archiveAppendix-${entry.key}`" x="-50%" y="-14.7%" width="200%" height="141.2%" filterUnits="objectBoundingBox">
                      <feOffset dy="1" in="SourceAlpha" result="shadowOffsetOuter1" />
                      <feGaussianBlur stdDeviation="1" in="shadowOffsetOuter1" result="shadowBlurOuter1" />
                      <feColorMatrix values="0 0 0 0 0.0621962482 0 0 0 0 0.138574144 0 0 0 0 0.185037364 0 0 0 0.15 0" in="shadowBlurOuter1" />
                    </filter>
                  </defs>
                  <g fill="none" fill-rule="evenodd">
                    <path :d="messageAppendixPath(entry.first.out)" fill="#000" :filter="`url(#archiveAppendix-${entry.key})`" />
                    <path :d="messageAppendixPath(entry.first.out)" class="archive-appendix-corner" />
                  </g>
                </svg>
              </article>
              <div v-if="entryVersions(entry) > 1 || entryIsDeleted(entry)" class="archive-message-actions">
                <button
                  type="button"
                  :class="{ restored: entryIsRestored(entry) }"
                  :aria-label="entryIsRestored(entry) ? '该消息已从受限前的备份版本恢复，查看历史版本' : '查看消息历史版本'"
                  :title="entryIsRestored(entry) ? '已从受限前的备份版本恢复 · 点击查看历史' : '查看消息历史版本'"
                  @click.stop="openMessageHistory(entry)"
                >
                  <svg class="history-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.35-5.65L4 8.7M4 4v4.7h4.7M12 8v4.3l3 1.7" /></svg>
                  <span v-if="entryIsRestored(entry)" class="archive-restored-badge" aria-hidden="true">
                    <svg viewBox="0 0 12 12"><path d="M3.5 4H2V2.5M2.3 3.6a4 4 0 1 1-.2 4.3M4.3 6.1l1.1 1.1 2.2-2.5" /></svg>
                  </span>
                </button>
              </div>
              <div v-if="entryButtons(entry).length" class="archive-inline-buttons">
                <div v-for="(row, rowIndex) in entryButtons(entry)" :key="rowIndex">
                  <template v-for="(button, buttonIndex) in row" :key="buttonIndex"><a v-if="button.url" :href="button.url" target="_blank" rel="noopener">{{ button.text }}</a><span v-else>{{ button.text }}</span></template>
                </div>
              </div>
              </div>
            </div>
          </template>
          <div v-if="loadingNewer" class="archive-newer"><span class="spinner small"></span>正在加载更新的消息</div>
          <div v-else-if="hasNewer" class="archive-newer">向下滚动加载更新的消息</div>
        </div>
      </div>

      <div v-if="stickyDateLabel" class="archive-sticky-date" aria-hidden="true"><span>{{ stickyDateLabel }}</span></div>

      <div v-if="jumpNotice" class="archive-jump-notice">{{ jumpNotice }}<button aria-label="关闭提示" @click="jumpNotice = ''">×</button></div>
      <button v-if="hasNewer" class="archive-return-latest" @click="returnToLatest"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6M12 4v11" /></svg>返回最新消息</button>
      <button v-if="newMessageCount" class="archive-new-messages" @click="handleNewMessages">{{ newMessageCount }} 条新消息</button>
      <footer class="archive-readonly-bar"><svg viewBox="0 0 24 24"><path d="M7 10V7a5 5 0 0 1 10 0v3m-11 0h12v10H6V10Z" /></svg><span><strong>只读备份</strong><small>更新至 {{ formatFullTime(selected.last_backup_at) }}</small></span></footer>
    </main>

    <Transition name="archive-info">
    <section v-if="selected && infoOpen" class="archive-info-pane" aria-label="会话归档信息">
      <header>
        <button class="archive-info-back" aria-label="返回上一层" @click="closeInfoPane"><svg viewBox="0 0 24 24"><path d="m15 5-7 7 7 7" /></svg></button>
        <strong>会话信息</strong>
        <button class="archive-info-close" aria-label="关闭会话信息" @click="closeInfoPane"><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
      </header>
      <div ref="infoScroll" class="archive-info-scroll" @scroll.passive="handleSharedMediaScroll">
      <div class="archive-profile">
        <span :class="['chat-avatar', 'profile-avatar', { 'saved-messages-avatar': selected.is_self }]" :style="avatarHue(selected.peer_id)"><SavedMessagesIcon v-if="selected.is_self" /><img v-else-if="selected.avatar_url" :src="selected.avatar_url" :alt="`${selected.title}头像`" /><template v-else>{{ avatarText(selected) }}</template></span>
        <h2>{{ selected.title }}</h2><span v-if="selected.username">@{{ selected.username }}</span>
      </div>
      <div class="archive-info-cards">
        <section><span>归档状态</span><strong>{{ ruleStatusLabel[selected.rule_status] }}</strong></section>
        <section class="archive-stats"><div><strong>{{ selected.message_count }}</strong><span>消息</span></div><div><strong>{{ selected.media_count }}</strong><span>媒体</span></div></section>
        <section><span>最近备份</span><strong>{{ formatFullTime(selected.last_backup_at) }}</strong></section>
        <section class="archive-display-setting">
          <span><strong>始终显示剧透消息</strong><small>直接显示隐藏文字和剧透媒体</small></span>
          <button type="button" class="switch" role="switch" aria-label="始终显示剧透消息" :aria-checked="alwaysShowSpoilers" @click="alwaysShowSpoilers = !alwaysShowSpoilers"><span></span></button>
        </section>
      </div>
      <p class="archive-info-note">这里显示的是保存在本服务器上的只读副本，不会改变 Telegram 中的消息状态。</p>
      <section class="archive-shared-media" aria-label="共享媒体">
        <nav class="archive-shared-tabs" aria-label="共享媒体分类">
          <button v-for="tab in sharedMediaTabs" :key="tab.type" :class="{ active: sharedMediaTab === tab.type }" @click="switchSharedMediaTab(tab.type)">{{ tab.label }}</button>
        </nav>
        <div v-if="sharedMediaTab === 'media'" class="archive-shared-kind-filter" role="group" aria-label="媒体类型筛选">
          <button
            type="button"
            :class="{ active: sharedMediaKinds.photo }"
            :aria-pressed="sharedMediaKinds.photo"
            :disabled="sharedMediaKinds.photo && !sharedMediaKinds.video"
            @click="toggleSharedMediaKind('photo')"
          ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4V5Zm0 10 4.5-4.5 3.2 3.2 2.1-2.1L20 18M16.5 8.5h.01" /></svg><span>显示图片</span></button>
          <button
            type="button"
            :class="{ active: sharedMediaKinds.video }"
            :aria-pressed="sharedMediaKinds.video"
            :disabled="sharedMediaKinds.video && !sharedMediaKinds.photo"
            @click="toggleSharedMediaKind('video')"
          ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v16H5V4Zm5 5 5 3-5 3V9Z" /></svg><span>显示视频</span></button>
        </div>
        <div v-if="sharedMediaLoading" class="archive-shared-state"><span class="spinner small"></span><span>正在读取</span></div>
        <div v-else-if="sharedMediaError" class="archive-shared-state error"><span>{{ sharedMediaError }}</span><button @click="loadSharedMedia(false)">重试</button></div>
        <div v-else-if="!sharedMediaTotal" class="archive-shared-state"><span>没有已归档的{{ sharedMediaTabs.find((tab) => tab.type === sharedMediaTab)?.label }}</span></div>
        <div v-else-if="['media', 'gif'].includes(sharedMediaTab)" ref="sharedMediaVirtual" class="archive-shared-virtual visual" :style="{ height: `${sharedMediaVirtualHeight()}px` }" @pointerdown="handleSharedLongPressPointerDown" @pointermove="moveSharedLongPress" @pointerup="endSharedLongPress" @pointercancel="endSharedLongPress" @click.capture="consumeSharedLongPressActivation">
          <template v-for="slot in sharedMediaVisibleSlots" :key="slot.index">
          <button v-if="slot.item" class="archive-shared-virtual-item" :style="slot.style" :data-shared-index="slot.index" :aria-label="`查看${slot.item.type === 'video' ? '视频' : slot.item.type === 'animation' ? 'GIF' : '图片'}`" @click="openSharedMedia(slot.item)" @contextmenu="openSharedContextMenu(slot.item, $event)">
            <img v-if="slot.item.type === 'photo' || slot.item.mime_type?.startsWith('image/')" :src="mediaPreviewUrl(slot.item)" :alt="slot.item.name || '共享媒体'" loading="lazy" @error="handleMediaPreviewError(slot.item, $event)" />
            <img v-else-if="mediaPosterUrl(slot.item)" class="archive-shared-video-poster" :src="mediaPosterUrl(slot.item)" :alt="slot.item.name || '视频预览'" @error="handleMediaPreviewError(slot.item, $event)" />
            <video
              v-else
              class="archive-shared-video-loader"
              :src="slot.item.url"
              :data-media-id="slot.item.id"
              muted
              playsinline
              preload="metadata"
              @loadedmetadata="startSharedVideoPoster(slot.item, $event)"
              @seeked="captureSharedVideoPoster(slot.item, $event)"
            ></video>
            <span v-if="sharedMediaDuration(slot.item)" class="archive-shared-duration">{{ sharedMediaDuration(slot.item) }}</span>
            <span v-else-if="slot.item.type === 'animation'" class="archive-shared-duration">GIF</span>
            <span v-if="slot.item.is_history_version" class="archive-shared-history-badge" :title="`历史版本 ${slot.item.version}`" aria-label="历史版本"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7v5h5M5.4 16.5A8 8 0 1 0 5 8.2L4 12m8-4v4l2.8 1.7" /></svg></span>
          </button>
          <span v-else class="archive-shared-virtual-item placeholder" :style="slot.style"></span>
          </template>
        </div>
        <div v-else-if="sharedMediaTab === 'documents'" ref="sharedMediaVirtual" class="archive-shared-virtual archive-shared-list files" :style="{ height: `${sharedMediaVirtualHeight()}px` }" @pointerdown="handleSharedLongPressPointerDown" @pointermove="moveSharedLongPress" @pointerup="endSharedLongPress" @pointercancel="endSharedLongPress" @click.capture="consumeSharedLongPressActivation">
          <template v-for="slot in sharedMediaVisibleSlots" :key="slot.index">
          <a v-if="slot.item" class="archive-shared-virtual-item" :style="slot.style" :data-shared-index="slot.index" :href="slot.item.download_url" @contextmenu="openSharedContextMenu(slot.item, $event)">
            <span class="archive-shared-file-icon"><svg viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7V3Zm7 0v5h5" /></svg></span>
            <span><strong>{{ slot.item.name || '文件' }}</strong><small>{{ formatBytes(slot.item.size_bytes) }} · {{ formatListTime(slot.item.sent_at) }}</small></span>
            <span v-if="slot.item.is_history_version" class="archive-shared-history-badge list" :title="`历史版本 ${slot.item.version}`" aria-label="历史版本"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7v5h5M5.4 16.5A8 8 0 1 0 5 8.2L4 12m8-4v4l2.8 1.7" /></svg></span>
          </a>
          <span v-else class="archive-shared-virtual-item placeholder list" :style="slot.style"></span>
          </template>
        </div>
        <div v-else-if="sharedMediaTab === 'links'" ref="sharedMediaVirtual" class="archive-shared-virtual archive-shared-list links" :style="{ height: `${sharedMediaVirtualHeight()}px` }" @pointerdown="handleSharedLongPressPointerDown" @pointermove="moveSharedLongPress" @pointerup="endSharedLongPress" @pointercancel="endSharedLongPress" @click.capture="consumeSharedLongPressActivation">
          <template v-for="slot in sharedMediaVisibleSlots" :key="slot.index"><a v-if="slot.item" class="archive-shared-virtual-item" :style="slot.style" :data-shared-index="slot.index" :href="slot.item.url" target="_blank" rel="noopener" @contextmenu="openSharedContextMenu(slot.item, $event)"><span><strong>{{ slot.item.content?.title || slot.item.text || slot.item.url }}</strong><small>{{ sharedMediaDomain(slot.item) }} · {{ formatListTime(slot.item.sent_at) }}</small></span><svg viewBox="0 0 24 24"><path d="m9 5 7 7-7 7" /></svg></a><span v-else class="archive-shared-virtual-item placeholder list" :style="slot.style"></span></template>
        </div>
        <div v-else ref="sharedMediaVirtual" class="archive-shared-virtual archive-shared-list audio" :style="{ height: `${sharedMediaVirtualHeight()}px` }" @pointerdown="handleSharedLongPressPointerDown" @pointermove="moveSharedLongPress" @pointerup="endSharedLongPress" @pointercancel="endSharedLongPress" @click.capture="consumeSharedLongPressActivation">
          <template v-for="slot in sharedMediaVisibleSlots" :key="slot.index"><div v-if="slot.item" class="archive-shared-virtual-item" :style="slot.style" :data-shared-index="slot.index" @contextmenu="openSharedContextMenu(slot.item, $event)"><TelegramAudioPlayer :media="slot.item" :content="slot.item.content" :own="false" /><span class="archive-shared-list-meta"><time>{{ formatListTime(slot.item.sent_at) }}</time><span v-if="slot.item.is_history_version" class="archive-shared-history-badge list" :title="`历史版本 ${slot.item.version}`" aria-label="历史版本"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7v5h5M5.4 16.5A8 8 0 1 0 5 8.2L4 12m8-4v4l2.8 1.7" /></svg></span></span></div><span v-else class="archive-shared-virtual-item placeholder list" :style="slot.style"></span></template>
        </div>
        <div v-if="sharedMediaLoadingMore" class="archive-shared-range-loading" aria-live="polite"><span class="spinner small"></span></div>
      </section>
      </div>
      <div
        ref="sharedScrollbar"
        :class="['archive-gallery-scrollbar', { visible: sharedScrollbarVisible, dragging: sharedScrollbarDragging, 'show-bubble': sharedScrollbarBubbleVisible }]"
        aria-hidden="true"
      >
        <span class="archive-gallery-scrollbar-bubble" :style="{ top: `${sharedScrollbarBubbleTop}px` }">{{ sharedScrollbarLabel }}</span>
        <span
          ref="sharedScrollbarThumb"
          class="archive-gallery-scrollbar-thumb"
          :style="{ transform: `translateY(${sharedScrollbarThumbTop}px)` }"
          @pointerdown="handleSharedScrollbarPointerDown"
          @pointermove="handleSharedScrollbarPointerMove"
          @pointerup="endSharedScrollbarDrag"
          @pointercancel="endSharedScrollbarDrag"
          @lostpointercapture="endSharedScrollbarDrag()"
        ><span><i></i><i></i></span></span>
      </div>
    </section>
    </Transition>

    <main v-if="!selected" class="archive-message-pane archive-no-selection"><div><strong>选择一个会话</strong><span>查看已经备份到本地的消息</span></div></main>

    <div v-if="senderProfile" class="archive-profile-overlay" role="dialog" aria-modal="true" :aria-label="`${senderProfile.name} 的资料`" @click.self="closeSenderProfile()">
      <section class="archive-sender-profile-card">
        <button ref="senderProfileCloseButton" class="archive-profile-close" aria-label="关闭资料卡" title="关闭" @click="closeSenderProfile()"><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
        <div class="archive-sender-profile-hero">
          <span class="archive-sender-profile-avatar" :style="avatarHue(senderProfile.peer_id || 0)">
            <img v-if="senderProfileAvatar()" :src="senderProfileAvatar()!" :alt="`${senderProfileDetail?.display_name || senderProfile.name}头像`" />
            <template v-else>{{ avatarText({ name: senderProfileDetail?.display_name || senderProfile.name }) }}</template>
          </span>
          <h2>{{ senderProfileDetail?.display_name || senderProfile.name }}</h2>
          <span v-if="senderProfileDetail?.username || senderProfile.username">@{{ senderProfileDetail?.username || senderProfile.username }}</span>
          <div class="archive-sender-profile-badges">
            <i>{{ senderKindLabel(senderProfileDetail?.kind || senderProfile.kind) }}</i>
            <i v-if="senderProfileDetail?.is_verified">已认证</i>
            <i v-if="senderProfileDetail?.is_contact">联系人</i>
            <i v-if="senderProfileDetail?.is_scam || senderProfileDetail?.is_fake" class="warning">风险标记</i>
          </div>
        </div>
        <div v-if="senderProfileLoading" class="archive-sender-profile-state"><span class="spinner"></span><span>正在读取本地资料</span></div>
        <div v-else-if="senderProfileError" class="archive-sender-profile-state error"><strong>无法读取完整资料</strong><span>{{ senderProfileError }}</span></div>
        <template v-else-if="senderProfileDetail">
          <p v-if="senderProfileDetail.about" class="archive-sender-profile-about">{{ senderProfileDetail.about }}</p>
          <dl class="archive-sender-profile-details">
            <div><dt>Telegram ID</dt><dd>{{ senderProfileDetail.telegram_id }}</dd></div>
            <div><dt>用户名</dt><dd>{{ senderProfileDetail.username ? `@${senderProfileDetail.username}` : '未设置' }}</dd></div>
            <div v-if="senderProfileDetail.phone_masked"><dt>手机号</dt><dd>{{ senderProfileDetail.phone_masked }}</dd></div>
            <div><dt>资料状态</dt><dd>{{ senderProfileDetail.access_state === 'available' ? '可访问' : '访问受限' }}</dd></div>
            <div class="wide"><dt>资料更新时间</dt><dd>{{ formatFullTime(senderProfileDetail.last_full_refreshed_at || senderProfileDetail.last_observed_at) }}</dd></div>
          </dl>
        </template>
        <p v-else class="archive-sender-profile-note">当前归档只保存了该来源的名称，完整资料仍在等待 Telegram 同步。</p>
      </section>
    </div>

    <div v-if="historyMessage" class="archive-history-overlay" role="dialog" aria-modal="true" aria-label="消息历史版本" @click.self="closeMessageHistory()">
      <section class="archive-history-panel">
        <header>
          <span><strong>消息历史</strong><small>消息 #{{ historyMessage.message_id }} · 已保存 {{ historyData?.items.length || historyMessage.current_version }} 个版本</small></span>
          <button aria-label="关闭消息历史" title="关闭" @click="closeMessageHistory()"><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
        </header>
        <main>
          <div v-if="historyLoading" class="archive-history-state"><i></i><span>正在读取已保存的版本…</span></div>
          <div v-else-if="historyError" class="archive-history-state error"><strong>无法显示消息历史</strong><span>{{ historyError }}</span></div>
          <div v-else-if="!historyData?.items.length" class="archive-history-state"><span>没有保存的历史版本</span></div>
          <ol v-else class="archive-history-timeline">
            <li v-for="version in historyData.items" :key="version.version">
              <i class="archive-history-node"></i>
              <article :class="{ deleted: version.is_deleted }">
                <header>
                  <span>
                    <strong>版本 {{ version.version }}</strong>
                    <em v-if="version.version === historyData.current_version">当前版本</em>
                    <em v-else-if="version.version === 1">最初备份</em>
                  </span>
                  <time>{{ versionTime(version.edit_date || version.observed_at) }}</time>
                </header>
                <p v-if="version.is_deleted" class="archive-history-deleted">此版本记录了消息已被删除</p>
                <p v-else-if="version.text" class="archive-history-text"><template v-for="(part, partIndex) in richTextParts(version.text, version.entities)" :key="partIndex"><TelegramCustomEmoji v-if="part.customEmojiId" :document-id="part.customEmojiId" :fallback="part.text" /><a v-else-if="part.href" :href="part.href" :class="[part.classes, { revealed: isTextSpoilerRevealed(`version-${historyMessage.id}-${version.version}`, partIndex) }]" target="_blank" rel="noopener" @click="part.classes.includes('rich-spoiler') && revealTextSpoiler(`version-${historyMessage.id}-${version.version}`, partIndex, $event)">{{ part.text }}</a><span v-else :class="[part.classes, { revealed: isTextSpoilerRevealed(`version-${historyMessage.id}-${version.version}`, partIndex) }]" :role="part.classes.includes('rich-spoiler') ? 'button' : undefined" :tabindex="part.classes.includes('rich-spoiler') ? 0 : undefined" @click="part.classes.includes('rich-spoiler') && revealTextSpoiler(`version-${historyMessage.id}-${version.version}`, partIndex, $event)" @keydown.enter="part.classes.includes('rich-spoiler') && revealTextSpoiler(`version-${historyMessage.id}-${version.version}`, partIndex, $event)">{{ part.text }}</span></template></p>
                <div v-if="version.media.length" class="archive-history-media">
                  <div v-for="media in version.media" :key="media.id" :class="['archive-history-media-item', media.type]">
                    <TgsSticker v-if="isTgsSticker(media)" :src="media.url" :alt="version.content.emoji || '动态贴纸'" />
                    <video v-else-if="isVideoSticker(media)" autoplay muted loop playsinline :src="media.url"></video>
                    <img v-else-if="isImageMedia(media)" :src="mediaPreviewUrl(media)" :alt="media.name || mediaLabel(media)" loading="lazy" @error="handleMediaPreviewError(media, $event)" />
                    <video v-else-if="isVideoMedia(media)" controls preload="metadata" playsinline :poster="mediaPosterUrl(media)" :src="media.url"></video>
                    <TelegramAudioPlayer v-else-if="isAudioMedia(media)" :media="media" :content="version.content" :own="historyMessage.out" />
                    <a v-else :href="media.download_url"><span class="archive-document-icon"><svg viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7V3Zm7 0v5h5" /></svg></span><span><strong>{{ media.name || mediaLabel(media) }}</strong><small>{{ formatBytes(media.size_bytes) }}</small></span></a>
                  </div>
                </div>
                <p v-if="!version.is_deleted && !version.text && !version.media.length" class="archive-history-summary">{{ version.content.summary || `${versionKindLabel(version.content_kind)}内容已保存` }}</p>
                <footer><span>{{ versionKindLabel(version.content_kind) }}</span><span>发现于 {{ versionTime(version.observed_at) }}</span></footer>
              </article>
            </li>
          </ol>
        </main>
      </section>
    </div>

    <div v-if="activeViewerItem" :class="['archive-viewer', { zoomed: viewerZoom > 1, dragging: viewerDragging }]" role="dialog" aria-modal="true" aria-label="媒体浏览器">
      <header class="archive-viewer-header">
        <div class="archive-viewer-author">
          <span class="sender-avatar" :style="avatarHue(activeViewerItem.message.sender_id || selected?.peer_id || 0)">
            <img v-if="activeViewerItem.message.sender?.avatar_url" :src="activeViewerItem.message.sender.avatar_url" alt="发送者头像" />
            <template v-else>{{ avatarText({ name: activeViewerItem.message.sender?.name || (activeViewerItem.message.out ? '我' : selected?.title) }) }}</template>
          </span>
          <span><strong>{{ activeViewerItem.message.sender?.name || (activeViewerItem.message.out ? '我' : selected?.title) }}</strong><small>{{ formatFullTime(activeViewerItem.message.sent_at) }}</small></span>
        </div>
        <div class="archive-viewer-tools">
          <button aria-label="缩小" title="缩小" @click="setViewerZoom(viewerZoom - .25)"><svg viewBox="0 0 24 24"><path d="M5 12h14" /></svg></button>
          <span>{{ Math.round(viewerZoom * 100) }}%</span>
          <button aria-label="放大" title="放大" @click="setViewerZoom(viewerZoom + .25)"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></button>
          <a :href="activeViewerItem.media.download_url" aria-label="下载" title="下载"><svg viewBox="0 0 24 24"><path d="M12 3v12m-5-5 5 5 5-5M5 20h14" /></svg></a>
          <button aria-label="关闭" title="关闭" @click="closeViewer()"><svg viewBox="0 0 24 24"><path d="m5 5 14 14M19 5 5 19" /></svg></button>
        </div>
      </header>
      <button v-if="viewerZoom === 1" class="archive-viewer-nav previous" aria-label="上一项" :disabled="viewerIndex === 0" @click="moveViewer(-1)"><svg viewBox="0 0 24 24"><path d="m15 5-7 7 7 7" /></svg></button>
      <div
        ref="viewerStage"
        class="archive-viewer-stage"
        @contextmenu="openViewerSharedContextMenu"
        @click="handleViewerStageClick"
        @wheel.prevent="handleViewerWheel"
        @pointerdown="handleViewerPointerDown"
        @pointermove="handleViewerPointerMove"
        @pointerup="handleViewerPointerUp"
        @pointercancel="handleViewerPointerUp"
        @lostpointercapture="releaseViewerPointer($event.pointerId)"
      >
        <img v-if="!isVideoMedia(activeViewerItem.media)" :key="activeViewerItem.media.id" ref="viewerMedia" :src="activeViewerItem.media.url" :alt="activeViewerItem.media.name || mediaLabel(activeViewerItem.media)" draggable="false" :style="viewerMediaStyle()" />
        <video v-else :key="activeViewerItem.media.id" ref="viewerMedia" controls autoplay playsinline :src="activeViewerItem.media.url" draggable="false" :style="viewerMediaStyle()"></video>
      </div>
      <button v-if="viewerZoom === 1" class="archive-viewer-nav next" aria-label="下一项" :disabled="viewerIndex === activeViewerItems.length - 1" @click="moveViewer(1)"><svg viewBox="0 0 24 24"><path d="m9 5 7 7-7 7" /></svg></button>
      <footer class="archive-viewer-footer">
        <div v-if="activeAlbumItems.length" class="archive-viewer-thumbnails">
          <button v-for="item in activeAlbumItems" :key="item.media.id" :class="{ active: item.media.id === activeViewerItem.media.id }" @click="openMedia(item.media)">
            <img v-if="!isVideoMedia(item.media) || mediaPosterUrl(item.media)" :src="isVideoMedia(item.media) ? mediaPosterUrl(item.media) : mediaPreviewUrl(item.media)" alt="" @error="handleMediaPreviewError(item.media, $event)" /><video v-else muted preload="metadata" :src="item.media.url"></video>
          </button>
        </div>
        <span>{{ (viewerIndex || 0) + 1 }} / {{ activeViewerItems.length }}</span>
        <p v-if="activeViewerItem.message.text">{{ activeViewerItem.message.text }}</p>
      </footer>
    </div>

    <Teleport to="body">
      <div
        v-if="sharedContextMenu"
        class="archive-shared-context-menu"
        role="menu"
        aria-label="共享媒体操作"
        :style="{ left: `${sharedContextMenu.x}px`, top: `${sharedContextMenu.y}px` }"
      >
        <button ref="sharedContextMenuButton" type="button" role="menuitem" @click="goToSharedMessage">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13m-5-5 5 5-5 5M5 5v14" /></svg>
          <span>前往消息</span>
        </button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.archive-layout { height: 100vh; height: 100dvh; min-width: 0; display: grid; grid-template-columns: 20.25rem minmax(24rem, 1fr) 0; color: var(--text); background: #eef2f7; transition: grid-template-columns 320ms cubic-bezier(.2,.8,.2,1); }
.archive-layout.info-open { grid-template-columns: 20.25rem minmax(24rem, 1fr) 19.25rem; }
.archive-chat-pane, .archive-info-pane { min-width: 0; background: rgba(247,249,252,.88); backdrop-filter: blur(28px) saturate(150%); -webkit-backdrop-filter: blur(28px) saturate(150%); }
.archive-chat-pane { display: grid; grid-template-rows: auto auto minmax(0,1fr); border-right: 1px solid rgba(45,60,85,.09); }
.archive-list-header { display: flex; align-items: end; justify-content: space-between; padding: 1.2rem 1.1rem .7rem; }
.archive-list-header h1 { margin: .2rem 0 0; font-size: 1.55rem; letter-spacing: -.035em; }
.archive-list-header > span { min-width: 1.7rem; padding: .22rem .48rem; border-radius: 999px; text-align: center; color: var(--muted); background: rgba(80,95,120,.08); font-size: .7rem; font-weight: 750; }
.archive-search { margin: 0 .8rem .75rem; min-height: 2.45rem; padding: 0 .75rem; border-radius: .85rem; display: flex; align-items: center; gap: .55rem; color: #778397; background: rgba(80,95,120,.075); }
.archive-search svg { width: 1rem; fill: currentColor; }
.archive-search input { min-width: 0; width: 100%; border: 0; outline: 0; color: var(--text); background: transparent; }
.archive-chat-list { min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 0 .45rem .7rem; }
.archive-chat-item { width: 100%; min-width: 0; padding: .62rem .58rem; border: 0; border-radius: .95rem; display: grid; grid-template-columns: auto minmax(0,1fr); gap: .68rem; align-items: center; color: inherit; background: transparent; text-align: left; cursor: pointer; transition: transform 90ms ease-out, background 150ms ease, color 150ms ease; }
.archive-chat-item:hover { background: rgba(70,90,120,.065); }
.archive-chat-item:active { transform: scale(.985); }
.archive-chat-item.active { color: white; background: linear-gradient(145deg, #2998f2, #0878df); box-shadow: 0 7px 18px rgba(8,122,245,.2); }
.archive-chat-copy { min-width: 0; display: grid; gap: .25rem; }
.archive-chat-copy > span { min-width: 0; display: flex; align-items: center; gap: .45rem; }
.archive-chat-copy strong, .archive-chat-copy small { min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.archive-chat-copy strong { font-size: .86rem; }
.archive-chat-copy time { color: var(--muted); font-size: .65rem; }
.archive-chat-copy small { color: var(--muted); font-size: .7rem; }
.archive-chat-copy i { padding: .18rem .35rem; border-radius: 999px; color: #8a6870; background: rgba(185,85,95,.09); font-size: .58rem; font-style: normal; white-space: nowrap; }
.archive-chat-item.active time, .archive-chat-item.active small { color: rgba(255,255,255,.8); }
.archive-chat-item.active i { color: white; background: rgba(255,255,255,.16); }
.archive-chat-item .chat-avatar { width: 2.75rem; height: 2.75rem; }
.archive-loading, .archive-empty { margin: 1rem; min-height: 8rem; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: .6rem; color: var(--muted); text-align: center; font-size: .78rem; line-height: 1.5; }
.archive-loading { flex-direction: row; min-height: 3rem; }
.archive-empty strong { color: var(--text); }
.archive-empty.compact { min-height: 5rem; }
.archive-message-pane { --archive-message-radius: .9375rem; --archive-message-radius-small: .375rem; --archive-message-list-width: 47.5rem; --archive-incoming-max-width: 29rem; --archive-outgoing-max-width: 30rem; min-width: 0; position: relative; display: grid; grid-template-rows: auto minmax(0,1fr) auto; overflow: hidden; background-color: #dfe8ef; background-image: linear-gradient(rgba(223,232,239,.78), rgba(223,232,239,.78)), url('../assets/chat-pattern.svg'), radial-gradient(circle at 18% 12%, rgba(70,165,225,.16), transparent 31%), radial-gradient(circle at 85% 78%, rgba(115,95,215,.12), transparent 29%); background-size: auto, 15rem 15rem, auto, auto; }
.archive-thread-header { z-index: 3; min-width: 0; min-height: 3rem; padding: .25rem .75rem; display: flex; align-items: center; gap: .625rem; color: var(--text); background: rgba(248,250,253,.82); backdrop-filter: blur(24px) saturate(160%); -webkit-backdrop-filter: blur(24px) saturate(160%); box-shadow: 0 1px 0 rgba(45,60,85,.08); }
.archive-message-search { position: absolute; z-index: 12; top: .3rem; left: 50%; width: min(46rem, calc(100% - 1rem)); transform: translateX(-50%); pointer-events: none; }
.archive-message-search > header { min-height: 2.55rem; padding: .22rem .28rem; border: 1px solid rgba(65,80,105,.09); border-radius: 1.35rem; display: flex; align-items: center; gap: .2rem; color: var(--text); background: rgba(249,251,254,.96); box-shadow: 0 .4rem 1.5rem rgba(30,50,75,.18); backdrop-filter: blur(22px) saturate(165%); -webkit-backdrop-filter: blur(22px) saturate(165%); pointer-events: auto; animation: archive-search-materialize 150ms cubic-bezier(.2,.8,.2,1); }
@keyframes archive-search-materialize { from { opacity: 0; transform: scale(.975) translateY(-.2rem); } to { opacity: 1; transform: none; } }
.archive-message-search > header > button, .archive-message-search label > button { width: 2rem; height: 2rem; padding: 0; border: 0; border-radius: 50%; flex: none; display: grid; place-items: center; color: var(--muted); background: transparent; cursor: pointer; transition: color 130ms ease, background 130ms ease, transform 90ms ease-out; }
.archive-message-search > header > button:hover:not(:disabled), .archive-message-search label > button:hover { color: var(--blue); background: rgba(8,122,245,.09); }.archive-message-search > header > button:active:not(:disabled), .archive-message-search label > button:active { transform: scale(.9); }.archive-message-search > header > button:disabled { opacity: .28; cursor: default; }
.archive-message-search > header svg { width: 1rem; height: 1rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.archive-message-search > header > label { min-width: 0; min-height: 2rem; padding: 0 .25rem 0 .55rem; border-radius: 1rem; flex: 1; display: flex; align-items: center; gap: .4rem; color: var(--muted); background: rgba(80,95,120,.075); }
.archive-message-search > header > label > svg { width: .95rem; flex: none; }
.archive-message-search input { min-width: 0; width: 100%; border: 0; outline: 0; color: var(--text); background: transparent; font: inherit; font-size: .78rem; }
.archive-message-search input::-webkit-search-cancel-button { display: none; }
.archive-message-search-count { min-width: 3.4rem; color: var(--muted); text-align: center; white-space: nowrap; font-size: .62rem; font-variant-numeric: tabular-nums; }
.archive-message-search-dropdown { width: calc(100% - 3rem); max-height: min(25rem, calc(100vh - 5rem)); margin: .3rem auto 0; overflow: hidden; border: 1px solid rgba(65,80,105,.09); border-radius: 1.05rem; color: var(--text); background: rgba(249,251,254,.97); box-shadow: 0 .65rem 2rem rgba(25,45,70,.2); backdrop-filter: blur(24px) saturate(165%); -webkit-backdrop-filter: blur(24px) saturate(165%); pointer-events: auto; animation: archive-search-materialize 150ms cubic-bezier(.2,.8,.2,1); }
.archive-message-search-results { max-height: min(24rem, calc(100vh - 5.5rem)); padding: .35rem; overflow-y: auto; overscroll-behavior: contain; }
.archive-message-search-results > button { width: 100%; min-width: 0; padding: .58rem .68rem; border: 0; border-radius: .72rem; display: grid; gap: .22rem; color: inherit; background: transparent; text-align: left; cursor: pointer; transition: background 120ms ease, transform 80ms ease-out; }
.archive-message-search-results > button:hover { background: rgba(75,90,115,.07); }.archive-message-search-results > button.active { background: rgba(8,122,245,.12); }.archive-message-search-results > button:active { transform: scale(.992); }
.archive-message-search-results > button > span { min-width: 0; display: flex; align-items: baseline; gap: .55rem; }.archive-message-search-results strong { min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--blue); font-size: .7rem; }.archive-message-search-results time { flex: none; color: var(--muted); font-size: .58rem; }
.archive-message-search-results small { overflow: hidden; color: var(--text); white-space: nowrap; text-overflow: ellipsis; font-size: .69rem; line-height: 1.35; }.archive-message-search-results mark { padding: .03rem .08rem; border-radius: .15rem; color: inherit; background: rgba(255,205,55,.42); }
.archive-message-search-results em { width: fit-content; padding: .12rem .32rem; border-radius: 999px; color: #9a6c16; background: rgba(190,135,30,.12); font-size: .53rem; font-style: normal; font-weight: 720; }
.archive-message-search-state { min-height: 6rem; padding: 1rem; display: flex; align-items: center; justify-content: center; gap: .45rem; color: var(--muted); text-align: center; font-size: .7rem; }.archive-message-search-state.error { flex-direction: column; color: #bd5560; }.archive-message-search-state button { padding: .35rem .58rem; border: 0; border-radius: .55rem; color: var(--blue); background: rgba(8,122,245,.1); cursor: pointer; font: inherit; font-weight: 700; }
.archive-message-search-more { min-height: 2.7rem; display: flex; align-items: center; justify-content: center; gap: .4rem; color: var(--muted); font-size: .62rem; }
.thread-avatar { width: 2.5rem; height: 2.5rem; }
.archive-thread-title { min-width: 0; flex: 1; display: grid; gap: .16rem; }
.archive-thread-title strong, .archive-thread-title span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.archive-thread-title strong { font-size: 1rem; }
.archive-thread-title span { color: var(--muted); font-size: .875rem; }
.archive-icon-button, .archive-back, .archive-info-pane header button { width: 2.35rem; height: 2.35rem; flex: none; border: 0; border-radius: 50%; display: grid; place-items: center; color: var(--muted); background: transparent; cursor: pointer; }
.archive-icon-button:hover, .archive-icon-button.active, .archive-info-pane header button:hover { color: var(--blue); background: rgba(8,122,245,.09); }
.archive-icon-button svg, .archive-back svg, .archive-info-pane header svg { width: 1.15rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.archive-back { display: none; }
.archive-message-viewport { min-height: 0; overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; scroll-behavior: auto; scrollbar-width: thin; scrollbar-color: rgba(100,120,145,.36) transparent; }
.archive-message-viewport.is-prepending { overflow-anchor: none; }
.archive-message-viewport::-webkit-scrollbar, .archive-chat-list::-webkit-scrollbar { width: .38rem; }
.archive-message-viewport::-webkit-scrollbar-track, .archive-chat-list::-webkit-scrollbar-track { background: transparent; }
.archive-message-viewport::-webkit-scrollbar-thumb, .archive-chat-list::-webkit-scrollbar-thumb { border: .08rem solid transparent; border-radius: 999px; background: rgba(100,120,145,.36); background-clip: padding-box; }
.archive-message-flow { width: min(var(--archive-message-list-width), 100%); min-height: 100%; margin: 0 auto; padding: 1rem 1rem .8rem 1.125rem; box-sizing: border-box; display: flex; flex-direction: column; }
.archive-message-flow.is-positioning { visibility: hidden; }
.archive-message-flow::before { content: ''; margin-top: auto; }
.archive-message-loading { min-height: 100%; display: flex; align-items: center; justify-content: center; gap: .65rem; color: var(--muted); }
.archive-older { align-self: center; min-height: 2rem; display: flex; align-items: center; gap: .45rem; color: #5f7186; font-size: .68rem; }
.archive-newer { align-self: center; min-height: 2rem; display: flex; align-items: center; gap: .45rem; color: #5f7186; font-size: .68rem; }
.archive-date-separator { position: relative; z-index: 1; display: flex; justify-content: center; margin: 1rem 0; pointer-events: none; }
.archive-date-separator span, .archive-sticky-date span { padding: .1875rem .5rem; border-radius: var(--archive-message-radius); color: white; background: rgba(52,70,88,.58); backdrop-filter: blur(12px); font-size: .9375rem; font-weight: 650; line-height: 1.25; box-shadow: 0 2px 8px rgba(25,40,60,.11); }
.archive-sticky-date { position: absolute; z-index: 5; top: 3.45rem; left: 50%; transform: translateX(-50%); display: flex; justify-content: center; pointer-events: none; }
.archive-message-line { display: grid; grid-template-columns: minmax(0,1fr); align-items: end; gap: .5rem; margin: 0 0 .375rem; background: transparent; transition: filter 180ms ease; }
.archive-message-line.group-first, .archive-message-line.group-middle { margin-bottom: .375rem; }
.archive-message-line.group-last, .archive-message-line.standalone { margin-bottom: .625rem; }
.archive-message-line.has-sender { grid-template-columns: 2rem minmax(0,1fr); }
.archive-message-line.out { grid-template-columns: minmax(0,1fr); justify-items: end; }
.archive-message-line.service-message { grid-template-columns: minmax(0,1fr); justify-items: center; margin: .5rem 0 1rem; }
.archive-message-content { min-width: 0; width: fit-content; max-width: var(--archive-incoming-max-width); position: relative; justify-self: start; }
.archive-message-line.out .archive-message-content { max-width: var(--archive-outgoing-max-width); justify-self: end; }
.archive-message-content .archive-bubble { max-width: 100%; }
.archive-message-content.has-inline-buttons .archive-bubble { width: 100%; border-bottom-left-radius: var(--archive-message-radius-small); border-bottom-right-radius: var(--archive-message-radius-small); }
.archive-message-line.is-highlighted { animation: archive-message-highlight 1.4s ease; }
.sender-avatar { width: 2rem; height: 2rem; padding: 0; overflow: hidden; border: 0; border-radius: 50%; display: grid; place-items: center; color: hsl(var(--avatar-hue) 58% 35%); background: hsl(var(--avatar-hue) 75% 88%); font: inherit; font-size: .68rem; font-weight: 780; cursor: pointer; transition: transform 100ms ease-out, box-shadow 140ms ease; }
.sender-avatar:hover:not(:disabled) { box-shadow: 0 0 0 .16rem rgba(51,144,236,.26); }
.sender-avatar:active:not(:disabled) { transform: scale(.92); }
.sender-avatar:focus-visible { outline: .15rem solid #3390ec; outline-offset: .12rem; }
.sender-avatar.hidden { visibility: hidden; }
.sender-avatar:disabled { cursor: default; }
.sender-avatar img { width: 100%; height: 100%; object-fit: cover; }
.archive-bubble { --bubble-color: rgba(255,255,255,.97); width: fit-content; min-width: 0; max-width: var(--archive-incoming-max-width); position: relative; justify-self: start; padding: .3125rem .5rem .375rem; border-radius: var(--archive-message-radius); color: #182235; background: var(--bubble-color); box-shadow: 0 1px 2px rgba(35,55,80,.16); }
.archive-message-line.out .archive-bubble { --bubble-color: #dceeff; max-width: var(--archive-outgoing-max-width); justify-self: end; }
.archive-message-line:not(.out).group-first .archive-bubble, .archive-message-line:not(.out).group-middle .archive-bubble { border-bottom-left-radius: var(--archive-message-radius-small); }
.archive-message-line:not(.out).group-middle .archive-bubble, .archive-message-line:not(.out).group-last .archive-bubble { border-top-left-radius: var(--archive-message-radius-small); }
.archive-message-line.out.group-first .archive-bubble, .archive-message-line.out.group-middle .archive-bubble { border-bottom-right-radius: var(--archive-message-radius-small); }
.archive-message-line.out.group-middle .archive-bubble, .archive-message-line.out.group-last .archive-bubble { border-top-right-radius: var(--archive-message-radius-small); }
.archive-message-line:not(.out).standalone .archive-bubble.has-appendix, .archive-message-line:not(.out).group-last .archive-bubble.has-appendix { border-bottom-left-radius: 0; }
.archive-message-line.out.standalone .archive-bubble.has-appendix, .archive-message-line.out.group-last .archive-bubble.has-appendix { border-bottom-right-radius: 0; }
.archive-appendix { position: absolute; bottom: -.0625rem; width: .5625rem; height: 1.125rem; overflow: hidden; pointer-events: none; }
.archive-appendix-corner { fill: var(--bubble-color); }
.archive-message-line:not(.out) .archive-appendix { left: -.5625rem; }
.archive-message-line.out .archive-appendix { right: -.551rem; }
.archive-bubble.album { width: var(--archive-album-width); max-width: none; box-sizing: border-box; }
.archive-bubble.album.media-only { padding: 0; }
.archive-bubble.album:not(.media-only) .archive-media-grid.album { margin-right: -.125rem; margin-left: -.125rem; }
.archive-bubble.album:not(.media-only) > .archive-media-grid.album:first-child { margin-top: -.125rem; }
.archive-bubble.visual-media { --single-media-max-height: 26rem; }
.archive-bubble.visual-media:not(.album):not(.media-only) { width: calc(clamp(9rem, calc(var(--media-ratio, 1) * var(--single-media-max-height)), min(30rem, calc(100vw - 1.75rem))) + .25rem); box-sizing: border-box; }
.archive-bubble.visual-media:not(.album):not(.media-only) .archive-visual,
.archive-bubble.visual-media:not(.album):not(.media-only) .archive-video-preview { width: 100%; }
.archive-bubble.sticker-only { --bubble-color: transparent; padding: 0 0 1.05rem; box-shadow: none; }
.archive-bubble.sticker-only::after { display: none; }
.archive-bubble.media-only { --bubble-color: transparent; padding: 0; background: transparent; box-shadow: none; }
.archive-bubble.media-only::after { display: none; }
.archive-bubble.media-only:has(.archive-reactions), .archive-bubble.has-outside-reactions { margin-bottom: 1.55rem; }
.archive-bubble.emoji-only { --bubble-color: transparent; min-width: 6rem; padding: 0 0 1rem; background: transparent; box-shadow: none; }
.archive-bubble.emoji-only::after { display: none; }
.archive-bubble.emoji-only .archive-message-text { margin: 0; line-height: .95; filter: drop-shadow(0 2px 3px rgba(0,0,0,.16)); }
.archive-bubble.emoji-1 .archive-message-text { font-size: 6.3rem; }
.archive-bubble.emoji-2 .archive-message-text { font-size: 5.0625rem; }
.archive-bubble.emoji-3 .archive-message-text { font-size: 4.725rem; }
.archive-bubble.emoji-4 .archive-message-text { font-size: 4.05rem; }
.archive-bubble.emoji-5 .archive-message-text { font-size: 3.375rem; }
.archive-bubble.emoji-6 .archive-message-text { font-size: 2.7rem; }
.archive-bubble.emoji-7 .archive-message-text { font-size: 2.025rem; }
.archive-bubble.emoji-only:not(.emoji-1) .archive-message-text { letter-spacing: -.12em; }
.archive-sender { display: block; margin: .25rem .42rem .3rem; color: #1576c9; font-size: .72rem; line-height: 1.15; }
.archive-via-bot { margin: 0 0 .18rem; display: flex; align-items: baseline; gap: .2rem; color: #237fc1; font-size: .6875rem; font-weight: 650; line-height: 1.15; white-space: nowrap; }
.archive-via-bot span { font-weight: 500; opacity: .84; }.archive-via-bot a, .archive-via-bot strong { overflow: hidden; color: inherit; text-overflow: ellipsis; text-decoration: none; }.archive-via-bot a:hover { text-decoration: underline; text-underline-offset: .12rem; }
.archive-bubble:not(.has-media) .archive-sender { margin: 0 0 .3rem; }
.archive-forward { margin: .22rem .42rem .36rem; display: flex; align-items: center; gap: .36rem; color: #227fbd; }
.archive-bubble:not(.has-media) .archive-forward { margin: 0 0 .36rem; }
.archive-forward svg { width: 1rem; flex: none; fill: currentColor; }
.archive-forward span { min-width: 0; display: grid; gap: .04rem; }.archive-forward small { font-size: .58rem; }.archive-forward strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .68rem; }
.archive-reply { width: calc(100% - .7rem); min-width: 9rem; margin: .22rem .35rem .38rem; padding: .3rem .44rem; border: 0; border-left: .19rem solid #2899ed; border-radius: .28rem; display: grid; gap: .1rem; color: #2578b5; background: rgba(45,145,215,.08); text-align: left; cursor: pointer; }
.archive-bubble:not(.has-media) .archive-reply { width: 100%; margin: 0 0 .35rem; }
.archive-reply:hover { background: rgba(45,145,215,.14); }
.archive-reply span, .archive-reply strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.archive-reply span { font-size: .64rem; }.archive-reply strong { color: inherit; font-size: .71rem; font-weight: 570; }
.archive-media-grid { display: grid; gap: .12rem; overflow: hidden; border-radius: .8rem; }
.archive-media-grid.album { position: relative; display: block; max-width: calc(100vw - 1.5rem); overflow: hidden; border-radius: inherit; background: rgba(0,0,0,.12); }
.archive-album-tile { position: absolute; padding: 0; overflow: hidden; border: 0; color: white; background: #131820; cursor: zoom-in; }
.archive-album-tile img, .archive-album-tile video { width: 100%; height: 100%; display: block; object-fit: cover; transition: transform 160ms ease-out, filter 160ms ease-out, opacity 90ms linear; }
.archive-album-tile:hover img, .archive-album-tile:hover video { transform: scale(1.018); filter: brightness(.93); }
.archive-album-tile:active img, .archive-album-tile:active video { transform: scale(.985); transition-duration: 80ms; }
.archive-media { min-width: 0; overflow: hidden; }
.archive-visual, .archive-video-preview { --single-media-max-height: 26rem; width: clamp(9rem, calc(var(--media-ratio, 1) * var(--single-media-max-height)), min(30rem, calc(100vw - 1.75rem))); aspect-ratio: var(--media-ratio, 1); max-height: var(--single-media-max-height); padding: 0; position: relative; display: block; overflow: hidden; border: 0; border-radius: .7rem; background: rgba(20,35,55,.08); cursor: zoom-in; }
.archive-visual img, .archive-visual video, .archive-video-preview video { display: block; width: 100%; height: 100%; object-fit: cover; border-radius: .7rem; }
.archive-video-poster { position: absolute !important; z-index: 0; inset: 0; width: 100%; height: 100%; display: block; object-fit: cover; pointer-events: none; }
.archive-inline-video { position: relative; z-index: 1; background: transparent; backface-visibility: hidden; }
.archive-inline-video.is-repainting { opacity: 0; }
.archive-video-duration { position: absolute; z-index: 3; top: .1875rem; left: .1875rem; min-height: 1.125rem; padding: 0 .375rem; border-radius: .75rem; display: flex; align-items: center; color: white; background: rgba(0,0,0,.25); font-size: .75rem; line-height: 1.125rem; pointer-events: none; }
.archive-video-badge { position: absolute; z-index: 3; left: 50%; top: 50%; width: 2.3rem; height: 2.3rem; transform: translate(-50%,-50%); border-radius: 50%; display: grid; place-items: center; color: white; background: rgba(13,19,27,.65); backdrop-filter: blur(8px); box-shadow: 0 3px 14px rgba(0,0,0,.25); opacity: 1; transition: opacity 140ms ease; pointer-events: none; }
.archive-album-tile.is-playing .archive-video-badge, .archive-video-preview.is-playing .archive-video-badge { opacity: 0; }
.archive-video-badge.large { width: 3rem; height: 3rem; }.archive-video-badge svg { width: 1.3rem; fill: currentColor; }
.archive-visual.sticker { width: min(12rem, 58vw); background: transparent; cursor: default; }
.archive-sticker-video { object-fit: contain !important; }
.media-spoiler-hidden > img, .media-spoiler-hidden > video { filter: blur(1.25rem) saturate(.45); transform: scale(1.08); }
.media-spoiler-hidden::after { content: ''; position: absolute; z-index: 2; inset: 0; background: radial-gradient(circle at 22% 28%, rgba(255,255,255,.24) 0 2px, transparent 3px), radial-gradient(circle at 72% 63%, rgba(255,255,255,.18) 0 2px, transparent 3px), rgba(32,39,50,.32); background-size: 21px 23px, 27px 25px, auto; pointer-events: none; }
.archive-spoiler-reveal { position: absolute; z-index: 3; top: 50%; left: 50%; min-width: 3.5rem; padding: .34rem .52rem; border-radius: 999px; display: flex; align-items: center; justify-content: center; gap: .25rem; transform: translate(-50%,-50%); color: white; background: rgba(20,25,34,.72); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); font-size: .66rem; font-weight: 720; pointer-events: none; }
.archive-spoiler-reveal svg { width: .9rem; fill: currentColor; }
.archive-ttl-badge { position: absolute; z-index: 4; top: .4rem; left: .4rem; padding: .22rem .38rem; border-radius: 999px; color: white; background: rgba(20,25,34,.68); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); font-size: .6rem; font-weight: 730; pointer-events: none; }
.archive-ttl-note { display: block; margin: .18rem .15rem 0; color: color-mix(in srgb, currentColor 68%, transparent); font-size: .58rem; }
.archive-document { width: min(29rem, calc(100vw - 3.5rem)); padding: .5rem; border-radius: .7rem; display: flex; align-items: center; gap: .55rem; color: inherit; background: rgba(45,95,135,.075); text-decoration: none; }
.archive-document-icon { width: 2.75rem; height: 2.75rem; flex: none; overflow: hidden; border-radius: .48rem; display: grid; place-items: center; color: white; background: #3597df; }
.archive-document-icon img { width: 100%; height: 100%; display: block; object-fit: cover; }
.archive-document svg { width: 1.2rem; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linejoin: round; }
.archive-document > span:last-child { min-width: 0; display: grid; gap: .16rem; }.archive-document strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .72rem; }.archive-document small { color: #6e7c8e; font-size: .62rem; }
.archive-message-text { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 1rem; line-height: 1.3125; }
.archive-unsupported { margin: 0; color: var(--muted); font-size: .76rem; font-style: italic; }
.archive-bubble.has-media { padding: .125rem; }
.archive-bubble.has-media .archive-message-text { margin: .375rem .375rem .25rem; }
.archive-bubble.content-document, .archive-bubble.content-audio, .archive-bubble.content-voice, .archive-bubble.content-webpage { padding-bottom: 1.375rem; }
.archive-message-line.service-message .archive-bubble { --bubble-color: transparent; max-width: min(34rem, 88%); justify-self: center; padding: 0; color: white; background: transparent; box-shadow: none; }
.archive-service-content { padding: .25rem .625rem; border-radius: var(--archive-message-radius); color: white; background: rgba(52,70,88,.62); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); box-shadow: 0 2px 8px rgba(15,25,40,.15); text-align: center; font-size: .8125rem; font-weight: 560; line-height: 1.35; }
.archive-service-content span { margin-right: .28rem; font-weight: 750; }
.archive-bubble.structured-content:not(.content-service):not(.content-dice) { min-width: min(20rem, calc(100vw - 4.5rem)); padding: .45rem .5rem 1.375rem; }
.archive-contact-card, .archive-location-card, .archive-generic-content-card { min-width: min(19rem, calc(100vw - 5.5rem)); display: flex; align-items: center; gap: .65rem; color: inherit; text-decoration: none; }
.archive-contact-avatar, .archive-location-pin, .archive-generic-icon { width: 2.75rem; height: 2.75rem; flex: none; border-radius: 50%; display: grid; place-items: center; color: white; background: #3399df; font-size: .82rem; font-weight: 800; }
.archive-contact-card > span:last-child, .archive-location-card > span:last-child, .archive-generic-content-card > span:last-child { min-width: 0; display: grid; gap: .12rem; }
.archive-contact-card strong, .archive-location-card strong, .archive-generic-content-card strong { overflow: hidden; text-overflow: ellipsis; font-size: .82rem; line-height: 1.3; }
.archive-contact-card a, .archive-contact-card small, .archive-location-card small, .archive-generic-content-card small { color: #638096; font-size: .68rem; text-decoration: none; }
.archive-location-pin { background: #e55050; }
.archive-location-pin svg, .archive-generic-icon svg { width: 1.35rem; fill: currentColor; }
.archive-poll-card { width: min(23rem, calc(100vw - 5.5rem)); display: grid; gap: .48rem; }
.archive-structured-eyebrow { color: #2b8ac7; font-size: .65rem; font-weight: 750; }
.archive-poll-card > strong { font-size: .86rem; line-height: 1.35; }
.archive-poll-card > small { color: #6e7c8e; font-size: .66rem; }
.archive-poll-options { display: grid; gap: .52rem; }
.archive-poll-options > div { position: relative; padding-bottom: .3rem; overflow: hidden; }
.archive-poll-options span { display: flex; align-items: baseline; gap: .45rem; font-size: .76rem; }
.archive-poll-options span strong { min-width: 2.15rem; color: #668095; font-size: .68rem; }
.archive-poll-options i { position: absolute; left: 0; bottom: 0; height: .18rem; max-width: 100%; border-radius: 999px; background: #3399df; transition: width 240ms ease; }
.archive-poll-options > div.chosen span strong, .archive-poll-options > div.correct span strong { color: #1688d2; }
.archive-poll-options > div.correct i { background: #38a667; }
.archive-todo-card { width: min(23rem, calc(100vw - 5.5rem)); display: grid; gap: .48rem; }
.archive-todo-card > strong { font-size: .86rem; line-height: 1.35; }
.archive-todo-card > small { color: #6e7c8e; font-size: .66rem; }
.archive-todo-items { display: grid; gap: .42rem; }
.archive-todo-items > div { display: grid; grid-template-columns: 1.15rem minmax(0,1fr); align-items: start; gap: .42rem; font-size: .76rem; line-height: 1.35; }
.archive-todo-items i { width: 1rem; height: 1rem; margin-top: .02rem; border: .1rem solid #8594a5; border-radius: 50%; display: grid; place-items: center; color: white; }
.archive-todo-items svg { width: .72rem; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.archive-todo-items > div.completed i { border-color: #3399df; background: #3399df; }
.archive-todo-items > div.completed span { color: #778797; text-decoration: line-through; }
.archive-bubble.content-dice { --bubble-color: transparent; min-width: 6rem; padding: 0 0 1rem; background: transparent; box-shadow: none; }
.archive-dice-card { position: relative; display: grid; justify-items: center; filter: drop-shadow(0 2px 3px rgba(0,0,0,.16)); }
.archive-dice-card > span { font-size: 5.75rem; line-height: 1; }
.archive-dice-card > small { margin-top: -.2rem; padding: .13rem .34rem; border-radius: 999px; color: white; background: rgba(20,24,31,.7); font-size: .6rem; }
.archive-generic-content-card { align-items: flex-start; }
.archive-generic-content-card p { margin: .1rem 0 0; display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 3; color: #52677b; font-size: .7rem; line-height: 1.4; }
.archive-bubble.content-round_video { --bubble-color: transparent; padding: 0; background: transparent; box-shadow: none; }
.archive-bubble.content-round_video .archive-video-preview { width: min(16.5rem, 66vw); max-height: none; aspect-ratio: 1; border-radius: 50%; background: #131820; }
.archive-bubble.content-round_video .archive-video-preview video, .archive-bubble.content-round_video .archive-video-poster { border-radius: 50%; }
.archive-bubble.content-round_video .archive-message-meta { right: .15rem; bottom: .15rem; padding: .2rem .34rem; border-radius: 999px; color: white; background: rgba(14,23,33,.55); backdrop-filter: blur(6px); }
.archive-message-text a { color: #087ed3; text-decoration: none; }.archive-message-text a:hover { text-decoration: underline; }
.rich-bold { font-weight: 760; }.rich-italic { font-style: italic; }.rich-underline { text-decoration: underline; }.rich-strike { text-decoration: line-through; }
.rich-code { padding: .08em .25em; border-radius: .25rem; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .92em; background: rgba(75,95,120,.12); }
.rich-spoiler { padding: 0 .015em; border-radius: .5rem; color: transparent !important; background-color: rgba(80,94,110,.14); background-image: url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHgAAAAnCAMAAADzYnzCAAAAb1BMVEUAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQEBAmuRwDAAAAI3RSTlMAAQIECBAYICgwOEBIUFhgaHB4gIiQmKCosLjAyNDY4Ojw+BokRScAAARxSURBVHgBjZeFkvNIDIQ/wSRZCGyYaf73f8Yjq06l8kEvZiKrR622MiZjtpswDoT/CSnB+aUxPzo4AUcEnEf/wRmDse3rIS5SuZMhCgCTZaJTA2PZDxio8uqrSBhBAvycGwQ1qhBwzn2FI4I7aK0MJAJP/Qerm97jgLF/fQzJB3bPKiHxA+YMaEgmwa8HNPFOZgDG5v0d68FPA1AzMkOGI/DzBSj+POe6QPIvZ9ZfaNrKu8/itfxZ+CcexSHDHxEBHASYfEAjqJn3F4bR+gPPWgbB2xEQvmZZA873hgBugLF/f+DDRSpgKE7U7GA8eyOhndZoyIkieHgHBJ7PEKIIlpYiIruEIaFK9GF/jfjS6AIpHTIjwyzeobiBX7+I1wIKOagR9ncDc9Bak8S1gXpLgUakENA/iZ0QedCfgSTyomUKhEFu749wad1NDgQvIcXgrxeoO8RIuUzx4HW2j2mp5N4/scrGaYfWDtUgyK1/PkOXuO1X2dr3/oVVLVVrFd5fYVRnioGxjfGH4oOkIl53Edq0BRnTebHa6AQWvj+AMBAOzq4fgnB8gAsW2kr4hgJlf2KUdx33nEBxbjQpqkGEyX6Ws2b4iBlEeXfHRoh7BwG3nKK67dg3ADQ2/ZoYvjdkgHutmPlq9JZiuUSznoiTFIixvRiIp5dFGiLPPo+rah+TKk3AMy+ZQcs7NjZxJH5Fn25TEIgJyWGFVRGKmzzPqVio8QGR4M7rQWluuIPy0Z+4CEhUUmRWodLERy1ChWksSppEqaWCv98gsF9i2fvVgMr3T0rsOIe+qaaq3RASzCxHPB557jSPnhQYz77EgeiPZCWdNPaFRh2XRchEGSJLfFfi5XWWbIcg5RgUL5xZv1erucD9hP4dq5rsurpMEMEbAuBGnUB56wrG8vWNkqsypu8LplT5+zMRZ1kb176j0fp7rGYnCT3A2cUBL/T2ULBEC60hFMSw302ByfuGpNwVQj7VTgg4IUbugNcBQLo8FS8kNbLCKaeojCWiogRJDU//tbykIsb3Yz5ecDWucnh9Qg3wSf1crVCqkkMP93g6jfQVThDWG26OQ67MuPUZSt2s4rRMSqY+vufQli2LHo866ZrGgOknQjpgAcr+0YjxCZgWoRyouA0kUpWIiR38mtqp2Rp1FsZ/j0vaTFyd0PKWck4GiwWrAOBtkNkQztuqIyJMXsfh2DQEzp+LaM/pRkFwyZAcEDYbVKHVERMz5M/ctbV89AcO8STibPoWB5xJ7w0Z85vkXsc5JAu5OaXw7ZKQNg/X72ksDWJPCSy/EahuiJU2G8gvl8gVgbx6y55QJbYmLJYIUqxSIRJsUWJsT3n172rEEIjZN0Qk7mSGPmwqduplMBq4RxENlMWckAWU83OGAaB1BggFMwAVDGF3gAbEb//zbColRbttsXymrBoRO41HfSdrGw8Kg2HrAKmH4oDz2Z84KAiXE5Jd7aVBHgMr+9HB+HmkRy73IlLVCxxYfY5Jp/X+qgOLGq3g5E66Ab8BXRclqpa8pEEAAAAASUVORK5CYII="); background-repeat: repeat-x; background-position: center; background-size: auto min(100%, 1.125rem); box-decoration-break: clone; -webkit-box-decoration-break: clone; text-decoration: none !important; text-shadow: none; cursor: pointer; animation: archive-spoiler-breathe 1.75s linear infinite; transition: color 250ms ease, opacity 250ms ease; }.rich-spoiler:hover, .rich-spoiler:focus-visible { outline: none; opacity: .82; }.rich-spoiler.revealed { color: inherit !important; background-color: transparent; background-image: none; cursor: text; animation: none; opacity: 1; }
@media (prefers-color-scheme: dark) { .rich-spoiler:not(.revealed), .archive-message-line.out .rich-spoiler:not(.revealed) { background-color: rgba(255,255,255,.055); background-image: url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHgAAAAnCAMAAADzYnzCAAAAb1BMVEUAAAD////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////w8PAzqESyAAAAI3RSTlMAAQIECBAYICgwOEBIUFhgaHB4gIiQmKCosLjAyNDY4Ojw+BokRScAAARxSURBVHgBjZeFkvNIDIQ/wSRZCGyYaf73f8Yjq06l8kEvZiKrR622MiZjtpswDoT/CSnB+aUxPzo4AUcEnEf/wRmDse3rIS5SuZMhCgCTZaJTA2PZDxio8uqrSBhBAvycGwQ1qhBwzn2FI4I7aK0MJAJP/Qerm97jgLF/fQzJB3bPKiHxA+YMaEgmwa8HNPFOZgDG5v0d68FPA1AzMkOGI/DzBSj+POe6QPIvZ9ZfaNrKu8/itfxZ+CcexSHDHxEBHASYfEAjqJn3F4bR+gPPWgbB2xEQvmZZA873hgBugLF/f+DDRSpgKE7U7GA8eyOhndZoyIkieHgHBJ7PEKIIlpYiIruEIaFK9GF/jfjS6AIpHTIjwyzeobiBX7+I1wIKOagR9ncDc9Bak8S1gXpLgUakENA/iZ0QedCfgSTyomUKhEFu749wad1NDgQvIcXgrxeoO8RIuUzx4HW2j2mp5N4/scrGaYfWDtUgyK1/PkOXuO1X2dr3/oVVLVVrFd5fYVRnioGxjfGH4oOkIl53Edq0BRnTebHa6AQWvj+AMBAOzq4fgnB8gAsW2kr4hgJlf2KUdx33nEBxbjQpqkGEyX6Ws2b4iBlEeXfHRoh7BwG3nKK67dg3ADQ2/ZoYvjdkgHutmPlq9JZiuUSznoiTFIixvRiIp5dFGiLPPo+rah+TKk3AMy+ZQcs7NjZxJH5Fn25TEIgJyWGFVRGKmzzPqVio8QGR4M7rQWluuIPy0Z+4CEhUUmRWodLERy1ChWksSppEqaWCv98gsF9i2fvVgMr3T0rsOIe+qaaq3RASzCxHPB557jSPnhQYz77EgeiPZCWdNPaFRh2XRchEGSJLfFfi5XWWbIcg5RgUL5xZv1erucD9hP4dq5rsurpMEMEbAuBGnUB56wrG8vWNkqsypu8LplT5+zMRZ1kb176j0fp7rGYnCT3A2cUBL/T2ULBEC60hFMSw302ByfuGpNwVQj7VTgg4IUbugNcBQLo8FS8kNbLCKaeojCWiogRJDU//tbykIsb3Yz5ecDWucnh9Qg3wSf1crVCqkkMP93g6jfQVThDWG26OQ67MuPUZSt2s4rRMSqY+vufQli2LHo866ZrGgOknQjpgAcr+0YjxCZgWoRyouA0kUpWIiR38mtqp2Rp1FsZ/j0vaTFyd0PKWck4GiwWrAOBtkNkQztuqIyJMXsfh2DQEzp+LaM/pRkFwyZAcEDYbVKHVERMz5M/ctbV89AcO8STibPoWB5xJ7w0Z85vkXsc5JAu5OaXw7ZKQNg/X72ksDWJPCSy/EahuiJU2G8gvl8gVgbx6y55QJbYmLJYIUqxSIRJsUWJsT3n172rEEIjZN0Qk7mSGPmwqduplMBq4RxENlMWckAWU83OGAaB1BggFMwAVDGF3gAbEb//zbColRbttsXymrBoRO41HfSdrGw8Kg2HrAKmH4oDz2Z84KAiXE5Jd7aVBHgMr+9HB+HmkRy73IlLVCxxYfY5Jp/X+qgOLGq3g5E66Ab8BXRclqpa8pEEAAAAASUVORK5CYII="); } }
@keyframes archive-spoiler-breathe { 25%, 75% { opacity: 1; } 50% { opacity: .34; } }
.archive-web-preview { min-width: min(20rem, calc(100vw - 3rem)); margin: .42rem .35rem .25rem; padding: .3rem; border-left: .2rem solid rgba(255,255,255,.72); border-radius: .42rem; display: grid; grid-template-columns: minmax(0,1fr) 4.8rem; align-items: center; gap: .48rem; color: inherit; background: rgba(255,255,255,.1); text-decoration: none; transition: background 130ms ease; }.archive-web-preview:hover { background: rgba(255,255,255,.16); }.archive-web-preview > span { min-width: 0; display: grid; gap: .12rem; }.archive-web-preview small { color: inherit; opacity: .7; font-size: .58rem; }.archive-web-preview strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .7rem; }.archive-web-preview p { margin: 0; display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 3; font-size: .66rem; line-height: 1.35; opacity: .88; }.archive-web-preview img { width: 4.8rem; height: 4.8rem; border-radius: .4rem; object-fit: cover; }
.archive-inline-buttons { width: 100%; display: flex; flex-direction: column; color: #182235; }.archive-inline-buttons > div { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0,1fr); }.archive-inline-buttons a, .archive-inline-buttons span { position: relative; min-width: 0; min-height: 2.125rem; margin: .125rem; padding: .4rem .55rem; border-radius: var(--archive-message-radius-small); display: grid; place-items: center; overflow: hidden; color: inherit; background: rgba(255,255,255,.72); box-shadow: 0 1px 2px rgba(25,45,70,.11); text-align: center; text-overflow: ellipsis; white-space: nowrap; text-decoration: none; font-size: .6875rem; font-weight: 720; transition: background-color 150ms ease, opacity 150ms ease, transform 90ms ease-out; }.archive-inline-buttons a:first-child, .archive-inline-buttons span:first-child { margin-left: 0; }.archive-inline-buttons a:last-child, .archive-inline-buttons span:last-child { margin-right: 0; }.archive-inline-buttons > div:first-child > * { margin-top: .25rem; }.archive-inline-buttons > div:last-child > :first-child { border-bottom-left-radius: var(--archive-message-radius); }.archive-inline-buttons > div:last-child > :last-child { border-bottom-right-radius: var(--archive-message-radius); }.archive-inline-buttons a:hover { background: rgba(255,255,255,.86); }.archive-inline-buttons a:active { transform: scale(.985); opacity: .84; }
.archive-message-meta { position: absolute; right: .5rem; bottom: .32rem; display: flex; justify-content: flex-end; align-items: center; gap: .25rem; color: #718095; font-size: .75rem; line-height: 1.35; }
.archive-message-meta.inline { position: relative; right: auto; bottom: auto; top: .375rem; float: right; height: 1.25rem; margin-right: -.375rem; margin-left: .4375rem; padding: 0 .25rem; gap: 0; box-sizing: border-box; }
.archive-message-meta.inline time { margin-right: .25rem; }
.archive-message-meta time { white-space: nowrap; }
.archive-delivery { width: 1.1875rem; height: 1.1875rem; margin-left: -.1875rem; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
.archive-views { display: inline-flex; align-items: center; gap: .12rem; }.archive-views svg { width: .72rem; fill: currentColor; }
.archive-bubble.media-only .archive-message-meta { right: .32rem; bottom: .3rem; padding: .2rem .34rem; border-radius: 999px; color: white; background: rgba(14,23,33,.48); backdrop-filter: blur(6px); }
.archive-bubble.emoji-only .archive-message-meta { right: 0; bottom: 0; padding: .16rem .3rem; border-radius: 999px; color: rgba(255,255,255,.88); background: rgba(20,24,31,.7); }
.archive-reactions { margin: .38rem 0 .12rem; display: flex; flex-wrap: wrap; gap: .28rem; }
.archive-bubble.has-media .archive-reactions { margin-right: .35rem; margin-left: .35rem; }
.archive-bubble.media-only .archive-reactions, .archive-bubble.has-outside-reactions .archive-reactions { position: absolute; z-index: 1; right: .08rem; bottom: -1.42rem; margin: 0; }
.archive-reactions > span { padding: .18rem .4rem; border-radius: 999px; color: #2878ad; background: rgba(50,145,210,.11); font-size: .75rem; }.archive-reactions small { font-size: .6rem; font-weight: 750; }
.archive-message-line.out .archive-bubble.media-only .archive-reactions > span, .archive-message-line.out .archive-bubble.has-outside-reactions .archive-reactions > span { color: white; background: rgba(35,38,46,.9); box-shadow: 0 1px 4px rgba(0,0,0,.25); }
.archive-deleted-copy { margin: .12rem 0; display: flex; align-items: center; gap: .42rem; color: #b0545a; font-size: .78rem; font-style: italic; }
.archive-deleted-copy svg { width: 1rem; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.archive-preserved { color: #8d7378; font-size: .62rem; }
.archive-version-trigger { padding: 0; border: 0; color: inherit; background: transparent; cursor: pointer; font: inherit; text-decoration: none; }
.archive-version-trigger:hover { text-decoration: underline; text-underline-offset: .13rem; }
.archive-preserved.archive-version-trigger { justify-self: start; color: #b06e77; }
.archive-message-actions { position: absolute; z-index: 2; right: -2.2rem; bottom: .06rem; }
.archive-message-line.out .archive-message-actions { right: auto; left: -2.2rem; }
.archive-message-actions button { position: relative; width: 1.7rem; height: 1.7rem; padding: 0; border: 1px solid rgba(65,86,112,.1); border-radius: 50%; display: grid; place-items: center; color: #758397; background: rgba(248,250,253,.76); box-shadow: 0 2px 6px rgba(35,55,80,.1); backdrop-filter: blur(10px); cursor: pointer; transition: color 130ms ease, background-color 130ms ease, transform 90ms ease, box-shadow 130ms ease; }
.archive-message-actions button:hover { color: var(--blue); background: rgba(255,255,255,.96); box-shadow: 0 3px 9px rgba(35,90,145,.16); }
.archive-message-actions button:active { transform: scale(.92); }
.archive-message-actions .history-icon { width: .94rem; height: .94rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.archive-restored-badge { position: absolute; right: -.28rem; bottom: -.24rem; width: .86rem; height: .86rem; border: 2px solid var(--archive-bg, #eef3f8); border-radius: 50%; display: grid; place-items: center; color: white; background: #c68119; box-shadow: 0 1px 3px rgba(111,72,14,.24); pointer-events: none; }
.archive-restored-badge svg { width: .68rem; height: .68rem; fill: none; stroke: currentColor; stroke-width: 1.55; stroke-linecap: round; stroke-linejoin: round; }
.archive-readonly-bar { min-height: 3.5rem; margin: .5rem auto .62rem; width: min(46rem, calc(100% - 1.25rem)); padding: .55rem .9rem; border: 0; border-radius: 1.35rem; display: flex; align-items: center; gap: .65rem; color: #617086; background: rgba(249,251,254,.88); backdrop-filter: blur(20px) saturate(160%); -webkit-backdrop-filter: blur(20px) saturate(160%); box-shadow: 0 5px 20px rgba(35,55,80,.14); }
.archive-readonly-bar svg { width: 1.1rem; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }.archive-readonly-bar span { display: grid; gap: .08rem; }.archive-readonly-bar strong { color: var(--text); font-size: .7rem; }.archive-readonly-bar small { font-size: .62rem; }
.archive-new-messages { position: absolute; z-index: 4; right: 1rem; bottom: 5.1rem; border: 0; border-radius: 999px; padding: .5rem .72rem; color: white; background: var(--blue); box-shadow: 0 8px 22px rgba(8,122,245,.26); cursor: pointer; font-weight: 700; font-size: .68rem; }
.archive-return-latest { position: absolute; z-index: 4; right: 1rem; bottom: 5.1rem; min-height: 2.25rem; padding: .45rem .7rem; border: 0; border-radius: 999px; display: flex; align-items: center; gap: .3rem; color: var(--text); background: color-mix(in srgb, var(--surface) 90%, transparent); box-shadow: 0 6px 20px rgba(25,45,70,.18); backdrop-filter: blur(16px); cursor: pointer; font-size: .7rem; font-weight: 700; }
.archive-return-latest svg { width: 1rem; height: 1rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.archive-return-latest + .archive-new-messages { bottom: 7.8rem; }
.archive-jump-notice { position: absolute; z-index: 5; left: 50%; bottom: 5rem; transform: translateX(-50%); max-width: min(90%, 30rem); padding: .48rem .55rem .48rem .75rem; border-radius: 999px; display: flex; align-items: center; gap: .5rem; color: white; background: rgba(38,53,70,.86); box-shadow: 0 6px 20px rgba(20,35,55,.2); backdrop-filter: blur(16px); font-size: .7rem; }
.archive-jump-notice button { width: 1.35rem; height: 1.35rem; border: 0; border-radius: 50%; color: inherit; background: rgba(255,255,255,.12); cursor: pointer; }
@keyframes archive-message-highlight { 0%,100% { filter: none; } 25%,65% { filter: drop-shadow(0 0 .45rem rgba(24,146,242,.8)); } }
.archive-info-pane { width: 19.25rem; min-width: 0; position: relative; grid-column: 3; justify-self: end; overflow: hidden; border-left: 1px solid rgba(45,60,85,.09); display: grid; grid-template-rows: auto minmax(0,1fr); }
.archive-info-scroll { min-height: 0; overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: none; -ms-overflow-style: none; }
.archive-info-scroll::-webkit-scrollbar { display: none; }
.archive-info-enter-active, .archive-info-leave-active { overflow: hidden; transition: opacity 180ms ease, transform 320ms cubic-bezier(.2,.8,.2,1); }
.archive-info-enter-from, .archive-info-leave-to { opacity: 0; transform: translateX(1.25rem); }
.archive-info-pane > header { min-height: 3rem; padding: .25rem .75rem; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 1px 0 rgba(45,60,85,.08); }
.archive-info-pane > header strong { font-size: .9rem; }
.archive-info-back { display: none !important; }
.archive-profile { padding: 2.1rem 1rem 1.4rem; display: grid; justify-items: center; gap: .4rem; text-align: center; }
.profile-avatar { width: 6rem; height: 6rem; font-size: 1.6rem; }.archive-profile h2 { margin: .35rem 0 0; font-size: 1.05rem; letter-spacing: -.02em; }.archive-profile > span:last-child { color: var(--muted); font-size: .72rem; }
.archive-info-cards { padding: 0 .75rem; display: grid; gap: .6rem; }
.archive-info-cards > section { padding: .85rem; border-radius: .9rem; display: grid; gap: .28rem; background: rgba(75,90,115,.055); }.archive-info-cards > section > span { color: var(--muted); font-size: .65rem; }.archive-info-cards > section > strong { font-size: .78rem; line-height: 1.45; }
.archive-info-cards .archive-stats { grid-template-columns: repeat(2,1fr); }.archive-stats div { display: grid; gap: .16rem; }.archive-stats strong { font-size: 1.15rem; }.archive-stats span { color: var(--muted); font-size: .62rem; }
.archive-info-cards > section.archive-display-setting { grid-template-columns: minmax(0,1fr) auto; align-items: center; gap: .7rem; }
.archive-display-setting > span { min-width: 0; display: grid; gap: .14rem; }.archive-display-setting > span strong { color: var(--text); font-size: .72rem; line-height: 1.3; }.archive-display-setting > span small { color: var(--muted); font-size: .58rem; line-height: 1.4; }
.archive-display-setting .switch { width: 2.45rem; height: 1.45rem; }.archive-display-setting .switch span { top: .15rem; left: .15rem; width: 1.15rem; height: 1.15rem; }.archive-display-setting .switch[aria-checked="true"] span { transform: translateX(1rem); }
.archive-info-note { margin: 1rem; color: var(--muted); font-size: .68rem; line-height: 1.55; }
.archive-shared-media { min-height: 15rem; padding-bottom: 1rem; position: relative; }
.archive-shared-tabs { position: sticky; z-index: 3; top: 0; margin-top: .35rem; padding: .75rem .5rem .55rem; overflow-x: auto; display: flex; gap: .05rem; scrollbar-width: none; background: linear-gradient(to bottom, color-mix(in srgb, var(--surface) 98%, transparent) 72%, transparent); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px); }.archive-shared-tabs::-webkit-scrollbar { display: none; }
.archive-shared-tabs button { min-width: 3.2rem; min-height: 2.2rem; padding: .35rem .55rem; border: 0; border-radius: .7rem; flex: 1 0 auto; color: var(--muted); background: transparent; cursor: pointer; font: inherit; font-size: .7rem; font-weight: 680; transition: color 150ms ease, background 150ms ease, transform 90ms ease-out; }.archive-shared-tabs button:hover { background: rgba(75,90,115,.07); }.archive-shared-tabs button:active { transform: scale(.96); }.archive-shared-tabs button.active { color: var(--blue); background: rgba(8,122,245,.11); }
.archive-shared-kind-filter { min-height: 2.75rem; padding: .3rem .65rem .55rem; display: flex; justify-content: flex-end; gap: .35rem; }.archive-shared-kind-filter button { min-height: 1.9rem; padding: .28rem .58rem .28rem .45rem; border: 0; border-radius: 999px; display: inline-flex; align-items: center; gap: .28rem; color: var(--muted); background: rgba(75,90,115,.065); cursor: pointer; font: inherit; font-size: .62rem; font-weight: 680; transition: color 140ms ease, background 140ms ease, transform 90ms ease-out, opacity 140ms ease; }.archive-shared-kind-filter button:hover { background: rgba(75,90,115,.11); }.archive-shared-kind-filter button:active { transform: scale(.95); }.archive-shared-kind-filter button.active { color: var(--blue); background: rgba(8,122,245,.12); }.archive-shared-kind-filter button:disabled { cursor: default; opacity: 1; }.archive-shared-kind-filter svg { width: .9rem; height: .9rem; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }.archive-shared-kind-filter svg path:last-child { vector-effect: non-scaling-stroke; }
.archive-shared-state { min-height: 11rem; padding: 1rem; display: flex; align-items: center; justify-content: center; gap: .45rem; color: var(--muted); text-align: center; font-size: .68rem; }.archive-shared-state.error { flex-direction: column; color: #bd5560; }.archive-shared-state button, .archive-shared-more { padding: .42rem .65rem; border: 0; border-radius: .65rem; color: var(--blue); background: rgba(8,122,245,.1); cursor: pointer; font: inherit; font-size: .65rem; font-weight: 700; }
.archive-shared-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); grid-auto-rows: 1fr; gap: .0625rem; overflow: hidden; }.archive-shared-grid > button { position: relative; aspect-ratio: 1; min-width: 0; padding: 0; overflow: hidden; border: 0; background: #141920; cursor: zoom-in; }.archive-shared-grid img, .archive-shared-grid video { width: 100%; height: 100%; display: block; object-fit: cover; transition: transform 160ms ease-out, filter 160ms ease-out; }.archive-shared-video-loader { background: linear-gradient(135deg, #1b222c, #11161d); }.archive-shared-grid button:hover img, .archive-shared-grid button:hover video { transform: scale(1.025); filter: brightness(.91); }.archive-shared-grid button:active img, .archive-shared-grid button:active video { transform: scale(.985); transition-duration: 80ms; }.archive-shared-duration { position: absolute; left: .2rem; bottom: .2rem; padding: .08rem .22rem; border-radius: .2rem; color: white; background: rgba(15,20,27,.62); font-size: .58rem; font-weight: 700; line-height: 1.25; backdrop-filter: blur(5px); }
.archive-shared-virtual { position: relative; min-height: 1px; overflow: hidden; }
.archive-shared-virtual.visual .archive-shared-virtual-item { position: absolute; min-width: 0; padding: 0; overflow: hidden; border: 0; background: #141920; cursor: zoom-in; }
.archive-shared-virtual-item, .archive-viewer-stage { -webkit-touch-callout: none; user-select: none; -webkit-user-select: none; }
.archive-shared-virtual.visual img, .archive-shared-virtual.visual video { width: 100%; height: 100%; display: block; object-fit: cover; transition: transform 160ms ease-out, filter 160ms ease-out; }
.archive-shared-virtual.visual button:hover img, .archive-shared-virtual.visual button:hover video { transform: scale(1.025); filter: brightness(.91); }
.archive-shared-virtual.visual button:active img, .archive-shared-virtual.visual button:active video { transform: scale(.985); transition-duration: 80ms; }
.archive-shared-virtual-item.placeholder { position: absolute; display: block; background: linear-gradient(110deg, rgba(95,110,130,.08) 25%, rgba(95,110,130,.14) 40%, rgba(95,110,130,.08) 55%); background-size: 220% 100%; animation: archive-shared-placeholder 1.15s linear infinite; }
.archive-shared-virtual-item.placeholder.list { margin: .3rem .75rem; width: calc(100% - 1.5rem) !important; height: 3.65rem !important; border-radius: .7rem; }
@keyframes archive-shared-placeholder { to { background-position-x: -220%; } }
.archive-shared-history-badge { position: absolute; z-index: 2; top: .22rem; right: .22rem; width: 1.25rem; height: 1.25rem; border: 1px solid rgba(255,255,255,.34); border-radius: 50%; display: grid; place-items: center; color: white; background: rgba(23,31,43,.72); box-shadow: 0 1px 5px rgba(0,0,0,.22); backdrop-filter: blur(6px); pointer-events: none; }.archive-shared-history-badge svg { width: .82rem; height: .82rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }.archive-shared-history-badge.list { position: static; width: 1.35rem; height: 1.35rem; flex: none; color: #9a6c16; background: rgba(190,135,30,.12); border-color: rgba(190,135,30,.18); box-shadow: none; backdrop-filter: none; }
.archive-shared-list { padding: .25rem .75rem; display: grid; }.archive-shared-list > a, .archive-shared-list.audio > div { min-width: 0; min-height: 3.9rem; padding: .55rem .15rem; display: flex; align-items: center; gap: .6rem; color: inherit; text-decoration: none; border-bottom: 1px solid rgba(75,90,115,.09); }.archive-shared-list > a:active { opacity: .72; }.archive-shared-list > a > span:not(.archive-shared-file-icon), .archive-shared-list.links a > span { min-width: 0; display: grid; gap: .14rem; }.archive-shared-list strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .7rem; }.archive-shared-list small, .archive-shared-list time { color: var(--muted); font-size: .58rem; }.archive-shared-file-icon { width: 2.5rem; height: 2.5rem; flex: none; border-radius: .65rem; display: grid; place-items: center; color: white; background: #3399df; }.archive-shared-file-icon svg { width: 1.2rem; fill: none; stroke: currentColor; stroke-width: 1.6; }.archive-shared-list.links a > svg { width: .9rem; margin-left: auto; flex: none; fill: none; stroke: var(--muted); stroke-width: 1.8; }.archive-shared-list.audio > div { display: grid; grid-template-columns: minmax(0,1fr) auto; }.archive-shared-list.audio .telegram-audio-player { min-width: 0; }.archive-shared-more { min-height: 2rem; margin: .65rem auto 0; display: flex; align-items: center; justify-content: center; gap: .35rem; }
.archive-shared-virtual.archive-shared-list { padding: 0; display: block; }
.archive-shared-virtual.archive-shared-list > a, .archive-shared-virtual.archive-shared-list.audio > div { position: absolute; box-sizing: border-box; padding-right: .9rem; padding-left: .9rem; }
.archive-shared-list.files > a > span:nth-child(2) { flex: 1; }.archive-shared-list-meta { display: grid; justify-items: end; gap: .28rem; }.archive-shared-list.audio .archive-shared-history-badge.list { justify-self: end; }
.archive-shared-range-loading { position: sticky; z-index: 3; bottom: .6rem; width: 2rem; height: 2rem; margin: -2.6rem auto .6rem; border-radius: 50%; display: grid; place-items: center; background: color-mix(in srgb, var(--solid-elevated) 88%, transparent); box-shadow: 0 .25rem .9rem rgba(25,40,60,.16); backdrop-filter: blur(10px); pointer-events: none; }
.archive-gallery-scrollbar { position: absolute; z-index: 8; top: 3.5rem; right: 0; bottom: .5rem; width: 3.375rem; display: none; pointer-events: none; }
.archive-gallery-scrollbar.visible { display: block; }
.archive-gallery-scrollbar-thumb { position: absolute; top: 0; right: -.625rem; width: 2.625rem; height: 2.625rem; padding-left: .55rem; border-radius: 50%; display: flex; align-items: center; color: white; background: rgba(21,31,43,.72); box-shadow: 0 .25rem .9rem rgba(0,0,0,.28); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); cursor: grab; touch-action: none; pointer-events: auto; will-change: transform; }
.archive-gallery-scrollbar.dragging .archive-gallery-scrollbar-thumb { background: rgba(43,61,79,.94); cursor: grabbing; }
.archive-gallery-scrollbar-thumb > span { width: .75rem; height: 1rem; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: .2rem; pointer-events: none; }
.archive-gallery-scrollbar-thumb i { width: 0; height: 0; border-right: .25rem solid transparent; border-left: .25rem solid transparent; }
.archive-gallery-scrollbar-thumb i:first-child { border-bottom: .3125rem solid rgba(255,255,255,.88); }
.archive-gallery-scrollbar-thumb i:last-child { border-top: .3125rem solid rgba(255,255,255,.88); }
.archive-gallery-scrollbar-bubble { position: absolute; right: 2.375rem; min-width: 3.4rem; max-width: 8rem; padding: .35rem .58rem; border-radius: 999px; color: white; background: rgba(43,61,79,.94); box-shadow: 0 .25rem .9rem rgba(0,0,0,.24); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); opacity: 0; transform: translate(8px,-50%); transition: opacity 160ms ease, transform 160ms ease; pointer-events: none; white-space: nowrap; text-align: center; font-size: .62rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.archive-gallery-scrollbar.show-bubble .archive-gallery-scrollbar-bubble, .archive-gallery-scrollbar.dragging .archive-gallery-scrollbar-bubble { opacity: 1; transform: translate(0,-50%); }
:global(.archive-shared-context-menu) { position: fixed; z-index: 1100; width: 11rem; padding: .32rem; border: 1px solid rgba(255,255,255,.42); border-radius: .82rem; color: var(--text); background: color-mix(in srgb, var(--solid-elevated) 90%, transparent); box-shadow: 0 .8rem 2.4rem rgba(5,12,24,.24); backdrop-filter: blur(24px) saturate(165%); -webkit-backdrop-filter: blur(24px) saturate(165%); transform-origin: top left; animation: archive-context-materialize 120ms cubic-bezier(.2,.8,.2,1); }
:global(.archive-shared-context-menu button) { width: 100%; min-height: 2.35rem; padding: .42rem .62rem; border: 0; border-radius: .58rem; display: flex; align-items: center; gap: .58rem; color: inherit; background: transparent; cursor: pointer; font: inherit; font-size: .72rem; font-weight: 650; text-align: left; transition: background 110ms ease, transform 80ms ease-out; }
:global(.archive-shared-context-menu button:hover), :global(.archive-shared-context-menu button:focus-visible) { outline: 0; background: rgba(8,122,245,.12); }
:global(.archive-shared-context-menu button:active) { transform: scale(.975); background: rgba(8,122,245,.18); }
:global(.archive-shared-context-menu svg) { width: 1.08rem; height: 1.08rem; flex: none; fill: none; stroke: var(--blue); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
@keyframes archive-context-materialize { from { opacity: 0; transform: scale(.94); } to { opacity: 1; transform: none; } }
.archive-no-selection { display: grid; place-items: center; }.archive-no-selection > div { display: grid; gap: .4rem; text-align: center; }.archive-no-selection span { color: var(--muted); font-size: .75rem; }

:global(body.archive-viewer-open) { overflow: hidden; }
.archive-profile-overlay { position: fixed; z-index: 975; inset: 0; padding: 1.25rem; display: grid; place-items: center; background: rgba(6,10,16,.42); backdrop-filter: blur(9px); -webkit-backdrop-filter: blur(9px); animation: viewer-materialize 150ms ease-out; }
.archive-sender-profile-card { width: min(23rem, 100%); max-height: min(42rem, calc(100vh - 2.5rem)); position: relative; overflow-y: auto; border: 1px solid rgba(255,255,255,.38); border-radius: 1.35rem; color: var(--text); background: rgba(249,251,254,.94); box-shadow: 0 28px 85px rgba(3,10,20,.32); backdrop-filter: blur(30px) saturate(170%); -webkit-backdrop-filter: blur(30px) saturate(170%); animation: archive-profile-materialize 180ms cubic-bezier(.2,.8,.2,1); }
@keyframes archive-profile-materialize { from { opacity: 0; transform: scale(.965) translateY(.35rem); } to { opacity: 1; transform: none; } }
.archive-profile-close { position: absolute; z-index: 2; top: .7rem; right: .7rem; width: 2.25rem; height: 2.25rem; padding: 0; border: 0; border-radius: 50%; display: grid; place-items: center; color: var(--muted); background: rgba(70,85,110,.1); cursor: pointer; transition: transform 100ms ease-out, background 140ms ease; }.archive-profile-close:hover { background: rgba(70,85,110,.17); }.archive-profile-close:active { transform: scale(.9); }.archive-profile-close svg { width: 1.05rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; }
.archive-sender-profile-hero { padding: 2.15rem 1.25rem 1.1rem; display: grid; justify-items: center; gap: .28rem; text-align: center; }.archive-sender-profile-avatar { width: 6.25rem; height: 6.25rem; overflow: hidden; border-radius: 50%; display: grid; place-items: center; color: hsl(var(--avatar-hue) 58% 35%); background: hsl(var(--avatar-hue) 75% 88%); box-shadow: 0 .45rem 1.5rem rgba(25,45,70,.15); font-size: 1.65rem; font-weight: 780; }.archive-sender-profile-avatar img { width: 100%; height: 100%; object-fit: cover; }.archive-sender-profile-hero h2 { margin: .6rem 0 0; max-width: 100%; overflow-wrap: anywhere; font-size: 1.12rem; line-height: 1.2; letter-spacing: -.025em; }.archive-sender-profile-hero > span:not(.archive-sender-profile-avatar) { color: var(--muted); font-size: .72rem; }
.archive-sender-profile-badges { margin-top: .45rem; display: flex; justify-content: center; flex-wrap: wrap; gap: .32rem; }.archive-sender-profile-badges i { padding: .18rem .42rem; border-radius: 999px; color: #247eb8; background: rgba(45,145,215,.11); font-size: .57rem; font-style: normal; font-weight: 720; }.archive-sender-profile-badges i.warning { color: #bb4651; background: rgba(205,65,75,.12); }
.archive-sender-profile-state { min-height: 8rem; padding: 1rem; display: grid; place-content: center; justify-items: center; gap: .55rem; color: var(--muted); text-align: center; font-size: .7rem; }.archive-sender-profile-state.error strong { color: #cf5360; }
.archive-sender-profile-about, .archive-sender-profile-note { margin: 0 .9rem .75rem; padding: .8rem .9rem; border-radius: .9rem; color: var(--text); background: rgba(75,90,115,.055); white-space: pre-wrap; overflow-wrap: anywhere; font-size: .72rem; line-height: 1.5; }.archive-sender-profile-note { color: var(--muted); }
.archive-sender-profile-details { margin: 0; padding: 0 .9rem 1rem; display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: .55rem; }.archive-sender-profile-details div { min-width: 0; padding: .68rem .72rem; border-radius: .82rem; display: grid; gap: .2rem; background: rgba(75,90,115,.055); }.archive-sender-profile-details div.wide { grid-column: 1 / -1; }.archive-sender-profile-details dt { color: var(--muted); font-size: .58rem; }.archive-sender-profile-details dd { margin: 0; overflow-wrap: anywhere; font-size: .7rem; font-weight: 650; line-height: 1.35; }
.archive-history-overlay { position: fixed; z-index: 950; inset: 0; padding: 1.5rem; display: grid; place-items: center; background: rgba(6,10,16,.58); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); animation: viewer-materialize 160ms ease-out; }
.archive-history-panel { width: min(42rem, 100%); max-height: min(48rem, calc(100vh - 3rem)); overflow: hidden; border: 1px solid rgba(55,70,95,.11); border-radius: 1.15rem; display: grid; grid-template-rows: auto minmax(0,1fr); color: var(--text); background: rgba(249,251,254,.97); box-shadow: 0 26px 80px rgba(0,0,0,.28); }
.archive-history-panel > header { min-height: 4.2rem; padding: .75rem .9rem .75rem 1.15rem; border-bottom: 1px solid rgba(55,70,95,.1); display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.archive-history-panel > header > span { min-width: 0; display: grid; gap: .18rem; }.archive-history-panel > header strong { font-size: .94rem; }.archive-history-panel > header small { color: var(--muted); font-size: .65rem; }
.archive-history-panel > header button { width: 2.25rem; height: 2.25rem; padding: 0; border: 0; border-radius: 50%; display: grid; place-items: center; color: var(--muted); background: rgba(70,85,110,.075); cursor: pointer; }.archive-history-panel > header button:hover { background: rgba(70,85,110,.13); }.archive-history-panel > header svg { width: 1.05rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; }
.archive-history-panel > main { min-height: 0; overflow-y: auto; padding: 1rem 1rem 1.4rem; }
.archive-history-state { min-height: 15rem; display: grid; place-content: center; justify-items: center; gap: .6rem; color: var(--muted); font-size: .72rem; text-align: center; }.archive-history-state i { width: 1.8rem; height: 1.8rem; border: 2px solid rgba(50,145,220,.2); border-top-color: #3390ec; border-radius: 50%; animation: archive-spin .8s linear infinite; }.archive-history-state.error strong { color: #d65762; }
@keyframes archive-spin { to { transform: rotate(360deg); } }
.archive-history-timeline { margin: 0; padding: 0; list-style: none; display: grid; gap: .85rem; }
.archive-history-timeline > li { position: relative; padding-left: 1.35rem; }.archive-history-timeline > li:not(:last-child)::before { content: ''; position: absolute; top: .6rem; bottom: -.9rem; left: .31rem; width: 2px; background: rgba(55,145,215,.22); }.archive-history-node { position: absolute; z-index: 1; top: .55rem; left: 0; width: .7rem; height: .7rem; border: 2px solid #3390ec; border-radius: 50%; background: #f9fbfe; }
.archive-history-timeline article { padding: .85rem; border: 1px solid rgba(55,75,100,.1); border-radius: .85rem; display: grid; gap: .65rem; background: white; box-shadow: 0 4px 16px rgba(25,45,70,.055); }.archive-history-timeline article.deleted { background: rgba(255,247,248,.96); }
.archive-history-timeline article > header { display: flex; align-items: start; justify-content: space-between; gap: .75rem; }.archive-history-timeline article > header > span { display: flex; align-items: center; gap: .38rem; }.archive-history-timeline article > header strong { font-size: .75rem; }.archive-history-timeline article > header em { padding: .14rem .34rem; border-radius: 999px; color: #2584c4; background: rgba(45,145,215,.1); font-size: .55rem; font-style: normal; font-weight: 720; }.archive-history-timeline article > header time { color: var(--muted); font-size: .58rem; white-space: nowrap; }
.archive-history-text, .archive-history-summary, .archive-history-deleted { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: .78rem; line-height: 1.48; }.archive-history-text a { color: #2189cf; }.archive-history-summary { color: var(--muted); }.archive-history-deleted { color: #b25460; font-style: italic; }
.archive-history-media { display: flex; flex-wrap: wrap; gap: .45rem; }.archive-history-media-item { max-width: 100%; }.archive-history-media-item.photo, .archive-history-media-item.video, .archive-history-media-item.animation { width: min(17rem, 100%); }.archive-history-media-item.sticker { width: 8.5rem; height: 8.5rem; }.archive-history-media-item img, .archive-history-media-item video { width: 100%; max-height: 16rem; border-radius: .65rem; display: block; object-fit: contain; background: rgba(20,30,45,.07); }.archive-history-media-item > a { min-width: min(18rem, 100%); padding: .5rem; border-radius: .65rem; display: flex; align-items: center; gap: .55rem; color: inherit; background: rgba(45,95,135,.075); text-decoration: none; }.archive-history-media-item > a > span:last-child { min-width: 0; display: grid; gap: .12rem; }.archive-history-media-item > a strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .7rem; }.archive-history-media-item > a small { color: var(--muted); font-size: .6rem; }
.archive-history-timeline article > footer { display: flex; justify-content: space-between; gap: .75rem; color: var(--muted); font-size: .58rem; }
.archive-viewer { position: fixed; z-index: 1000; inset: 0; display: grid; grid-template: auto minmax(0,1fr) auto / minmax(3.5rem,1fr) minmax(0,82vw) minmax(3.5rem,1fr); color: white; background: rgba(0,2,5,.975); animation: viewer-materialize 180ms ease-out; }
.archive-viewer-header { position: relative; z-index: 2; grid-column: 1 / -1; min-height: 4.5rem; padding: .65rem 1rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem; background: linear-gradient(rgba(0,0,0,.5), transparent); }
.archive-viewer-author { min-width: 0; display: flex; align-items: center; gap: .62rem; }.archive-viewer-author .sender-avatar { width: 2.5rem; height: 2.5rem; }.archive-viewer-author > span:last-child { min-width: 0; display: grid; gap: .08rem; }.archive-viewer-author strong, .archive-viewer-author small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.archive-viewer-author strong { font-size: .82rem; }.archive-viewer-author small { color: rgba(255,255,255,.62); font-size: .65rem; }
.archive-viewer-tools { display: flex; align-items: center; gap: .2rem; }.archive-viewer-tools > span { min-width: 2.8rem; color: rgba(255,255,255,.7); text-align: center; font-size: .62rem; }
.archive-viewer-tools button, .archive-viewer-tools a { width: 2.45rem; height: 2.45rem; padding: 0; border: 0; border-radius: 50%; display: grid; place-items: center; color: white; background: transparent; cursor: pointer; transition: background 130ms ease, transform 90ms ease-out; }.archive-viewer-tools button:hover, .archive-viewer-tools a:hover { background: rgba(255,255,255,.12); }.archive-viewer-tools button:active, .archive-viewer-tools a:active { transform: scale(.92); }.archive-viewer-tools svg { width: 1.15rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.archive-viewer-stage { min-width: 0; min-height: 0; position: absolute; z-index: 0; inset: 0; overflow: hidden; display: flex; align-items: center; justify-content: center; padding: 4.5rem 0 4.2rem; touch-action: none; overscroll-behavior: none; cursor: zoom-in; user-select: none; -webkit-user-select: none; }
.archive-viewer-stage img, .archive-viewer-stage video { max-width: 100vw; max-height: calc(100dvh - 8.7rem); flex: none; object-fit: contain; box-shadow: 0 12px 55px rgba(0,0,0,.38); transform-origin: center; transition: transform 180ms cubic-bezier(.2,.8,.2,1); will-change: transform; -webkit-user-drag: none; }
.archive-viewer.zoomed .archive-viewer-stage { cursor: grab; }
.archive-viewer.dragging .archive-viewer-stage { cursor: grabbing; }
.archive-viewer.dragging .archive-viewer-stage img, .archive-viewer.dragging .archive-viewer-stage video { transition: none; }
.archive-viewer-nav { position: relative; z-index: 1; align-self: center; width: 3.1rem; height: 3.1rem; padding: 0; border: 0; border-radius: 50%; display: grid; place-items: center; color: white; background: rgba(255,255,255,.08); backdrop-filter: blur(10px); cursor: pointer; transition: background 130ms ease, transform 90ms ease-out, opacity 130ms ease; }.archive-viewer-nav.previous { grid-column: 1; justify-self: center; }.archive-viewer-nav.next { grid-column: 3; justify-self: center; }.archive-viewer-nav:hover:not(:disabled) { background: rgba(255,255,255,.17); }.archive-viewer-nav:active:not(:disabled) { transform: scale(.9); }.archive-viewer-nav:disabled { opacity: .2; cursor: default; }.archive-viewer-nav svg { width: 1.35rem; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.archive-viewer-footer { position: relative; z-index: 1; grid-column: 1 / -1; min-height: 4.2rem; padding: .45rem 1rem .8rem; display: grid; justify-items: center; gap: .32rem; background: linear-gradient(transparent, rgba(0,0,0,.48)); pointer-events: none; }.archive-viewer-footer > * { pointer-events: auto; }.archive-viewer-footer > span { color: rgba(255,255,255,.62); font-size: .64rem; }.archive-viewer-footer > p { max-width: min(40rem,80vw); margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .72rem; }
.archive-viewer-thumbnails { max-width: min(36rem,80vw); display: flex; justify-content: center; gap: .28rem; overflow-x: auto; }.archive-viewer-thumbnails button { width: 2.8rem; height: 2.8rem; padding: 0; flex: none; overflow: hidden; border: 2px solid transparent; border-radius: .38rem; background: #181d24; opacity: .62; cursor: pointer; transition: opacity 130ms ease, border-color 130ms ease, transform 90ms ease-out; }.archive-viewer-thumbnails button.active { border-color: #8778ea; opacity: 1; }.archive-viewer-thumbnails button:active { transform: scale(.92); }.archive-viewer-thumbnails img, .archive-viewer-thumbnails video { width: 100%; height: 100%; display: block; object-fit: cover; }
@keyframes viewer-materialize { from { opacity: 0; } to { opacity: 1; } }

@media (max-width: 1120px) {
  .archive-layout { grid-template-columns: 19rem minmax(22rem,1fr); }
  .archive-layout.info-open { grid-template-columns: 19rem minmax(22rem,1fr); }
  .archive-info-pane { position: fixed; z-index: 25; top: 0; right: 0; bottom: 0; width: min(19.25rem, calc(100% - 4rem)); box-shadow: -18px 0 55px rgba(25,35,50,.18); }
  .archive-info-enter-from, .archive-info-leave-to { opacity: 0; transform: translateX(100%); }
}
@media (max-width: 760px) {
  .archive-layout { height: 100%; display: block; }
  .archive-chat-pane, .archive-message-pane { width: 100%; height: 100%; }
  .archive-layout:not(.mobile-thread-open) .archive-message-pane, .archive-layout:not(.mobile-thread-open) .archive-info-pane { display: none; }
  .archive-layout.mobile-thread-open .archive-chat-pane { display: none; }
  .archive-layout.mobile-thread-open.mobile-nav-forward .archive-message-pane { animation: archive-mobile-thread-enter 280ms cubic-bezier(.2,.8,.2,1); }
  .archive-layout:not(.mobile-thread-open).mobile-nav-back .archive-chat-pane { animation: archive-mobile-list-return 260ms cubic-bezier(.2,.8,.2,1); }
  .archive-layout.mobile-transition-forward .archive-info-enter-active, .archive-layout.mobile-transition-forward .archive-info-leave-active { transition-duration: 300ms; }
  .archive-layout.mobile-transition-forward.mobile-from-thread.info-open .archive-message-pane { animation: archive-mobile-source-push 300ms cubic-bezier(.2,.8,.2,1) both; }
  .archive-layout.mobile-transition-forward.mobile-from-thread .archive-info-enter-from { opacity: 1; transform: translateX(100%); }
  .archive-layout.mobile-transition-forward.mobile-from-info .archive-info-leave-to { opacity: 1; transform: translateX(-24%); }
  .archive-layout.mobile-transition-back.mobile-from-thread .archive-info-enter-from { opacity: .72; transform: translateX(-24%); }
  .archive-layout.mobile-transition-forward.mobile-from-info .archive-message-pane { z-index: 26; animation: archive-mobile-deeper-chat-enter 300ms cubic-bezier(.2,.8,.2,1) both; will-change: transform; }
  .archive-layout.mobile-transition-back.mobile-from-info .archive-message-pane { animation: archive-mobile-chat-return 280ms cubic-bezier(.2,.8,.2,1); }
  .archive-list-header { padding-top: max(1.2rem, calc(env(safe-area-inset-top) + .65rem)); padding-right: max(1.1rem, env(safe-area-inset-right)); padding-left: max(1.1rem, env(safe-area-inset-left)); }
  .archive-search { min-height: 2.75rem; margin-right: max(.8rem, env(safe-area-inset-right)); margin-left: max(.8rem, env(safe-area-inset-left)); }
  .archive-search input, .archive-message-search input { font-size: 1rem; }
  .archive-chat-list { padding-right: max(.45rem, env(safe-area-inset-right)); padding-bottom: max(.7rem, env(safe-area-inset-bottom)); padding-left: max(.45rem, env(safe-area-inset-left)); scroll-padding-bottom: 1rem; }
  .archive-chat-item { min-height: 4.25rem; padding-top: .7rem; padding-bottom: .7rem; touch-action: manipulation; }
  .archive-back { display: grid; }
  .archive-icon-button, .archive-back, .archive-info-pane header button { width: 2.75rem; height: 2.75rem; touch-action: manipulation; }
  .archive-thread-header { min-height: calc(3.5rem + env(safe-area-inset-top)); padding: env(safe-area-inset-top) max(.35rem, env(safe-area-inset-right)) 0 max(.25rem, env(safe-area-inset-left)); gap: .35rem; }
  .archive-thread-title span { max-width: 44vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .archive-message-search { top: calc(env(safe-area-inset-top) + .35rem); width: calc(100% - .6rem); }
  .archive-message-search > header { padding-right: .18rem; padding-left: .18rem; }
  .archive-message-search > header > button, .archive-message-search label > button { width: 2.5rem; height: 2.5rem; }
  .archive-message-search > header > label { min-height: 2.5rem; }
  .archive-message-search-dropdown { width: calc(100% - .5rem); max-height: calc(100dvh - 4.7rem - env(safe-area-inset-top)); }
  .archive-message-search-results { max-height: calc(100dvh - 5.2rem - env(safe-area-inset-top)); }
  .archive-message-search-results > button { min-height: 3.6rem; padding: .72rem; }
  .archive-message-search-count { min-width: 2.9rem; }
  .thread-avatar { width: 2.35rem; height: 2.35rem; }
  .archive-message-viewport { touch-action: pan-y pinch-zoom; overscroll-behavior-y: contain; }
  .archive-message-flow { width: 100%; padding: .75rem max(.5rem, env(safe-area-inset-right)) max(.8rem, env(safe-area-inset-bottom)) max(.5rem, env(safe-area-inset-left)); }
  .archive-bubble { max-width: min(var(--archive-incoming-max-width), calc(100vw - 6.25rem)); }
  .archive-message-line.out .archive-bubble { max-width: min(var(--archive-outgoing-max-width), calc(100vw - 3.75rem)); }
  .archive-message-content { max-width: calc(100vw - 1rem); }
  .archive-message-line.has-sender .archive-message-content { max-width: calc(100vw - 4rem); }
  .archive-message-line.out .archive-message-content { max-width: calc(100vw - 1rem); }
  .archive-bubble.album { max-width: 100%; }
  .archive-bubble.album .archive-media-grid.album { max-width: 100%; }
  .archive-bubble.visual-media, .archive-visual, .archive-video-preview { --single-media-max-height: 20rem; }
  .archive-message-actions button { width: 2.1rem; height: 2.1rem; }
  .archive-info-pane { width: 100%; height: 100vh; height: 100dvh; top: 0; bottom: auto; }
  .archive-info-pane > header { min-height: calc(3.5rem + env(safe-area-inset-top)); padding: env(safe-area-inset-top) max(.45rem, env(safe-area-inset-right)) 0 max(.25rem, env(safe-area-inset-left)); justify-content: flex-start; gap: .35rem; }
  .archive-info-back { display: grid !important; }
  .archive-info-close { display: none !important; }
  .archive-info-scroll { padding-bottom: env(safe-area-inset-bottom); }
  .archive-shared-tabs button { min-height: 2.75rem; }
  .archive-shared-kind-filter button { min-height: 2.5rem; }
  .archive-readonly-bar { min-height: 3.25rem; margin-bottom: max(.45rem, env(safe-area-inset-bottom)); }
  .archive-viewer { grid-template-columns: 3rem minmax(0,1fr) 3rem; }
  .archive-viewer-header { min-height: calc(4.25rem + env(safe-area-inset-top)); padding: env(safe-area-inset-top) max(.35rem, env(safe-area-inset-right)) 0 max(.55rem, env(safe-area-inset-left)); }.archive-viewer-tools > span { display: none; }.archive-viewer-tools button, .archive-viewer-tools a { width: 2.75rem; height: 2.75rem; }
  .archive-viewer-stage { padding: calc(4.25rem + env(safe-area-inset-top)) 0 calc(4.8rem + env(safe-area-inset-bottom)); }.archive-viewer-stage img, .archive-viewer-stage video { max-height: calc(100dvh - 9.05rem - env(safe-area-inset-top) - env(safe-area-inset-bottom)); }
  .archive-viewer-footer { padding-bottom: max(.8rem, env(safe-area-inset-bottom)); }
  .archive-viewer-nav { opacity: 0; pointer-events: none; }
  .archive-history-overlay { padding: 0; align-items: end; }.archive-history-panel { width: 100%; max-height: 88vh; max-height: min(88dvh, calc(100dvh - env(safe-area-inset-top))); padding-bottom: env(safe-area-inset-bottom); border-radius: 1.15rem 1.15rem 0 0; border-bottom: 0; }.archive-history-panel > main { padding: .8rem .7rem 1.2rem; }
  .archive-profile-overlay { padding: 0; align-items: end; }.archive-sender-profile-card { width: 100%; max-height: 88vh; max-height: min(88dvh, calc(100dvh - env(safe-area-inset-top))); padding-bottom: env(safe-area-inset-bottom); border-radius: 1.35rem 1.35rem 0 0; border-bottom: 0; }
}
@keyframes archive-mobile-thread-enter { from { opacity: .5; transform: translateX(16%); } to { opacity: 1; transform: none; } }
@keyframes archive-mobile-list-return { from { opacity: .65; transform: translateX(-10%); } to { opacity: 1; transform: none; } }
@keyframes archive-mobile-source-push { from { transform: none; } to { transform: translateX(-24%); } }
@keyframes archive-mobile-deeper-chat-enter { from { opacity: 1; transform: translateX(100%); } to { opacity: 1; transform: none; } }
@keyframes archive-mobile-chat-return { from { opacity: .7; transform: translateX(-10%); } to { opacity: 1; transform: none; } }
@media (prefers-color-scheme: dark) {
  .archive-layout { background: #11151c; }
  .archive-chat-pane, .archive-info-pane { background: rgba(25,30,39,.94); border-color: rgba(255,255,255,.065); }
  .archive-search { background: rgba(255,255,255,.065); }
  .archive-chat-item:hover { background: rgba(255,255,255,.05); }
  .archive-chat-copy i { color: #d79ca3; background: rgba(215,90,100,.12); }
  .archive-message-pane { background-color: #0d1219; background-image: linear-gradient(rgba(13,18,25,.76), rgba(13,18,25,.76)), url('../assets/chat-pattern.svg'), radial-gradient(circle at 18% 12%, rgba(30,115,170,.14), transparent 30%), radial-gradient(circle at 85% 78%, rgba(95,73,178,.13), transparent 28%); background-size: auto, 15rem 15rem, auto, auto; }
  .archive-thread-header { background: rgba(23,28,36,.9); box-shadow: 0 1px 0 rgba(255,255,255,.065); }
  .archive-message-search > header, .archive-message-search-dropdown { color: #f2f4f7; background: rgba(27,33,42,.97); border-color: rgba(255,255,255,.08); box-shadow: 0 .55rem 1.8rem rgba(0,0,0,.4); }
  .archive-message-search > header > label { background: rgba(255,255,255,.065); }
  .archive-message-search-results > button:hover { background: rgba(255,255,255,.055); }.archive-message-search-results > button.active { background: rgba(51,144,236,.17); }
  .archive-message-search-results mark { background: rgba(224,174,35,.42); }
  .archive-bubble { --bubble-color: #222a35; color: #f2f4f7; background: var(--bubble-color); box-shadow: 0 1px 2px rgba(0,0,0,.34); }
  .archive-message-line.out .archive-bubble { --bubble-color: #7568c6; }
  .archive-message-line.out .archive-via-bot { color: #f0efff; }
  .archive-inline-buttons { color: white; }.archive-inline-buttons a, .archive-inline-buttons span { background: rgba(56,66,78,.78); box-shadow: 0 1px 2px rgba(0,0,0,.26); }.archive-inline-buttons a:hover { background: rgba(70,82,96,.9); }
  .archive-bubble.sticker-only { --bubble-color: transparent; }
  .archive-message-line.out .archive-sender, .archive-message-line.out .archive-reply, .archive-message-line.out .archive-forward { color: #edf0ff; }
  .archive-message-line.out .archive-reply { border-left-color: rgba(255,255,255,.78); background: rgba(255,255,255,.1); }
  .archive-message-line.out .archive-message-text a { color: #fff; text-decoration: underline; text-decoration-color: rgba(255,255,255,.45); }
  .archive-document { background: rgba(255,255,255,.075); }
  .archive-document small, .archive-message-meta { color: #aeb8c5; }
  .archive-message-line.out .archive-message-meta { color: rgba(255,255,255,.72); }
  .archive-message-actions button { color: #a8b4c4; background: rgba(35,43,54,.78); border-color: rgba(255,255,255,.07); box-shadow: 0 2px 6px rgba(0,0,0,.24); }
  .archive-message-actions button:hover { color: #72b9f1; background: #303b49; }
  .archive-restored-badge { border-color: #111821; background: #c88927; }
  .archive-readonly-bar { background: rgba(27,33,42,.92); }
  .archive-info-cards > section { background: rgba(255,255,255,.045); }
  .archive-shared-tabs { background: linear-gradient(to bottom, rgba(25,30,39,.98) 72%, transparent); }.archive-shared-tabs button:hover { background: rgba(255,255,255,.05); }.archive-shared-list > a, .archive-shared-list.audio > div { border-color: rgba(255,255,255,.065); }
  :global(.archive-shared-context-menu) { border-color: rgba(255,255,255,.09); box-shadow: 0 .8rem 2.4rem rgba(0,0,0,.42); }
  .archive-history-panel { color: #f2f4f7; background: rgba(27,32,41,.98); border-color: rgba(255,255,255,.08); }.archive-history-panel > header { border-color: rgba(255,255,255,.08); }.archive-history-timeline article { background: rgba(255,255,255,.055); border-color: rgba(255,255,255,.07); box-shadow: 0 4px 16px rgba(0,0,0,.12); }.archive-history-timeline article.deleted { background: rgba(100,45,52,.18); }.archive-history-node { background: #1b2029; }
  .archive-sender-profile-card { color: #f2f4f7; background: rgba(27,32,41,.96); border-color: rgba(255,255,255,.09); }.archive-sender-profile-about, .archive-sender-profile-note, .archive-sender-profile-details div { background: rgba(255,255,255,.055); }
}

@media (prefers-reduced-motion: reduce) {
  .archive-profile-overlay, .archive-sender-profile-card, .archive-message-search > header, .archive-message-search-dropdown { animation: none; }
  .archive-viewer-stage img, .archive-viewer-stage video { transition: none; }
  .archive-gallery-scrollbar-bubble { transition: none; }
  .archive-shared-virtual-item.placeholder { animation: none; }
  .archive-layout { transition: none; }
  .archive-info-enter-active, .archive-info-leave-active { transition: opacity 120ms ease; }
  .archive-info-enter-from, .archive-info-leave-to { transform: none; }
  .archive-layout .archive-message-pane { animation: none !important; }
  .archive-layout .archive-info-enter-from, .archive-layout .archive-info-leave-to { transform: none !important; }
}
@media (prefers-reduced-transparency: reduce) {
  .archive-profile-overlay { backdrop-filter: none; -webkit-backdrop-filter: none; }.archive-sender-profile-card, :global(.archive-shared-context-menu), .archive-message-search > header, .archive-message-search-dropdown { background: var(--solid-elevated); backdrop-filter: none; -webkit-backdrop-filter: none; }.archive-gallery-scrollbar-thumb, .archive-gallery-scrollbar-bubble { backdrop-filter: none; -webkit-backdrop-filter: none; }
}
@media (prefers-reduced-motion: reduce) {
  .archive-message-viewport { scroll-behavior: auto; }
  .archive-viewer, .archive-viewer-stage img, .archive-viewer-stage video { animation: none; transition: opacity 120ms ease; }
}
</style>
