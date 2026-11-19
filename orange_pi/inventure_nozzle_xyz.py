# SPDX-License-Identifier: GPL-3.0-or-later
"""Guarded, single-reference conductive-pad calibration for Inventure."""

import logging
import math

from .inventure_factory_core import E_STEPS_PER_MM, XY_STEPS_PER_MM, FactoryError
from .inventure_z_home import NOZZLE_CONTACT, Z_BOTTOM, Z_OPEN_CONFIRM_S, Z_STEPS_PER_MM, ZHomeProgram


PAD_DIAMETER_MM = 7.
PAD_TAP_DIAMETER_MM = 5.
PAD_WIDTH_TOLERANCE_MM = 1.5
PAD_TEMPERATURE_C = 100.
PAD_MEASUREMENT_BAND_C = (PAD_TEMPERATURE_C - 10., PAD_TEMPERATURE_C + 10.)
PAD_TRAVEL_SPEED = 8.
PAD_MAX_SWEEP_MM = 6.
PAD_MAX_SWEEP_SPEED = .5
PAD_MIN_SWEEP_STEPS = round(.25 * XY_STEPS_PER_MM)
PAD_MAX_UNCERTAIN_TAPS = 6
PAD_MIN_SWEEP_CLEARANCE_STEPS = Z_STEPS_PER_MM // 2
PAD_EDGE_TAP_HEADROOM_STEPS = round(.05 * Z_STEPS_PER_MM)
PAD_EDGE_TAP_REPEAT_STEPS = math.floor(.05 * Z_STEPS_PER_MM)
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
    scan = str(gcmd.get('SCAN', 'SWEEP')).strip().upper()
    if scan not in ('SWEEP', 'TAP'):
        raise FactoryError('Pad calibration SCAN must be SWEEP or TAP')
    warm_tap = scan == 'TAP' and not center_only
    diameter = number('PAD_DIAMETER_MM', PAD_TAP_DIAMETER_MM if warm_tap else PAD_DIAMETER_MM, 2., 10.)
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
    clearance = number('Z_CLEARANCE_MM', 1., .5, 4.)
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
        edge_tolerance_mm=number('EDGE_TOLERANCE_MM', .02 if warm_tap else .05, .005, .1),
        z_clearance_mm=clearance,
        z_tap_speed=number('Z_TAP_SPEED', .8 if warm_tap else .25, .2, 1.),
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

    def _touch(self, point, safe_z, ceiling, tap_speed, settle=False,
               latched_contact=False, observation=None):
        if self.z_steps != safe_z:
            raise FactoryError('Pad tap requires the verified XY clearance plane')
        yield from self._pad_xy_move(point)
        sent, hit, raw = yield from self._z_move(
            ceiling - safe_z, tap_speed, NOZZLE_CONTACT, 0, 1, 0,
            allow_contact_transient=True)
        closed = hit == NOZZLE_CONTACT and sent > 0 and raw['z_bottom'] == 0
        if observation is not None:
            observation.update(touch_hit=hit,touch_sent_steps=sent,
                               touch_raw=raw['nozzle_contact'])
        if closed and settle:
            raw = yield dict(kind='z_verify_closed', delay_s=.15)
        # Center Z measures the MCU-latched contact/release edges, like
        # homing Z. The input may already be open at the later host read.
        # Sliding SWEEP still requires a currently closed, settled contact.
        closed = closed and (latched_contact or raw['nozzle_contact'] == 0)
        missed = not hit and raw['nozzle_contact'] == 1 and self.z_steps == ceiling
        return closed, missed

    def _edge_tap(self, point, safe_z, ceiling, tap_speed):
        misses = 0
        heights = []
        for _ in range(2):
            # This is a vertical probe, not a sliding sweep. Like center Z,
            # qualify the locally latched edge rather than requiring the
            # electrical contact to persist until a later host read.
            closed, missed = yield from self._touch(point, safe_z, ceiling, tap_speed,
                                                    latched_contact=True)
            height = self.z_steps
            yield from self._open_z_at(safe_z, allow_closed=True)
            fresh = yield dict(kind='z_verify_open', delay_s=Z_OPEN_CONFIRM_S)
            if fresh['nozzle_contact'] != 1 or fresh['z_bottom']:
                raise FactoryError('PC14 did not remain open at pad XY clearance',
                                   point_mm=_xy_mm(point))
            if closed:
                heights.append(height)
            misses += missed
        repeatable = len(heights) == 2 and max(heights)-min(heights) <= PAD_EDGE_TAP_REPEAT_STEPS
        state = True if repeatable else False if misses == 2 else None
        logging.info('Inventure pad T%d TAP xy=%s result=%s latched_z_steps=%s misses=%d',
                     self.p['tool'], _xy_mm(point), state, heights, misses)
        # Mixed hit/miss, zero-step or nonrepeatable contacts cannot move a
        # verified XY bound. Two full no-hit ascents still qualify outside.
        return state, min(heights) if repeatable else None

    def _tap(self, point, safe_z, ceiling, tap_speed, release=False, sample_index=1):
        if release:
            return (yield from self._center_z_release(point,safe_z,ceiling,tap_speed,sample_index))
        state, _ = yield from self._edge_tap(point, safe_z, ceiling, tap_speed)
        if state is None:
            raise FactoryError('Unstable PC14 response during pad tap')
        return state

    def _center_z_release(self,point,safe_z,ceiling,tap_speed,sample_index):
        history=[]
        for attempt in range(self.settings['z_transient_retries']+1):
            yield from self._phase('Z_PAD_T%d_SAMPLE_%d_ATTEMPT_%d' %
                                   (self.p['tool'],sample_index,attempt+1))
            observation={}
            closed,missed=yield from self._touch(point,safe_z,ceiling,tap_speed,
                                                latched_contact=True,observation=observation)
            record=dict(tool='T%d'%self.p['tool'],sample=sample_index,attempt=attempt+1,
                        touch_steps=self.z_steps,closed=closed,missed=missed,
                        release_hit=None,release_sent_steps=None,release_raw=None,
                        settle_raw=None,recovery_steps=0,accepted=False)
            record.update(observation,touch_qualification='mcu_latched_edge')
            opening=None
            if closed:
                allowed=min(self.z_steps-safe_z,round(self.settings['z_release_mm']*Z_STEPS_PER_MM))
                lower=self.z_steps-allowed
                sent,hit,raw=yield from self._z_move(
                    -allowed,self.settings['z_release_speed'],Z_BOTTOM|NOZZLE_CONTACT,
                    Z_BOTTOM|NOZZLE_CONTACT,None,0,allow_contact_transient=True)
                record.update(release_hit=hit,release_sent_steps=sent,
                              release_raw=raw['nozzle_contact'],edge_steps=self.z_steps)
                valid=hit==NOZZLE_CONTACT and raw['nozzle_contact']==1
                if valid:
                    fresh=yield dict(kind='z_verify_open',delay_s=Z_OPEN_CONFIRM_S)
                    record['settle_raw']=fresh['nozzle_contact']
                    valid=fresh['nozzle_contact']==1
                if hit==NOZZLE_CONTACT and not valid and self.settings['z_release_recovery_steps']:
                    # Reuse the bounded homing-Z recovery: four downward
                    # steps at a time, at most 16 total, with a fresh open
                    # confirmation at rest. Never raise the table for recovery.
                    progress=dict(release_recovery_samples=0,release_recovery_max_steps=0)
                    recovery_start=self.z_steps
                    valid=yield from self._module('z_release_recovery',
                                                  self._z_recover_release(lower,progress))
                    record['recovery_steps']=recovery_start-self.z_steps
                if valid:
                    opening=self.z_steps
                    record.update(accepted=True,release_steps=opening)
            history.append(record)
            yield dict(kind='xyz_z_release',diagnostic=record)
            yield from self._open_z_at(safe_z,allow_closed=True)
            if opening is not None:return opening
            logging.info('Inventure pad T%d center Z sample %d attempt %d unqualified: %s',
                         self.p['tool'],sample_index,attempt+1,record)
        reason=('PC14 release was not repeatable at pad center' if any(row['closed'] for row in history)
                else 'PC14 contact was not confirmed at pad center')
        raise FactoryError(reason,
                           sample=sample_index,attempts=history,center_mm=_xy_mm(point))

    def _edge(self, origin, axis, outside, safe_z, ceiling, units, tap_speed,
              confirm=False, prior_edge=None):
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
                raise FactoryError('PC14 release was not confirmed at the scan boundary',
                                   direction=label, point_mm=_xy_mm(endpoint))
        elif state is True:
            if confirm:
                raise FactoryError('PC14 remained closed at the end of the scan range', point_mm=_xy_mm(endpoint))
            raise FactoryError('Pad edge is outside the qualified scan box',
                               axis=name, point_mm=_xy_mm(endpoint))

        # The second pass measures a new chord. Use the first pass only to
        # choose which two bisection points to try first. Both endpoints of
        # the coarse bracket must qualify on this chord before narrowing.
        if prior_edge is not None and min(inside, outside) < prior_edge < max(inside, outside):
            inner, outer = inside, outside
            direction = 1 if outer > inner else -1
            while abs(outer - inner) > 4 * units:
                midpoint = (inner + outer) // 2
                if direction * (prior_edge - midpoint) > 0:
                    inner = midpoint
                else:
                    outer = midpoint
            if (inner == inside or (yield from contact(inner)) is True) and (
                    outer == outside or (yield from contact(outer)) is False):
                inside, outside = inner, outer

        uncertain = set()
        while abs(inside - outside) > units:
            lower, upper = sorted((inside, outside))
            # A confirmed bound can rule out earlier inconclusive positions.
            uncertain = {p for p in uncertain if lower < p < upper}
            known_positions = [lower, *sorted(uncertain), upper]
            left, right = max(zip(known_positions, known_positions[1:]), key=lambda pair: pair[1] - pair[0])
            if right - left <= 1 or len(uncertain) >= PAD_MAX_UNCERTAIN_TAPS:
                raise FactoryError('PC14 edge interval is still %.2f mm wide' % ((upper - lower) / XY_STEPS_PER_MM),
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

    def _tap_edges(self, origin, axis, bounds, safe_z, ceiling, xyz, prior_pair):
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
            raise FactoryError('PC14 contact was not confirmed when probing axis ' + name,
                               start_mm=_xy_mm(origin), z_limit_mm=_z_mm(ceiling))

        if xyz['prep'] != 'COLD' or xyz['scan'] == 'TAP':
            # Cap rim probes relative to the two confirmed vertical contacts.
            ceiling = min(ceiling, anchor_z + PAD_EDGE_TAP_HEADROOM_STEPS)
            safe_z = min(safe_z, anchor_z - PAD_MIN_SWEEP_CLEARANCE_STEPS)
            if safe_z < PAD_PREP_Z_STEPS:
                raise FactoryError('Pad contact is too low for the required XY clearance plane')
            yield from self._open_z_at(safe_z)

        pair = [None, None]
        for side in (1, 0):
            yield from self._phase(name + ('+' if side else '-') + '_TOUCH')
            pair[side] = yield from self._edge(
                anchor, axis, bounds[side], safe_z, ceiling, units, tap_speed,
                confirm=True, prior_edge=prior_pair[side] if prior_pair else None)
        return pair, safe_z

    def _scan_axes(self, seed, limits, safe_z, ceiling, xyz, prior_edges=None):
        origin = list(seed)
        edges = []
        sources = {}
        diameter = xyz['pad_diameter_mm']
        min_sweep_width = diameter - PAD_WIDTH_TOLERANCE_MM
        max_width = diameter + PAD_WIDTH_TOLERANCE_MM
        for axis, bounds in enumerate(limits):
            name = ('X', 'Y')[axis]
            pair = [None, None]
            contacts = []
            if xyz['scan'] == 'SWEEP':
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
                pair, safe_z = yield from self._tap_edges(
                    origin, axis, bounds, safe_z, tap_ceiling, xyz,
                    prior_edges[axis] if prior_edges else None)
                method = 'tap'
            sources[name] = method.upper()
            low, high = pair
            width = (high - low) / XY_STEPS_PER_MM
            # Vertical taps can qualify a short X chord off the pad center.
            min_width = .5 if axis == 0 else min_sweep_width
            if not min_width <= width <= max_width:
                raise FactoryError('Contact width on %s is %.2f mm, outside the %.1f–%.1f mm range' %
                                   (name, width, min_width, max_width))
            for side, edge in zip(('-', '+'), pair):
                logging.info('Inventure pad T%d %s%s edge_mm=%.4f method=%s',
                             self.p['tool'], name, side, edge / XY_STEPS_PER_MM, method)
            edges.append((low, high))
            origin[axis] = round((low + high) / 2.)
        return edges, tuple(origin), safe_z, sources

    def _scan_taps(self, origin, limits, safe_z, ceiling, xyz):
        tol = max(1, math.floor(xyz['edge_tolerance_mm'] * XY_STEPS_PER_MM))
        tap_speed = xyz['z_tap_speed']
        centers = []
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
            centers.append(origin)
        return edges, centers[0], centers[1]

    def _scan(self, seed, limits, ceiling, xyz):
        safe_z = ceiling - round(xyz['z_clearance_mm'] * Z_STEPS_PER_MM)
        yield from self._pad_xy_move(seed)
        yield from self._open_z_at(safe_z)
        if xyz['scan'] == 'SWEEP':
            prior_edges, first, safe_z, first_sources = yield from self._scan_axes(
                seed, limits, safe_z, ceiling, xyz)
            edges, origin, safe_z, second_sources = yield from self._scan_axes(
                first, limits, safe_z, ceiling, xyz, prior_edges)
            sources = [first_sources, second_sources]
            method = 'SWEEP+TAP' if any('TAP' in row.values() for row in sources) else 'SWEEP'
        else:
            edges, first, origin = yield from self._scan_taps(seed, limits, safe_z, ceiling, xyz)
            sources = [dict(X='TAP', Y='TAP') for _ in range(2)]
            method = 'TAP'

        repeat = max(abs(a - b) for a, b in zip(first, origin)) / XY_STEPS_PER_MM
        if repeat > xyz['xy_repeat_mm']:
            raise FactoryError('Pad %s center did not repeat' % xyz['scan'].lower(),
                               xy_center_repeat_mm=repeat)
        widths = [(high - low) / XY_STEPS_PER_MM for low, high in edges]
        diameter = xyz['pad_diameter_mm']
        # The nozzle face slightly enlarges the effective conductive outline.
        # Require a plausible shape without forcing the outline to the nominal diameter.
        if (max(abs(w - diameter) for w in widths) > PAD_WIDTH_TOLERANCE_MM
                or abs(widths[0] - widths[1]) > .6):
            raise FactoryError('Pad contact outline is not consistent with a round %g mm pad' % diameter,
                               electrical_diameter_mm=widths)
        measurement = dict(center_xy_mm=_xy_mm(origin), edges_mm=[_xy_mm(pair) for pair in edges],
                           electrical_diameter_mm=widths, scan_method=method,
                           edge_sources=sources, xy_center_repeat_mm=repeat)
        yield dict(kind='xyz_xy_sample', tool=self.p['tool'], measurement=measurement)
        releases = []
        for sample in range(2):
            z = yield from self._tap(origin, safe_z, ceiling, xyz['z_tap_speed'],
                                    release=True,sample_index=sample+1)
            if type(z) is not int:
                raise FactoryError('No PC14 contact at the fitted pad center')
            releases.append(z)
        spread = _z_mm(abs(releases[0] - releases[1]))
        if spread > xyz['z_repeat_mm']:
            raise FactoryError('Pad center Z release is not repeatable', spread_mm=spread)
        return dict(measurement, z_release_mm_from_pe12=_z_mm(sum(releases) / 2.),
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

    def _wipe_at_measurement_temperature(self):
        yield from self._phase('XYZ_T%d_WIPE_%d' % (self.p['tool'], PAD_TEMPERATURE_C))
        yield from self.position_bin()
        yield from self._wipe_and_return_to_bin()

    def _wipe_and_return_to_bin(self):
        yield from self.clean()
        yield from self.return_to_bin()

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
        edges, origin, safe_z, sources = yield from self._scan_axes(seed, limits, safe_z, ceiling, xyz)
        yield from self._phase('PARK')
        yield from self._open_z_at(min(safe_z, ceiling - 5 * Z_STEPS_PER_MM))
        yield from self._pad_xy_move(origin)
        # Report the measured midpoint, preserving half-step resolution.
        center = _xy_mm(tuple((low + high) / 2. for low, high in edges))
        widths = [(high - low) / XY_STEPS_PER_MM for low, high in edges]
        scan_method = xyz['scan']
        if scan_method == 'SWEEP' and 'TAP' in sources.values():
            scan_method = 'SWEEP+TAP'
        measurement = dict(center_xy_mm=center, edges_mm=[_xy_mm(pair) for pair in edges],
                           electrical_diameter_mm=widths, edge_sources=[sources],
                           scan_method=scan_method)
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
            yield from self._phase('XYZ_COOL_%d' % PAD_TEMPERATURE_C)
            yield dict(kind='xyz_cool_start')
        yield dict(kind='xyz_cool_wait')
        if not empty and verify_t0:
            yield from self._wipe_at_measurement_temperature()

        measurements = {}
        for index, tool in enumerate(scan_order):
            name = ('T0', 'T1', 'T0_RETURN')[index]
            if index or not empty:
                yield from self._pick(tool, plans)
            yield dict(kind='xyz_cool_wait')
            if index < 2:
                yield from self._wipe_at_measurement_temperature()
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
