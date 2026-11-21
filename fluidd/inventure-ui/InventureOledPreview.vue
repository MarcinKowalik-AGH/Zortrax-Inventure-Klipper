<!-- Marcin Kowalik <mkowalik@agh.edu.pl> -->
<template>
  <section
    v-if="preview"
    class="inventure-oled-preview"
    :data-navigation="navigationActive ? 'event' : 'fallback'"
    data-preview-revision="NAV-PIPELINE-2"
    data-zoom-controls="OLED-ZOOM-2"
    :data-pixel-scale="pixelLayout.scale"
    aria-label="OLED INVENTURE"
  >
    <v-divider />
    <div class="d-flex align-center justify-space-between px-3 py-2">
      <span class="text-caption font-weight-medium">OLED INVENTURE</span>
      <v-btn
        text
        x-small
        :aria-label="collapsed ? 'Show OLED preview' : 'Hide OLED preview'"
        @click="collapsed = !collapsed"
      >
        {{ collapsed ? 'Show' : 'Hide' }}
      </v-btn>
    </div>
    <div
      v-if="!collapsed"
      class="inventure-oled-display-row px-3 pb-3"
    >
      <div
        ref="pixelViewport"
        class="inventure-oled-viewport"
        tabindex="0"
        aria-label="Pixel-exact OLED preview. Scroll horizontally in narrow windows."
      >
        <canvas
          v-if="preview.image"
          ref="oledCanvas"
          class="inventure-oled-frame"
          :width="pixelLayout.canvasWidth"
          :height="pixelLayout.canvasHeight"
          :style="pixelStyle"
          role="img"
          aria-label="Live host-rendered 256 by 64 Inventure OLED screen"
        />
        <div
          v-else
          class="inventure-oled-unavailable text-caption text-center"
          :style="pixelStyle"
        >
          {{ preview.display === 'off' ? 'Display asleep' : 'Display offline' }}
        </div>
      </div>
      <div
        class="inventure-oled-zoom-controls"
        role="group"
        aria-label="OLED preview pixel zoom"
        :style="zoomControlsStyle"
      >
        <button
          v-for="level in zoomLevels"
          :key="level"
          type="button"
          class="inventure-oled-zoom-button"
          :class="{ 'inventure-oled-zoom-button--selected': zoom === level }"
          :aria-label="`Set OLED zoom to ${level} times`"
          :aria-pressed="zoom === level ? 'true' : 'false'"
          :title="`OLED ×${level} — square pixels`"
          @click="setZoom(level)"
        >
          ×{{ level }}
        </button>
      </div>
    </div>
  </section>
</template>

<script lang="ts">
import { Component, Mixins, Prop, Watch } from 'vue-property-decorator'
import StateMixin from '@/mixins/state'
import { SocketActions } from '@/api/socketActions'
import { InventureNavigationEvents } from '@/util/inventure-oled-navigation'
import { InventureLatestPainter } from '@/util/inventure-oled-paint'
import { oledPixelLayout, oledPixelStyle, alignOledPixels, drawOledPixels, parseOledZoom, type OledZoom } from '@/util/inventure-oled-pixels'
import { readInventureOledPreview, selectInventureOledPreview, type InventureOledPreviewStatus } from '@/util/inventure-oled-preview'

// Preserve the existing animation/fallback cadence. Menu navigation uses
// its own event wait below, outside objects/query and the status cache.
const POLL_DELAY_MS = 125
const RETRY_DELAY_MS = 350
const OLED_ZOOM_STORAGE_KEY = 'inventure-oled-preview-zoom'

function restoredOledZoom (): OledZoom {
  // Storage might be disabled in privacy mode. It must never affect rendering.
  try {
    return parseOledZoom(window.localStorage.getItem(OLED_ZOOM_STORAGE_KEY) ?? '2')
  } catch {
    return 2
  }
}

@Component({})
export default class InventureOledPreview extends Mixins(StateMixin) {
  @Prop({ type: Boolean, default: true })
  readonly panelVisible!: boolean

  get previewVisible (): boolean {
    return this.panelVisible && !this.collapsed
  }

  collapsed = false
  polledPreview: InventureOledPreviewStatus | null = null
  navigationPreview: InventureOledPreviewStatus | null = null
  navigationActive = false
  zoom: OledZoom = restoredOledZoom()
  readonly zoomLevels: OledZoom[] = [1, 2, 3]
  private navigation: InventureNavigationEvents | null = null
  pixelLayout = oledPixelLayout(window.devicePixelRatio, this.zoom)
  private painter: InventureLatestPainter | null = null
  private pixelObserver: ResizeObserver | null = null
  private pixelMedia: MediaQueryList | null = null
  private pixelTick: number | null = null
  private pollTimer: number | null = null
  private pollEpoch = 0
  private disposed = false

