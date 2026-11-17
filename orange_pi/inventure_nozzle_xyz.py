# SPDX-License-Identifier: GPL-3.0-or-later
"""Guarded, single-reference conductive-pad calibration for Inventure."""

import logging
import math

from .inventure_factory_core import E_STEPS_PER_MM, XY_STEPS_PER_MM, FactoryError
from .inventure_z_home import NOZZLE_CONTACT, Z_BOTTOM, Z_OPEN_CONFIRM_S, Z_STEPS_PER_MM, ZHomeProgram


PAD_DIAMETER_MM = 7.
PAD_TAP_DIAMETER_MM = 5.
PAD_WIDTH_TOLERANCE_MM = 1.5
PAD_TEMPERATURE_C = 160.
PAD_TRAVEL_SPEED = 8.
PAD_MAX_SWEEP_MM = 6.
PAD_MAX_SWEEP_SPEED = .5
PAD_MIN_SWEEP_STEPS = round(.25 * XY_STEPS_PER_MM)
PAD_MAX_UNCERTAIN_TAPS = 6
PAD_MIN_SWEEP_CLEARANCE_STEPS = Z_STEPS_PER_MM // 2
PAD_EDGE_TAP_HEADROOM_STEPS = round(.05 * Z_STEPS_PER_MM)
PAD_MAX_Z_SPEED = 3.9
PAD_PREP_Z_STEPS = 20 * Z_STEPS_PER_MM


