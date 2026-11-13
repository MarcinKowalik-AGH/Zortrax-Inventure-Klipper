# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded Inventure SERVICE reference to the release of the T0 nozzle."""
import math

from .inventure_factory_core import FactoryError, FactoryProgram


Z_STEPS_PER_MM = 802
Z_BOTTOM = 1 << 2
NOZZLE_CONTACT = 1 << 3
Z_STABLE_CYCLES = 101
Z_BOTTOM_RELEASE_STEPS = 5 * Z_STEPS_PER_MM
Z_BOTTOM_BACKOFF_STEPS = Z_STEPS_PER_MM
Z_HOME_ACK = 'PAD_AND_CLEARANCE_CHECKED'

Z_HOME_PROCEDURES = {
    'Z_PREPARE': ('home', 'heat', 'select_tool', 'z_position_pad'),
    'HOME_Z': ('home', 'heat', 'select_tool', 'z_position_pad',
               'z_bottom_release', 'z_contact_reference'),
}


def z_home_parameters(gcmd, p, required=False):
    """Read SERVICE coordinates and bounds before sending any motion."""
    if p['tool'] != 0:
        raise FactoryError('Z_PREPARE and HOME_Z use TOOL=T0')

    def number(name, default, low, high):
        value = gcmd.get(name, default)
        if value is None:
            return None
        try:
            result = float(value)
        except (ValueError, TypeError, OverflowError):
            result = math.nan
        if isinstance(value, bool) or not math.isfinite(result) or not low <= result <= high:
            raise FactoryError('%s must be %g..%g' % (name, low, high))
        return result

    approach = number('Z_APPROACH_MM', None, 2., min(250., p['home_z_max']))
    span = number('Z_PROBE_MM', 1., .02, 10.)
    if required and approach is None:
        raise FactoryError('HOME_Z requires Z_APPROACH_MM measured from the PE12 trigger')
    if required and gcmd.get('ACK', '') != Z_HOME_ACK:
        raise FactoryError('HOME_Z requires ACK=' + Z_HOME_ACK)
    if approach is not None and approach + span > min(250., p['home_z_max']):
        raise FactoryError('Z_APPROACH_MM plus Z_PROBE_MM exceeds the Z travel bound')
    approach_speed = number('Z_APPROACH_SPEED', 1., .1, 2.)
    if approach is not None and approach / approach_speed > 595.:
        raise FactoryError('Z coarse approach exceeds 595 seconds at this speed')
    cycles = number('Z_MAX_CYCLES', 512, Z_STABLE_CYCLES + 1, 512)
    if not cycles.is_integer():
        raise FactoryError('Z_MAX_CYCLES must be an integer')
    return dict(
        z_approach_mm=approach,
        z_probe_x=number('Z_PROBE_X', 82.8, p['min_x'], p['max_x']),
        z_probe_y=number('Z_PROBE_Y', 68., p['min_y'], p['max_y']),
        z_probe_mm=span,
        z_release_mm=number('Z_RELEASE_MM', 1., .02, 2.),
        z_approach_speed=approach_speed,
        z_probe_speed=number('Z_PROBE_SPEED', .2, .05, 1.),
        z_release_speed=number('Z_RELEASE_SPEED', .2, .05, 1.),
        z_max_cycles=int(cycles),
    )


