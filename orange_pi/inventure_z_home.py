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
OEM_Z_BOTTOM_SPEED = 2_000_000. / 301 / Z_STEPS_PER_MM
OEM_Z_BOTTOM_RELEASE_SPEED = 2_000_000. / 2001 / Z_STEPS_PER_MM
OEM_Z_BACKOFF_SPEED = 20.

Z_HOME_PROCEDURES = {
    'Z_PREPARE': ('home', 'heat', 'select_tool', 'z_position_pad'),
    'HOME_Z': ('home', 'heat', 'select_tool', 'z_position_pad',
               'z_bottom_release', 'z_contact_reference'),
}


def z_home_parameters(gcmd, p, required=False):
    """Read SERVICE coordinates and bounds before sending any motion."""
    if p['tool'] != 0:
        raise FactoryError('Z_PREPARE and HOME_Z use TOOL=T0')
    oem = p['speed_profile'] == 'OEM'

    def number(name, default, low, high):
        value = gcmd.get(name, default)
        if value is None and default is None:
            return None
        try:
            result = float(value)
        except (ValueError, TypeError, OverflowError):
            result = math.nan
        if isinstance(value, bool) or not math.isfinite(result) or not low <= result <= high:
            raise FactoryError('%s must be %g..%g' % (name, low, high))
        return result

    def integer(name, default, low, high):
        value = number(name, default, low, high)
        if not value.is_integer():
            raise FactoryError(name + ' must be an integer')
        return int(value)

    bottom_max = number('HOME_Z_MAX', min(p['home_z_max'], 150.) if oem
                        else p['home_z_max'], 1., 300.)
    travel = min(250., bottom_max)
    approach = number('Z_APPROACH_MM', None, 2., travel)
    span = number('Z_PROBE_MM', 1., .02, 10.)
    if required and approach is None:
        raise FactoryError('HOME_Z requires Z_APPROACH_MM measured from the PE12 trigger')
    if required and gcmd.get('ACK', '') != Z_HOME_ACK:
        raise FactoryError('HOME_Z requires ACK=' + Z_HOME_ACK)
    if approach is not None and approach + span > travel:
        raise FactoryError('Z_APPROACH_MM plus Z_PROBE_MM exceeds the Z travel bound')
    approach_speed = number('Z_APPROACH_SPEED', OEM_Z_BACKOFF_SPEED if oem else 1.,
                            .1, OEM_Z_BACKOFF_SPEED if oem else 2.)
    if approach is not None and approach / approach_speed > 595.:
        raise FactoryError('Z coarse approach exceeds 595 seconds at this speed')
    stable = integer('Z_STABLE_CYCLES', Z_STABLE_CYCLES, 1, Z_STABLE_CYCLES)
    cycles = integer('Z_MAX_CYCLES', 512, stable + 1, 512)
    default_bottom_speed = (OEM_Z_BOTTOM_SPEED if oem and gcmd.get('Z_SPEED', None) is None
                            else p['z_speed'])
    bottom_speed = number('Z_BOTTOM_SPEED', default_bottom_speed, .1,
                          OEM_Z_BOTTOM_SPEED if oem else 5.)
    if bottom_max / bottom_speed > 595.:
        raise FactoryError('Z bottom seek exceeds 595 seconds at this speed')
    return dict(
        z_approach_mm=approach,
        z_probe_x=number('Z_PROBE_X', 82.8, p['min_x'], p['max_x']),
        z_probe_y=number('Z_PROBE_Y', 68., p['min_y'], p['max_y']),
        z_probe_mm=span,
        z_release_mm=number('Z_RELEASE_MM', 1., .02, 2.),
        z_bottom_max_mm=bottom_max,
        z_bottom_speed=bottom_speed,
        z_bottom_release_speed=number('Z_BOTTOM_RELEASE_SPEED',
                                      OEM_Z_BOTTOM_RELEASE_SPEED if oem else 1.,
                                      .1, OEM_Z_BOTTOM_RELEASE_SPEED if oem else 1.),
        z_bottom_release_steps=5000 if oem else Z_BOTTOM_RELEASE_STEPS,
        z_backoff_speed=number('Z_BACKOFF_SPEED', OEM_Z_BACKOFF_SPEED if oem else 1.,
                               .1, OEM_Z_BACKOFF_SPEED if oem else 1.),
        z_approach_speed=approach_speed,
        z_probe_speed=number('Z_PROBE_SPEED', .2, .05, 1.),
        z_release_speed=number('Z_RELEASE_SPEED', .2, .05, 1.),
        z_stable_cycles=stable,
        z_max_cycles=cycles,
        z_transient_backoff_mm=number('Z_TRANSIENT_BACKOFF_MM', .2, .05, .5),
        z_transient_retries=integer('Z_TRANSIENT_RETRIES', 3, 0, 10),
        z_transient_total=integer('Z_TRANSIENT_TOTAL', 10, 0, 30),
    )


