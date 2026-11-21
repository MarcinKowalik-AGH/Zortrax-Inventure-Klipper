// Marcin Kowalik <mkowalik@agh.edu.pl>
// Read-only, strictly validated status adapter for the Inventure OLED preview.
// Moonraker already subscribes to all Klipper status objects in Fluidd.
export interface InventureOledPreviewStatus {
  display: string
  online: boolean
  sequence: number
  image: string | null
  epoch?: string
  navigationRevision?: number
  navigationApi?: string
}

export function readInventureOledPreview (printer: unknown): InventureOledPreviewStatus | null {
  if (!printer || typeof printer !== 'object') return null
  const state = (printer as { inventure_oled_preview?: unknown }).inventure_oled_preview
  if (!state || typeof state !== 'object') return null
  const item = state as Record<string, unknown>
  const seq = typeof item.sequence === 'number' && Number.isSafeInteger(item.sequence)
    ? item.sequence
    : 0
  const valid = item.online === true &&
    item.width === 256 && item.height === 64 &&
    item.format === 'indexed4-png' && item.source === 'host-rendered' &&
    typeof item.image_base64 === 'string' &&
    item.image_base64.length > 0 && item.image_base64.length <= 32768 &&
    /^[a-z0-9+/]+={0,2}$/i.test(item.image_base64)
  return {
    display: typeof item.display === 'string' ? item.display : 'offline',
    online: valid,
    sequence: seq,
    image: valid ? 'data:image/png;base64,' + (item.image_base64 as string) : null,
    ...(typeof item.epoch === 'string' && /^[a-f0-9]{32}$/.test(item.epoch)
      ? { epoch: item.epoch }
      : {}),
    ...(typeof item.navigation_revision === 'number' && Number.isSafeInteger(item.navigation_revision) && item.navigation_revision >= 0
      ? { navigationRevision: item.navigation_revision }
      : {}),
    ...(item.navigation_api === 'NAV-EVENT-1' ? { navigationApi: item.navigation_api } : {})
  }
}
export function selectInventureOledPreview (
  subscribed: InventureOledPreviewStatus | null,
  queried: InventureOledPreviewStatus | null,
  connected: boolean
): InventureOledPreviewStatus | null {
  if (!subscribed) return null
  if (!connected) return { ...subscribed, display: 'offline', online: false, image: null }
  // Never compare counters across a Klippy restart / preview reset.
  if (queried?.epoch && subscribed.epoch && queried.epoch !== subscribed.epoch) return subscribed
  if (queried && queried.sequence >= subscribed.sequence) return queried
  return subscribed
}