def pad_xyz_parameters(gcmd, center_only=False):
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

    center_tool = None
    if center_only:
        tool = str(gcmd.get('TOOL', 'T0')).strip().upper()
        if tool not in ('T0', 'T1'):
            raise FactoryError('CENTER TOOL must be T0 or T1')
        mode = 'CENTER_' + tool
        center_tool = int(tool[1])
    else:
        mode = str(gcmd.get('MODE', 'FULL')).strip().upper()
        if mode not in ('FULL', 'VERIFY_T0'):
            raise FactoryError('Pad calibration MODE must be FULL or VERIFY_T0')
    scan = 'SWEEP' if center_only else str(gcmd.get('SCAN', 'SWEEP')).strip().upper()
    if scan not in ('SWEEP', 'TAP'):
        raise FactoryError('Pad calibration SCAN must be SWEEP or TAP')
    diameter = number('PAD_DIAMETER_MM', PAD_TAP_DIAMETER_MM if scan == 'TAP' else PAD_DIAMETER_MM, 2., 10.)
    scan_radius = 5. if center_only or diameter > PAD_TAP_DIAMETER_MM else 3.5
    prep = 'COLD' if center_only else str(gcmd.get('PREP', 'RETRACT')).strip().upper()
    if not center_only and prep not in ('RETRACT', 'EMPTY'):
        raise FactoryError('Pad calibration PREP must be RETRACT or EMPTY')
    if prep != 'RETRACT':
        for name in ('T0_TARGET', 'T1_TARGET', 'T0_RETRACT_MM', 'T1_RETRACT_MM'):
            if gcmd.get(name, None) is not None:
                raise FactoryError('PREP=%s uses zero retraction, omit %s' % (prep, name))
        retract_mm = (0., 0.)
    else:
        retract_mm = (number('T0_RETRACT_MM', None, .1, 100.),
                      number('T1_RETRACT_MM', None, .1, 100.))
    required_tools = (center_tool,) if center_only else (0, 1) if mode == 'FULL' else (0,)
    ceilings = tuple(number('T%d_Z_MAX' % tool, None, 20., 150., optional=tool not in required_tools)
                     for tool in (0, 1))
    clearance = number('Z_CLEARANCE_MM', 1.5, .5, 4.)
    for tool, ceiling in enumerate(ceilings):
        if ceiling is not None and (math.floor(ceiling * Z_STEPS_PER_MM)
                                    - round(clearance * Z_STEPS_PER_MM) < PAD_PREP_Z_STEPS):
            raise FactoryError('T%d_Z_MAX minus Z_CLEARANCE_MM must allow the 20 mm selector stage' % tool)
    return dict(
        mode=mode,
        center_tool=center_tool,
        scan=scan,
        prep=prep,
        z_max_mm=ceilings,
        retract_mm=retract_mm,
        retract_speed=number('RETRACT_SPEED', 1.6, .5, 6.),
        pad_diameter_mm=diameter,
        scan_radius_mm=number('SCAN_RADIUS_MM', scan_radius, 3., 6.),
        xy_sweep_speed=number('XY_SWEEP_SPEED', .25, .05, PAD_MAX_SWEEP_SPEED),
        edge_tolerance_mm=number('EDGE_TOLERANCE_MM', .05 if scan == 'SWEEP' else .02, .005, .1),
        z_clearance_mm=clearance,
        z_tap_speed=number('Z_TAP_SPEED', .25 if scan == 'SWEEP' else .8, .2, 1.),
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
    """Measure a conductive pad using PC14 and one known PE12 origin."""

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

    def _touch(self, point, safe_z, ceiling, tap_speed, settle=False):
        if self.z_steps != safe_z:
            raise FactoryError('Pad tap requires the verified XY clearance plane')
        yield from self._pad_xy_move(point)
        sent, hit, raw = yield from self._z_move(
            ceiling - safe_z, tap_speed, NOZZLE_CONTACT, 0, 1, 0,
            allow_contact_transient=True)
        closed = hit == NOZZLE_CONTACT and sent > 0
        if closed and settle:
            raw = yield dict(kind='z_verify_closed', delay_s=.15)
        closed = closed and raw['nozzle_contact'] == 0
        missed = not hit and raw['nozzle_contact'] == 1 and self.z_steps == ceiling
        return closed, missed

    def _edge_tap(self, point, safe_z, ceiling, tap_speed):
        misses = 0
        for _ in range(2):
            closed, missed = yield from self._touch(point, safe_z, ceiling, tap_speed, settle=True)
            height = self.z_steps
            yield from self._open_z_at(safe_z, allow_closed=True)
            if closed:
                return True, height
            misses += missed
        # Two ascents to the limit with no PC14 latch qualify an open point.
        # A brief closure followed by a miss provides no edge information.
        return (False if misses == 2 else None), None

    def _tap(self, point, safe_z, ceiling, tap_speed, release=False):
        closed, missed = yield from self._touch(point, safe_z, ceiling, tap_speed)
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

    def _edge(self, origin, axis, outside, safe_z, ceiling, units, tap_speed, confirm=False):
        def contact(value):
            xy = list(origin)
            xy[axis] = value
            if confirm:
                state, _ = yield from self._edge_tap(xy, safe_z, ceiling, tap_speed)
                return state
            return (yield from self._tap(xy, safe_z, ceiling, tap_speed))

        inside = origin[axis]
        endpoint = list(origin)
        endpoint[axis] = outside
        name = ('X', 'Y')[axis]
        label = name + ('+' if outside > inside else '-')
        state = yield from contact(outside)
        if state is None:
            # A noisy limit point can be bypassed within the search range,
            # provided a nearby point still confirms a fully open contact.
            direction = 1 if outside > inside else -1
            for offset_mm in (.1, .2, .4):
                probe = outside - direction * round(offset_mm * XY_STEPS_PER_MM)
                if direction * (probe - inside) <= 0:
                    break
                state = yield from contact(probe)
                if state is False:
                    outside = probe
                    break
                if state is True:
                    break
            if state is not False:
                raise FactoryError('PC14 nie potwierdził rozwarcia przy granicy skanu',
                                   direction=label, point_mm=_xy_mm(endpoint))
        elif state is True:
            if confirm:
                raise FactoryError('PC14 nadal zwarty na końcu zakresu', point_mm=_xy_mm(endpoint))
            raise FactoryError('Pad edge is outside the qualified scan box',
                               axis=name, point_mm=_xy_mm(endpoint))

        uncertain = set()
        while abs(inside - outside) > units:
            lower, upper = sorted((inside, outside))
            known_positions = sorted([lower, upper] + [p for p in uncertain if lower < p < upper])
            left, right = max(zip(known_positions, known_positions[1:]), key=lambda pair: pair[1] - pair[0])
            if right - left <= 1 or len(uncertain) >= PAD_MAX_UNCERTAIN_TAPS:
                raise FactoryError('Przedział krawędzi PC14 nadal ma %.2f mm' % ((upper - lower) / XY_STEPS_PER_MM),
                                   direction=label, edge_interval_mm=_xy_mm((lower, upper)))
            midpoint = (left + right) // 2
            state = yield from contact(midpoint)
            if state is None:
                # Preserve both verified bounds. Probe a different part of the
                # bracket instead of treating a short closure as either state.
                uncertain.add(midpoint)
                logging.info('Inventure pad T%d %s: uncertain tap at %.3f mm, bounds=%s',
                             self.p['tool'], label, midpoint / XY_STEPS_PER_MM, _xy_mm((lower, upper)))
            elif state:
                inside = midpoint
            else:
                outside = midpoint
        if confirm:
            logging.info('Inventure pad T%d %s: confirmed edge bracket %s mm',
                         self.p['tool'], label, _xy_mm(tuple(sorted((inside, outside)))))
        return (inside + outside) / 2.

    def _sweep_edge(self, origin, axis, outside, safe_z, ceiling, xyz):
        direction = 1 if outside > origin[axis] else -1
        span = min(abs(outside - origin[axis]), round(PAD_MAX_SWEEP_MM * XY_STEPS_PER_MM))
        if not span:
            raise FactoryError('Pad sweep start has no room toward its scan boundary')
        target = list(origin)
        target[axis] += direction * span
        leg = ('X', 'Y')[axis] + ('+' if direction == 1 else '-')
        if xyz['prep'] == 'COLD':
            yield from self._phase(leg)
        row = None
        x, y = _xy_mm(target)
        for _ in range(2):
            closed, missed = yield from self._touch(origin, safe_z, ceiling,
                                                   xyz['z_tap_speed'], settle=True)
            if closed:
                contact_z = self.z_steps
                retreat_z = min(safe_z, contact_z - PAD_MIN_SWEEP_CLEARANCE_STEPS)
                if retreat_z < PAD_PREP_Z_STEPS:
                    yield from self._open_z_at(retreat_z, allow_closed=True)
                    raise FactoryError('Pad contact is too low for the required XY clearance plane')
                result = yield dict(kind='move', x=x, y=y, e=0., speed=xyz['xy_sweep_speed'],
                                    stage='pad_sweep')
                if not isinstance(result, dict):
                    raise FactoryError('Missing pad sweep result')
                if result.get('reason') != 'pad_sweep_contact_unqualified':
                    row = result
                    break
                logging.info('Inventure pad T%d %s: PC14 contact not qualified before the first XY step at %s',
                             self.p['tool'], leg, _xy_mm(origin))
            if self.z_steps > safe_z:
                yield from self._open_z_at(safe_z, allow_closed=True)
            if missed:
                break
        if row is None:
            logging.info('Inventure pad T%d %s: no stable sweep start at %s, z_limit_mm=%.3f',
                         self.p['tool'], leg, _xy_mm(origin), _z_mm(ceiling))
            return None, safe_z, None

        position = row.get('position')
        edge = None
        if (isinstance(position, (list, tuple)) and len(position) == 2
                and all(type(value) in (int, float) and self.p['min_' + name] <= value <= self.p['max_' + name]
                        for name, value in zip('xy', position))):
            self.xy = tuple(position)
            reached = tuple(round(value * XY_STEPS_PER_MM) for value in position)
            travelled = direction * (reached[axis] - origin[axis])
            if (row.get('ok') is True and row.get('reason') == 'stop_input'
                    and row.get('hit') == NOZZLE_CONTACT and reached[1 - axis] == origin[1 - axis]
                    and 0 < travelled <= span):
                fresh = yield dict(kind='z_verify_open', delay_s=Z_OPEN_CONFIRM_S)
                if fresh['nozzle_contact'] == 1:
                    # Fit midway between the last closed and first open step.
                    edge = reached[axis] - direction / 2.
        yield from self._open_z_at(retreat_z, allow_closed=True)
        logging.info('Inventure pad T%d %s sweep: reason=%s hit=%s steps=%s xy=%s z_mm=%.3f',
                     self.p['tool'], leg, row.get('reason'), row.get('hit'), row.get('sent_steps'), position,
                     _z_mm(contact_z))
        if edge is not None and abs(edge - origin[axis]) < PAD_MIN_SWEEP_STEPS:
            logging.info('Inventure pad T%d %s: near-start opening at %.4f mm needs verification',
                         self.p['tool'], leg, edge / XY_STEPS_PER_MM)
            edge = None
        return edge, retreat_z, contact_z

    def _tap_edges(self, origin, axis, bounds, safe_z, ceiling, xyz):
        name = ('X', 'Y')[axis]
        yield from self._phase(name + '_TOUCH')
        tap_speed = xyz['z_tap_speed']
        units = max(1, math.floor(xyz['edge_tolerance_mm'] * XY_STEPS_PER_MM))
        anchor = list(origin)
        for offset in (0, 1, -1, 2, -2):
            anchor[axis] = origin[axis] + offset * XY_STEPS_PER_MM
            if not bounds[0] < anchor[axis] < bounds[1]:
                continue
            contact, anchor_z = yield from self._edge_tap(anchor, safe_z, ceiling, tap_speed)
            logging.info('Inventure pad T%d %s tap seed: xy=%s contact=%s z_limit_mm=%.3f',
                         self.p['tool'], name, _xy_mm(anchor), contact, _z_mm(ceiling))
            if contact is True:
                break
        else:
            raise FactoryError('PC14 nie potwierdził zwarcia w próbach osi ' + name,
                               start_mm=_xy_mm(origin), z_limit_mm=_z_mm(ceiling))

        if xyz['prep'] != 'COLD':
            # Bound hot taps at the measured pad plane before sampling the rim.
            ceiling = min(ceiling, anchor_z + PAD_EDGE_TAP_HEADROOM_STEPS)
            safe_z = min(safe_z, anchor_z - PAD_MIN_SWEEP_CLEARANCE_STEPS)
            if safe_z < PAD_PREP_Z_STEPS:
                raise FactoryError('Pad contact is too low for the required XY clearance plane')
            yield from self._open_z_at(safe_z)

        pair = [None, None]
        for side in (1, 0):
            yield from self._phase(name + ('+' if side else '-') + '_TOUCH')
            pair[side] = yield from self._edge(anchor, axis, bounds[side], safe_z, ceiling,
                                             units, tap_speed, confirm=True)
        return pair, safe_z

    def _scan_axes(self, seed, limits, safe_z, ceiling, xyz):
        origin = list(seed)
        edges = []
        used_taps = False
        diameter = xyz['pad_diameter_mm']
        min_sweep_width = diameter - PAD_WIDTH_TOLERANCE_MM
        max_width = diameter + PAD_WIDTH_TOLERANCE_MM
        for axis, bounds in enumerate(limits):
            name = ('X', 'Y')[axis]
            pair = [None, None]
            contacts = []
            for side in (1, 0):
                pair[side], safe_z, contact_z = yield from self._sweep_edge(
                    origin, axis, bounds[side], safe_z, ceiling, xyz)
                if contact_z is not None:
                    contacts.append(contact_z)
                if pair[side] is None:
                    break
            method = 'sweep'
            if (None in pair
                    or not min_sweep_width <= (pair[1] - pair[0]) / XY_STEPS_PER_MM <= max_width):
                if None not in pair:
                    logging.info('Inventure pad T%d %s: sweep width %.4f mm needs vertical verification',
                                 self.p['tool'], name, (pair[1] - pair[0]) / XY_STEPS_PER_MM)
                # Probe both sides of the same chord. No XY movement takes
                # place at the contact height during these checks.
                tap_ceiling = min(ceiling, max(contacts) + PAD_EDGE_TAP_HEADROOM_STEPS) if contacts else ceiling
                pair, safe_z = yield from self._tap_edges(origin, axis, bounds, safe_z, tap_ceiling, xyz)
                method = 'tap'
                used_taps = True
            low, high = pair
            width = (high - low) / XY_STEPS_PER_MM
            # Vertical taps can qualify a short X chord off the pad center.
            min_width = .5 if axis == 0 else min_sweep_width
            if not min_width <= width <= max_width:
                raise FactoryError('Szerokość styku %s %.2f mm poza zakresem %.1f–%.1f mm' %
                                   (name, width, min_width, max_width))
            for side, edge in zip(('-', '+'), pair):
                logging.info('Inventure pad T%d %s%s edge_mm=%.4f method=%s',
                             self.p['tool'], name, side, edge / XY_STEPS_PER_MM, method)
            edges.append((low, high))
            origin[axis] = round((low + high) / 2.)
        return edges, tuple(origin), safe_z, used_taps

    def _scan_sweeps(self, seed, limits, safe_z, ceiling, xyz):
        _, first, safe_z, first_taps = yield from self._scan_axes(seed, limits, safe_z, ceiling, xyz)
        edges, origin, safe_z, second_taps = yield from self._scan_axes(first, limits, safe_z, ceiling, xyz)
        repeat = max(abs(a - b) for a, b in zip(first, origin)) / XY_STEPS_PER_MM
        if repeat > xyz['xy_repeat_mm']:
            raise FactoryError('Pad sweep center did not repeat', xy_center_repeat_mm=repeat)
        method = 'SWEEP+TAP' if first_taps or second_taps else 'SWEEP'
        return edges, origin, safe_z, repeat, method

    def _scan_taps(self, origin, limits, safe_z, ceiling, xyz):
        tol = max(1, math.floor(xyz['edge_tolerance_mm'] * XY_STEPS_PER_MM))
        tap_speed = xyz['z_tap_speed']
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
        return edges, origin

    def _scan(self, seed, limits, ceiling, xyz):
        safe_z = ceiling - round(xyz['z_clearance_mm'] * Z_STEPS_PER_MM)
        yield from self._pad_xy_move(seed)
        yield from self._open_z_at(safe_z)
        if xyz['scan'] == 'SWEEP':
            edges, origin, safe_z, repeat, method = yield from self._scan_sweeps(seed, limits, safe_z, ceiling, xyz)
        else:
            edges, origin = yield from self._scan_taps(seed, limits, safe_z, ceiling, xyz)
            repeat = None
            method = 'TAP'

        widths = [(high - low) / XY_STEPS_PER_MM for low, high in edges]
        diameter = xyz['pad_diameter_mm']
        # The nozzle face slightly enlarges the effective conductive outline.
        # Require a plausible shape without forcing the outline to the nominal diameter.
        if (max(abs(w - diameter) for w in widths) > PAD_WIDTH_TOLERANCE_MM
                or abs(widths[0] - widths[1]) > .6):
            raise FactoryError('Pad contact outline is not consistent with a round %g mm pad' % diameter,
                               electrical_diameter_mm=widths)
        releases = []
        for _ in range(2):
            z = yield from self._tap(origin, safe_z, ceiling, xyz['z_tap_speed'], release=True)
            if type(z) is not int:
                raise FactoryError('No PC14 contact at the fitted pad center')
            releases.append(z)
        spread = _z_mm(abs(releases[0] - releases[1]))
        if spread > xyz['z_repeat_mm']:
            raise FactoryError('Pad center Z release is not repeatable', spread_mm=spread)
        return dict(center_xy_mm=_xy_mm(origin),
                    edges_mm=[_xy_mm(pair) for pair in edges],
                    electrical_diameter_mm=widths,
                    scan_method=method, xy_center_repeat_mm=repeat,
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
        yield from self._wipe_and_return_to_bin()

    def _wipe_at_160(self):
        yield from self._phase('XYZ_T%d_WIPE_160' % self.p['tool'])
        yield from self.position_bin()
        yield from self._wipe_and_return_to_bin()

    def _wipe_and_return_to_bin(self):
        yield from self.clean()
        # Leave the brush at its exit X before crossing to the bin center.
        yield from self._move(self.xy[0], self.p['center_y'])
        yield from self.position_bin()

    def _verify_t0_at_pad(self):
        yield from self._position_contact_pad()
        yield dict(kind='xyz_confirm_t0')

    @staticmethod
    def _pad_plan(p, settings):
        settings = dict(settings)
        for name in ('z_bottom_speed', 'z_approach_speed', 'z_backoff_speed'):
            settings[name] = min(settings[name], PAD_MAX_Z_SPEED)
        return ZHomeProgram(dict(p, z_speed=settings['z_bottom_speed']), settings)

    def _pad_ceiling(self, plan, xyz):
        tool = plan.p['tool']
        ceiling = xyz['z_max_mm'][tool]
        steps = 0 if ceiling is None else math.floor(ceiling * Z_STEPS_PER_MM)
        if (ceiling is None or ceiling > plan.settings['z_bottom_max_mm']
                or steps - round(xyz['z_clearance_mm'] * Z_STEPS_PER_MM) < PAD_PREP_Z_STEPS):
            raise FactoryError('T%d pad Z ceiling is outside the qualified travel range' % tool)
        return steps

    def _home_pad(self, plans):
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
        yield from self._pick(self.p['tool'], plans)

    def run_pad_center(self, xyz):
        plan = self._pad_plan(self.p, self.settings)
        tool = plan.p['tool']
        if tool != xyz['center_tool']:
            raise FactoryError('Cold pad center tool does not match the motion plan')
        ceiling = self._pad_ceiling(plan, xyz)
        seed, limits = self._scan_box(plan, xyz)
        yield from self._home_pad((plan,))
        yield dict(kind='xyz_arm', tool=tool, ceiling_steps=ceiling)
        yield from self._pad_xy_move(seed)
        safe_z = ceiling - round(xyz['z_clearance_mm'] * Z_STEPS_PER_MM)
        yield from self._open_z_at(safe_z)
        edges, origin, safe_z, used_taps = yield from self._scan_axes(seed, limits, safe_z, ceiling, xyz)
        yield from self._phase('PARK')
        yield from self._open_z_at(min(safe_z, ceiling - 5 * Z_STEPS_PER_MM))
        yield from self._pad_xy_move(origin)
        # Report the measured midpoint, preserving half-step resolution.
        center = _xy_mm(tuple((low + high) / 2. for low, high in edges))
        widths = [(high - low) / XY_STEPS_PER_MM for low, high in edges]
        measurement = dict(center_xy_mm=center, edges_mm=[_xy_mm(pair) for pair in edges],
                           electrical_diameter_mm=widths, scan_method='SWEEP+TAP' if used_taps else 'SWEEP')
        name = 'T%d' % tool
        yield dict(kind='done', success=True, terminal='COLD_PAD_CENTER',
                   measurements={name: measurement}, retract_e_steps={name: 0})

    def run_nozzle_xyz(self, t1_p, t1_settings, xyz):
        plans = (self._pad_plan(self.p, self.settings), self._pad_plan(t1_p, t1_settings))
        if tuple(plan.p['tool'] for plan in plans) != (0, 1):
            raise FactoryError('Pad calibration requires ordered T0 and T1 plans')
        empty = xyz['prep'] == 'EMPTY'
        verify_t0 = xyz['mode'] == 'VERIFY_T0'
        scan_order = (0,) if verify_t0 else (0, 1, 0)
        boxes, ceilings = {}, {}
        for tool in set(scan_order):
            plan = plans[tool]
            ceilings[tool] = self._pad_ceiling(plan, xyz)
            boxes[tool] = self._scan_box(plan, xyz)
        yield from self._home_pad(plans)
        if empty and verify_t0:
            yield from self._verify_t0_at_pad()
            yield from self.position_bin()
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
        if not empty and verify_t0:
            yield from self._wipe_at_160()

        measurements = {}
        for index, tool in enumerate(scan_order):
            name = ('T0', 'T1', 'T0_RETURN')[index]
            if index or not empty:
                yield from self._pick(tool, plans)
            yield dict(kind='xyz_cool_wait')
            if index < 2:
                yield from self._wipe_at_160()
            if verify_t0 and not empty:
                yield dict(kind='xyz_confirm_t0')
            ceiling = ceilings[tool]
            yield dict(kind='xyz_arm', tool=tool, ceiling_steps=ceiling)
            yield from self._phase('XYZ_SCAN_' + name)
            measurements[name] = yield from self._module(
                'pad_scan_' + name.lower(), self._scan(*boxes[tool], ceiling, xyz))
            yield dict(kind='xyz_sample', name=name, measurement=measurements[name])
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