  get preview (): InventureOledPreviewStatus | null {
    const subscribed = readInventureOledPreview(this.$typedState.printer.printer)
    const connected = this.socketReady && this.klippyReady
    const periodic = selectInventureOledPreview(subscribed, this.polledPreview, connected)
    return selectInventureOledPreview(periodic, this.navigationPreview, connected)
  }

  get navigationSupported (): boolean {
    return readInventureOledPreview(this.$typedState.printer.printer)?.navigationApi === 'NAV-EVENT-1'
  }

  get pixelStyle () {
    return oledPixelStyle(this.pixelLayout)
  }

  get zoomControlsStyle () {
    // The toolbar always occupies the height of the ×1 canvas, regardless of
    // the active zoom. Recalculate on DPR/zoom changes without fetching a frame.
    return { height: `${oledPixelLayout(this.pixelLayout.dpr, 1).cssHeight}px` }
  }

  mounted () {
    this.painter = new InventureLatestPainter(image => {
      const canvas = this.$refs.oledCanvas as HTMLCanvasElement | undefined
      if (!this.disposed && this.previewVisible && !document.hidden && canvas) {
        drawOledPixels(canvas, image, this.pixelLayout)
      }
    })
    window.addEventListener('resize', this.queuePixelLayout)
    window.addEventListener('scroll', this.queuePixelLayout, true)
    this.watchPixelRatio()
    this.observePixelViewport()
    this.queuePixelLayout()
    this.navigation = new InventureNavigationEvents(async frame => {
      if (this.disposed) return
      this.navigationPreview = frame
      await this.$nextTick()
      this.paintFrame()
    }, active => {
      if (this.navigationActive === active) return
      this.navigationActive = active
      if (active) this.clearPoll()
      else this.schedulePoll(RETRY_DELAY_MS)
    })
    this.syncNavigation()
    document.addEventListener('visibilitychange', this.onVisibilityChange)
    this.schedulePoll(0)
    this.$nextTick(() => this.paintFrame())
  }

  beforeDestroy () {
    this.disposed = true
    this.navigation?.stop()
    this.pollEpoch++
    this.painter?.dispose()
    this.pixelObserver?.disconnect()
    this.unwatchPixelRatio()
    window.removeEventListener('resize', this.queuePixelLayout)
    window.removeEventListener('scroll', this.queuePixelLayout, true)
    if (this.pixelTick !== null) window.cancelAnimationFrame(this.pixelTick)
    this.pixelTick = null
    document.removeEventListener('visibilitychange', this.onVisibilityChange)
    this.clearPoll()
  }

  private clearPoll () {
    if (this.pollTimer !== null) {
      window.clearTimeout(this.pollTimer)
      this.pollTimer = null
    }
  }

  private schedulePoll (delay: number) {
    if (this.disposed || !this.previewVisible || document.hidden || this.navigationActive) return
    this.clearPoll()
    this.pollTimer = window.setTimeout(() => {
      this.poll().catch(() => undefined)
    }, delay)
  }

  private async poll () {
    this.pollTimer = null
    if (this.disposed || !this.previewVisible || document.hidden || this.navigationActive) return
    if (!this.socketReady || !this.klippyReady || !this.preview) return
    const epoch = this.pollEpoch
    try {
      const result = await SocketActions.printerObjectsQuery(
        { inventure_oled_preview: null }, { suppressError: true }
      )
      if (epoch !== this.pollEpoch || this.disposed) return
      const frame = readInventureOledPreview(result.status)
      if (frame) this.polledPreview = frame
    } catch {
      // A failed query cannot change the printer's state. Socket reconnect
      // will reset local observation; no retries are sent concurrently.
    } finally {
      if (epoch === this.pollEpoch && !this.disposed) this.schedulePoll(POLL_DELAY_MS)
    }
  }

  private onVisibilityChange () {
    this.restartPolling()
  }

  @Watch('previewAvailable')
  onPreviewAvailable () {
    this.restartPolling()
  }

  get previewAvailable () {
    return !!this.$typedState.printer.printer.inventure_oled_preview
  }

  @Watch('navigationSupported')
  syncNavigation () {
    if (!this.disposed && this.previewVisible && !document.hidden &&
      this.socketReady && this.klippyReady && this.navigationSupported) {
      this.navigation?.start()
    } else {
      this.navigation?.stop()
      this.navigationPreview = null
    }
  }

  @Watch('socketReady')
  @Watch('klippyReady')
  @Watch('previewVisible')
  onAvailabilityChanged () {
    this.restartPolling()
  }

  private restartPolling () {
    if (this.disposed) return
    this.pollEpoch++
    this.clearPoll()
    this.polledPreview = null
    this.navigation?.stop()
    this.navigationPreview = null
    this.syncNavigation()
    this.painter?.clear()
    this.observePixelViewport()
    this.queuePixelLayout()
    this.schedulePoll(0)
    this.$nextTick(() => this.paintFrame())
  }

