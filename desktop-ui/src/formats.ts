import { t } from './i18n'

const names: Record<string, string> = { webp: 'WebP', webm: 'WebM', jp2: 'JPEG 2000', apng: 'APNG' }
export function formatName(value: string) { return names[value] ?? value.toUpperCase() }

// One plain sentence per output, so the choice is clear without codec details.
const hints: Record<string, string> = {
  mp4: 'Plays on any phone, computer or editor.',
  mkv: 'Keeps every stream. Good for archiving.',
  mov: 'For Apple devices and video editors.',
  webm: 'Small files made for the web.',
  mp3: 'Plays everywhere.',
  m4a: 'Good quality in small files.',
  aac: 'Good quality in small files.',
  opus: 'Very small files, great for voice.',
  ogg: 'Open format with small files.',
  flac: 'Lossless audio.',
  wav: 'Uncompressed audio. Large files.',
  aiff: 'Uncompressed audio for Apple apps.',
  png: 'Lossless and keeps transparency.',
  jpg: 'Small photos. No transparency.',
  webp: 'Small files that keep transparency.',
  avif: 'Very small modern format.',
  bmp: 'Uncompressed image.',
  tiff: 'Lossless, for printing and editing.',
  ico: 'Windows icon.',
  tga: 'For games and 3D tools.',
  jp2: 'High quality, for archives.',
  gif: 'Plays everywhere, with up to 256 colors.',
  apng: 'Lossless animation with transparency.',
}
const animationHints: Record<string, string> = {
  webp: 'Small animation in full color.',
  gif: 'Animated image that plays everywhere, with up to 256 colors.',
  mp4: 'Video that plays everywhere. Much smaller than a GIF.',
}
export function formatHint(value: string, animation = false) {
  const hint = (animation ? animationHints[value] : undefined) ?? hints[value]
  return hint ? t(hint) : ''
}