class ZHomeProgram(FactoryProgram):
    def __init__(self, p, settings):
        super().__init__(p)
        self.settings = settings
        self.z_steps = 0  # Table rises in positive Z; zero here is the PE12 trigger.

    def _z_move(self, steps, speed, stop_mask, stop_value, contact, bottom):
        if not steps:
            raise FactoryError('Z reference move requires a nonzero step count')
        row = yield dict(kind='z_contact_move', steps=steps, speed=speed,
                         stop_mask=stop_mask, stop_value=stop_value,
                         initial_raw=dict(nozzle_contact=contact, z_bottom=bottom))
        if not isinstance(row, dict):
            raise FactoryError('Missing Z motion result')
        sent, hit = row.get('sent_steps'), row.get('hit')
        if (type(sent) is not int or not 0 <= sent <= abs(steps)
                or type(hit) is not int or hit < 0 or hit & ~(Z_BOTTOM | NOZZLE_CONTACT)):
            raise FactoryError('Invalid Z motion result')
        reason = row.get('reason')
        if not ((reason == 'completed' and sent == abs(steps) and not hit)
                or (reason == 'stop_input' and hit and not hit & ~stop_mask)):
            raise FactoryError('Unverified Z motion result', motion_reason=reason, hit=hit)
        self.z_steps += sent if steps > 0 else -sent
        raw = row.get('end_raw')
        if (not isinstance(raw, dict) or type(raw.get('nozzle_contact')) is not int
                or raw['nozzle_contact'] not in (0, 1) or type(raw.get('z_bottom')) is not int
                or raw['z_bottom'] not in (0, 1)):
            raise FactoryError('Missing fresh Z motion inputs', hit=hit, sent_steps=sent)
        for bit, name in ((NOZZLE_CONTACT, 'nozzle_contact'), (Z_BOTTOM, 'z_bottom')):
            if hit & bit and raw[name] != int(bool(stop_value & bit)):
                raise FactoryError('Z input changed after latched stop', input=name,
                                   hit=hit, sent_steps=sent)
        return sent, hit, raw

    def _z_leave_bottom(self):
        yield from self._phase('Z_BOTTOM_RELEASE')
        sent, hit, raw = yield from self._z_move(Z_BOTTOM_RELEASE_STEPS, 1.,
                                                 Z_BOTTOM | NOZZLE_CONTACT, 0, 1, 1)
        if not sent or hit != Z_BOTTOM or raw['nozzle_contact'] != 1:
            raise FactoryError('z_bottom_did_not_release', hit=hit)
        _, hit, raw = yield from self._z_move(Z_BOTTOM_BACKOFF_STEPS, 1.,
                                              NOZZLE_CONTACT, 0, 1, 0)
        if hit or raw['nozzle_contact'] != 1 or raw['z_bottom'] != 0:
            raise FactoryError('z_contact_during_bottom_backoff', hit=hit)

    def _z_open_contact(self, lower):
        p = self.settings
        allowed = min(self.z_steps - lower, round(p['z_release_mm'] * Z_STEPS_PER_MM))
        if allowed < 1:
            raise FactoryError('z_contact_release_outside_bounds')
        sent, hit, raw = yield from self._z_move(-allowed, p['z_release_speed'],
                                                 Z_BOTTOM | NOZZLE_CONTACT,
                                                 Z_BOTTOM | NOZZLE_CONTACT, 0, 0)
        if not sent or hit != NOZZLE_CONTACT or raw['z_bottom'] != 0:
            raise FactoryError('z_contact_did_not_release', hit=hit, sent_steps=sent)

    def _z_reference(self):
        p = self.settings
        approach = round(p['z_approach_mm'] * Z_STEPS_PER_MM)
        span = round(p['z_probe_mm'] * Z_STEPS_PER_MM)
        clearance = self.z_steps
        if approach <= clearance:
            raise FactoryError('z_approach_below_bottom_clearance', clearance_steps=clearance)
        lower, upper = max(clearance, approach - span), approach + span

        yield from self._phase('Z_APPROACH')
        _, hit, raw = yield from self._z_move(approach - self.z_steps, p['z_approach_speed'],
                                              NOZZLE_CONTACT, 0, 1, 0)
        if raw['z_bottom'] != 0:
            raise FactoryError('z_bottom_reasserted', hit=hit)
        if hit or raw['nozzle_contact'] == 0:
            yield from self._phase('Z_CONTACT_RELEASE')
            yield from self._z_open_contact(clearance)
            raise FactoryError('z_contact_during_coarse_approach', release_steps=self.z_steps)

        yield from self._phase('Z_CONTACT_REFERENCE')
        best, misses = None, 0
        for cycle in range(1, p['z_max_cycles'] + 1):
            sent, hit, raw = yield from self._z_move(upper - self.z_steps, p['z_probe_speed'],
                                                     NOZZLE_CONTACT, 0, 1, 0)
            if raw['z_bottom'] != 0:
                raise FactoryError('z_bottom_reasserted', hit=hit)
            if hit != NOZZLE_CONTACT:
                if raw['nozzle_contact'] == 0:
                    yield from self._z_open_contact(lower)
                    raise FactoryError('z_contact_not_latched', release_steps=self.z_steps)
                raise FactoryError('z_contact_not_found', limit_steps=upper)
            yield from self._z_open_contact(lower)
            if not sent:
                raise FactoryError('z_contact_before_fine_step', release_steps=self.z_steps)
            if best is None or self.z_steps < best:
                best, misses = self.z_steps, 0
            else:
                misses += 1
                if misses == Z_STABLE_CYCLES:
                    break
        else:
            raise FactoryError('z_contact_cycle_limit', best_release_steps=best, cycles=cycle)

        if self.z_steps != best:
            # The minimum opening is lower than the last opening. Stop on PE12
            # or on recontact, rather than replaying steps into a changed setup.
            _, hit, raw = yield from self._z_move(best - self.z_steps, p['z_release_speed'],
                                                  Z_BOTTOM | NOZZLE_CONTACT, Z_BOTTOM, 1, 0)
            if raw['z_bottom'] == 0 and raw['nozzle_contact'] == 0:
                yield from self._z_open_contact(lower)
            if hit or raw['nozzle_contact'] != 1 or raw['z_bottom'] != 0:
                raise FactoryError('z_contact_best_release_changed', hit=hit)
        return dict(method='minimum_release_101_non_improvements',
                    release_steps_from_bottom=best,
                    release_mm_from_bottom=round(best / Z_STEPS_PER_MM, 6),
                    probe_lower_steps=lower, probe_upper_steps=upper,
                    cycles=cycle, non_improvements=misses, coordinate_saved=False)

    def run(self, operation='HOME_Z'):
        if operation not in Z_HOME_PROCEDURES:
            raise FactoryError('Unknown Z procedure ' + operation)
        if operation == 'HOME_Z' and self.settings['z_approach_mm'] is None:
            raise FactoryError('HOME_Z requires Z_APPROACH_MM measured from the PE12 trigger')
        yield from self.home(y_first=True)
        yield from self.heat()
        yield from self.select_tool()
        yield from self._phase('Z_POSITION_PAD')
        yield from self._module('z_position_pad', self._move(
            self.settings['z_probe_x'], self.settings['z_probe_y'], leg='z_contact_pad'))
        result = None
        if operation == 'HOME_Z':
            yield from self._module('z_bottom_release', self._z_leave_bottom())
            result = yield from self._module('z_contact_reference', self._z_reference())
        yield from self._phase('DONE')
        yield dict(kind='done', success=True, terminal=operation, cleaning_done=False,
                   z_home_result=result)
