<template>
  <section v-if="configured" class="inventure-oled" aria-label="OLED INVENTURE">
    <v-divider />
    <header class="d-flex align-center px-3 py-1">
      <strong class="text-caption font-weight-medium">OLED INVENTURE</strong>
      <v-spacer />
      <v-btn v-if="expanded" x-small text :aria-label="'OLED zoom ' + zoom + ' times'" @click="cycleZoom">×{{ zoom }}</v-btn>
      <v-btn x-small text :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? 'Hide' : 'Show' }}</v-btn>
    </header>
    <div v-if="expanded" class="px-3 pb-2">
      <div v-if="image" class="inventure-oled-screen" tabindex="0">
        <img :src="image" :width="256 * zoom" :height="64 * zoom" alt="Acknowledged 256 by 64 Inventure OLED frame">
      </div>
      <span v-else class="text-caption">{{ sleeping ? 'Display asleep' : 'Display offline' }}</span>
    </div>
  </section>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import StateMixin from '@/mixins/state'

@Component
export default class InventureOledPreview extends Mixins(StateMixin) {
  expanded = true
  zoom = 1

  get configured () {
    return this.$typedState.printer.printer.configfile.settings.inventure_oled != null
  }

  get frame () {
    return this.$typedState.printer.printer.inventure_oled_preview
  }

  get sleeping () {
    return this.klippyReady && this.frame?.display === 'off'
  }

  get image () {
    const frame = this.frame
    return this.klippyReady && frame && ['awake', 'dim'].includes(frame.display) && frame.image_base64
      ? 'data:image/png;base64,' + frame.image_base64 : ''
  }

  cycleZoom () {
    this.zoom = this.zoom % 3 + 1
  }
}
</script>

<style scoped>
.inventure-oled-screen { max-width: 100%; overflow-x: auto; line-height: 0; }
.inventure-oled-screen img { display: block; image-rendering: pixelated; background: #020610; }
</style>
