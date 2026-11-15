# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventure r0323 two-nozzle alignment pattern and guarded service plan."""
import math
import struct

from .inventure_factory_core import (
    E_STEPS_PER_MM, OEM_T0_TO_T1_X_MM, XY_STEPS_PER_MM, FactoryError,
)
from .inventure_z_home import (
    NOZZLE_CONTACT, NOZZLE_Z_CLEARANCE_STEPS, NOZZLE_Z_SAMPLES, Z_BOTTOM, Z_STEPS_PER_MM, ZHomeProgram,
)


MIN_LINE = -6
MAX_LINE = 6
NEUTRAL_LINE = 0
NOMINAL_SPACING_MM = -OEM_T0_TO_T1_X_MM
PATTERN_COUNT = 2 * (MAX_LINE - MIN_LINE + 2)
PATTERN_E_CAP_STEPS = 960000
_BUILD_AREA = 135.
_FRAME_ORIGINS = ((15.3, 2.5), (4.5, 2.5))
_PROFILES = {
    'STANDARD': dict(e_per_mm=.3, retract_mm=1., draw_speed=10.,
                     retract_speed=40., unretract_speed=40.),
    'MATERIAL14': dict(e_per_mm=.3, retract_mm=3.5, draw_speed=500. / 60.,
                       retract_speed=25., unretract_speed=1000. / 60.),
    'T1': dict(e_per_mm=.1, retract_mm=3., draw_speed=700. / 60.,
               retract_speed=25., unretract_speed=800. / 60.),
}


