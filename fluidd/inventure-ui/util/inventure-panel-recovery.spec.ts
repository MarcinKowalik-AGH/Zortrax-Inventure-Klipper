// Marcin Kowalik <mkowalik@agh.edu.pl>
// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WebSocketClient } from '@/plugins/socketClient'
import { InventurePanelRecovery } from './inventure-panel-recovery'
import type { TypedStore } from '@/store'

class TestSocket {
  static OPEN = 1
  static instances: TestSocket[] = []
  readyState = 1
  onopen: (() => void) | null = null
  onclose: ((event: object) => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn()

  constructor () { TestSocket.instances.push(this) }
}

describe('Inventure embedded panel reconnection', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', TestSocket)
    TestSocket.instances = []
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  function panel () {
    const state = { socket: { status: 'initializing' }, config: { socketUrl: 'ws://inventure/websocket' } }
    const store = {
      state,
      typedDispatch: vi.fn((action, value) => { if (action === 'socket/onSetStatus') state.socket.status = value })
    }
    const client = new WebSocketClient({ store: store as unknown as TypedStore })
    client.connect()
    return { client, state, store }
  }

  it('refreshes a recovered printer once and skips initial connections and printer switches', () => {
    const reload = vi.fn()
    sessionStorage.clear()
    const panel = new InventurePanelRecovery(reload)
    panel.observe('socket', 'ws://inventure', true, false)
    panel.observe('socket', 'ws://old', false, true)
    panel.observe('socket', 'ws://inventure', true, false)
    expect(reload).not.toHaveBeenCalled()
    panel.observe('klippy', 'ws://inventure', false, true)
    panel.observe('socket', 'ws://inventure', false, true)
    panel.observe('socket', 'ws://inventure', true, false)
    panel.observe('klippy', 'ws://inventure', true, false)
    expect(reload).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(120_001)
    const nextPage = new InventurePanelRecovery(reload)
    nextPage.observe('klippy', 'ws://inventure', false, true)
    nextPage.observe('klippy', 'ws://inventure', true, false)
    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('recovers a second confirmed Klipper restart inside the socket cooldown even when Vue misses the transient state', () => {
    sessionStorage.setItem('inventure-panel-reload:ws://inventure', String(Date.now()))
    const reload = vi.fn()
    const panel = new InventurePanelRecovery(reload)
    panel.klippyNotification('ws://inventure', true)
    panel.klippyNotification('ws://other', false)
    panel.klippyNotification('ws://inventure', true)
    expect(reload).not.toHaveBeenCalled()
    panel.klippyNotification('ws://inventure', false)
    panel.klippyNotification('ws://inventure', true)
    panel.klippyNotification('ws://inventure', true)
    panel.observe('socket', 'ws://inventure', false, true)
    panel.observe('socket', 'ws://inventure', true, false)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('keeps retrying beyond the original three attempts and stops on explicit close', async () => {
    const { client, state } = panel()
    for (let i = 0; i < 9; i++) {
      TestSocket.instances[i].onclose!({})
      await vi.advanceTimersByTimeAsync(30_000)
      expect(TestSocket.instances.length).toBe(i + 2)
    }
    TestSocket.instances[9].onopen!()
    expect(state.socket.status).toBe('identifying')
    client.close()
    await vi.advanceTimersByTimeAsync(180_000)
    expect(TestSocket.instances.length).toBe(10)
  })

  it.each([false, true])('probes an idle connection, server answers = %s', async (answers) => {
    const { client, state } = panel()
    const socket = TestSocket.instances[0]
    socket.onopen!()
    state.socket.status = 'ready'
    await vi.advanceTimersByTimeAsync(30_000)
    const packet = JSON.parse(socket.send.mock.calls[0][0]) as { method: string, id: number }
    expect(packet.method).toBe('server.info')
    if (answers) socket.onmessage!({ data: JSON.stringify({ id: packet.id, result: {} }) })
    await vi.advanceTimersByTimeAsync(12_000)
    expect(TestSocket.instances.length).toBe(answers ? 1 : 2)
    expect(socket.close).toHaveBeenCalledTimes(answers ? 0 : 1)
    client.close()
  })
})
