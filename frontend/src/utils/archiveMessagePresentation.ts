import type { ArchiveMessageMedia } from '../api'

export type ArchivePrimaryContent =
  | 'deleted'
  | 'emoji'
  | 'sticker'
  | 'album'
  | 'photo'
  | 'video'
  | 'round_video'
  | 'animation'
  | 'audio'
  | 'voice'
  | 'document'
  | 'webpage'
  | 'text'
  | 'contact'
  | 'location'
  | 'venue'
  | 'poll'
  | 'dice'
  | 'game'
  | 'invoice'
  | 'story'
  | 'paid_media'
  | 'giveaway'
  | 'giveaway_results'
  | 'todo'
  | 'service'
  | 'unsupported'

export interface ArchivePresentationInput {
  media: ArchiveMessageMedia[]
  text: string
  entities?: Record<string, unknown>[]
  isDeleted: boolean
  hasReply: boolean
  hasForward: boolean
  hasSender: boolean
  hasWebPage: boolean
  hasReactions: boolean
  hasInlineButtons: boolean
  contentKind?: string
}

export interface ArchiveMessagePresentation {
  primary: ArchivePrimaryContent
  classes: string[]
  emojiCount: number
  hasVisualMedia: boolean
  isVisualAlbum: boolean
  isCustomShape: boolean
  isMediaOnly: boolean
  hasSolidBackground: boolean
  metaPosition: 'in-text' | 'standalone'
  reactionsPosition: 'inside' | 'outside' | 'none'
}

export function isVideoMedia(media: ArchiveMessageMedia) {
  return media.type === 'video'
    || (media.type === 'animation' && !media.mime_type?.startsWith('image/'))
}

export function isImageMedia(media: ArchiveMessageMedia) {
  return ['photo', 'sticker'].includes(media.type)
    || (media.type === 'animation' && Boolean(media.mime_type?.startsWith('image/')))
}

export function isVisualMedia(media: ArchiveMessageMedia) {
  return media.type !== 'sticker' && (isImageMedia(media) || isVideoMedia(media))
}

export function isAudioMedia(media: ArchiveMessageMedia) {
  return ['audio', 'voice'].includes(media.type)
}

export function countEmojiOnly(text: string, entities: Record<string, unknown>[] = []) {
  if (!text.trim()) return 0
  const hasOtherFormatting = entities.some((entity) => String(entity._ || '') !== 'MessageEntityCustomEmoji')
  if (hasOtherFormatting) return 0

  const normalized = text.trim().replace(/\s+/g, '')
  const glyphs = normalized.match(/\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier}|\u200D\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier})?)*/gu)
  if (!glyphs?.length || glyphs.join('') !== normalized) return 0
  return glyphs.length
}

export function buildArchiveMessagePresentation(input: ArchivePresentationInput): ArchiveMessagePresentation {
  const {
    media, text, entities = [], isDeleted, hasReply, hasForward, hasSender,
    hasWebPage, hasReactions, hasInlineButtons, contentKind,
  } = input
  const hasText = Boolean(text.trim())
  const emojiCount = media.length || hasWebPage ? 0 : countEmojiOnly(text, entities)
  const stickers = media.filter((item) => item.type === 'sticker')
  const visualMedia = media.filter(isVisualMedia)
  const isVisualAlbum = visualMedia.length > 1 && visualMedia.length === media.length
  const hasVisualMedia = visualMedia.length > 0 && visualMedia.length === media.length
  const isStickerOnly = stickers.length === 1 && media.length === 1 && !hasText
  const isRoundVideo = contentKind === 'round_video'
  const isService = contentKind === 'service'
  const isDice = contentKind === 'dice'
  const isCustomShape = Boolean(emojiCount || isStickerOnly || isRoundVideo || isService || isDice)
  const hasSubheader = hasReply || hasForward || hasSender
  const isMediaWithNoText = hasVisualMedia && !hasText
  const isMediaOnly = isMediaWithNoText && !hasSubheader && !hasWebPage && !isDeleted
  const hasSolidBackground = !isCustomShape && (hasSubheader || !isMediaWithNoText || hasWebPage)
  const metaPosition = hasText && !hasWebPage && !emojiCount ? 'in-text' : 'standalone'
  const reactionsPosition = !hasReactions ? 'none' : (isCustomShape || isMediaOnly ? 'outside' : 'inside')

  let primary: ArchivePrimaryContent
  const structuredKinds: ArchivePrimaryContent[] = [
    'contact', 'location', 'venue', 'poll', 'dice', 'game', 'invoice', 'story',
    'paid_media', 'giveaway', 'giveaway_results', 'todo', 'service',
  ]
  if (isDeleted) primary = 'deleted'
  else if (isRoundVideo) primary = 'round_video'
  else if (structuredKinds.includes(contentKind as ArchivePrimaryContent)) primary = contentKind as ArchivePrimaryContent
  else if (hasWebPage) primary = 'webpage'
  else if (isStickerOnly) primary = 'sticker'
  else if (isVisualAlbum) primary = 'album'
  else if (visualMedia[0]) primary = visualMedia[0].type as 'photo' | 'video' | 'animation'
  else if (media[0]?.type === 'audio') primary = 'audio'
  else if (media[0]?.type === 'voice') primary = 'voice'
  else if (media[0]?.type === 'document') primary = 'document'
  else if (emojiCount) primary = 'emoji'
  else if (hasText) primary = 'text'
  else primary = 'unsupported'

  const classes = [
    `content-${primary}`,
    hasText ? 'text' : 'no-text',
    hasVisualMedia ? 'media' : '',
    hasVisualMedia ? 'has-media' : '',
    hasVisualMedia ? 'visual-media' : '',
    isVisualAlbum ? 'album' : '',
    isMediaOnly ? 'media-only' : '',
    isCustomShape ? 'custom-shape' : '',
    isStickerOnly ? 'sticker-only' : '',
    structuredKinds.includes(primary) ? 'structured-content' : '',
    emojiCount ? 'emoji-only' : '',
    emojiCount ? `emoji-${Math.min(7, emojiCount)}` : '',
    hasSolidBackground ? 'has-solid-background' : '',
    !isCustomShape ? 'has-shadow' : '',
    !isCustomShape && !hasInlineButtons && (!isMediaWithNoText || hasSubheader) ? 'has-appendix' : '',
    hasReactions ? 'has-reactions' : '',
    reactionsPosition === 'outside' ? 'has-outside-reactions' : '',
    `meta-${metaPosition}`,
  ].filter(Boolean)

  return {
    primary,
    classes,
    emojiCount,
    hasVisualMedia,
    isVisualAlbum,
    isCustomShape,
    isMediaOnly,
    hasSolidBackground,
    metaPosition,
    reactionsPosition,
  }
}
