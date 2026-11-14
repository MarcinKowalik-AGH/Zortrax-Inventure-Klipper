# SPDX-License-Identifier: GPL-3.0-or-later
"""Native CoreXY T0 reference with a bounded, local STM32 contact cycle."""
from contextlib import nullcontext
import json
import logging
import math

from . import homing
from .inventure_factory_core import FactoryError
from .inventure_z_home import (
    OEM_Z_BACKOFF_SPEED, OEM_Z_BOTTOM_RELEASE_SPEED, OEM_Z_CONTACT_SPEED,
    Z_HOME_ACK, Z_MAX_CYCLES, Z_STABLE_CYCLES, Z_STEPS_PER_MM, ZReleaseTracker,
)


GUARD_COMMAND = 'inventure_native_z_guard action=%c owner=%u'
GUARD_RESPONSE = 'inventure_native_z_guard_state owner=%u active=%c reject=%c'
REFINE_COMMAND = ('inventure_native_z_refine action=%c owner=%u token=%u span=%hu '
                  'interval_us=%hu min_c=%hi max_c=%hi')
REFINE_RESPONSE = ('inventure_native_z_refine_state owner=%u token=%u active=%c status=%c '
                   'reject=%c net_steps=%i best_steps=%i cycles=%u non_improvements=%c '
                   'up_steps=%u down_steps=%u')
REFINE_STATUS = ('running', 'done', 'no contact', 'no release', 'guard dropped',
                 'cycle or time limit', 'stopped')
