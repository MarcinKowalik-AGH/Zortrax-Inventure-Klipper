# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in native CoreXY T0 reference using an STM32 endstop per motion leg."""
from contextlib import nullcontext
import json
import math

from . import homing
from .inventure_factory_core import FactoryError
from .inventure_z_home import Z_HOME_ACK, ZReleaseTracker, Z_STEPS_PER_MM


GUARD_COMMAND = 'inventure_native_z_guard action=%c owner=%u'
GUARD_RESPONSE = 'inventure_native_z_guard_state owner=%u active=%c reject=%c'


class InventureNativeZ:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.board = self.printer.load_object(config, 'inventure_mcu')
        if config.getsection('printer').get('kinematics') != 'corexy':
            raise config.error('Inventure native Z requires a configured CoreXY printer')
        if (config.has_section('inventure_contact')
                and config.getsection('inventure_contact').getboolean('enabled', False)):
            raise config.error('PC14 cannot be shared with enabled [inventure_contact]')
        if config.getsection('stepper_z').get('enable_pin') not in (
                'PE10', '%s:PE10' % self.board.mcu_name):
            raise config.error('Inventure native Z requires active-high PE10 enable')
        if not self.board.allow_actuators:
            raise config.error('Inventure native Z requires allow_actuators on the native profile')
        self.pad_x = config.getfloat('pad_x')
        self.pad_y = config.getfloat('pad_y')
        self.bottom_position = config.getfloat('bottom_position', minval=2., maxval=250.)
        self.bottom_release = config.getfloat('bottom_release', 5., above=1., maxval=6.3)
        self.coarse_speed = config.getfloat('coarse_speed', 2., above=0., maxval=20.)
        self.fine_speed = config.getfloat('fine_speed', .312, minval=.1, maxval=1.)
        self.target = config.getfloat('nozzle_temperature', 200., minval=180., maxval=240.)
        self.repeat_count = config.getint('repeat_count', 101, minval=1, maxval=101)
        self.endstop = self.printer.lookup_object('pins').setup_pin(
            'endstop', '^!%s:PC14' % self.board.mcu_name)
        self.toolhead = None
        self.kin = None
        self.bottom = None
        self.running = False
        self.guard = None
        self.guard_owner = None
        self.last_result = None
        self.z_enable = None
        self.interrupted = False
        self.printer.register_event_handler('klippy:mcu_identify', self._identify)
        self.printer.register_event_handler('klippy:connect', self._connect)
        self.printer.register_event_handler('klippy:shutdown', self._lost_enable)
        self.printer.register_event_handler('inventure:native_motion_lost', self._lost_enable)
        self.board.mcu.register_config_callback(self._build_config)
        self.printer.lookup_object('gcode').register_command(
            'INVENTURE_NATIVE_HOME_Z', self.cmd_home,
            desc='Establish native Z at the measured T0 release from the center pad')

    def _build_config(self):
        if int(self.board.mcu.get_constants().get('INVENTURE_NATIVE_Z_GUARD_ABI', 0)) != 1:
            raise self.printer.config_error('Inventure native Z requires local tray guard ABI 1')
        self.guard = self.board.mcu.lookup_query_command(GUARD_COMMAND, GUARD_RESPONSE)

    def _verify_guard(self, action, token, active):
        row = self.board._thermal_query_send(self.guard, [action, token], start=action == 1)
        self.board._control_validate(row, dict(owner=(0, 0xffffffff), active=(0, 1), reject=(0, 4)))
        if row['reject'] or row['owner'] != token or row['active'] != int(active):
            raise FactoryError('MCU did not confirm native Z tray guard ownership')

    def _identify(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.kin = self.toolhead.get_kinematics()
        rail = self.kin.rails[2]
        steppers = rail.get_steppers()
        endstops = rail.get_endstops()
        if len(steppers) != 1 or len(endstops) != 1:
            raise self.printer.config_error('Inventure native Z requires one Z stepper and PE12')
        stepper = steppers[0]
        bottom = endstops[0][0]
        low, high = rail.get_range()
        if (stepper.get_mcu() is not self.board.mcu or bottom.get_mcu() is not self.board.mcu
                or stepper._step_pin != 'PB8' or stepper._invert_step
                or stepper._dir_pin != 'PE7' or not stepper._invert_dir
                or bottom._pin != 'PE12' or bottom._invert
                or tuple(bottom.get_steppers()) != tuple(steppers) or not low <= 0. < high
                or not math.isclose(1. / stepper.get_step_dist(), Z_STEPS_PER_MM, abs_tol=.01)):
            raise self.printer.config_error(
                'Native Z requires PB8 STEP, !PE7 DIR, active-high PE12 and 802 steps/mm')
        self.endstop.add_stepper(stepper)
        self.bottom = bottom
        self.stepper = stepper
        self.stepper_name = stepper.get_name()

    def _connect(self):
        if self.stepper.get_pulse_duration()[1]:
            raise self.printer.config_error('Inventure native Z requires rising-edge STEP')
        stepper_enable = self.printer.lookup_object('stepper_enable')
        self.z_enable = stepper_enable.lookup_enable(self.stepper_name)
        self.z_enable.register_state_callback(self._enable_changed)

    def _enable_changed(self, print_time, enabled):
        if not enabled:
            self._lost_enable()

    def _lost_enable(self):
        if self.running:
            self.interrupted = True
        if self.last_result is not None and self.last_result.get('reference_established'):
            self.last_result = dict(reference_established=False, coordinate_saved=False, terminal='invalidated',
                                    reason='Native Z drive disabled or MCU shutdown')
            operations = self.printer.lookup_object('inventure_operations', None)
            if operations is not None:
                operations.invalidate('xyz', 'native_z_reference_lost')
            elif self.kin is not None:
                self.kin.clear_homing_state('xyz')

    def _check(self, bottom_expected=None):
        if self.interrupted or self.reactor.monotonic() >= self.deadline or self.printer.is_shutdown():
            raise FactoryError('Native Inventure Z reference expired or was interrupted')
        state = self.board._control_query()
        if state['mode'] != 'NATIVE' or not state['armed'] or state['fault'] or state['latched']:
            raise FactoryError('Inventure native motion must remain armed')
        if self.guard_owner is not None:
            self._verify_guard(0, self.guard_owner, True)
        raw = self.board._z_home_inputs({})
        if bottom_expected is not None and raw['z_bottom'] != bottom_expected:
            raise FactoryError('Unexpected PE12 state during native Z reference')
        if self.toolhead.get_extruder().get_name() != 'extruder':
            raise FactoryError('Native Z reference uses the physically selected T0')
        temperature, valid = self.board.panel_temperature(0, self.reactor.monotonic())
        if (valid != 'valid' or temperature is None
                or not self.target - 10. <= temperature <= self.target + 8.):
            raise FactoryError('T0 left the native Z temperature band')

    def _edge(self, pin, target, speed, triggered, bottom_expected, check_triggered):
        self._check(bottom_expected)
        position = self.toolhead.get_position()
        position[2] = target
        move = homing.HomingMove(self.printer, [(pin, 'inventure_native_z')], self.toolhead)
        measured = move.homing_move(position, speed, probe_pos=True, triggered=triggered,
                                    check_triggered=check_triggered)
        self.toolhead.wait_moves()
        self._check()
        return round(measured[2] * Z_STEPS_PER_MM)

    def _move(self, target, speed):
        self._check(0)
        position = self.toolhead.get_position()
        position[2] = target / Z_STEPS_PER_MM
        self.toolhead.move(position, speed)
        self.toolhead.wait_moves()
        self._check(0)

    def _current_steps(self):
        return round(self.toolhead.get_position()[2] * Z_STEPS_PER_MM)

    def _is_triggered(self, pin):
        return bool(pin.query_endstop(self.toolhead.get_last_move_time()))

    def _matching_edge(self, triggered, limit, speed):
        # A bounded native leg uses the hardware pin for its stop. Limiting
        # each dispatched leg also bounds travel between fresh tray checks.
        direction = -1 if triggered else 1
        current = self._current_steps()
        while direction * (limit - current) > 0:
            self._check(0)
            if self._is_triggered(self.endstop) == triggered:
                return current
            remaining = abs(limit - current)
            target = current + direction * min(remaining, Z_STEPS_PER_MM)
            require_trigger = (not triggered and target == limit)
            measured = self._edge(self.endstop, target / Z_STEPS_PER_MM,
                                  speed, triggered, 0, require_trigger)
            actual = self._current_steps()
            if not (0 <= direction * (measured - current)
                    <= direction * (actual - current) <= abs(target - current)):
                raise FactoryError('Native Z trigger or halt fell outside its own move')
            if actual != target or measured != target:
                return measured
            current = actual
        return None

    def _reference(self, start, low, high, span):
        tracker = ZReleaseTracker(span, self.repeat_count, 512)
        basis = 'stable_repetitions'
        while True:
            closed = self._matching_edge(True, low, self.fine_speed)
            if closed is None:
                if tracker.best is None:
                    raise FactoryError('Native Z never reached PC14 within its span')
                basis = 'upper_bound_with_best_release'
                break
            opened = self._matching_edge(False, high, self.fine_speed)
            if opened is None:
                self._check(0)
                if self._is_triggered(self.endstop):
                    raise FactoryError('Native Z did not release PC14 within its span')
                opened = high
            if tracker.add(start - opened):
                break
        best = start - tracker.best
        if self._current_steps() > best:
            if self._matching_edge(True, best, self.fine_speed) is not None:
                raise FactoryError('PC14 closed on the return to the selected native reference')
            if self._current_steps() != best:
                raise FactoryError('Native Z did not return to its recorded best release')
        while self._current_steps() != best:
            self._move(min(best, self._current_steps() + Z_STEPS_PER_MM), self.fine_speed)
        if self._is_triggered(self.endstop):
            raise FactoryError('PC14 stayed closed at the selected native reference')
        self._check(0)
        return best, tracker, basis

    def _establish(self, approach_mm, span_mm):
        start_pos = self.toolhead.get_position()
        start_pos[2] = self.bottom_position
        bottom_steps = round(self.bottom_position * Z_STEPS_PER_MM)
        start = bottom_steps - round(approach_mm * Z_STEPS_PER_MM)
        span = round(span_mm * Z_STEPS_PER_MM)
        low, high = start - span, start + span
        native_low, native_high = self.kin.rails[2].get_range()
        if (low < round(native_low * Z_STEPS_PER_MM)
                or bottom_steps > round(native_high * Z_STEPS_PER_MM)
                or high >= bottom_steps - round((self.bottom_release + 1.) * Z_STEPS_PER_MM)):
            raise FactoryError('Native Z probe and clearance exceed its configured travel')
        if not self._is_triggered(self.bottom) or self._is_triggered(self.endstop):
            raise FactoryError('Start with PE12 asserted and PC14 open')
        self.started = True
        self.toolhead.set_position(start_pos, homing_axes='z')
        self._edge(self.bottom, self.bottom_position - self.bottom_release,
                   min(self.coarse_speed, 1.25), False, 1, True)
        if self._is_triggered(self.bottom):
            raise FactoryError('PE12 did not release during native Z clearance')
        self._move(self._current_steps() - Z_STEPS_PER_MM, self.coarse_speed)
        if (self._matching_edge(True, start, self.coarse_speed) is not None
                or self._is_triggered(self.endstop)):
            raise FactoryError('PC14 closed during the guarded coarse approach')
        if self._current_steps() != start:
            raise FactoryError('Native Z did not reach the start of refinement')
        best, tracker, basis = self._reference(start, low, high, span)
        return dict(reference_established=True, coordinate_saved=True,
                    persistence='current_klipper_session', terminal='done',
                    release_steps_from_bottom=bottom_steps - best,
                    release_mm_from_bottom=round((bottom_steps - best) / Z_STEPS_PER_MM, 6),
                    cycles=tracker.cycles, non_improvements=tracker.non_improvements,
                    completion_basis=basis)

    def cmd_home(self, gcmd):
        if self.running or self.bottom is None or self.guard is None or self.z_enable is None:
            raise gcmd.error('Native Inventure Z reference already running or unavailable')
        if gcmd.get('ACK', '') != Z_HOME_ACK:
            raise gcmd.error('Native Inventure Z requires ACK=' + Z_HOME_ACK)
        approach = gcmd.get_float('Z_APPROACH_MM', minval=2., maxval=self.bottom_position)
        span = gcmd.get_float('Z_PROBE_MM', 10., minval=.02, maxval=10.)
        now = self.reactor.monotonic()
        status = self.kin.get_status(now)
        position = self.toolhead.get_position()
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is not None and print_stats.get_status(now)['state'] in ('printing', 'paused'):
            raise gcmd.error('Native Z reference requires an idle printer')
        if ('x' not in status['homed_axes'] or 'y' not in status['homed_axes']
                or not math.isclose(position[0], self.pad_x, rel_tol=0., abs_tol=.05)
                or not math.isclose(position[1], self.pad_y, rel_tol=0., abs_tol=.05)):
            raise gcmd.error('Home native X/Y and position physically selected T0 over the configured pad')
        gm = self.printer.lookup_object('gcode_move')
        if gm.move_transform is not None:
            raise gcmd.error('Native Inventure Z requires an untransformed working frame')
        if not self.z_enable.is_motor_enabled():
            raise gcmd.error('Keep the native Z drive energized and physically verify PE12 first')
        operations = self.printer.lookup_object('inventure_operations', None)
        owner = nullcontext() if operations is None else operations.procedure('homing')
        self.running = True
        self.interrupted = False
        self.started = False
        guard_attempted = False
        self.deadline = now + 600.
        self.last_result = None
        try:
            with owner:
                self.toolhead.wait_moves()
                self._check(1)
                token = self.board._control_next_token()
                guard_attempted = True
                self._verify_guard(1, token, True)
                self.guard_owner = token
                result = self._establish(approach, span)
                self._check(0)
                if self._is_triggered(self.endstop) or not self.z_enable.is_motor_enabled():
                    raise FactoryError('Native Z lost the selected reference before assigning zero')
                position = self.toolhead.get_position()
                position[2] = 0.
                self.toolhead.set_position(position, homing_axes='z')
                gm.base_position[2] = gm.homing_position[2] = 0.
                gm.reset_last_position()
                self._verify_guard(2, token, False)
                self.guard_owner = None
                if operations is not None:
                    operations.state.homed('z')
            self.last_result = result
        except Exception as exc:
            if guard_attempted:
                self.board._control_stop_outputs()
            if self.started or guard_attempted:
                if operations is None:
                    self.kin.clear_homing_state('xyz')
                else:
                    operations.invalidate('xyz', 'native_z_reference_failed')
            self.last_result = dict(reference_established=False, coordinate_saved=False,
                                    terminal='failed', reason=str(exc))
            raise gcmd.error(str(exc)) from exc
        finally:
            self.guard_owner = None
            self.running = False
        gcmd.respond_info('V9_NATIVE_Z ' + json.dumps(self.last_result, sort_keys=True))

    def get_status(self, eventtime):
        return dict(running=self.running, last_result=self.last_result)


def load_config(config):
    return InventureNativeZ(config)
