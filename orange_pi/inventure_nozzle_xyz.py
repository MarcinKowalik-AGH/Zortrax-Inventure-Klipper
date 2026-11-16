# SPDX-License-Identifier: GPL-3.0-or-later
"""Guarded, single-reference conductive-pad calibration for Inventure."""

import math

from .inventure_factory_core import E_STEPS_PER_MM, XY_STEPS_PER_MM, FactoryError
from .inventure_z_home import (
    NOZZLE_CONTACT, NOZZLE_Z_CLEARANCE_STEPS, Z_BOTTOM, Z_OPEN_CONFIRM_S,
    Z_STEPS_PER_MM, ZHomeProgram,
)


PAD_DIAMETER_MM = 5.
PAD_TEMPERATURE_C = 160.
PAD_TRAVEL_SPEED = 8.
PAD_MAX_Z_SPEED = 3.9
PAD_PREP_Z_STEPS = 20 * Z_STEPS_PER_MM


def pad_xyz_parameters(gcmd):
    """Validate the Z ceilings and the preparation for both tools."""
    def number(name, default, low, high, optional=False):
        value = gcmd.get(name, default)
        if value is None:
            if optional:
                return None
            raise FactoryError('Pad calibration requires ' + name)
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            result = math.nan
        if isinstance(value, bool) or not math.isfinite(result) or not low <= result <= high:
            raise FactoryError('%s must be %g..%g' % (name, low, high))
        return result

    mode = str(gcmd.get('MODE', 'FULL')).strip().upper()
    if mode not in ('FULL', 'VERIFY_T0'):
        raise FactoryError('Pad calibration MODE must be FULL or VERIFY_T0')
    prep = str(gcmd.get('PREP', 'RETRACT')).strip().upper()
    if prep not in ('RETRACT', 'EMPTY'):
        raise FactoryError('Pad calibration PREP must be RETRACT or EMPTY')
    if prep == 'EMPTY':
        for name in ('T0_TARGET', 'T1_TARGET', 'T0_RETRACT_MM', 'T1_RETRACT_MM'):
            if gcmd.get(name, None) is not None:
                raise FactoryError('PREP=EMPTY uses 160 C and zero retraction; omit ' + name)
        retract_mm = (0., 0.)
    else:
        retract_mm = (number('T0_RETRACT_MM', None, .1, 100.),
                      number('T1_RETRACT_MM', None, .1, 100.))
    ceilings = tuple(number('T%d_Z_MAX' % tool, None, 20., 150.,
                            optional=tool == 1 and mode == 'VERIFY_T0')
                     for tool in (0, 1))
    clearance = number('Z_CLEARANCE_MM', 1.5, .5, 4.)
    for tool, ceiling in enumerate(ceilings):
        if ceiling is not None and (math.floor(ceiling * Z_STEPS_PER_MM)
                                    - round(clearance * Z_STEPS_PER_MM) < PAD_PREP_Z_STEPS):
            raise FactoryError('T%d_Z_MAX minus Z_CLEARANCE_MM must allow the 20 mm selector stage' % tool)
    return dict(
        mode=mode,
        prep=prep,
        z_max_mm=ceilings,
        retract_mm=retract_mm,
        retract_speed=number('RETRACT_SPEED', 1.6, .5, 6.),
        scan_radius_mm=number('SCAN_RADIUS_MM', 3.5, 3., 6.),
        edge_tolerance_mm=number('EDGE_TOLERANCE_MM', .02, .005, .1),
        z_clearance_mm=clearance,
        z_tap_speed=number('Z_TAP_SPEED', .8, .2, 1.),
        xy_repeat_mm=number('XY_REPEAT_MM', .08, .01, .25),
        z_repeat_mm=number('Z_REPEAT_MM', .05, .005, .25),
    )


def pad_xyz_e_budget(xyz):
    return sum(round(length * E_STEPS_PER_MM) for length in xyz['retract_mm'])


def _xy_mm(point):
    return tuple(steps / XY_STEPS_PER_MM for steps in point)


def _z_mm(steps):
    return steps / Z_STEPS_PER_MM