PHASE_MARGIN_S = 10.
PROGRESS_INTERVAL_S = 5.


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
        self.bottom_release = config.getfloat('bottom_release', 5000. / Z_STEPS_PER_MM,
                                              above=1., maxval=6.3)
        self.coarse_speed = config.getfloat('coarse_speed', OEM_Z_BACKOFF_SPEED,
                                            above=0., maxval=OEM_Z_BACKOFF_SPEED)
        self.fine_speed = config.getfloat('fine_speed', OEM_Z_CONTACT_SPEED,
                                          minval=.1, maxval=.997)
        self.target = config.getfloat('nozzle_temperature', 200., minval=180., maxval=240.)
        self.repeat_count = config.getint('repeat_count', 101, minval=1, maxval=101)
        self.xy_speed_factor = config.getfloat('xy_speed_factor', 1.5, minval=.1, maxval=2.)
        self.xy_clearance = config.getfloat('xy_clearance', 10., above=0.)
        self.pad_speed = config.getfloat('pad_speed', 80., minval=1., maxval=150.)
        self.park_z = config.getfloat('park_z', 10., above=0.)
        self.park_speed = config.getfloat('park_speed', 5., minval=.1, maxval=20.)
        self.heat_timeout = config.getfloat('heat_timeout', 300., minval=30., maxval=600.)
        self.endstop = self.printer.lookup_object('pins').setup_pin(
            'endstop', '^!%s:PC14' % self.board.mcu_name)
        self.toolhead = None
        self.kin = None
        self.bottom = None
        self.running = False
        self.guard = None
        self.refine = None
        self.guard_owner = None
        self.last_result = None
        self.z_enable = None
        self.interrupted = False
        self.printer.register_event_handler('klippy:mcu_identify', self._identify)
        self.printer.register_event_handler('klippy:connect', self._connect)
        self.printer.register_event_handler('klippy:shutdown', self._shutdown)
        self.printer.register_event_handler('inventure:native_motion_lost', self._lost_enable)
        self.printer.register_event_handler('homing:home_rails_begin', self._home_rails_begin)
        self.board.mcu.register_config_callback(self._build_config)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command(
            'INVENTURE_NATIVE_HOME_Z', self.cmd_home,
            desc='Establish native Z at the measured T0 release from the center pad')
        gcode.register_command(
            'INVENTURE_NATIVE_HOME', self.cmd_home_axes,
            desc='Run the Inventure X, Y, XY, heated Z or heated XYZ homing procedure')

    def _build_config(self):
        constants = self.board.mcu.get_constants()
        if int(constants.get('INVENTURE_NATIVE_Z_GUARD_ABI', 0)) != 1:
            raise self.printer.config_error('Inventure native Z requires local tray guard ABI 1')
        self.guard = self.board.mcu.lookup_query_command(GUARD_COMMAND, GUARD_RESPONSE)
        if int(constants.get('INVENTURE_NATIVE_Z_REFINE_ABI', 0)) == 1:
            self.refine = self.board.mcu.lookup_query_command(REFINE_COMMAND, REFINE_RESPONSE)

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

    def _home_rails_begin(self, homing_state, rails):
        if self.kin is not None and self.kin.rails[2] in rails:
            self.last_result = None

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

    def _shutdown(self):
        if self.running:
            # Klipper's trsync shutdown handler completes a pending trigger
            # wait. Release serial ACK waiters as well, after emergency STOP
            # has been enqueued by the MCU's earlier shutdown callback.
            self.board.mcu._serial.abort_wait_acks()
        self._lost_enable()

    def _stage(self, name, timeout=PHASE_MARGIN_S):
        self.active_phase = name
        self.reactor.update_timer(self.watchdog_timer,
                                  min(self.deadline, self.reactor.monotonic() + timeout))

    def _watchdog(self, eventtime):
        if self.running and not self.printer.is_shutdown():
            self.timeout_reason = 'Inventure native Z timeout during ' + self.active_phase
            self.printer.invoke_shutdown(self.timeout_reason)
        return self.reactor.NEVER

    def _motion_stage(self, name, target, speed):
        speed = min(speed, getattr(self.kin, 'max_z_velocity', speed))
        distance = abs(target - self.toolhead.get_position()[2])
        accel = getattr(self.kin, 'max_z_accel', 20.)
        self._stage(name, distance / speed + speed / accel + PHASE_MARGIN_S)

    def _require_native(self):
        state = self.board._control_query()
        if state['mode'] != 'NATIVE' or not state['armed'] or state['fault'] or state['latched']:
            raise FactoryError('Select INVENTURE_CONTROL MODE=NATIVE and clear any native fault')

    def _require_idle(self, now):
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is not None and print_stats.get_status(now)['state'] in ('printing', 'paused'):
            raise FactoryError('Inventure homing requires an idle printer')

    def _check(self, bottom_expected=None):
        if self.interrupted or self.reactor.monotonic() >= self.deadline or self.printer.is_shutdown():
            raise FactoryError('Native Inventure Z reference expired or was interrupted')
        self._require_native()
        if self.guard_owner is not None:
            self._verify_guard(0, self.guard_owner, True)
        raw = self.board._z_home_inputs({})
        if bottom_expected is not None and raw['z_bottom'] != bottom_expected:
            raise FactoryError('Unexpected PE12 state during native Z reference')
        if self.toolhead.get_extruder().get_name() != 'extruder':
            raise FactoryError('Native Z reference uses the physically selected T0')
        temperature, valid = self.board.panel_temperature(0, self.reactor.monotonic())
        if (valid != 'valid' or temperature is None
                or not self.reference_target - 10. <= temperature <= self.reference_target + 8.):
            raise FactoryError('T0 left the native Z temperature band')

    def _edge(self, pin, target, speed, triggered, bottom_expected, check_triggered, phase):
        self._motion_stage(phase, target, speed)
        self._check(bottom_expected)
        position = self.toolhead.get_position()
        position[2] = target
        move = homing.HomingMove(self.printer, [(pin, phase)], self.toolhead)
        measured = move.homing_move(position, speed, probe_pos=True, triggered=triggered,
                                    check_triggered=check_triggered)
        self.toolhead.wait_moves()
        self._check()
        return round(measured[2] * Z_STEPS_PER_MM)

    def _move(self, target, speed):
        self._motion_stage('return to measured release', target / Z_STEPS_PER_MM, speed)
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

    def _matching_edge(self, triggered, limit, speed, phase):
        # One native move keeps the local PC14 stop armed for the entire leg.
        # The MCU independently supervises tray presence while it is queued.
        direction = -1 if triggered else 1
        current = self._current_steps()
        if direction * (limit - current) <= 0:
            return None
        self._stage(phase + ' input check')
        self._check(0)
        if self._is_triggered(self.endstop) == triggered:
            return current
        measured = self._edge(self.endstop, limit / Z_STEPS_PER_MM,
                              speed, triggered, 0, not triggered, phase)
        actual = self._current_steps()
        if not (0 <= direction * (measured - current)
                <= direction * (actual - current) <= abs(limit - current)):
            raise FactoryError(phase + ': Z trigger or halt fell outside its own move')
        if (not triggered or actual != limit or measured != limit
                or self._is_triggered(self.endstop)):
            return measured
        return None

    def _reference(self, start, low, high, span, repeat_count, probe_cycles, report):
        tracker = ZReleaseTracker(span, repeat_count, 512)
        next_report = self.reactor.monotonic() + PROGRESS_INTERVAL_S
        basis = 'stable_repetitions'
        while True:
            closed = self._matching_edge(True, low, self.fine_speed, 'PC14 contact')
            if closed is None:
                if tracker.best is None or probe_cycles is not None:
                    raise FactoryError('Native Z never reached PC14 within its span')
                basis = 'upper_bound_with_best_release'
                break
            opened = self._matching_edge(False, high, self.fine_speed, 'PC14 release')
            if opened is None:
                self._check(0)
                if self._is_triggered(self.endstop):
                    raise FactoryError('Native Z did not release PC14 within its span')
                opened = high
            stable = tracker.add(start - opened)
            complete = tracker.cycles == probe_cycles if probe_cycles is not None else stable
            now = self.reactor.monotonic()
            if complete or now >= next_report:
                report(dict(stage='PC14 refinement', cycles=tracker.cycles,
                            non_improvements=tracker.non_improvements,
                            required_non_improvements=repeat_count if probe_cycles is None else None,
                            requested_cycles=probe_cycles))
                next_report = now + PROGRESS_INTERVAL_S
            if complete:
                if probe_cycles is not None:
                    basis = 'requested_contact_cycles'
                break
        best = start - tracker.best
        if self._current_steps() > best:
            if self._matching_edge(True, best, self.fine_speed, 'PC14 return') is not None:
                raise FactoryError('PC14 closed on the return to the selected native reference')
            if self._current_steps() != best:
                raise FactoryError('Native Z did not return to its recorded best release')
        elif self._current_steps() < best:
            self._move(best, self.fine_speed)
        if self._is_triggered(self.endstop):
            raise FactoryError('PC14 stayed closed at the selected native reference')
        self._check(0)
        return best, tracker, basis

    def _native_step_position(self):
        raw = self.stepper._get_position_cmd.send([self.stepper.get_oid()])['pos']
        position = -raw if self.stepper._invert_dir else raw
        if position != self.stepper.get_mcu_position():
            raise FactoryError('Native Z stepper position does not match its queued step count')
        return position

    def _refine_on_mcu(self, start, span, report):
        self._stage('STM32 PC14 refinement handoff')
        self._check(0)
        native_steps = self._native_step_position()
        token = self.board._control_next_token()
        interval = round(1_000_000. / (self.fine_speed * Z_STEPS_PER_MM))
        args = [self.guard_owner, token, span, interval, math.ceil(self.reference_target - 10.),
                math.ceil(self.reference_target + 8.)]
        limits = dict(owner=(1, 0xffffffff), token=(1, 0xffffffff), active=(0, 1),
                      status=(0, len(REFINE_STATUS) - 1), reject=(0, 4),
                      net_steps=(-span, span), best_steps=(-span, span),
                      cycles=(0, Z_MAX_CYCLES), non_improvements=(0, Z_STABLE_CYCLES),
                      up_steps=(0, 0xffffffff), down_steps=(0, 0xffffffff))
        next_report = self.reactor.monotonic() + PROGRESS_INTERVAL_S
        last = None
        action = 1
        while True:
            self._stage('STM32 PC14 refinement status')
            row = self.board._thermal_query_send(self.refine, [action] + args, start=action == 1)
            if row.get('reject'):
                raise FactoryError('MCU rejected native Z refinement (%s)' % row['reject'])
            self.board._control_validate(row, limits)
            if (row['owner'] != self.guard_owner or row['token'] != token
                    or row['active'] != int(row['status'] == 0)
                    or row['net_steps'] != row['up_steps'] - row['down_steps']
                    or row['non_improvements'] >= max(1, row['cycles'])
                    or (last is not None and any(row[field] < last[field] for field in
                                                  ('up_steps', 'down_steps', 'cycles')))):
                raise FactoryError('MCU reported inconsistent native Z refinement state')
            now = self.reactor.monotonic()
            if not row['active'] or now >= next_report:
                report(dict(stage='PC14 refinement', engine='STM32',
                            cycles=row['cycles'], non_improvements=row['non_improvements'],
                            required_non_improvements=Z_STABLE_CYCLES))
                next_report = now + PROGRESS_INTERVAL_S
            if not row['active']:
                break
            if self.interrupted or self.printer.is_shutdown() or now >= self.deadline:
                raise FactoryError('Native Inventure Z refinement expired or was interrupted')
            last = row
            action = 0
            self.reactor.pause(now + .25)

        status = REFINE_STATUS[row['status']]
        if status != 'done':
            raise FactoryError('MCU native Z refinement failed: ' + status)
        if (not row['cycles'] or not row['up_steps'] or row['net_steps'] != row['best_steps']
                or (row['non_improvements'] != Z_STABLE_CYCLES and row['up_steps'] < span)):
            raise FactoryError('MCU did not settle on a valid native Z release')
        self._stage('STM32 PC14 refinement verification')
        self._check(0)
        if (self._is_triggered(self.endstop) or self._current_steps() != start
                or self._native_step_position() != native_steps):
            raise FactoryError('Native Z lost its physical reference or stepper ownership')
        basis = ('stable_repetitions' if row['non_improvements'] == Z_STABLE_CYCLES
                 else 'upper_bound_with_best_release')
        return start - row['best_steps'], row, basis

    def _reference_window(self, approach_mm, span_mm, local):
        bottom_steps = round(self.bottom_position * Z_STEPS_PER_MM)
        start = bottom_steps - round(approach_mm * Z_STEPS_PER_MM)
        span = round(span_mm * Z_STEPS_PER_MM)
        low, high = start - span, start + span
        native_low, native_high = self.kin.rails[2].get_range()
        if ((start if local else low) < round(native_low * Z_STEPS_PER_MM)
                or bottom_steps > round(native_high * Z_STEPS_PER_MM)
                or high >= bottom_steps - round((self.bottom_release + 1.) * Z_STEPS_PER_MM)):
            raise FactoryError('Native Z probe and clearance exceed its configured travel')
        return bottom_steps, start, span

    def _establish(self, approach_mm, span_mm, repeat_count, probe_cycles, report, local):
        start_pos = self.toolhead.get_position()
        start_pos[2] = self.bottom_position
        bottom_steps, start, span = self._reference_window(approach_mm, span_mm, local)
        if not self._is_triggered(self.bottom) or self._is_triggered(self.endstop):
            raise FactoryError('Start with PE12 asserted and PC14 open')
        self.started = True
        self.toolhead.set_position(start_pos, homing_axes='z')
        self._edge(self.bottom, self.bottom_position - self.bottom_release,
                   OEM_Z_BOTTOM_RELEASE_SPEED, False, 1, True, 'PE12 release')
        if self._is_triggered(self.bottom):
            raise FactoryError('PE12 did not release during native Z clearance')
        if self._current_steps() - start < Z_STEPS_PER_MM:
            raise FactoryError('Native Z coarse approach leaves less than 1 mm above PE12 release')
        if local:
            clearance = self._current_steps() - Z_STEPS_PER_MM
            if self._matching_edge(True, clearance, self.coarse_speed, 'PE12 clearance') is not None:
                raise FactoryError('PC14 closed during PE12 clearance')
        logging.info('Inventure native Z: PC14 coarse approach %.5f -> %.5f at %.3f mm/s',
                     self._current_steps() / Z_STEPS_PER_MM, start / Z_STEPS_PER_MM,
                     self.coarse_speed)
        report(dict(stage='PC14 coarse approach'))
        if (self._matching_edge(True, start, self.coarse_speed, 'PC14 coarse approach') is not None
                or self._is_triggered(self.endstop)):
            raise FactoryError('PC14 closed during the guarded coarse approach')
        if self._current_steps() != start:
            raise FactoryError('Native Z did not reach the start of refinement')
        logging.info('Inventure native Z: starting PC14 refinement at %.5f', start / Z_STEPS_PER_MM)
        report(dict(stage='PC14 refinement', cycles=0,
                    required_non_improvements=repeat_count if probe_cycles is None else None,
                    requested_cycles=probe_cycles))
        if local:
            best, telemetry, basis = self._refine_on_mcu(start, span, report)
        else:
            best, tracker, basis = self._reference(
                start, start - span, start + span, span, repeat_count, probe_cycles, report)
            telemetry = dict(cycles=tracker.cycles, non_improvements=tracker.non_improvements)
        return dict(reference_established=True, coordinate_saved=True,
                    persistence='current_klipper_session', terminal='done',
                    release_steps_from_bottom=bottom_steps - best,
                    release_mm_from_bottom=round((bottom_steps - best) / Z_STEPS_PER_MM, 6),
                    cycles=telemetry['cycles'], non_improvements=telemetry['non_improvements'],
                    local_refinement=local,
                    required_non_improvements=repeat_count if probe_cycles is None else None,
                    requested_cycles=probe_cycles,
                    completion_basis=basis)

    def _check_available(self, gcmd):
        if self.running or self.bottom is None or self.guard is None or self.z_enable is None:
            raise gcmd.error('Native Inventure Z reference already running or unavailable')

    def _reference_parameters(self, gcmd):
        if gcmd.get('ACK', '') != Z_HOME_ACK:
            raise gcmd.error('Native Inventure Z requires ACK=' + Z_HOME_ACK)
        stable_requested = gcmd.get('Z_STABLE_CYCLES', None) is not None
        local = not stable_requested and gcmd.get('Z_PROBE_CYCLES', None) is None
        if local and self.refine is None:
            raise gcmd.error('OEM native Z requires the RC8-CONTROL-R7-Z-OEM-NATIVE MCU application')
        if local and getattr(self.kin, 'max_z_velocity', self.coarse_speed) < self.coarse_speed:
            raise gcmd.error('Native Z max_z_velocity must allow the configured coarse_speed')
        approach = gcmd.get_float('Z_APPROACH_MM', self.bottom_position - 2.,
                                  minval=2., maxval=self.bottom_position)
        span = gcmd.get_float('Z_PROBE_MM', 10., minval=.02, maxval=10.)
        repeat_count = gcmd.get_int('Z_STABLE_CYCLES',
                                   Z_STABLE_CYCLES if local else self.repeat_count,
                                   minval=1, maxval=Z_STABLE_CYCLES)
        probe_cycles = gcmd.get_int('Z_PROBE_CYCLES', None, minval=1, maxval=101)
        if probe_cycles is not None and stable_requested:
            raise gcmd.error('Choose either Z_PROBE_CYCLES or Z_STABLE_CYCLES')
        target = gcmd.get_float('TARGET', self.target, minval=180., maxval=240.)
        if not all(map(math.isfinite, (approach, span, target))):
            raise gcmd.error('TARGET, Z_APPROACH_MM and Z_PROBE_MM must be finite')
        return approach, span, repeat_count, probe_cycles, local, target

    def _home_limit(self, axis, factor=1.):
        rail = self.kin.rails['XYZ'.index(axis)]
        speed_fields = ('homing_speed', 'homing_retract_speed', 'second_homing_speed')
        speeds = [getattr(rail, name) for name in speed_fields]
        gcode = self.printer.lookup_object('gcode')
        command = gcode.create_gcode_command('G28', 'G28 ' + axis, {axis: ''})
        try:
            for name, speed in zip(speed_fields, speeds):
                setattr(rail, name, speed * factor)
            self.printer.lookup_object('homing').cmd_G28(command)
            self.toolhead.wait_moves()
        finally:
            for name, speed in zip(speed_fields, speeds):
                setattr(rail, name, speed)

    def _home_bottom(self):
        info = self.kin.rails[2].get_homing_info()
        if (not info.positive_dir or info.retract_dist
                or not math.isclose(info.position_endstop, self.bottom_position, abs_tol=.005)):
            raise FactoryError('Configure native G28 Z toward PE12 without retract at bottom_position')
        self.last_result = None
        self._home_limit('Z')
        if not self._is_triggered(self.bottom):
            raise FactoryError('PE12 did not assert after bottom homing')

    def _move_to(self, coords, speed):
        self.toolhead.manual_move(coords, speed)
        self.toolhead.wait_moves()

    def _discard_z_position(self):
        self.last_result = None
        self.kin.clear_homing_state('z')

    def _wait_for_home_temperature(self, heater, target):
        deadline = self.reactor.monotonic() + self.heat_timeout
        while not self.printer.is_shutdown():
            now = self.reactor.monotonic()
            if now >= deadline:
                raise FactoryError('Timed out waiting for T0 homing temperature')
            self._require_native()
            self.board._z_home_inputs({'z_bottom': 1, 'nozzle_contact': 1})
            temperature, valid = self.board.panel_temperature(0, now)
            if (valid == 'valid' and temperature is not None
                    and target - 10. <= temperature < math.ceil(target + 8.)
                    and not heater.check_busy(now)):
                return
            self.reactor.pause(min(deadline, now + .5))
        raise FactoryError('Inventure homing was interrupted while heating')

    def cmd_home_axes(self, gcmd):
        self._check_available(gcmd)
        axes = gcmd.get('AXES', '').upper()
        if axes not in ('X', 'Y', 'XY', 'Z', 'XYZ'):
            raise gcmd.error('AXES must be X, Y, XY, Z or XYZ')
        hot = 'Z' in axes
        options = gcmd.get_command_parameters()
        allowed = {'AXES'}
        if axes != 'Z':
            allowed.add('XY_SPEED_FACTOR')
        if hot:
            allowed |= {'TARGET', 'PAD_SPEED', 'PARK_Z', 'PARK_SPEED', 'ACK',
                        'Z_APPROACH_MM', 'Z_PROBE_MM'}
        else:
            allowed.add('XY_CLEARANCE')
        unknown = options.keys() - allowed
        if unknown:
            raise gcmd.error('Unsupported homing parameters: ' + ', '.join(sorted(unknown)))
        xy_factor = gcmd.get_float('XY_SPEED_FACTOR', self.xy_speed_factor, minval=.1, maxval=2.)
        z_max = self.kin.rails[2].get_range()[1]
        if hot:
            pad_speed = gcmd.get_float('PAD_SPEED', self.pad_speed, minval=1., maxval=150.)
            park_speed = gcmd.get_float('PARK_SPEED', self.park_speed, minval=.1, maxval=20.)
            park_z = gcmd.get_float('PARK_Z', self.park_z, above=0., maxval=z_max)
        else:
            clearance = gcmd.get_float('XY_CLEARANCE', self.xy_clearance, above=0., maxval=z_max)
        motion = (pad_speed, park_speed, park_z) if hot else (clearance,)
        if not all(map(math.isfinite, (xy_factor, *motion))):
            raise gcmd.error('Homing motion parameters must be finite')
        if motion[-1] > z_max:
            raise gcmd.error('Homing clearance exceeds the configured Z travel')
        temporary_z = heat_started = success = False
        try:
            now = self.reactor.monotonic()
            self._require_idle(now)
            if self.printer.lookup_object('gcode_move').move_transform is not None:
                raise FactoryError('Inventure homing requires an untransformed working frame')
            self._require_native()
            status = self.kin.get_status(now)['homed_axes']
            self.toolhead.wait_moves()
            if hot:
                if axes == 'Z' and not all(axis in status for axis in 'xy'):
                    raise FactoryError('Home both X and Y first or use INVENTURE_HOME_XYZ')
                z_command = self._z_macro_command(options)
                heater, target = self._validate_z_macro(z_command)
                self.board._z_home_inputs({})
                previous_target = heater.get_temp(now)[1]
                heaters = self.printer.lookup_object('heaters')
            contact_known = ('z' in status and self.last_result is not None
                             and self.last_result.get('reference_established'))
            if hot or not contact_known:
                temporary_z = True
                self._home_bottom()
            elif self.toolhead.get_position()[2] < clearance:
                self._move_to([None, None, clearance], self.park_speed)
            if self._is_triggered(self.endstop):
                raise FactoryError('PC14 must be open before moving X or Y')
            for axis in 'YX':
                if axis in axes:
                    self._require_native()
                    self._home_limit(axis, xy_factor)
            self._require_native()
            if hot:
                self._move_to([self.pad_x, self.pad_y], pad_speed)
                self.board._z_home_inputs({'z_bottom': 1, 'nozzle_contact': 1})
                if not self.z_enable.is_motor_enabled():
                    self.z_enable.motor_enable(self.toolhead.get_last_move_time())
                    self.toolhead.wait_moves()
                heat_started = True
                heaters.set_temperature(heater, target, wait=False)
                self._wait_for_home_temperature(heater, target)
                self.cmd_home(z_command)
                self._require_native()
                self._move_to([None, None, park_z], park_speed)
                self._require_native()
                if (not self.last_result or not self.last_result.get('reference_established')
                        or not self.z_enable.is_motor_enabled()):
                    raise FactoryError('Inventure lost the working Z reference while parking')
                temporary_z = False
            success = True
        except Exception as exc:
            raise gcmd.error(str(exc)) from exc
        finally:
            if temporary_z:
                self._discard_z_position()
            if heat_started:
                heater.set_temp(previous_target if success and not self.printer.is_shutdown() else 0.)
        gcmd.respond_info('INVENTURE_HOME %s done%s' % (axes, ' Z=%.3f' % park_z if hot else ''))

    def _z_macro_command(self, options):
        name = 'INVENTURE_NATIVE_HOME_Z'
        params = {key: options[key] for key in ('ACK', 'TARGET', 'Z_APPROACH_MM', 'Z_PROBE_MM')
                  if key in options}
        return self.printer.lookup_object('gcode').create_gcode_command(name, name, params)

    def _validate_z_macro(self, gcmd):
        approach, span, _, _, local, target = self._reference_parameters(gcmd)
        self._reference_window(approach, span, local)
        extruder = self.toolhead.get_extruder()
        if extruder.get_name() != 'extruder':
            raise gcmd.error('Native Z reference requires extruder T0')
        heater = extruder.get_heater()
        if not heater.min_temp <= target <= heater.max_temp - 8.:
            raise gcmd.error('TARGET requires an 8 C margin below the configured T0 maximum')
        return heater, target

    def cmd_home(self, gcmd):
        self._check_available(gcmd)
        approach, span, repeat_count, probe_cycles, local, target = self._reference_parameters(gcmd)
        now = self.reactor.monotonic()
        status = self.kin.get_status(now)
        position = self.toolhead.get_position()
        try:
            self._require_idle(now)
        except FactoryError as exc:
            raise gcmd.error(str(exc)) from exc
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
        self.reference_target = target
        self.interrupted = False
        self.started = False
        display_was_held = bool(getattr(self.board, 'thermal_display_held', False))
        display_hold_requested = False
        guard_attempted = False
        self.deadline = now + 600.
        self.last_result = None
        self.timeout_reason = None
        self.watchdog_timer = self.reactor.register_timer(self._watchdog)

        def report(fields):
            gcmd.respond_info('V9_NATIVE_Z_PROGRESS ' + json.dumps(fields, sort_keys=True))

        try:
            with owner:
                self._stage('preflight')
                self.toolhead.wait_moves()
                self._check(1)
                display_hold_requested = True
                self.board._thermal_display_hold(True)
                token = self.board._control_next_token()
                guard_attempted = True
                self._verify_guard(1, token, True)
                self.guard_owner = token
                result = self._establish(approach, span, repeat_count, probe_cycles, report, local)
                self._stage('verify and commit Z reference')
                self._check(0)
                if self._is_triggered(self.endstop) or not self.z_enable.is_motor_enabled():
                    raise FactoryError('Native Z lost the selected reference before assigning zero')
                position = self.toolhead.get_position()
                position[2] = 0.
                self.toolhead.set_position(position, homing_axes='z')
                self._verify_guard(2, token, False)
                self.guard_owner = None
                gm.base_position[2] = gm.homing_position[2] = 0.
                gm.reset_last_position()
                if operations is not None:
                    operations.state.homed('z')
            self.last_result = result
        except Exception as exc:
            if guard_attempted and not self.printer.is_shutdown():
                self._stage('stop after error')
                self.board._control_stop_outputs()
            if guard_attempted or self.timeout_reason is not None:
                try:
                    self.toolhead.get_extruder().get_heater().set_temp(0.)
                except Exception:
                    logging.exception('Inventure native Z: unable to clear the nozzle target')
            if self.started or guard_attempted:
                if operations is None:
                    self.kin.clear_homing_state('xyz')
                else:
                    operations.invalidate('xyz', 'native_z_reference_failed')
            reason = self.timeout_reason or str(exc)
            self.last_result = dict(reference_established=False, coordinate_saved=False,
                                    terminal='failed', reason=reason)
            logging.warning('Inventure native Z failed: %s (UART buffers: %s)', reason,
                            getattr(self.board, 'uart_buffer_stats', {}))
            raise gcmd.error(reason) from exc
        finally:
            self.reactor.unregister_timer(self.watchdog_timer)
            self.guard_owner = None
            self.running = False
            if display_hold_requested:
                try:
                    self.board._thermal_display_hold(display_was_held)
                except Exception:
                    logging.exception('Inventure native Z: unable to restore OLED refresh')
        gcmd.respond_info('V9_NATIVE_Z ' + json.dumps(self.last_result, sort_keys=True))

    def get_status(self, eventtime):
        return dict(running=self.running, last_result=self.last_result)


def load_config(config):
    return InventureNativeZ(config)