def _number(value, name, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = math.nan
    if isinstance(value, bool) or not math.isfinite(number) or not minimum <= number <= maximum:
        raise FactoryError('%s must be %g..%g' % (name, minimum, maximum))
    return number


def xy_alignment_parameters(gcmd, residual=None):
    """Snapshot geometry used for both printing and line selection."""
    def num(name, default, minimum, maximum):
        return _number(gcmd.get(name, default), name, minimum, maximum)

    x_mode = str(gcmd.get('X_MODE', 'DIFFERENTIAL_X')).strip().upper()
    if x_mode not in ('DIFFERENTIAL_X', 'STOCK_REFERENCE'):
        raise FactoryError('X_MODE must be DIFFERENTIAL_X or STOCK_REFERENCE')
    t0_profile = str(gcmd.get('T0_PROFILE', 'STANDARD')).strip().upper()
    if t0_profile not in ('STANDARD', 'MATERIAL14'):
        raise FactoryError('T0_PROFILE must be STANDARD or MATERIAL14')
    baseline = {} if residual is None else residual
    return dict(
        x_mode=x_mode, t0_profile=t0_profile,
        residual_mm={axis: _number(baseline.get(axis, 0.), 'XY residual ' + axis, -5., 5.)
                     for axis in ('X', 'Y')},
        step_mm=num('XY_STEP_MM', .05, .025, .15),
        shift_mm=(num('PATTERN_SHIFT_X', 0., -10., 10.), num('PATTERN_SHIFT_Y', 0., -10., 10.)),
        gap_mm=(num('T0_GAP_MM', .8, .4, 2.), num('T1_GAP_MM', 1.1, .4, 2.)),
        lift_mm=num('PATTERN_LIFT_MM', .2, .1, 2.),
        travel_speed=num('PATTERN_TRAVEL_SPEED', 30., 1., 30.),
        z_speed=num('PATTERN_Z_SPEED', 2., .2, 4.),
        e_speed=num('PATTERN_E_SPEED', 10., .5, 10.),
        flow=(num('T0_FLOW', 100., 25., 150.) / 100., num('T1_FLOW', 100., 25., 150.) / 100.),
        z_max_spread_mm=num('Z_MAX_SPREAD_MM', .15, .005, .5),
    )


def xy_alignment_candidate(lines, xy):
    """Return the OEM residual change and its inverse carriage adjustment."""
    residual = dict(xy['residual_mm'])
    for axis, line in lines.items():
        if axis not in residual:
            raise FactoryError('Alignment axis must be X or Y')
        if line is None:
            continue
        if type(line) is not int or not MIN_LINE <= line <= MAX_LINE:
            raise FactoryError('Alignment line must be -6..+6')
        if axis == 'X' and xy['x_mode'] == 'STOCK_REFERENCE' and line != NEUTRAL_LINE:
            raise FactoryError('Stock X is a reference pattern. Choose X line 0')
        candidate = residual[axis] - line * xy['step_mm']
        # Round the displayed and persisted decimal while preserving sign.
        residual[axis] = round(_number(candidate, 'XY residual ' + axis, -5., 5.), 6)
    return dict(residual_mm=residual,
                t1_carriage_adjust_mm={axis: -value if value else 0. for axis, value in residual.items()},
                nominal_spacing_mm=NOMINAL_SPACING_MM,
                t1_total_nozzle_offset_mm={'X': round(NOMINAL_SPACING_MM + residual['X'], 6), 'Y': residual['Y']})


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def xy_pattern_segments(tool, xy):
    """Build the left Y comb then the right X comb in the released-switch frame."""
    if type(tool) is not int or tool not in (0, 1):
        raise FactoryError('An alignment pattern needs T0 or T1')
    profile = _PROFILES['T1' if tool else xy['t0_profile']]
    origin = _FRAME_ORIGINS[tool]
    frame = tuple(origin[i] - tool * xy['residual_mm'][axis] for i, axis in enumerate(('X', 'Y')))

    def point(p):
        logical = tuple(p[i] + xy['shift_mm'][i] for i in range(2))
        if any(not 0. <= value <= _BUILD_AREA for value in logical):
            raise FactoryError('Alignment pattern leaves the 135 mm build area')
        # The logic at 0x08018420 truncates float32 logical positions to steps.
        raw = tuple(math.trunc(_f32(_f32(value) * XY_STEPS_PER_MM)) / XY_STEPS_PER_MM
                    + frame[i] for i, value in enumerate(logical))
        return raw

    def segment(axis, start, end, line=None):
        extension = tuple(b + 2. * ((b > a) - (b < a)) for a, b in zip(start, end))
        e_steps = math.trunc(math.dist(start, end) * _f32(profile['e_per_mm'])
                             * xy['flow'][tool] * E_STEPS_PER_MM)
        return dict(axis=axis, line=line, start=point(start), end=point(end), exit=point(extension),
                    e=e_steps / E_STEPS_PER_MM, speed=profile['draw_speed'])

    left = [segment('Y', (10., 95.5), (10., 35.5))]
    right = [segment('X', (132.5, 43.), (72.5, 43.))]
    for line in range(MIN_LINE, MAX_LINE + 1):
        bias = _f32(line * _f32(xy['step_mm'])) if tool else 0.
        y = _f32(65.5 + 5. * line + bias)
        x = _f32(102.5 + 5. * line + (bias if xy['x_mode'] == 'DIFFERENTIAL_X' else 0.))
        left.append(segment('Y', (10., y), (55., y), line))
        right.append(segment('X', (x, 43.), (x, 88.), line))
    return left + right


def xy_pattern_e_budget(xy):
    """Absolute E pulses for each signed, independently quantized leg."""
    return _pattern_e_budget(xy, [xy_pattern_segments(tool, xy) for tool in (0, 1)])


def _pattern_e_budget(xy, patterns):
    steps = 0
    for tool, lines in enumerate(patterns):
        profile = _PROFILES['T1' if tool else xy['t0_profile']]
        # One initial retract, then one unretract and one retract per line.
        retract_steps = round(profile['retract_mm'] * E_STEPS_PER_MM)
        steps += (1 + 2 * len(lines)) * retract_steps
        steps += sum(round(line['e'] * E_STEPS_PER_MM) for line in lines)
    if steps > PATTERN_E_CAP_STEPS:
        raise FactoryError('XY pattern exceeds the MCU extrusion budget', e_steps=steps)
    return steps


class NozzleXYProgram(ZHomeProgram):
    """Use one owned PE12 reference for both probe and print planes."""

    def _pattern_height(self, target, speed):
        if target <= NOZZLE_Z_CLEARANCE_STEPS:
            raise FactoryError('Alignment pattern leaves no Z clearance for the tool change')
        if target == self.z_steps:
            return
        _, hit, raw = yield from self._z_move(target - self.z_steps, speed,
                                             Z_BOTTOM | NOZZLE_CONTACT, Z_BOTTOM, 1, 0)
        if hit or raw['z_bottom'] or not raw['nozzle_contact'] or self.z_steps != target:
            raise FactoryError('Z stopped before the alignment print height', hit=hit)

    def _print_comb(self, segments, xy, draw_height, travel_height, profile):
        e_speed = xy['e_speed']
        retract = profile['retract_mm']
        retract_speed = min(e_speed, profile['retract_speed'])
        unretract_speed = min(e_speed, profile['unretract_speed'])
        for line in segments:
            yield from self._pattern_height(travel_height, xy['z_speed'])
            yield from self._move(*line['start'], speed=xy['travel_speed'])
            yield from self._pattern_height(draw_height, xy['z_speed'])
            yield from self._move(*self.xy, e=retract, speed=unretract_speed)
            yield from self._move(*line['end'], e=line['e'], speed=line['speed'])
            yield from self._move(*self.xy, e=-retract, speed=retract_speed)
            yield from self._move(*line['exit'], speed=line['speed'])

    def _print_pattern(self, tool, segments, ref, xy):
        profile = _PROFILES['T1' if tool else xy['t0_profile']]
        draw_height = ref - round(xy['gap_mm'][tool] * Z_STEPS_PER_MM)
        travel_height = draw_height - round(xy['lift_mm'] * Z_STEPS_PER_MM)
        yield from self._pattern_height(travel_height, xy['z_speed'])
        yield from self._move(*self.xy, e=-profile['retract_mm'],
                              speed=min(xy['e_speed'], profile['retract_speed']))
        for axis in ('Y', 'X'):
            name = 'print_t%d_%s' % (tool, axis.lower())
            yield from self._phase(name.upper())
            yield from self._module(name, self._print_comb(
                (line for line in segments if line['axis'] == axis), xy, draw_height, travel_height, profile))

    def run_nozzle_xy(self, t1_p, t1_settings, xy):
        t1 = NozzleXYProgram(t1_p, t1_settings)
        if self.p['tool'] != 0 or t1.p['tool'] != 1:
            raise FactoryError('XY alignment requires ordered T0 and T1 plans')
        start = round(self.settings['z_approach_mm'] * Z_STEPS_PER_MM)
        if round(t1_settings['z_approach_mm'] * Z_STEPS_PER_MM) != start:
            raise FactoryError('XY alignment needs a shared probe approach position')
        patterns = [xy_pattern_segments(tool, xy) for tool in (0, 1)]
        _pattern_e_budget(xy, patterns)
        for plan, segments in zip((self.p, t1.p), patterns):
            for line in segments:
                for x, y in (line['start'], line['end'], line['exit']):
                    if not (plan['min_x'] <= x <= plan['max_x'] and plan['min_y'] <= y <= plan['max_y']):
                        raise FactoryError('Raw alignment pattern exceeds the XY service envelope')

        yield from self._phase('Z_INITIAL_CLEARANCE')
        yield from self._module('z_initial_clearance', self._nozzle_pre_xy_clearance())
        yield from self._module('home_xy', self._home_steps(y_first=True, include_z=False))
        yield from self.select_tool()
        yield from self._position_contact_pad()
        yield dict(kind='dual_heat_start')
        yield from self._module('home_z', self._nozzle_dual_bottom())

        references = {}
        printed = {}
        for tool, samples in enumerate(NOZZLE_Z_SAMPLES):
            span = round(self.settings['z_probe_mm'] * Z_STEPS_PER_MM)
            if start - span <= 0 or (tool == 0 and start - span <= self.z_steps):
                raise FactoryError('XY alignment probe window reaches the lower clearance')
            yield from self._z_approach(start, self.z_steps, stage='coarse')
            yield from self._phase('HEAT_WAIT')
            yield dict(kind='dual_heat_wait')
            readings = yield from self._module('measure_nozzle', self._measure_nozzle_samples(samples, start, span))
            releases = [row['release_steps_from_bottom'] for row in readings]
            spread = (max(releases) - min(releases)) / Z_STEPS_PER_MM
            if spread > xy['z_max_spread_mm']:
                raise FactoryError('XY alignment Z reference is not repeatable', tool=tool, spread_mm=spread)
            name = 'T%d' % tool
            references[name] = readings
            yield from self._module('print_' + name.lower(), self._print_pattern(tool, patterns[tool], releases[-1], xy))
            printed[name] = len(patterns[tool])
            yield from self._phase('Z_TOOL_CLEARANCE' if tool == 0 else 'Z_PARK_CLEARANCE')
            yield from self._module('z_nozzle_clearance', self._nozzle_dual_retreat())
            if tool == 0:
                yield dict(kind='dual_switch_tool')
                self.p, self.settings, self.stock_sequence = t1.p, t1.settings, t1.stock_sequence
                yield from self.select_tool()
                yield from self._position_contact_pad()

        yield from self._phase('PARK')
        yield from self.park()
        yield from self._phase('DONE')
        yield dict(kind='done', success=True, terminal='NOZZLE_XY_PRINT', cleaning_done=False,
                   table_at_bottom=False, printed_segments=printed, references=references)
