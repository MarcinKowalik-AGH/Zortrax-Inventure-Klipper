<template>
  <v-sheet v-if="status" aria-label="Inventure platform leveling" class="px-3 py-2">
    <div class="text-subtitle-2">Five-point platform leveling</div>
    <div class="text-body-2">{{ status.phase }}</div>
    <div class="inventure-pads my-2">
      <div
        v-for="pad in status.pads"
        :key="pad.id"
        :class="['inventure-pad', { 'primary--text': pad.active }]"
        :aria-current="pad.active ? 'step' : undefined"
      >
        {{ pad.name }}
        <span>{{ pad.height === null ? '—' : pad.height.toFixed(3) + ' mm' }}</span>
      </div>
    </div>
    <div v-if="status.depth !== null">Platform depth {{ status.depth.toFixed(3) }} mm</div>
    <div v-if="status.deviation !== null">Center deviation {{ status.deviation.toFixed(3) }} mm</div>
    <div v-if="status.error" role="alert">{{ status.error }}</div>
  </v-sheet>
</template>
<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import StateMixin from '@/mixins/state'
import { platformStatus } from '@/util/inventure-platform'

@Component({})
export default class InventurePlatformStatus extends Mixins(StateMixin) {
  get status () {
    return this.klippyReady ? platformStatus(this.$typedState.printer.printer) : null
  }
}
</script>
<style scoped>
.inventure-pads { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }
.inventure-pad { text-align: center; padding: 3px; border: 1px solid #7777; }
.inventure-pad span { display: block; font-variant-numeric: tabular-nums; }
.inventure-pad:nth-child(2), .inventure-pad:nth-child(5) { grid-column: 3; }
.inventure-pad:nth-child(3) { grid-column: 2; }
.inventure-pad:nth-child(4) { grid-column: 1; }
.inventure-pad[aria-current="step"] { border-style: solid; border-color: currentColor; }
</style>
