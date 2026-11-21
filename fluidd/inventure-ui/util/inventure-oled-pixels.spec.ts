// Marcin Kowalik <mkowalik@agh.edu.pl>
// SPDX-License-Identifier: GPL-3.0-only
// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { alignOledPixels, drawOledPixels, oledPixelLayout, oledPixelStyle, parseOledZoom } from './inventure-oled-pixels'

describe('square native OLED pixels', () => {
  it.each([0.25, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.333333333, 1.5, 1.75, 2, 2.5, 3, 4, 5, 8])(
    'maps each source pixel to equal integer device blocks at DPR %s', dpr => {
      const p = oledPixelLayout(dpr)
      expect(Number.isInteger(p.scale)).toBe(true)
      expect(p.width / 256).toBe(p.scale)
      expect(p.height / 64).toBe(p.scale)
      expect(p.cssWidth * dpr).toBeCloseTo(p.canvasWidth, 8)
      expect(p.cssHeight * dpr).toBeCloseTo(p.canvasHeight, 8)
      expect(p.width).toBe(4 * p.height)
      expect(p.cssWidth - 2 * p.guard / dpr).toBeCloseTo(4 * (p.cssHeight - 2 * p.guard / dpr))
      expect(oledPixelStyle(p).maxWidth).toBe('none')
    }
  )

  it.each([1, 1.25, 1.5, 2, 2.5, 3, 4])(
    'keeps all three OLED magnifications square in actual device pixels at DPR %s', dpr => {
      for (const level of [1, 2, 3] as const) {
        const p = oledPixelLayout(dpr, level)
        expect(p.scale).toBe(Math.round(level * dpr))
        expect(Number.isInteger(p.width / 256)).toBe(true)
        expect(p.width / 256).toBe(p.height / 64)
        expect(p.cssWidth * dpr).toBeCloseTo(p.canvasWidth, 8)
        expect(p.cssHeight * dpr).toBeCloseTo(p.canvasHeight, 8)
      }
    }
  )

  it('provides 256×64 / 512×128 / 768×192 at DPR 1, and retains them as CSS sizes at DPR 2', () => {
    for (const dpr of [1, 2]) {
      for (const level of [1, 2, 3] as const) {
        const p = oledPixelLayout(dpr, level)
        expect(p.scale).toBe(level * dpr)
        expect(p.cssWidth - (2 * p.guard / dpr)).toBe(256 * level)
        expect(p.cssHeight - (2 * p.guard / dpr)).toBe(64 * level)
      }
    }
  })

  it('restores only the supported scale values from browser storage', () => {
    expect(parseOledZoom('1')).toBe(1)
    expect(parseOledZoom('2')).toBe(2)
    expect(parseOledZoom('3')).toBe(3)
    expect(parseOledZoom('auto')).toBe(1)
    expect(parseOledZoom('7')).toBe(1)
    expect(parseOledZoom(null)).toBe(1)
  })

  it.each([NaN, Infinity, 0, -1, 100])('bounds invalid DPR %s', dpr => {
    expect(oledPixelLayout(dpr)).toEqual(oledPixelLayout(1))
  })

  it('disables interpolation and aligns fractional canvas origins', () => {
    const canvas = document.createElement('canvas')
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({ left: 11.3, top: 5.7 } as DOMRect)
    const context = { imageSmoothingEnabled: true, drawImage: vi.fn(), fillRect: vi.fn() }
    vi.spyOn(canvas, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
    const image = new Image()
    const p = oledPixelLayout(1.25)
    drawOledPixels(canvas, image, p)
    expect(canvas.width).toBe(516)
    expect(canvas.height).toBe(132)
    expect(context.imageSmoothingEnabled).toBe(false)
    expect(context.drawImage).toHaveBeenCalledWith(image, 2, 2, 512, 128)
    const left = canvas.style.left
    const top = canvas.style.top
    expect((11.3 + parseFloat(left)) * 1.25).toBeCloseTo(14)
    expect((5.7 + parseFloat(top)) * 1.25).toBeCloseTo(7)
    alignOledPixels(canvas, 1.25)
    expect(canvas.style.left).toBe(left)
    expect(canvas.style.top).toBe(top)
  })
})