def pad_xyz_candidate(measurements, xyz):
    before, t1, after = (measurements[key] for key in ('T0', 'T1', 'T0_RETURN'))
    reseat = {axis: after['center_xy_mm'][i] - before['center_xy_mm'][i]
              for i, axis in enumerate(('X', 'Y'))}
    reseat['Z'] = after['z_release_mm_from_pe12'] - before['z_release_mm_from_pe12']
    if (any(abs(reseat[axis]) > xyz['xy_repeat_mm'] for axis in ('X', 'Y'))
            or abs(reseat['Z']) > xyz['z_repeat_mm']):
        raise FactoryError('T0 did not repeat after reselect', t0_reseat_mm=reseat)
    physical = {
        axis: (before['center_xy_mm'][i] + after['center_xy_mm'][i]) / 2. - t1['center_xy_mm'][i]
        for i, axis in enumerate(('X', 'Y'))
    }
    return dict(
        t1_offset_mm={axis: round(delta, 6) for axis, delta in physical.items()},
        t1_carriage_shift_mm={axis: round(-delta, 6) for axis, delta in physical.items()},
        t1_lower_by_mm=round((before['z_release_mm_from_pe12'] + after['z_release_mm_from_pe12']) / 2.
                            - t1['z_release_mm_from_pe12'], 6),
        t0_reseat_mm={axis: round(delta, 6) for axis, delta in reseat.items()},
    )


