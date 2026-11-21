// Marcin Kowalik <mkowalik@agh.edu.pl>
// SPDX-License-Identifier: GPL-3.0-only
import Vue from 'vue'
import { readInventureOledPreview, type InventureOledPreviewStatus } from './inventure-oled-preview'

export interface NavigationCursor {
  client: string
  epoch: string
  navigation_revision: number
}

export interface NavigationReply {
  changed: boolean
  frame?: unknown
}

declare global {
  // Augment the upstream ambient RPC namespace; no runtime namespace is emitted.
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Moonraker {
    interface Methods {
      'printer.inventure.oled_preview.wait': { params: NavigationCursor; result: NavigationReply }
      'printer.inventure.oled_preview.cancel_wait': { params: { client: string }; result: { cancelled: boolean } }
    }
  }
}

export interface NavigationTransport {
  wait: (cursor: NavigationCursor) => Promise<NavigationReply>
  cancel: (client: string) => Promise<unknown>
}

const transport: NavigationTransport = {
  wait: params => Vue.$socket.emit('printer.inventure.oled_preview.wait', { params, suppressError: true }),
  cancel: client => Vue.$socket.emit('printer.inventure.oled_preview.cancel_wait', {
    params: { client }, suppressError: true
  })
}

/** One outstanding event wait; no polling interval or debounce after a detent. */
export class InventureNavigationEvents {
  private active = false
  private generation = 0
  private requestClient: string | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null

  constructor (
    private readonly show: (frame: InventureOledPreviewStatus) => Promise<void>,
    private readonly setLive: (live: boolean) => void,
    private readonly api: NavigationTransport = transport,
    // Correlation only, not an authentication credential.
    private readonly clientId = 'inventure-' + Math.random().toString(36).slice(2)
  ) {}

  start () {
    if (this.active) return
    this.active = true
    const generation = ++this.generation
    const client = this.requestClient = this.clientId + '-' + generation
    this.run(generation, { client, epoch: '', navigation_revision: -1 }).catch(() => undefined)
  }

  stop () {
    this.active = false
    this.generation++
    this.setLive(false)
    if (this.retryTimer !== null) clearTimeout(this.retryTimer)
    this.retryTimer = null
    const client = this.requestClient
    this.requestClient = null
    if (client) this.api.cancel(client).catch(() => undefined)
  }

  private current (generation: number): boolean {
    return this.active && generation === this.generation
  }

  private async run (generation: number, cursor: NavigationCursor): Promise<void> {
    try {
      while (this.current(generation)) {
        const response = await this.api.wait(cursor)
        if (!this.current(generation)) return
        if (!response || typeof response.changed !== 'boolean') throw new Error('Invalid OLED navigation reply')
        // An unchanged response is the server's bounded 10 s keepalive, not
        // a high-frequency poll. A detent completes the wait immediately.
        if (!response.changed) continue
        const frame = readInventureOledPreview({ inventure_oled_preview: response.frame })
        if (!frame?.epoch || frame.navigationApi !== 'NAV-EVENT-1' || frame.navigationRevision === undefined) {
          throw new Error('Invalid OLED navigation frame')
        }
        cursor = { ...cursor, epoch: frame.epoch, navigation_revision: frame.navigationRevision }
        // Re-arm reception without waiting for Vue, PNG decode or painting.
        // The view owns one decode plus one replaceable latest frame, not a FIFO.
        // This keeps exactly one network wait outstanding even on a slow browser.
        this.show(frame).catch(() => undefined)
        if (this.current(generation)) this.setLive(true)
      }
    } catch {
      if (!this.current(generation)) return
      this.setLive(false)
      // Existing status/animation preview remains available during failure.
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null
        if (this.current(generation)) this.run(generation, cursor).catch(() => undefined)
      }, 3000)
    }
  }
}