class ZHomeProgram(FactoryProgram):
    def __init__(self, p, settings):
        super().__init__(p)
        self.settings = settings
        # Keep the shared XY/home sequence and validated factory settings.
        # Replace only the lower seek's audited speed and travel for this program.
        self.p = dict(self.p, z_speed=settings['z_bottom_speed'],
                      home_z_max=settings['z_bottom_max_mm'])
        self.z_steps = 0  # Table rises in positive Z; zero here is the PE12 trigger.

    def _z_move(self, steps, speed, stop_mask, stop_value, contact, bottom,
                allow_contact_transient=False):
        if not steps:
            raise FactoryError('Z reference move requires a nonzero step count')
        initial_raw = dict(z_bottom=bottom)
        if contact is not None:
            initial_raw['nozzle_contact'] = contact
        row = yield dict(kind='z_contact_move', steps=steps, speed=speed,
                         stop_mask=stop_mask, stop_value=stop_value,
                         initial_raw=initial_raw)
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
                if allow_contact_transient and hit == NOZZLE_CONTACT:
                    continue
                raise FactoryError('Z input changed after latched stop', input=name,
                                   hit=hit, sent_steps=sent)
        return sent, hit, raw

    def _z_leave_bottom(self):
        p = self.settings
        yield from self._phase('Z_BOTTOM_RELEASE')
        sent, hit, raw = yield from self._z_move(p['z_bottom_release_steps'],
                                                 p['z_bottom_release_speed'],
                                                 Z_BOTTOM | NOZZLE_CONTACT, 0, 1, 1)
        if not sent or hit != Z_BOTTOM or raw['nozzle_contact'] != 1:
            raise FactoryError('z_bottom_did_not_release', hit=hit)
        _, hit, raw = yield from self._z_move(Z_BOTTOM_BACKOFF_STEPS, p['z_backoff_speed'],
                                              NOZZLE_CONTACT, 0, 1, 0)
        if hit or raw['nozzle_contact'] != 1 or raw['z_bottom'] != 0:
            raise FactoryError('z_contact_during_bottom_backoff', hit=hit)

    def _z_open_contact(self, lower, progress=None):
        p = self.settings
        start = self.z_steps
        limit = max(lower, start - round(p['z_release_mm'] * Z_STEPS_PER_MM))
        retry = progress is not None
        while self.z_steps > limit:
            # While descending, either initial PC14 level is safe. An open
            # input still stops the MCU even before its first step.
            sent, hit, raw = yield from self._z_move(
                limit - self.z_steps, p['z_release_speed'], Z_BOTTOM | NOZZLE_CONTACT,
                Z_BOTTOM | NOZZLE_CONTACT, None if retry else 0, 0,
                allow_contact_transient=retry)
            if hit != NOZZLE_CONTACT or raw['z_bottom'] != 0:
                raise FactoryError('z_contact_did_not_release', hit=hit, sent_steps=sent)
            if raw['nozzle_contact'] == 1 and self.z_steps < start:
                return True
            if not retry:
                raise FactoryError('z_contact_did_not_release', hit=hit, sent_steps=sent)
            yield from self._z_record_transient(progress, 'transient_openings')
            if raw['nozzle_contact'] == 1:
                # No downward step was measured. Restart from below this point.
                yield from self._module('z_contact_retry', self._z_retry_contact(limit))
                return False
        raise FactoryError('z_contact_release_outside_bounds')

    def _z_approach(self, approach, clearance):
        p = self.settings
        if approach <= clearance:
            raise FactoryError('z_approach_below_bottom_clearance', clearance_steps=clearance)
        yield from self._phase('Z_APPROACH')
        _, hit, raw = yield from self._z_move(approach - self.z_steps, p['z_approach_speed'],
                                              NOZZLE_CONTACT, 0, 1, 0)
        if raw['z_bottom'] != 0:
            raise FactoryError('z_bottom_reasserted', hit=hit)
        if hit or raw['nozzle_contact'] == 0:
            yield from self._phase('Z_CONTACT_RELEASE')
            yield from self._module('z_contact_open', self._z_open_contact(clearance))
            raise FactoryError('z_contact_during_coarse_approach', release_steps=self.z_steps)

    def _z_close_contact(self, lower, upper):
        remaining = upper - self.z_steps
        if remaining < 1:
            raise FactoryError('z_contact_not_found', limit_steps=upper)
        sent, hit, raw = yield from self._z_move(remaining, self.settings['z_probe_speed'],
                                                 NOZZLE_CONTACT, 0, 1, 0,
                                                 allow_contact_transient=True)
        if raw['z_bottom'] != 0:
            raise FactoryError('z_bottom_reasserted', hit=hit)
        if hit == NOZZLE_CONTACT:
            return sent, raw['nozzle_contact'] == 0
        if raw['nozzle_contact'] == 0:
            yield from self._module('z_contact_open', self._z_open_contact(lower))
            raise FactoryError('z_contact_not_latched', release_steps=self.z_steps)
        raise FactoryError('z_contact_not_found', limit_steps=upper)

    def _z_down_open(self, steps):
        _, hit, raw = yield from self._z_move(-steps, self.settings['z_release_speed'],
                                              Z_BOTTOM | NOZZLE_CONTACT, Z_BOTTOM, 1, 0)
        return hit, raw

    def _z_retry_contact(self, lower):
        backoff = round(self.settings['z_transient_backoff_mm'] * Z_STEPS_PER_MM)
        allowed = min(self.z_steps - lower, backoff)
        if allowed < 1:
            raise FactoryError('z_contact_retry_outside_bounds', position_steps=self.z_steps)
        hit, raw = yield from self._z_down_open(allowed)
        if hit or raw['nozzle_contact'] != 1 or raw['z_bottom'] != 0:
            raise FactoryError('z_contact_changed_during_retry_backoff', hit=hit,
                               position_steps=self.z_steps)

    def _z_settle(self, lower, best):
        if self.z_steps != best:
            # The minimum opening is lower than the last opening. Stop on PE12
            # or on recontact, rather than replaying steps into a changed setup.
            hit, raw = yield from self._z_down_open(self.z_steps - best)
            if raw['z_bottom'] == 0 and raw['nozzle_contact'] == 0:
                yield from self._module('z_contact_open', self._z_open_contact(lower))
            if hit or raw['nozzle_contact'] != 1 or raw['z_bottom'] != 0:
                raise FactoryError('z_contact_best_release_changed', hit=hit)

    def _z_repeat_contact(self, lower, upper):
        p = self.settings
        progress = dict(qualified_cycles=0, non_improvements=0, transient_closures=0,
                        transient_openings=0, consecutive_transients=0, best_release_steps=None)
        while progress['qualified_cycles'] < p['z_max_cycles']:
            sent, closed = yield from self._module('z_contact_close', self._z_close_contact(lower, upper))
            if not closed:
                yield from self._z_record_transient(progress, 'transient_closures')
                yield from self._module('z_contact_retry', self._z_retry_contact(lower))
                continue
            opened = yield from self._module('z_contact_open', self._z_open_contact(lower, progress))
            if not opened:
                continue
            if not sent:
                raise FactoryError('z_contact_before_fine_step', release_steps=self.z_steps)
            progress['consecutive_transients'] = 0
            progress['qualified_cycles'] += 1
            best = progress['best_release_steps']
            if best is None or self.z_steps < best:
                progress.update(best_release_steps=self.z_steps, non_improvements=0)
            else:
                progress['non_improvements'] += 1
            yield self._z_progress(progress)
            if progress['non_improvements'] == p['z_stable_cycles']:
                return progress
        raise FactoryError('z_contact_cycle_limit', cycles=progress['qualified_cycles'], **progress)

    def _z_record_transient(self, progress, field):
        progress[field] += 1
        progress['consecutive_transients'] += 1
        progress['non_improvements'] = 0
        yield self._z_progress(progress)
        total = progress['transient_closures'] + progress['transient_openings']
        if (progress['consecutive_transients'] > self.settings['z_transient_retries']
                or total > self.settings['z_transient_total']):
            raise FactoryError('z_contact_retry_limit', position_steps=self.z_steps, **progress)

    def _z_progress(self, progress):
        return dict(kind='z_progress', position_steps=self.z_steps, **progress)

    def _z_reference(self):
        p = self.settings
        approach = round(p['z_approach_mm'] * Z_STEPS_PER_MM)
        span = round(p['z_probe_mm'] * Z_STEPS_PER_MM)
        clearance = self.z_steps
        lower, upper = max(clearance, approach - span), approach + span
        yield from self._module('z_contact_approach', self._z_approach(approach, clearance))
        yield from self._phase('Z_CONTACT_REFERENCE')
        progress = yield from self._module(
            'z_contact_repeat', self._z_repeat_contact(lower, upper))
        best = progress['best_release_steps']
        yield from self._module('z_contact_settle', self._z_settle(lower, best))
        return dict(method='minimum_release_%d_non_improvements' % p['z_stable_cycles'],
                    release_steps_from_bottom=best,
                    release_mm_from_bottom=round(best / Z_STEPS_PER_MM, 6),
                    probe_lower_steps=lower, probe_upper_steps=upper,
                    cycles=progress['qualified_cycles'], non_improvements=progress['non_improvements'],
                    transient_closures=progress['transient_closures'],
                    transient_openings=progress['transient_openings'], coordinate_saved=False)

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
