// Marcin Kowalik <mkowalik@agh.edu.pl>
// SPDX-License-Identifier: GPL-3.0-only
import { afterEach, describe, expect, it, vi } from 'vitest'
import { InventureNavigationEvents, type NavigationReply, type NavigationTransport } from './inventure-oled-navigation'
import { readInventureOledPreview, selectInventureOledPreview, type InventureOledPreviewStatus } from './inventure-oled-preview'

const epoch = 'a'.repeat(32)
function frame (navigation: number, sequence = navigation) {
  return {
    online: true,
    sequence,
    width: 256,
    height: 64,
    format: 'indexed4-png',
    source: 'host-rendered',
    image_base64: 'iVBORw0KGgo=',
    epoch,
    navigation_revision: navigation,
    navigation_api: 'NAV-EVENT-1'
  }
}
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }

function setup (paint: (frame: InventureOledPreviewStatus) => Promise<void> = async () => undefined) {
  const show = vi.fn(paint)
  const replies: ((reply: NavigationReply) => void)[] = []
  const api: NavigationTransport = {
    wait: vi.fn(() => new Promise<NavigationReply>(resolve => replies.push(resolve))),
    cancel: vi.fn(async () => ({}))
  }
  const live = vi.fn()
  const events = new InventureNavigationEvents(show, live, api, 'test-widget')
  return { api, replies, show, live, events }
}

afterEach(() => vi.useRealTimers())

describe('immediate Inventure menu navigation', () => {
  it('opens only one event wait even when start is called repeatedly', () => {
    const h = setup()
    h.events.start(); h.events.start(); h.events.start()
    expect(h.api.wait).toHaveBeenCalledTimes(1)
    expect(h.api.wait).toHaveBeenCalledWith({ client: 'test-widget-1', epoch: '', navigation_revision: -1 })
    h.events.stop()
  })

  it('renders one detent and re-arms immediately, without a polling delay', async () => {
    vi.useFakeTimers()
    const h = setup()
    h.events.start()
    h.replies[0]({ changed: true, frame: frame(1) })
    await tick()
    expect(h.show).toHaveBeenCalledTimes(1)
    expect(h.api.wait).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
    h.replies[1]({ changed: true, frame: frame(2) })
    await tick()
    expect(h.show).toHaveBeenLastCalledWith(expect.objectContaining({ navigationRevision: 2 }))
    expect(h.api.wait).toHaveBeenCalledTimes(3)
    h.events.stop()
  })

  it('accepts a first-to-last jump as the newest view, not intermediate rows', async () => {
    const h = setup()
    h.events.start()
    h.replies[0]({ changed: true, frame: frame(1) }); await tick()
    h.replies[1]({ changed: true, frame: frame(16) }); await tick()
    expect(h.show.mock.calls.map(call => call[0].navigationRevision)).toEqual([1, 16])
    h.events.stop()
  })

  it('re-arms the network while a slow browser paints, without concurrent waits', async () => {
    let finish!: () => void
    const show = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
    const h = setup(show)
    h.events.start()
    h.replies[0]({ changed: true, frame: frame(5) }); await tick()
    expect(h.api.wait).toHaveBeenCalledTimes(2)
    h.replies[1]({ changed: true, frame: frame(16) }); await tick()
    expect(h.show).toHaveBeenLastCalledWith(expect.objectContaining({ navigationRevision: 16 }))
    expect(h.api.wait).toHaveBeenCalledTimes(3)
    finish(); await tick()
    expect(h.api.wait).toHaveBeenCalledTimes(3)
    h.events.stop()
  })

  it('keeps receiving after a view promise rejects', async () => {
    const h = setup(async () => { throw new Error('view removed') })
    h.events.start()
    h.replies[0]({ changed: true, frame: frame(1) }); await tick()
    expect(h.api.wait).toHaveBeenCalledTimes(2)
    h.events.stop()
  })

  it('does not paint an unchanged keepalive or restart the animation', async () => {
    const h = setup()
    h.events.start()
    h.replies[0]({ changed: false }); await tick()
    expect(h.show).not.toHaveBeenCalled()
    expect(h.api.wait).toHaveBeenCalledTimes(2)
    h.events.stop()
  })

  it('cancels on hide and ignores a late reply from that generation', async () => {
    const h = setup()
    h.events.start(); h.events.stop()
    h.replies[0]({ changed: true, frame: frame(3) }); await tick()
    expect(h.show).not.toHaveBeenCalled()
    expect(h.api.cancel).toHaveBeenCalledWith('test-widget-1')
    expect(h.api.wait).toHaveBeenCalledTimes(1)
  })

  it('uses a fresh request identity and cursor after reconnect', async () => {
    const h = setup()
    h.events.start(); h.events.stop(); h.events.start()
    expect(h.api.wait).toHaveBeenLastCalledWith({ client: 'test-widget-3', epoch: '', navigation_revision: -1 })
    h.replies[0]({ changed: true, frame: frame(99) }); await tick()
    expect(h.show).not.toHaveBeenCalled()
    h.replies[1]({ changed: true, frame: { ...frame(1), epoch: 'b'.repeat(32) } }); await tick()
    expect(h.show).toHaveBeenCalledTimes(1)
    h.events.stop()
  })

  it('rejects malformed event data and retries without a busy loop', async () => {
    vi.useFakeTimers()
    const h = setup()
    h.events.start()
    h.replies[0]({ changed: true, frame: { ...frame(1), epoch: 'invalid' } }); await tick()
    expect(h.show).not.toHaveBeenCalled()
    expect(h.api.wait).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2999)
    expect(h.api.wait).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(h.api.wait).toHaveBeenCalledTimes(2)
    h.events.stop()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not restore an older selection when periodic status arrives later', () => {
    const parse = (value: unknown) => readInventureOledPreview({ inventure_oled_preview: value })!
    const old = parse(frame(1, 10))
    const latest = parse(frame(8, 15))
    expect(selectInventureOledPreview(old, latest, true)).toEqual(latest)
    expect(selectInventureOledPreview(latest, old, true)).toEqual(latest)
  })

  it('never compares image counters across a host restart', () => {
    const parse = (value: unknown) => readInventureOledPreview({ inventure_oled_preview: value })!
    const old = parse(frame(99, 999))
    const restarted = parse({ ...frame(1, 1), epoch: 'b'.repeat(32) })
    expect(selectInventureOledPreview(restarted, old, true)).toEqual(restarted)
    expect(selectInventureOledPreview(restarted, old, false)?.online).toBe(false)
  })
})
