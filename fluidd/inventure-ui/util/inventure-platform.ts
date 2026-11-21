// SPDX-License-Identifier: GPL-3.0-only
export type HomeAxes = 'X' | 'Y' | 'Z' | 'XY' | 'ALL'

export function homeCommand (settings: Record<string, unknown>, axes: HomeAxes): string {
  const macro = 'INVENTURE_HOME_' + axes
  if ('inventure_mcu' in settings) {
    if (('gcode_macro ' + macro.toLowerCase()) in settings) return macro
    if (axes === 'XY' && (settings.inventure_motion as { enabled?: boolean } | undefined)?.enabled) return macro
    if ('inventure_native_z' in settings) return 'INVENTURE_HOME_REQUEST AXES=' + (axes === 'ALL' ? 'XYZ' : axes)
  }
  return axes === 'ALL' ? 'G28' : 'G28 ' + axes.split('').join(' ')
}

const pads = ['Front right', 'Front left', 'Center', 'Rear right', 'Rear left']
const order = [4, 3, 2, 1, 0]
const titles: Record<string, string> = {
  idle: 'Ready', preparing: 'Preparing platform', await_knobs: 'Tighten all three knobs',
  heating: 'Heating nozzles', reference: 'Finding reference', measuring: 'Measuring platform',
  adjust: 'Adjust platform', finishing: 'Finishing', candidate: 'Result ready to save',
  saved: 'Saved', cancelled: 'Cancelled', failed: 'Failed'
}
const record = (value: unknown): Record<string, unknown> | undefined =>
  value !== null && typeof value === 'object' ? value as Record<string, unknown> : undefined
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value)

export function platformStatus (printer: unknown) {
  const run = record(record(record(printer)?.inventure_mcu)?.platform_level)
  if (!run || typeof run.phase !== 'string' || !Object.hasOwn(titles, run.phase)) return null
  const heights = record(run.heights_steps)
  const finished = run.phase === 'candidate' || run.phase === 'saved'
  return {
    phase: titles[run.phase],
    pads: order.map(id => {
      const steps = heights?.[id]
      return { id, name: pads[id], height: integer(steps) ? steps / 802 : null,
        active: run.phase === 'measuring' && run.next_point === id }
    }),
    depth: finished && integer(run.candidate_depth_um) ? run.candidate_depth_um / 1000 : null,
    deviation: finished && integer(run.center_error_steps) ? run.center_error_steps / 802 : null,
    error: run.phase === 'failed' && typeof run.error === 'string' ? run.error : null
  }
}