  @Watch('preview.image')
  onPreviewImageChanged () {
    this.$nextTick(() => this.paintFrame())
  }

  private paintFrame () {
    const uri = this.preview?.image
    const canvas = this.$refs.oledCanvas as HTMLCanvasElement | undefined
    if (!uri || !canvas || !this.previewVisible || document.hidden) {
      this.painter?.clear()
      return
    }
    this.painter?.submit(uri)
  }

  private observePixelViewport () {
    this.pixelObserver?.disconnect()
    this.$nextTick(() => {
      if (this.disposed || typeof ResizeObserver === 'undefined') return
      this.pixelObserver?.disconnect()
      const viewport = this.$refs.pixelViewport as HTMLElement | undefined
      if (viewport) {
        this.pixelObserver = new ResizeObserver(this.queuePixelLayout)
        this.pixelObserver.observe(viewport)
      }
    })
  }

  private unwatchPixelRatio () {
    const media = this.pixelMedia
    if (!media) return
    if (media.removeEventListener) media.removeEventListener('change', this.onPixelRatioChange)
    else media.removeListener(this.onPixelRatioChange)
    this.pixelMedia = null
  }

  private watchPixelRatio () {
    this.unwatchPixelRatio()
    if (!window.matchMedia) return
    const media = this.pixelMedia = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`)
    if (media.addEventListener) media.addEventListener('change', this.onPixelRatioChange)
    else media.addListener(this.onPixelRatioChange)
  }

  private onPixelRatioChange () {
    this.watchPixelRatio()
    this.queuePixelLayout()
  }

  setZoom (zoom: OledZoom) {
    if (this.zoom === zoom) return
    this.zoom = zoom
    try {
      window.localStorage.setItem(OLED_ZOOM_STORAGE_KEY, String(zoom))
    } catch {
      // Browsers denying storage still support the live zoom controls.
    }
    // Paint the cached decoded image at the new scale; do not request
    // another frame or interfere with the fast navigation channel.
    this.queuePixelLayout()
  }

  private queuePixelLayout () {
    if (this.disposed || this.pixelTick !== null) return
    this.pixelTick = window.requestAnimationFrame(() => {
      this.pixelTick = null
      if (this.disposed) return
      this.pixelLayout = oledPixelLayout(window.devicePixelRatio, this.zoom)
      this.$nextTick(() => {
        if (this.disposed) return
        const canvas = this.$refs.oledCanvas as HTMLCanvasElement | undefined
        if (canvas) alignOledPixels(canvas, this.pixelLayout.dpr)
        this.painter?.redraw()
      })
    })
  }
}
</script>

<style scoped lang="scss">
.inventure-oled-display-row {
  // Equal left and right gutters keep the OLED centered in the card itself,
  // rather than in the space remaining beside the buttons.
  --inventure-oled-bright: #16a1fe; // exact brightness 15 of the host OLED palette
  --inventure-oled-border: #0a436c; // exact brightness 6
  --inventure-oled-selected: #0c588e; // exact brightness 7
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) 40px;
  align-items: start;
  width: 100%;
  min-width: 0;
}
.inventure-oled-viewport {
  grid-column: 2;
  width: 100%;
  min-width: 0;
  overflow-x: auto;
}
.inventure-oled-zoom-controls {
  grid-column: 3;
  justify-self: end;
  display: flex;
  box-sizing: border-box;
  flex-direction: column;
  flex: none;
  width: 40px;
  min-width: 40px;
  gap: 1px;
  padding: 2px;
  background: #020610;
  border: 1px solid var(--inventure-oled-border);
  border-radius: 0;
}
.inventure-oled-zoom-button {
  display: block;
  box-sizing: border-box;
  flex: 1 1 0;
  width: 100%;
  min-height: 0;
  padding: 0;
  color: var(--inventure-oled-bright);
  background: #020610;
  border: 1px solid var(--inventure-oled-border);
  border-radius: 0;
  font: 700 11px/1 "Courier New", monospace;
  text-align: center;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.inventure-oled-zoom-button:hover,
.inventure-oled-zoom-button:focus-visible {
  color: var(--inventure-oled-bright);
  border-color: var(--inventure-oled-bright);
  outline: 1px solid var(--inventure-oled-bright);
  outline-offset: -1px;
}
.inventure-oled-zoom-button--selected {
  color: var(--inventure-oled-bright);
  background: var(--inventure-oled-selected);
  border-color: var(--inventure-oled-bright);
}
.inventure-oled-frame,
.inventure-oled-unavailable {
  display: block;
  margin: 0 auto;
  box-sizing: border-box;
  background: #020610;
}
.inventure-oled-frame {
  image-rendering: pixelated;
}
.inventure-oled-unavailable {
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
