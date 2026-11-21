// SPDX-License-Identifier: GPL-3.0-only
import { describe, expect, it } from 'vitest'
import { homeCommand, platformStatus, type HomeAxes } from './inventure-platform'

describe('Inventure platform', () => {
  it('routes each fitted home control through its own configured macro', () => {
    for (const axes of ['X', 'Y', 'Z', 'XY', 'ALL'] as HomeAxes[]) {
      const settings = { inventure_mcu: {}, ['gcode_macro inventure_home_' + axes.toLowerCase()]: {} }
      expect(homeCommand(settings, axes)).toBe('INVENTURE_HOME_' + axes)
    }
    expect(homeCommand({ inventure_mcu: {} }, 'XY')).toBe('G28 X Y')
    expect(homeCommand({ 'gcode_macro inventure_home_all': {} }, 'ALL')).toBe('G28')
  })
  it('keeps the true four corners and center and accepts a measured zero', () => {
    const result = platformStatus({ inventure_mcu: { platform_level: {
      phase: 'measuring', next_point: 3, heights_steps: { 4: 802, 3: 0, 0: -401 }, candidate_depth_um: 4000
    } } })
    expect(result?.pads.map(p => [p.name, p.height, p.active])).toEqual([
      ['Rear left', 1, false], ['Rear right', 0, true], ['Center', null, false],
      ['Front left', null, false], ['Front right', -0.5, false]
    ])
    expect(result?.depth).toBeNull()
  })
  it('only shows a verified candidate or saved depth and handles disconnect', () => {
    expect(platformStatus({})).toBeNull()
    expect(platformStatus({ inventure_mcu: { platform_level: { phase: 'saved', candidate_depth_um: 138349,
      center_error_steps: 25 } } })).toMatchObject({ phase: 'Saved', depth: 138.349, deviation: 25 / 802 })
    expect(platformStatus({ inventure_mcu: { platform_level: { phase: 'failed', error: 'Stopped' } } })?.error).toBe('Stopped')
  })
})
