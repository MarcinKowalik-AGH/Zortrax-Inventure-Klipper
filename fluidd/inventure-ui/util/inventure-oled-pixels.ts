// Marcin Kowalik <mkowalik@agh.edu.pl>
// SPDX-License-Identifier: GPL-3.0-only
export const OLED_WIDTH = 256
export const OLED_HEIGHT = 64
export const OLED_TARGET_CSS_WIDTH = 344

// User-selected logical zoom is expressed in source OLED pixels per CSS pixel
// at DPR 1. At fractional DPR the nearest integer DEVICE-pixel block wins.
export type OledZoom = 1 | 2 | 3

export function parseOledZoom (stored: string | null): OledZoom {
  return stored === '3' ? 3 : stored === '2' ? 2 : 1
}

/** Equal integer DEVICE-pixel blocks, not fractional CSS stretching. */
export function oledPixelLayout (ratio: number, zoom?: OledZoom) {
  const dpr = Number.isFinite(ratio) && ratio >= 0.125 && ratio <= 16 ? ratio : 1
  // Default preserves the legacy test/auto geometry. The Fluidd widget
  // always supplies explicit ×1/×2/×3 and never uses CSS interpolation.
  const desiredBlock = zoom === undefined ? OLED_TARGET_CSS_WIDTH * dpr / OLED_WIDTH : zoom * dpr
  const scale = Math.max(1, Math.min(16, Math.round(desiredBlock)))
  const width = OLED_WIDTH * scale
  const height = OLED_HEIGHT * scale
  // Guard pixels keep browser edge antialiasing outside the native OLED image.
  // Fractional zoom can round CSS layout to 1/64 px even after origin alignment.
  const guard = 2
  const canvasWidth = width + 2 * guard
  const canvasHeight = height + 2 * guard
  return {
    dpr,
    scale,
    width,
    height,
    guard,
    canvasWidth,
    canvasHeight,
    cssWidth: canvasWidth / dpr,
    cssHeight: canvasHeight / dpr
  }
}

export function oledPixelStyle (layout: ReturnType<typeof oledPixelLayout>) {
  return {
    width: `${layout.cssWidth}px`,
    minWidth: `${layout.cssWidth}px`,
    maxWidth: 'none',
    height: `${layout.cssHeight}px`,
    minHeight: `${layout.cssHeight}px`,
    maxHeight: `${layout.cssHeight}px`
  }
}

/** Align the origin as well as the size; fractional centered origins also blur. */
export function alignOledPixels (canvas: HTMLCanvasElement, dpr: number) {
  canvas.style.position = 'relative'
  canvas.style.left = canvas.style.top = ''
  const rect = canvas.getBoundingClientRect()
  const x = (Math.round(rect.left * dpr) - rect.left * dpr) / dpr
  const y = (Math.round(rect.top * dpr) - rect.top * dpr) / dpr
  canvas.style.left = `${x}px`
  canvas.style.top = `${y}px`
}

export function drawOledPixels (
  canvas: HTMLCanvasElement, image: CanvasImageSource, layout: ReturnType<typeof oledPixelLayout>
) {
  // Assigning the backing size clears a canvas; do it only on actual DPR changes.
  if (canvas.width !== layout.canvasWidth) canvas.width = layout.canvasWidth
  if (canvas.height !== layout.canvasHeight) canvas.height = layout.canvasHeight
  Object.assign(canvas.style, oledPixelStyle(layout))
  const context = canvas.getContext('2d', { alpha: false })
  if (!context) return
  context.imageSmoothingEnabled = false
  context.fillStyle = '#020610'
  context.fillRect(0, 0, layout.canvasWidth, layout.canvasHeight)
  context.drawImage(image, layout.guard, layout.guard, layout.width, layout.height)
  alignOledPixels(canvas, layout.dpr)
}