class NozzleXYZProgram(ZHomeProgram):
    """Use a vertical PC14 tap at each XY candidate and one known PE12 origin."""

    def _scan_box(self, plan, xyz):
        p, settings = plan.p, plan.settings
        center = tuple(round(settings['z_probe_' + axis] * XY_STEPS_PER_MM)
                       for axis in ('x', 'y'))
        radius = round(xyz['scan_radius_mm'] * XY_STEPS_PER_MM)
        limits = tuple((c - radius, c + radius) for c in center)
        for axis, (lower, upper) in zip(('x', 'y'), limits):
            if not p['min_' + axis] <= lower / XY_STEPS_PER_MM <= upper / XY_STEPS_PER_MM <= p['max_' + axis]:
                raise FactoryError('Pad scan exceeds the XY service envelope')
        return center, limits

    def _open_z_at(self, target, allow_closed=False):
        if target == self.z_steps:
            yield dict(kind='z_inputs', expected={'z_bottom': int(target == 0), 'nozzle_contact': 1})
            return
        steps = target - self.z_steps
        if allow_closed and steps >= 0:
            raise FactoryError('Pad retreat must lower the table')
        speed = self.settings['z_backoff_speed'] if steps < 0 else self.settings['z_approach_speed']
        # Any closure while ascending stops locally. A retreat tolerates PC14
        # chatter while moving away, then requires a fresh open read at rest.
        mask = Z_BOTTOM if steps < 0 else NOZZLE_CONTACT
        value = Z_BOTTOM if steps < 0 else 0
        _, hit, raw = yield from self._z_move(
            steps, speed, mask, value, None if allow_closed else 1, int(self.z_steps == 0))
        if hit or self.z_steps != target or raw['z_bottom'] or not raw['nozzle_contact']:
            raise FactoryError('Pad clearance Z move stopped unexpectedly', hit=hit, reached=self.z_steps)

    def _pad_xy_move(self, point):
        yield dict(kind='z_inputs', expected={'z_bottom': int(self.z_steps == 0), 'nozzle_contact': 1})
        x, y = _xy_mm(point)
        result = yield dict(kind='move', x=x, y=y, e=0., speed=PAD_TRAVEL_SPEED, stage='pad_travel')
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise FactoryError('Guarded pad XY move did not complete')
        self.xy = (x, y)

    def _tap(self, point, safe_z, ceiling, tap_speed, release=False):
        if self.z_steps != safe_z:
            raise FactoryError('Pad tap requires the verified XY clearance plane')
        yield from self._pad_xy_move(point)
        sent, hit, raw = yield from self._z_move(
            ceiling - safe_z, tap_speed, NOZZLE_CONTACT, 0, 1, 0,
            allow_contact_transient=True)
        closed = hit == NOZZLE_CONTACT and raw['nozzle_contact'] == 0 and sent > 0
        missed = not hit and raw['nozzle_contact'] == 1 and self.z_steps == ceiling
        if not closed or not release:
            yield from self._open_z_at(safe_z, allow_closed=True)
            if not (closed or missed):
                raise FactoryError('Unstable PC14 response during pad tap')
            return closed

        # Compare the same electrical Z edge as the previous nozzle Z test:
        # release during a slow downward move, qualified by a second fresh read.
        allowed = min(self.z_steps - safe_z, round(self.settings['z_release_mm'] * Z_STEPS_PER_MM))
        _, hit, raw = yield from self._z_move(
            -allowed, self.settings['z_release_speed'], Z_BOTTOM | NOZZLE_CONTACT,
            Z_BOTTOM | NOZZLE_CONTACT, None, 0, allow_contact_transient=True)
        opening = self.z_steps
        valid = hit == NOZZLE_CONTACT and raw['nozzle_contact'] == 1
        if valid:
            fresh = yield dict(kind='z_verify_open', delay_s=Z_OPEN_CONFIRM_S)
            valid = fresh['nozzle_contact'] == 1
        yield from self._open_z_at(safe_z, allow_closed=True)
        if not valid:
            raise FactoryError('PC14 release was not repeatable at pad center')
        return opening

    def _edge(self, origin, axis, outside, safe_z, ceiling, units, tap_speed):
        def point(value):
            xy = list(origin)
            xy[axis] = value
            return tuple(xy)

        if (yield from self._tap(point(outside), safe_z, ceiling, tap_speed)):
            raise FactoryError('Pad edge is outside the qualified scan box',
                               axis=('X', 'Y')[axis], point_mm=_xy_mm(point(outside)))
        inside = origin[axis]
        while abs(inside - outside) > units:
            midpoint = (inside + outside) // 2
            if (yield from self._tap(point(midpoint), safe_z, ceiling, tap_speed)):
                inside = midpoint
            else:
                outside = midpoint
        return (inside + outside) / 2.

    def _scan(self, seed, limits, ceiling, xyz):
        tol = max(1, math.floor(xyz['edge_tolerance_mm'] * XY_STEPS_PER_MM))
        safe_z = ceiling - round(xyz['z_clearance_mm'] * Z_STEPS_PER_MM)
        tap_speed = xyz['z_tap_speed']
        yield from self._pad_xy_move(seed)
        yield from self._open_z_at(safe_z)
        origin = seed
        for _ in range(2):
            if not (yield from self._tap(origin, safe_z, ceiling, tap_speed)):
                raise FactoryError('No PC14 contact at the pad seed or fitted center',
                                   center_mm=_xy_mm(origin))
            edges = []
            for axis, (lower, upper) in enumerate(limits):
                low = yield from self._edge(origin, axis, lower, safe_z, ceiling, tol, tap_speed)
                high = yield from self._edge(origin, axis, upper, safe_z, ceiling, tol, tap_speed)
                edges.append((low, high))
            origin = tuple(round((low + high) / 2.) for low, high in edges)

        widths = [(high - low) / XY_STEPS_PER_MM for low, high in edges]
        # The nozzle face slightly enlarges the effective conductive outline.
        # Require a plausible shape without fitting the radius to exactly 5 mm.
        if (min(widths) < PAD_DIAMETER_MM - 1.5 or max(widths) > PAD_DIAMETER_MM + 1.5
                or abs(widths[0] - widths[1]) > .6):
            raise FactoryError('Pad contact outline is not consistent with a round 5 mm pad',
                               electrical_diameter_mm=widths)
        releases = []
        for _ in range(2):
            z = yield from self._tap(origin, safe_z, ceiling, tap_speed, release=True)
            if type(z) is not int:
                raise FactoryError('No PC14 contact at the fitted pad center')
            releases.append(z)
        spread = _z_mm(abs(releases[0] - releases[1]))
        if spread > xyz['z_repeat_mm']:
            raise FactoryError('Pad center Z release is not repeatable', spread_mm=spread)
        return dict(center_xy_mm=_xy_mm(origin),
                    edges_mm=[_xy_mm(pair) for pair in edges],
                    electrical_diameter_mm=widths,
                    z_release_mm_from_pe12=_z_mm(sum(releases) / 2.),
                    z_release_spread_mm=spread)

    def _pick(self, tool, plans):
        if self.p['tool'] != tool:
            yield dict(kind='xyz_switch_tool', tool=tool)
            plan = plans[tool]
            self.p, self.settings, self.stock_sequence = plan.p, plan.settings, plan.stock_sequence
        yield from self.select_tool()

    def _prepare_hot(self, xyz):
        tool = self.p['tool']
        yield from self._phase('XYZ_T%d_RETRACT' % tool)
        yield from self.position_bin()
        steps = round(xyz['retract_mm'][tool] * E_STEPS_PER_MM)
        yield from self._move(*self.xy, e=-steps / E_STEPS_PER_MM, speed=xyz['retract_speed'])
        yield from self._phase('XYZ_T%d_WIPE_HOT' % tool)
        yield from self.clean()

    def _wipe_at_160(self):
        yield from self._phase('XYZ_T%d_WIPE_160' % self.p['tool'])
        yield from self.position_bin()
        yield from self.clean()

    def run_nozzle_xyz(self, t1_p, t1_settings, xyz):
        def pad_plan(p, settings):
            settings = dict(settings)
            for name in ('z_bottom_speed', 'z_approach_speed', 'z_backoff_speed'):
                settings[name] = min(settings[name], PAD_MAX_Z_SPEED)
            return ZHomeProgram(dict(p, z_speed=settings['z_bottom_speed']), settings)

        plans = (pad_plan(self.p, self.settings), pad_plan(t1_p, t1_settings))
        if tuple(plan.p['tool'] for plan in plans) != (0, 1):
            raise FactoryError('Pad calibration requires ordered T0 and T1 plans')
        empty = xyz['prep'] == 'EMPTY'
        scan_order = (0,) if xyz['mode'] == 'VERIFY_T0' else (0, 1, 0)
        boxes, ceilings = {}, {}
        clearance_steps = round(xyz['z_clearance_mm'] * Z_STEPS_PER_MM)
        for tool in set(scan_order):
            plan = plans[tool]
            ceiling = xyz['z_max_mm'][tool]
            if (ceiling is None or ceiling > plan.settings['z_bottom_max_mm']
                    or math.floor(ceiling * Z_STEPS_PER_MM) - clearance_steps < PAD_PREP_Z_STEPS):
                raise FactoryError('T%d pad Z ceiling is outside the qualified travel range' % tool)
            # Floor so quantisation cannot exceed the supplied hard ceiling.
            ceilings[tool] = math.floor(ceiling * Z_STEPS_PER_MM)
            boxes[tool] = self._scan_box(plan, xyz)
        self.p, self.settings, self.stock_sequence = plans[0].p, plans[0].settings, plans[0].stock_sequence
        yield from self._phase('XYZ_INITIAL_CLEARANCE')
        yield from self._module('z_initial_clearance', self._nozzle_pre_xy_clearance())
        yield from self._module('home_xy', self._home_steps(y_first=True, include_z=False))
        yield from self._phase('Z_HOME_BOTTOM')
        bottom = yield dict(kind='home', axis='Z', max_mm=self.p['home_z_max'],
                            speed=self.p['z_speed'], dual_z_home=True)
        if not isinstance(bottom, dict) or bottom.get('ok') is not True:
            raise FactoryError('homing_z_failed')
        self.z_steps = 0
        if not empty:
            yield from self._pick(0, plans)
        yield from self._phase('XYZ_HEAT')
        yield dict(kind='xyz_heat_start')
        if not empty:
            yield dict(kind='xyz_heat_wait')
            yield from self._prepare_hot(xyz)
            yield from self._pick(1, plans)
            yield dict(kind='xyz_heat_wait')
            yield from self._prepare_hot(xyz)
            yield from self._phase('XYZ_COOL_160')
            yield dict(kind='xyz_cool_start')
        yield dict(kind='xyz_cool_wait')
        if not empty and len(scan_order) == 1:
            yield from self._wipe_at_160()

        measurements = {}
        for index, tool in enumerate(scan_order):
            name = ('T0', 'T1', 'T0_RETURN')[index]
            yield from self._pick(tool, plans)
            yield dict(kind='xyz_cool_wait')
            if index < 2:
                yield from self._wipe_at_160()
            ceiling = ceilings[tool]
            yield dict(kind='xyz_arm', tool=tool, ceiling_steps=ceiling)
            yield from self._phase('XYZ_SCAN_' + name)
            measurements[name] = yield from self._module(
                'pad_scan_' + name.lower(), self._scan(*boxes[tool], ceiling, xyz))
            yield from self._phase('XYZ_TOOL_CLEARANCE')
            yield from self._open_z_at(PAD_PREP_Z_STEPS)
            yield from self._module('z_nozzle_clearance', self._nozzle_dual_retreat())

        candidate = pad_xyz_candidate(measurements, xyz) if len(scan_order) == 3 else None
        yield from self._phase('PARK')
        yield from self.park()
        yield from self._phase('DONE')
        yield dict(kind='done', success=True, terminal='NOZZLE_PAD_XYZ', cleaning_done=True,
                   table_at_bottom=False, measurements=measurements, candidate=candidate,
                   measurement_temperature_c=PAD_TEMPERATURE_C,
                   retract_e_steps={name: round(xyz['retract_mm'][tool] * E_STEPS_PER_MM)
                                    for tool, name in enumerate(('T0', 'T1'))})
