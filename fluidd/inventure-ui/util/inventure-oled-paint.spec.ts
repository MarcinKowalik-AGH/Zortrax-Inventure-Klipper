// Marcin Kowalik <mkowalik@agh.edu.pl>
// SPDX-License-Identifier: GPL-3.0-only
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { InventureLatestPainter } from './inventure-oled-paint'

const images: TestImage[] = []
class TestImage {
  src = ''
  naturalWidth = 256
  naturalHeight = 64
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor () { images.push(this) }
}
beforeEach(() => { images.length = 0; vi.stubGlobal('Image', TestImage) })
afterEach(() => vi.unstubAllGlobals())

describe('bounded latest-frame decoding', () => {
  it('decodes at most one frame, retains only the latest of a 1000-frame burst', () => {
    const draw = vi.fn()
    const painter = new InventureLatestPainter(draw)
    for (let i = 0; i < 1000; i++) painter.submit(String(i))
    expect(images).toHaveLength(1)
    images[0].onload?.()
    expect(draw).not.toHaveBeenCalled()
    expect(images).toHaveLength(2)
    expect(images[1].src).toBe('999')
    images[1].onload?.()
    expect(draw).toHaveBeenCalledExactlyOnceWith(images[1])
  })

  it('cancels a stale decode on hide/reconnect and permits the same image afterwards', () => {
    const draw = vi.fn()
    const painter = new InventureLatestPainter(draw)
    painter.submit('A')
    const late = images[0].onload
    painter.clear()
    painter.submit('A')
    late?.()
    expect(draw).not.toHaveBeenCalled()
    images[1].onload?.()
    expect(draw).toHaveBeenCalledExactlyOnceWith(images[1])
  })

  it('redraws for a changed DPI without decoding the same PNG again', () => {
    const draw = vi.fn()
    const painter = new InventureLatestPainter(draw)
    painter.submit('A'); images[0].onload?.()
    painter.submit('A'); painter.redraw()
    expect(images).toHaveLength(1)
    expect(draw).toHaveBeenCalledTimes(2)
  })

  it('retries a failed image and continues to a queued newer frame', () => {
    const draw = vi.fn()
    const painter = new InventureLatestPainter(draw)
    painter.submit('A'); images[0].onerror?.()
    painter.submit('A'); painter.submit('B'); images[1].onerror?.()
    expect(images[2].src).toBe('B')
    images[2].onload?.()
    expect(draw).toHaveBeenCalledExactlyOnceWith(images[2])
  })

  it('rejects non-native dimensions', () => {
    const draw = vi.fn()
    const painter = new InventureLatestPainter(draw)
    painter.submit('A'); images[0].naturalHeight = 65; images[0].onload?.()
    expect(draw).not.toHaveBeenCalled()
  })

  it('does not duplicate a pending image that returns to the currently decoding URI', () => {
    const draw = vi.fn()
    const painter = new InventureLatestPainter(draw)
    painter.submit('A'); painter.submit('B'); painter.submit('A')
    images[0].onload?.()
    expect(images).toHaveLength(1)
    expect(draw).toHaveBeenCalledExactlyOnceWith(images[0])
  })

  it('does nothing after disposal even if an old decoder calls back', () => {
    const draw = vi.fn()
    const painter = new InventureLatestPainter(draw)
    painter.submit('A'); const late = images[0].onload
    painter.dispose(); painter.submit('B'); painter.redraw(); late?.()
    expect(images).toHaveLength(1)
    expect(draw).not.toHaveBeenCalled()
  })
})
