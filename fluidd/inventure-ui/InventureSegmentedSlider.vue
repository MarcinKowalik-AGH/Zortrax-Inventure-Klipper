<!-- Marcin Kowalik <mkowalik@agh.edu.pl> -->
<template>
  <label class="inventure-slider-control">
    <span>{{ label }}<template v-if="unit"> · {{ value }} {{ unit }}</template></span>
    <span class="inventure-slider-bar">
      <span
        v-for="segment in 16"
        :key="segment"
        :class="{ filled: segment <= Math.ceil(16 * value / max) }"
        aria-hidden="true"
      />
      <input
        type="range"
        min="1"
        :max="max"
        step="1"
        :aria-label="label"
        :aria-valuetext="unit ? value + ' ' + unit : undefined"
        :value="value"
        @input="emitValue('input', $event)"
        @change="emitValue('change', $event)"
      >
    </span>
  </label>
</template>

<script lang="ts">
import { Component, Prop, Vue } from 'vue-property-decorator'

@Component
export default class InventureSegmentedSlider extends Vue {
  @Prop({ type: String, required: true }) readonly label!: string
  @Prop({ type: Number, required: true }) readonly value!: number
  @Prop({ type: Number, required: true }) readonly max!: number
  @Prop({ type: String, default: '' }) readonly unit!: string

  emitValue (name: 'input' | 'change', event: Event) {
    if (event.target instanceof HTMLInputElement) {
      this.$emit(name, event.target.valueAsNumber)
    }
  }
}
</script>

<style lang="scss" scoped>
  .inventure-slider-control {
    display: block;
    cursor: pointer;
  }

  .inventure-slider-bar {
    position: relative;
    display: grid;
    grid-template-columns: repeat(16, 1fr);
    gap: 3px;
    height: 26px;
    padding: 5px 0;

    span {
      border: 1px solid currentColor;
      border-radius: 2px;
      opacity: .45;
    }

    .filled {
      background: currentColor;
      opacity: 1;
    }

    input {
      position: absolute;
      inset: 0;
      width: 100%;
      opacity: 0;
      cursor: pointer;
    }

    &:focus-within {
      outline: 1px solid currentColor;
      outline-offset: 3px;
    }
  }
</style>
