"""Exercise the opt-in native Z adapter with a pinned Klipper API model."""
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

from orange_pi.inventure_z_home import Z_HOME_ACK, Z_STEPS_PER_MM


def _load_r19_factory():
    path = Path(__file__).resolve().parents[1]/'restored/single_owner_r19/inventure_z_home.py'
    spec = importlib.util.spec_from_file_location('orange_pi.inventure_z_home_r19_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_restored_native_z():
    # Test the exact code that the bootstrap installs, never the pinned
    # historical R38.6 source in orange_pi/inventure_native_z.py.
    path = Path(__file__).resolve().parents[1]/'restored/configuration_audit_r23/inventure_native_z.py'
    spec = importlib.util.spec_from_file_location('orange_pi.inventure_native_z_r20_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_r20_mcu():
    path = Path(__file__).resolve().parents[1]/'restored/scheduler_r21/inventure_mcu.py'
    spec = importlib.util.spec_from_file_location('orange_pi.inventure_mcu_r20_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Model:
    NOW = 0.
    NEVER = float('inf')

    def __init__(self):
        self.z = 145.
        self.physical_z = self.z
        self.axes = 'xy'
        self.calls = []
        self.edge_calls = []
        self.timeout_edge = None
        self.stalled_phase = None
        self.timers = []
        self.shutdown_reason = None
        self.mcu = types.SimpleNamespace(
            _serial=types.SimpleNamespace(abort_wait_acks=mock.Mock()))
        self.native_counter = 102_937
        self.native_counter_after_refinement = None
        self.refine = object()
        self.refine_polls = 0
        self.refine_reply_overrides = {}
        self.refine_complete = False
        self.refine_count = 101
        self.contact_stays_closed = False
        self.run_token = 22
        self.counter_query = types.SimpleNamespace(send=self._read_stepper_position)
        self.stepper = types.SimpleNamespace(
            get_oid=lambda: 10, get_mcu_position=lambda: -self.native_counter,
            _get_position_cmd=self.counter_query, _invert_dir=True)
        self.guard = False
        self.bad_guard_reply = None
        self.tray = 0
        self.trip_at = None
        self.threshold = 5.
        self.close_budget = None
        self.close_count = 0
        self.contact_disabled = False
        self.release_overrun_steps = 0
        self.time = 0.
        self.stopped = False
        self.heater_target = 200.
        self.thermal_display_held = False
        self.move_transform = None
        self.base_position = [1., 2., 3., 4.]
        self.homing_position = [1., 2., 3., 4.]
        self.endstop = types.SimpleNamespace(
            query_endstop=lambda _: not self.contact_disabled and self.physical_z <= self.threshold)
        self.bottom = types.SimpleNamespace(query_endstop=lambda _: self.physical_z >= 145.)
        self.name = 'extruder'

    def monotonic(self):
        return self.time

    def register_timer(self, callback, waketime=NEVER):
        timer = types.SimpleNamespace(callback=callback, waketime=waketime)
        self.timers.append(timer)
        return timer

    def update_timer(self, timer, waketime):
        timer.waketime = waketime

    def unregister_timer(self, timer):
        self.timers.remove(timer)

    def advance_time(self, seconds):
        deadline = self.time + seconds
        while self.timers:
            timer = min(self.timers, key=lambda item: item.waketime)
            if timer.waketime > deadline or self.shutdown_reason is not None:
                break
            self.time = max(self.time, timer.waketime)
            timer.waketime = timer.callback(self.time)
        self.time = deadline

    def pause(self, waketime):
        self.advance_time(waketime - self.time)
        return self.time

    def invoke_shutdown(self, reason):
        self.shutdown_reason = reason
        self.stopped = True
        self.on_shutdown()

    def stall_until_shutdown(self, phase):
        if self.stalled_phase != phase:
            return
        self.advance_time(60.)
        if self.shutdown_reason is None:
            raise AssertionError('Native Z waiter was still blocked after 60 seconds')
        raise ValueError('MCU shutdown interrupted the simulated wait')

    def lookup_object(self, name, default=None):
        return self if name == 'gcode_move' else default

    def is_shutdown(self):
        return self.stopped

    def is_motor_enabled(self):
        return not self.stopped

    def wait_moves(self):
        return None

    def flush_step_generation(self):
        return None

    def get_extruder(self):
        return self

    def get_heater(self):
        return self

    def set_temp(self, temperature):
        self.heater_target = temperature

    def _thermal_display_hold(self, hold):
        self.thermal_display_held = hold
        self.calls.append(('display_hold', hold))

    def get_name(self):
        return self.name

    def get_last_move_time(self):
        return self.time

    def get_position(self):
        return [67.5, 65.5, self.z, 9.]

    def get_status(self, now):
        return {'homed_axes': self.axes}

    @property
    def rails(self):
        return [None, None, types.SimpleNamespace(get_range=lambda: (-10., 145.))]

    def move(self, position, speed):
        self.calls.append(('move', position[2]))
        self.physical_z += position[2] - self.z
        self.z = position[2]

    def set_position(self, position, homing_axes):
        self.calls.append(('set', position[2], homing_axes))
        self.z = position[2]
        self.axes = 'xyz'

    def clear_homing_state(self, axes):
        self.calls.append(('clear', axes))
        self.axes = ''.join(axis for axis in self.axes if axis not in axes)

    def reset_last_position(self):
        pass

    def _control_query(self):
        self.stall_until_shutdown('preflight')
        return dict(mode='NATIVE', armed=not self.stopped, fault=0, latched=0)

    def _z_home_inputs(self, expected):
        if self.tray:
            raise ValueError('Tray absent')
        return {'z_bottom': int(self.bottom.query_endstop(0))}

    def panel_temperature(self, tool, now):
        return (200., 'valid')

    def _control_validate(self, row, limits):
        if any(type(row.get(key)) is not int or not lo <= row[key] <= hi
               for key, (lo, hi) in limits.items()):
            raise ValueError('Invalid R5 MCU response')
        return row

    def _control_next_token(self):
        self.run_token += 1
        return self.run_token

    def _read_stepper_position(self, data, **kwargs):
        self.calls.append(('native_counter', tuple(data)))
        counter = self.native_counter
        if self.refine_complete and self.native_counter_after_refinement is not None:
            counter = self.native_counter_after_refinement
        return {'pos': counter}

    def _refine_reply(self, data, start):
        action, owner, token = data[:3]
        clearance = data[7] if len(data) == 9 else 0
        self.calls.append(('refine', tuple(data), start))
        if action == 1:
            assert self.guard and start
            self.refine_polls = 0
        else:
            assert action == 0 and not start
            self.refine_polls += 1
        if self.refine_polls < 2:
            cycles = self.refine_polls
            row = dict(owner=owner, token=token, active=1, status=0, reject=0,
                        net_steps=0, best_steps=0, cycles=cycles,
                        non_improvements=0, up_steps=cycles, down_steps=cycles)
            if clearance:
                row['clearance_steps'] = 0
            return row
        self.refine_complete = True
        self.physical_z = self.threshold + 1. / Z_STEPS_PER_MM
        net = round((self.z - self.physical_z) * Z_STEPS_PER_MM)
        self.physical_z += clearance / Z_STEPS_PER_MM
        if self.contact_stays_closed:
            self.physical_z = self.threshold
        row = dict(owner=owner, token=token, active=0, status=1, reject=0,
                   net_steps=net, best_steps=net, cycles=self.refine_count + 1,
                   non_improvements=self.refine_count,
                   up_steps=net + self.refine_count, down_steps=self.refine_count)
        row.update(self.refine_reply_overrides)
        if clearance:
            row.setdefault('clearance_steps', clearance)
        return row

    def _thermal_query_send(self, query, data, start=False):
        if query is self.refine or query is getattr(self, 'refine_clearance', None):
            return self._refine_reply(data, start)
        action, token = data
        self.calls.append(('guard', action, token, start))
        if action:
            self.guard = action == 1
        if self.bad_guard_reply == action:
            token -= 1
        return dict(owner=token, active=int(self.guard), reject=0)

    def _control_stop_outputs(self):
        self.calls.append(('stop',))
        self.guard = False
        self.stopped = True


class HomingMove:
    def __init__(self, printer, endstops, toolhead):
        self.model = toolhead
        self.pin, self.phase = endstops[0]

    def homing_move(self, position, speed, probe_pos, triggered, check_triggered):
        model = self.model
        assert probe_pos and model.guard
        target = position[2]
        old = model.physical_z
        limit = old + target - model.z
        pin_name = 'PE12' if self.pin is model.bottom else 'PC14'
        model.edge_calls.append((pin_name, speed, triggered,
                                 round(target * Z_STEPS_PER_MM), check_triggered))
        model.calls.append(('edge', triggered, round(abs(limit - old) * Z_STEPS_PER_MM)))
        if model.timeout_edge == (pin_name, speed, triggered):
            model.z -= 2.
            model.physical_z -= 2.
            raise ValueError('Error during homing %s: Communication timeout during homing'
                             % self.phase)
        if self.pin is model.bottom:
            hit = not triggered and limit < 145.
            if hit:
                limit = 145. - 1. / Z_STEPS_PER_MM
        else:
            if triggered:
                hit = (limit <= model.threshold
                       and (model.close_budget is None or model.close_count < model.close_budget))
                if hit:
                    model.close_count += 1
                    limit = min(old, model.threshold)
            else:
                released = model.threshold + 1. / Z_STEPS_PER_MM
                hit = limit >= released
                if hit:
                    limit = max(old, released)
                    if model.close_count == model.close_budget:
                        model.contact_disabled = True
        if check_triggered and not hit:
            raise ValueError('No trigger after full movement')
        model.z += limit - old
        model.physical_z = limit
        trigger = model.get_position()
        if self.pin is model.endstop and not triggered and hit:
            model.z += model.release_overrun_steps / Z_STEPS_PER_MM
            model.physical_z += model.release_overrun_steps / Z_STEPS_PER_MM
        if model.trip_at is not None and model.physical_z < model.trip_at:
            model.tray = 1
        model.stall_until_shutdown(self.phase)
        return trigger


class Command(dict):
    def __init__(self, **params):
        super().__init__(**params)
        self.messages = []

    @staticmethod
    def error(message):
        return ValueError(message)

    def get_command_parameters(self):
        return self

    def get_float(self, key, default=None, minval=None, maxval=None, above=None, below=None):
        if key not in self:
            return default
        value = float(self[key])
        if ((minval is not None and value < minval) or (maxval is not None and value > maxval)
                or (above is not None and value <= above) or (below is not None and value >= below)):
            raise self.error(key + ' outside the allowed range')
        return value

    def get_int(self, key, default=None, minval=None, maxval=None):
        if key not in self:
            return default
        value = int(self[key])
        if minval is not None and value < minval or maxval is not None and value > maxval:
            raise self.error(key + ' outside the allowed range')
        return value

    def respond_info(self, message):
        self.message = message
        self.messages.append(message)


class NativeZBridgeTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict(sys.modules, {
            'orange_pi.homing': types.SimpleNamespace(HomingMove=HomingMove),
        }):
            self.module = _load_restored_native_z()
        self.model = Model()
        self.adapter = self.module.InventureNativeZ.__new__(self.module.InventureNativeZ)
        for name in ('printer', 'reactor', 'board', 'toolhead', 'kin', 'z_enable'):
            setattr(self.adapter, name, self.model)
        self.adapter.endstop = self.model.endstop
        self.adapter.bottom = self.model.bottom
        self.adapter.guard = object()
        self.adapter.guard_owner = None
        self.adapter.refine = self.model.refine
        self.adapter.stepper = self.model.stepper
        self.adapter.running = self.adapter.interrupted = False
        self.adapter.last_result = None
        self.adapter.pad_x, self.adapter.pad_y = 67.5, 65.5
        self.adapter.bottom_position = 145.
        self.adapter.bottom_release = 5.
        self.adapter.coarse_speed = 2.
        self.adapter.fine_speed = .3
        self.adapter.target = 200.
        self.adapter.reference_target = self.adapter.target
        self.adapter.repeat_count = 2
        self.model.on_shutdown = self.adapter._shutdown
        self.command = Command(ACK=Z_HOME_ACK, Z_APPROACH_MM=135, Z_PROBE_MM=8,
                               Z_STABLE_CYCLES=self.adapter.repeat_count)

    def test_reports_and_commits_actual_native_reference_while_guarded(self):
        self.adapter.cmd_home(self.command)
        res = self.adapter.last_result
        self.assertTrue(res['reference_established'])
        self.assertTrue(res['coordinate_saved'])
        self.assertEqual((res['cycles'], res['non_improvements']), (3, 2))
        self.assertEqual(res['release_steps_from_bottom'], 140 * Z_STEPS_PER_MM - 1)
        self.assertEqual(self.model.get_position(), [67.5, 65.5, 0., 9.])
        self.assertEqual(self.model.base_position, [1., 2., 0., 4.])
        self.assertEqual(self.model.physical_z, 5. + 1. / Z_STEPS_PER_MM)
        self.assertEqual([call for call in self.model.calls if call[0] != 'display_hold'][-2:],
                         [('set', 0., 'z'), ('guard', 2, 23, False)])
        self.assertFalse(self.model.guard)
        self.assertFalse(self.model.timers)
        self.assertTrue(any(message.startswith('V9_NATIVE_Z_PROGRESS ')
                            for message in self.command.messages))
        self.adapter._enable_changed(0., False)
        self.assertNotIn('z', self.model.axes)
        self.assertEqual(self.adapter.last_result['terminal'], 'invalidated')

    def test_wrong_or_uncertain_guard_aborts_without_committing_z(self):
        for action in (1, 2):
            with self.subTest(action=action):
                self.setUp()
                self.model.bad_guard_reply = action
                with self.assertRaisesRegex(ValueError, 'guard ownership'):
                    self.adapter.cmd_home(self.command)
                self.assertIn(('stop',), self.model.calls)
                self.assertEqual(self.model.axes, '')
                self.assertFalse(self.adapter.last_result['coordinate_saved'])
                if action == 1:
                    self.assertNotIn(('set', 0., 'z'), self.model.calls)

    def test_tray_loss_aborts_before_assigning_reference(self):
        self.model.trip_at = 141.
        with self.assertRaisesRegex(ValueError, 'Tray absent'):
            self.adapter.cmd_home(self.command)
        self.assertEqual(self.model.axes, '')
        self.assertTrue(self.model.stopped)
        self.assertNotIn(('set', 0., 'z'), self.model.calls)

    def test_coarse_approach_is_one_guarded_pc14_leg_to_refinement_start(self):
        self.adapter.cmd_home(self.command)
        coarse = [edge for edge in self.model.edge_calls
                  if edge[0] == 'PC14' and edge[1] == self.adapter.coarse_speed]
        expected_start = round((self.adapter.bottom_position
                                - self.command['Z_APPROACH_MM']) * Z_STEPS_PER_MM)
        self.assertEqual(coarse, [('PC14', self.adapter.coarse_speed, True,
                                   expected_start, False)])
        self.assertEqual(self.model.edge_calls[0][0], 'PE12')
        self.assertFalse([call for call in self.model.calls if call[0] == 'move'])
        self.assertTrue(self.adapter.last_result['coordinate_saved'])

    def test_coarse_communication_timeout_aborts_without_reference(self):
        self.model.timeout_edge = ('PC14', self.adapter.coarse_speed, True)
        with self.assertRaisesRegex(ValueError, 'PC14 coarse approach.*Communication timeout'):
            self.adapter.cmd_home(self.command)
        self.assertEqual([edge[0] for edge in self.model.edge_calls], ['PE12', 'PC14'])
        self.assertIn(('stop',), self.model.calls)
        self.assertNotIn(('set', 0., 'z'), self.model.calls)
        self.assertEqual(self.model.axes, '')
        self.assertTrue(self.model.stopped)
        self.assertFalse(self.model.guard)
        self.assertFalse(self.model.thermal_display_held)
        self.assertEqual(self.model.heater_target, 0.)
        self.assertEqual([call for call in self.model.calls if call[0] == 'display_hold'],
                         [('display_hold', True), ('display_hold', False)])
        self.assertFalse(self.adapter.last_result['reference_established'])
        self.assertFalse(self.adapter.last_result['coordinate_saved'])
        self.assertEqual(self.adapter.last_result['terminal'], 'failed')
        self.assertIn('Communication timeout', self.adapter.last_result['reason'])
        self.assertFalse(self.adapter.running)

    def test_pc14_touch_at_coarse_target_aborts_before_fine_moves(self):
        self.model.threshold = self.adapter.bottom_position - self.command['Z_APPROACH_MM']
        with self.assertRaisesRegex(ValueError, 'PC14.*coarse approach'):
            self.adapter.cmd_home(self.command)
        self.assertEqual([edge[0] for edge in self.model.edge_calls], ['PE12', 'PC14'])
        self.assertEqual(self.model.physical_z, self.model.threshold)
        self.assertTrue(self.model.stopped)
        self.assertNotIn(('set', 0., 'z'), self.model.calls)

    def test_pc14_touch_at_fine_limit_counts_as_real_contact(self):
        self.model.threshold = (self.adapter.bottom_position
                                - self.command['Z_APPROACH_MM']
                                - self.command['Z_PROBE_MM'])
        self.adapter.cmd_home(self.command)
        self.assertTrue(self.adapter.last_result['coordinate_saved'])
        self.assertEqual(self.adapter.last_result['cycles'], 3)
        self.assertAlmostEqual(self.model.physical_z,
                               self.model.threshold + 1. / Z_STEPS_PER_MM)

    def test_watchdog_stops_a_blocked_pc14_waiter_at_contact(self):
        self.model.stalled_phase = 'PC14 contact'
        with self.assertRaisesRegex(ValueError, 'timeout.*PC14 contact'):
            self.adapter.cmd_home(self.command)
        self.assertIn('PC14 contact', self.model.shutdown_reason)
        self.model.mcu._serial.abort_wait_acks.assert_called_once_with()
        self.assertLessEqual(self.model.physical_z, self.model.threshold)
        self.assertEqual([edge[0] for edge in self.model.edge_calls], ['PE12', 'PC14', 'PC14'])
        self.assertNotIn(('set', 0., 'z'), self.model.calls)
        self.assertEqual(self.model.heater_target, 0.)
        self.assertFalse(self.model.thermal_display_held)
        self.assertFalse(self.model.timers)
        self.assertFalse(self.adapter.running)
        self.assertFalse(self.adapter.last_result['coordinate_saved'])

    def test_watchdog_covers_blocked_preflight_before_guard_ownership(self):
        self.model.stalled_phase = 'preflight'
        with self.assertRaisesRegex(ValueError, 'timeout.*preflight'):
            self.adapter.cmd_home(self.command)
        self.assertIn('preflight', self.model.shutdown_reason)
        self.model.mcu._serial.abort_wait_acks.assert_called_once_with()
        self.assertFalse(self.model.edge_calls)
        self.assertFalse(any(call[0] == 'guard' for call in self.model.calls))
        self.assertEqual(self.model.heater_target, 0.)
        self.assertFalse(self.model.timers)
        self.assertFalse(self.adapter.running)
        self.assertFalse(self.adapter.last_result['coordinate_saved'])

    def test_per_command_stable_cycles_override_reports_short_test_threshold(self):
        self.adapter.repeat_count = 101
        self.command['Z_STABLE_CYCLES'] = 5
        self.adapter.cmd_home(self.command)
        result = self.adapter.last_result
        self.assertEqual((result['cycles'], result['non_improvements']), (6, 5))
        self.assertEqual(self.adapter.repeat_count, 101)
        progress = [json.loads(message.split(' ', 1)[1]) for message in self.command.messages
                    if message.startswith('V9_NATIVE_Z_PROGRESS ')]
        counts = [row['required_non_improvements'] for row in progress
                  if row['stage'] == 'PC14 refinement']
        self.assertEqual(counts, [5, 5])

    def test_rejects_out_of_range_stable_cycles_before_motion(self):
        for repeats in (0, 102):
            with self.subTest(repeats=repeats):
                self.command['Z_STABLE_CYCLES'] = repeats
                with self.assertRaisesRegex(ValueError, 'Z_STABLE_CYCLES'):
                    self.adapter.cmd_home(self.command)
                self.assertFalse(self.model.edge_calls)
                self.assertFalse(self.model.timers)
        self.assertFalse(self.model.calls)

    def test_exact_probe_cycles_stops_after_three_measured_pairs(self):
        self.adapter.repeat_count = 101
        del self.command['Z_STABLE_CYCLES']
        self.command['Z_PROBE_CYCLES'] = 3
        self.adapter.cmd_home(self.command)
        result = self.adapter.last_result
        self.assertTrue(result['coordinate_saved'])
        self.assertEqual(result['completion_basis'], 'requested_contact_cycles')
        self.assertEqual((result['cycles'], result['requested_cycles']), (3, 3))
        self.assertIsNone(result['required_non_improvements'])
        self.assertEqual(self.adapter.repeat_count, 101)
        fine_edges = [edge[2] for edge in self.model.edge_calls if edge[1] == self.adapter.fine_speed]
        self.assertEqual(fine_edges, [True, False] * 3)
        progress = [json.loads(message.split(' ', 1)[1]) for message in self.command.messages
                    if message.startswith('V9_NATIVE_Z_PROGRESS ')]
        refine = [row for row in progress if row['stage'] == 'PC14 refinement']
        self.assertEqual([(row['cycles'], row['requested_cycles']) for row in refine],
                         [(0, 3), (3, 3)])

    def test_exact_probe_cycles_rejects_stability_override_before_motion(self):
        self.command.update(Z_PROBE_CYCLES=3, Z_STABLE_CYCLES=5)
        with self.assertRaisesRegex(ValueError, 'either Z_PROBE_CYCLES or Z_STABLE_CYCLES'):
            self.adapter.cmd_home(self.command)
        self.assertFalse(self.model.calls)
        self.assertFalse(self.model.timers)

    def test_exact_probe_cycles_aborts_when_second_contact_limit_is_reached(self):
        self.model.close_budget = 1
        del self.command['Z_STABLE_CYCLES']
        self.command['Z_PROBE_CYCLES'] = 3
        with self.assertRaisesRegex(ValueError, 'never reached PC14 within its span'):
            self.adapter.cmd_home(self.command)
        self.assertEqual(self.model.close_count, 1)
        fine_edges = [edge[2] for edge in self.model.edge_calls if edge[1] == self.adapter.fine_speed]
        self.assertEqual(fine_edges, [True, False, True])
        self.assertIn(('stop',), self.model.calls)
        self.assertNotIn(('set', 0., 'z'), self.model.calls)
        self.assertFalse(self.adapter.last_result['coordinate_saved'])

    def test_preflight_requires_existing_xy_and_t0(self):
        self.model.name = 'extruder1'
        with self.assertRaisesRegex(ValueError, 'selected T0'):
            self.adapter.cmd_home(self.command)
        self.assertFalse([call for call in self.model.calls if call[0] != 'display_hold'])
        self.model.calls.clear()
        self.model.name = 'extruder'
        self.model.axes = 'x'
        with self.assertRaisesRegex(ValueError, 'Home native X/Y'):
            self.adapter.cmd_home(self.command)
        self.assertFalse(self.model.calls)

    def test_keeps_previous_release_if_a_later_upward_leg_exhausts(self):
        self.model.close_budget = 1
        self.adapter.cmd_home(self.command)
        res = self.adapter.last_result
        self.assertEqual(res['completion_basis'], 'upper_bound_with_best_release')
        self.assertEqual(res['cycles'], 1)
        self.assertEqual(self.model.axes, 'xyz')

    def test_local_stop_can_verify_same_trigger_after_opening_halt_overrun(self):
        self.model.release_overrun_steps = 3
        self.adapter.cmd_home(self.command)
        self.assertTrue(self.adapter.last_result['coordinate_saved'])
        self.assertAlmostEqual(self.model.physical_z, 5. + 1. / Z_STEPS_PER_MM)
        self.assertIn(('edge', True, 3), self.model.calls)

    def test_default_refines_on_mcu_and_rebases_only_after_confirmed_end(self):
        del self.command['Z_STABLE_CYCLES']
        self.adapter.repeat_count = 101
        self.adapter.coarse_speed = 20.
        self.adapter.cmd_home(self.command)

        result = self.adapter.last_result
        self.assertTrue(result['coordinate_saved'])
        self.assertTrue(result['local_refinement'])
        self.assertEqual(result['completion_basis'], 'stable_repetitions')
        self.assertEqual((result['cycles'], result['non_improvements']), (102, 101))
        self.assertEqual(result['release_steps_from_bottom'], 140 * Z_STEPS_PER_MM - 1)
        self.assertEqual(self.model.get_position(), [67.5, 65.5, 0., 9.])
        self.assertAlmostEqual(self.model.physical_z, 5. + 1. / Z_STEPS_PER_MM)
        self.assertEqual([edge[:3] for edge in self.model.edge_calls],
                         [('PE12', self.module.OEM_Z_BOTTOM_RELEASE_SPEED, False),
                          ('PC14', 20., True), ('PC14', 20., True)])
        clearance, coarse = self.model.edge_calls[1:]
        self.assertEqual(clearance[3], 144 * Z_STEPS_PER_MM - 1)
        self.assertEqual(coarse[3], 10 * Z_STEPS_PER_MM)
        self.assertFalse(any(call[0] == 'move' for call in self.model.calls))
        refine = [call for call in self.model.calls if call[0] == 'refine']
        self.assertEqual([call[1][0] for call in refine], [1, 0, 0])
        span = round(self.command['Z_PROBE_MM'] * Z_STEPS_PER_MM)
        interval = round(1_000_000 / (self.adapter.fine_speed * Z_STEPS_PER_MM))
        self.assertEqual([call[1][1:] for call in refine],
                         [(23, 24, span, interval, 180, 215)] * 3)
        self.assertEqual([call[2] for call in refine], [True, False, False])
        counter_positions = [index for index, call in enumerate(self.model.calls)
                             if call[0] == 'native_counter']
        self.assertEqual(len(counter_positions), 2)
        self.assertLess(counter_positions[0], self.model.calls.index(refine[0]))
        self.assertLess(self.model.calls.index(refine[-1]), counter_positions[1])
        self.assertLess(counter_positions[1], self.model.calls.index(('set', 0., 'z')))
        self.assertLess(self.model.calls.index(('set', 0., 'z')),
                        self.model.calls.index(('guard', 2, 23, False)))
        self.model.move([67.5, 65.5, 1., 9.], 1.)
        self.assertAlmostEqual(self.model.physical_z, 6. + 1. / Z_STEPS_PER_MM)

    def test_mcu_refinement_rejects_inconsistent_result_before_rebase(self):
        for overrides in ({'token': 100}, {'net_steps': 0}, {'non_improvements': 102},
                          {'up_steps': True}, {'active': 1}):
            with self.subTest(overrides=overrides):
                self.setUp()
                del self.command['Z_STABLE_CYCLES']
                self.adapter.repeat_count = 101
                self.model.refine_reply_overrides = overrides
                with self.assertRaises(ValueError):
                    self.adapter.cmd_home(self.command)
                self.assertIn(('stop',), self.model.calls)
                self.assertNotIn(('set', 0., 'z'), self.model.calls)
                self.assertEqual(self.model.axes, '')
                self.assertFalse(self.adapter.last_result['coordinate_saved'])

    def test_default_refinement_requires_local_abi_before_issuing_motion(self):
        del self.command['Z_STABLE_CYCLES']
        self.adapter.refine = None
        with self.assertRaisesRegex(ValueError, 'Z-OEM-NATIVE MCU application'):
            self.adapter.cmd_home(self.command)
        self.assertFalse(self.model.calls)
        self.assertFalse(self.model.timers)

    def test_mcu_refinement_requires_fresh_open_contact_before_rebase(self):
        del self.command['Z_STABLE_CYCLES']
        self.model.contact_stays_closed = True
        with self.assertRaisesRegex(ValueError, 'physical reference'):
            self.adapter.cmd_home(self.command)
        self.assertIn(('stop',), self.model.calls)
        self.assertNotIn(('set', 0., 'z'), self.model.calls)
        self.assertFalse(self.adapter.last_result['coordinate_saved'])

    def test_local_refinement_uses_requested_target_for_its_mcu_temperature_band(self):
        del self.command['Z_STABLE_CYCLES']
        self.command['TARGET'] = '205'
        self.adapter.cmd_home(self.command)
        self.assertTrue(self.adapter.last_result['coordinate_saved'])
        refine_start = next(call for call in self.model.calls if call[0] == 'refine')
        self.assertEqual(refine_start[1][-2:], (185, 220))
        self.assertEqual(self.adapter.target, 200.)

    def test_mcu_refinement_lost_handoff_invalidates_xyz_without_gcode_reset(self):
        del self.command['Z_STABLE_CYCLES']
        self.model.bad_guard_reply = 2
        with self.assertRaisesRegex(ValueError, 'guard ownership'):
            self.adapter.cmd_home(self.command)
        self.assertTrue(self.model.refine_complete)
        self.assertIn(('stop',), self.model.calls)
        self.assertEqual(self.model.axes, '')
        self.assertEqual(self.model.base_position, [1., 2., 3., 4.])
        self.assertEqual(self.model.homing_position, [1., 2., 3., 4.])
        self.assertFalse(self.adapter.last_result['coordinate_saved'])

    def test_mcu_refinement_invalidates_native_frame_on_counter_change(self):
        del self.command['Z_STABLE_CYCLES']
        self.adapter.repeat_count = 101
        self.model.native_counter_after_refinement = self.model.native_counter + 1
        with self.assertRaisesRegex(ValueError, 'counter|stepper position'):
            self.adapter.cmd_home(self.command)
        self.assertIn(('stop',), self.model.calls)
        self.assertNotIn(('set', 0., 'z'), self.model.calls)
        self.assertEqual(self.model.axes, '')
        self.assertFalse(self.adapter.last_result['coordinate_saved'])


class HomeSequenceModel(Model):
    def __init__(self):
        self.position = [50., 60., 145., 0.]
        super().__init__()
        self.axes = ''
        self.z = 30.
        self.physical_z = self.z
        self.heater_target = 0.
        self.min_temp, self.max_temp = 0., 220.
        self.speeds = []
        self.native_mode = True
        self.printer = self.reactor = self
        self.inactive_mode = 'SERVICE'
        self.service_active = self.control_fault = self.control_latched = False
        self.control_reject = None
        self.mode_requests = []
        self.print_state = 'standby'
        self.fail_axis = None
        self.heater_busy = False
        self.heaters = {'extruder': self}
        self.last_pwm_value = 0.
        self.physical_tool = 1
        self.load_test_context = None
        self.load_test_last = {}
        self.prepare_failure = None
        self.omit_native_handoff_receipt = False
        self.prepare_actions = []
        self.refine_clearance = object()
        self.native_xy_handoff_abi = 1
        self.mcu.get_constants = lambda: {'INVENTURE_NATIVE_HEATER_HANDOFF_ABI': 1,
                                         'INVENTURE_NATIVE_Z_CLEARANCE_ABI': 1}
        self.stats = types.SimpleNamespace(get_status=lambda _: {'state': self.print_state})
        self.axis_rails = [
            types.SimpleNamespace(homing_speed=5., second_homing_speed=2.,
                                  homing_retract_speed=5., get_range=lambda: (0., 138.),
                                  get_homing_info=lambda: types.SimpleNamespace(positive_dir=False, position_endstop=0.)),
            types.SimpleNamespace(homing_speed=5., second_homing_speed=2.,
                                  homing_retract_speed=5., get_range=lambda: (0., 138.),
                                  get_homing_info=lambda: types.SimpleNamespace(positive_dir=False, position_endstop=0.)),
            types.SimpleNamespace(homing_speed=8.2849, second_homing_speed=2.,
                                  homing_retract_speed=8.2849, get_range=lambda: (-.7, 145.),
                                  get_homing_info=lambda: types.SimpleNamespace(
                                      positive_dir=True, retract_dist=0., position_endstop=145.)),
        ]

    @property
    def rails(self):
        return self.axis_rails

    @property
    def target_temp(self):
        return self.heater_target

    @property
    def z(self):
        return self.position[2]

    @z.setter
    def z(self, value):
        self.position[2] = value

    def lookup_object(self, name, default=None):
        if name == 'print_stats':
            return self.stats
        if name in ('gcode', 'gcode_move', 'heaters', 'homing', 'toolhead', 'stepper_enable'):
            return self
        return default

    def _control_query(self):
        return dict(mode='NATIVE' if self.native_mode else self.inactive_mode,
                    armed=self.native_mode, latched=self.control_latched, fault=self.control_fault,
                    active=self.native_mode or self.service_active, reject=None)

    def cmd_control(self, command):
        self.mode_requests.append(command.get('MODE'))
        if self.control_reject:
            raise ValueError(self.control_reject)
        self.native_mode = True

    def turn_off_all_heaters(self):
        for heater in self.heaters.values():
            heater.set_temp(0.)

    def motor_off(self):
        self.calls.append(('motor_off',))

    def _enter_operator_service(self, homing=None):
        assert homing is not None and homing.sequence_running and not homing.running
        if self.native_mode or self.inactive_mode != 'SERVICE':
            self.mode_requests.append('SERVICE')
        self.native_mode = False
        self.inactive_mode = 'SERVICE'

    def _factory_start(self, command, operation, native_homing=None):
        from orange_pi.inventure_factory_core import FactoryProgram, parameters
        z_home = _load_r19_factory()
        ZHomeProgram, z_home_parameters = z_home.ZHomeProgram, z_home.z_home_parameters
        p = parameters(command, max_target=310.)
        generator = (FactoryProgram(p, operation).run(operation) if native_homing is None else
                     ZHomeProgram(p, z_home_parameters(command, p)).prepare_native_home(
                         native_homing['pad_xy'], native_homing['pad_speed'],
                         klipper_xy_prepared=bool(native_homing.get('klipper_xy_prepared'))))
        factory = dict(operation=operation, total_deadline=self.monotonic()+1800.,
                       selection_commanded=False, cleaning_done=False, result=None, position=[0.,0.,0.])
        context = self.load_test_context = dict(factory=factory, tool=p['tool'])
        reply = None
        def advance(eventtime):
            nonlocal reply
            if self.load_test_context is not context:
                return self.NEVER
            action = generator.send(reply)
            self.prepare_actions.append(action)
            reply = dict(ok=True, a=1, b=0, pulses=0, pulse_age_ms=0, starts=0, fault=0)
            if action['kind'] == self.prepare_failure:
                self.load_test_last = dict(phase='failed', reason='Test preparation stopped')
                self.load_test_context = None
                return self.NEVER
            if action['kind'] == 'home':
                self.calls.append(('service_home', action['axis']))
            if action['kind'] == 'heat':
                self.calls.append(('service_heat', p['target']))
            if action['kind'] == 'z_inputs':
                reply = self._z_home_inputs(action['expected'])
            if action['kind'] == 'z_contact_move':
                factory['position'][2] += action['steps'] / Z_STEPS_PER_MM
                self.physical_z -= action['steps'] / Z_STEPS_PER_MM
                reply = dict(reason='completed', sent_steps=abs(action['steps']), hit=0,
                             end_raw=self._z_home_inputs({}))
            if action['kind'] == 'native_home_frame':
                reply = dict(native_xy_offsets_mm=([0.,0.] if
                    native_homing.get('klipper_xy_prepared') else [.2,.3]))
            if action['kind'] == 'move':
                factory['position'][:2] = [action['x'], action['y']]
                self.calls.append(('service_move', action.get('leg'), action['x'], action['y']))
            if action.get('leg') == 'selector_press':
                self.physical_tool = p['tool']
            if action['kind'] == 'module' and action['name'] == 'select_tool' and action['state'] == 'done':
                factory['selection_commanded'] = True
            if action['kind'] == 'done':
                if self.control_reject:
                    self.load_test_last = dict(phase='failed', reason=self.control_reject)
                    self.load_test_context = None
                    return self.NEVER
                factory['result'] = dict(action)
                if not self.omit_native_handoff_receipt:
                    factory['result']['native_handoff'] = dict(
                        owner=21, run=22, token=23, mode='NATIVE', enabled=1,
                        tool=0, verified=True)
                factory['cleaning_done'] = action['cleaning_done']
                self.load_test_last = dict(phase='done')
                self.load_test_context = None
                self.native_mode = True
                self.calls.append(('native_handoff',))
                return self.NEVER
            return eventtime + .01
        self.register_timer(advance, self.monotonic()+.01)
        return context

    def _control_abort(self):
        self.load_test_context = None
        self.heater_target = 0.
        self.native_mode = False

    def _z_home_inputs(self, expected):
        raw = super()._z_home_inputs(expected)
        raw['nozzle_contact'] = int(not self.endstop.query_endstop(0))
        raw.update({axis+'_endstop':int(abs(self.position[index])<.001)
                    for index,axis in enumerate('xy')})
        if any(raw[name] != setting for name, setting in expected.items()):
            raise ValueError('Unexpected home input')
        return raw

    def get_position(self):
        return list(self.position)

    def set_position(self, position, homing_axes=''):
        self.position = list(position)
        self.axes = ''.join(axis for axis in 'xyz' if axis in self.axes + homing_axes)

    def manual_move(self, position, speed):
        self.calls.append(('move', tuple(position), speed))
        for axis, value in enumerate(position):
            if value is not None:
                if axis == 2:
                    self.physical_z += value - self.position[axis]
                self.position[axis] = value

    def cmd_G28(self, gcmd):
        axis, = (letter for letter in 'XYZ' if letter in gcmd)
        self.calls.append(('G28', axis))
        axis = axis.lower()
        index = 'xyz'.index(axis)
        rail = self.axis_rails[index]
        self.speeds.append((axis, rail.homing_speed, rail.second_homing_speed,
                            rail.homing_retract_speed))
        if self.fail_axis == axis:
            raise ValueError('Test endstop never latched')
        self.position[index] = 145. if axis == 'z' else 0.
        self.set_position(self.position, axis)
        if axis == 'z':
            self.physical_z = 145.

    @staticmethod
    def create_gcode_command(name, line, params):
        return Command(**params)

    def get_temp(self, eventtime):
        return self.heater_target, self.heater_target

    def set_temperature(self, heater, target, wait=False):
        self.calls.append(('heat', target, wait))
        heater.set_temp(target)

    def check_busy(self, eventtime):
        return self.heater_busy

    def panel_temperature(self, tool, now):
        return self.heater_target, 'valid'


class NativeHomeSequenceTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict(sys.modules, {
            'orange_pi.homing': types.SimpleNamespace(HomingMove=HomingMove),
        }):
            module = _load_restored_native_z()
        model = self.model = HomeSequenceModel()
        self.adapter = module.InventureNativeZ.__new__(module.InventureNativeZ)
        for name in ('printer', 'reactor', 'board', 'toolhead', 'kin', 'z_enable',
                     'gcode', 'heaters'):
            setattr(self.adapter, name, model)
        self.adapter.bottom = model.bottom
        self.adapter.endstop = model.endstop
        self.adapter.guard = object()
        self.adapter.refine = model.refine
        self.adapter.refine_clearance = model.refine_clearance
        self.adapter.sequence_running = self.adapter.running = self.adapter.interrupted = False
        self.adapter.last_result = None
        self.adapter.pad_x, self.adapter.pad_y = 82.8, 68.
        self.adapter.bottom_position = 145.
        self.adapter.bottom_release = 5.
        self.adapter.coarse_speed = 20.
        self.adapter.repeat_count = 101
        self.adapter.target = 200.
        self.adapter.xy_speed_factor = 1.5
        self.adapter.xy_clearance = 10.
        self.adapter.pad_speed = 80.
        self.adapter.park_z = 10.
        self.adapter.park_speed = 5.
        self.adapter.heat_timeout = 300.
        self.adapter.cmd_home = self.reference

    def reference(self, command):
        self.model.calls.append(('reference', dict(command)))
        self.model.set_position([82.8, 68., 0., 0.], 'z')
        self.model.physical_z = self.model.threshold + 1. / Z_STEPS_PER_MM
        self.adapter.last_result = dict(reference_established=True, coordinate_saved=True)

    def operator_menu(self):
        from test_orange_pi_zortrax_menu import MenuFixture
        board, menu = MenuFixture().host()
        self.model.zortrax = menu
        board.printer.objects['inventure_native_z'] = self.adapter
        lines = []
        board.gcode.register_output_handler(lines.append)
        return menu, lines

    def use_mcu_reference(self):
        # Run the production Z handler, including the guard and refinement requests.
        del self.adapter.cmd_home
        self.model.threshold = 1.  # Simulated contact below the 2 mm start of refinement.
        self.adapter.guard_owner = None
        self.adapter.stepper = self.model.stepper
        self.adapter.fine_speed = .3117206982543641
        self.model.on_shutdown = self.adapter._shutdown

    def test_normal_x_y_xy_have_only_klipper_stepper_planner(self):
        for axes,expected in [('X',['Z','X']),('Y',['Z','Y']),('XY',['Z','Y','X'])]:
            with self.subTest(axes=axes):
                self.setUp()
                self.operator_menu()
                self.adapter.cmd_request_home(Command(AXES=axes))
                self.assertEqual([c[1] for c in self.model.calls if c[0]=='G28'],expected)
                self.assertFalse([c for c in self.model.calls if c[0]=='service_home'])
                self.assertFalse([c for c in self.model.calls if c[0]=='service_move'])
                self.assertFalse(self.model.prepare_actions)
                self.assertNotIn('SERVICE',self.model.mode_requests)

    def test_home_z_prepares_t0_once_then_runs_the_existing_native_reference(self):
        options = dict(ACK=Z_HOME_ACK, TARGET='205', PARK_Z='8')
        for homed in ('', 'x', 'yz', 'xy', 'xyz'):
            with self.subTest(homed=homed):
                self.setUp()
                self.use_mcu_reference()
                self.model.axes = homed
                self.operator_menu()
                self.adapter.cmd_request_home(Command(AXES='XYZ', **options))
                expected_moves = list(self.model.calls)
                expected_result = dict(self.adapter.last_result)

                self.setUp()
                self.use_mcu_reference()
                self.model.axes = homed
                self.model.native_mode = False
                menu, _ = self.operator_menu()
                self.adapter.cmd_request_home(Command(AXES='Z', TARGET='205', PARK_Z='8'))
                self.assertEqual((self.model.calls, self.model.mode_requests), ([], []))
                menu.choose(menu.view.token, confirm=True)
                self.assertEqual(self.model.mode_requests, ['NATIVE', 'SERVICE'])
                self.assertFalse([c for c in self.model.calls if c[0]=='service_home'])
                self.assertEqual([c[1] for c in self.model.calls if c[0]=='G28'],
                                 ['Z','Z'] if all(a in homed for a in 'xy')
                                 else ['Z','Y','X','Z'])
                self.assertEqual(self.adapter.last_result, expected_result)
                self.assertTrue(expected_result['coordinate_saved'])
                self.assertTrue(expected_result['local_refinement'])
                self.assertEqual(self.model.position, [82.8, 68., 8., 0.])

    def test_no_native_position_is_imported_without_mcu_handoff_receipt(self):
        self.model.omit_native_handoff_receipt = True
        self.operator_menu()
        with self.assertRaisesRegex(ValueError, 'handoff receipt'):
            self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
        self.assertFalse(any(c[0]=='reference' for c in self.model.calls))
        self.assertTrue(any(c[0]=='native_handoff' for c in self.model.calls))
        self.assertFalse(self.adapter.last_result['coordinate_saved'])

    def test_home_z_rechecks_xy_at_confirmation_and_keeps_probe_failure(self):
        self.use_mcu_reference()
        self.model.axes = 'xy'
        menu, _ = self.operator_menu()
        self.adapter.cmd_request_home(Command(AXES='Z'))
        self.model.clear_homing_state('xy')
        self.model.refine_reply_overrides = dict(status=2)
        with self.assertRaisesRegex(ValueError, 'no contact'):
            menu.choose(menu.view.token, confirm=True)
        self.assertEqual([call[1] for call in self.model.calls if call[0] == 'G28'],
                         ['Z','Y','X','Z'])
        self.assertFalse([c for c in self.model.calls if c[0] == 'service_home'])
        self.assertIn('no contact', menu.last['reason'])
        self.assertEqual(self.adapter.last_result['terminal'], 'failed')
        self.assertIn('no contact', self.adapter.last_result['reason'])
        self.assertFalse(self.adapter.last_result['coordinate_saved'])
        self.assertNotIn('z', self.model.axes)
        self.assertEqual(self.model.heater_target, 0.)

    def test_home_all_selects_and_cleans_t0_when_t1_is_physically_lowered(self):
        self.model.axes = 'xy'
        self.model.physical_tool = 1
        self.operator_menu()
        reference = self.adapter.cmd_home
        def probe(command):
            self.assertEqual(self.model.physical_tool, 0)
            self.assertEqual(self.model.prepare_actions[-1]['kind'], 'done')
            self.assertTrue(self.model.prepare_actions[-1]['cleaning_done'])
            reference(command)
        self.adapter.cmd_home = probe
        self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK, TARGET='200'))
        moves = [a for a in self.model.prepare_actions if a.get('leg') == 'selector_press']
        self.assertEqual([(a['x'], a['y']) for a in moves], [(154.5, 154.5)])
        self.assertEqual(self.model.mode_requests, ['SERVICE'])
        self.assertEqual(self.model.get_position()[2], 10.)
        self.assertTrue(self.adapter.last_result['reference_established'])

    def test_home_x_then_y_then_all_still_switches_the_physical_t1_to_t0(self):
        self.operator_menu()
        self.model.physical_tool = 1
        for axis in ('X', 'Y'):
            self.adapter.cmd_request_home(Command(AXES=axis))
        self.assertEqual(self.model.physical_tool, 1)
        self.assertEqual(self.model.axes, 'xy')
        self.model.calls.clear()
        self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
        self.assertEqual(self.model.physical_tool, 0)
        self.assertEqual([c[1] for c in self.model.calls if c[0] == 'G28'],
                         ['Z','Y','X','Z'])
        self.assertFalse([c for c in self.model.calls if c[0] == 'service_home'])
        self.assertTrue(self.adapter.last_result['reference_established'])

    def test_home_z_uses_known_native_xy_frame_without_second_yx_home(self):
        self.model.axes = 'xy'
        self.model.position[:2] = [46., 49.]
        self.operator_menu()
        self.adapter.cmd_request_home(Command(AXES='Z', ACK=Z_HOME_ACK))
        # Only Klipper can move from an already-qualified XY origin. No
        # SERVICE Y/X home is allowed, and XYZ still establishes native Z.
        self.assertEqual([c[1] for c in self.model.calls if c[0]=='G28'], ['Z','Z'])
        self.assertTrue(any(c[0]=='move' and c[1][:2]==(0.,0.)
                            for c in self.model.calls))
        self.assertFalse([a for a in self.model.prepare_actions
                          if a['kind'] in ('home','home_release')])
        self.assertTrue(self.adapter.last_result['reference_established'])

    def test_operator_order_is_one_xy_home_bin_heat_t0_wipe_pad_bottom_probe_and_retreat(self):
        self.use_mcu_reference()
        self.operator_menu()
        self.model.base_position[:2] = [99., -44.]
        self.model.homing_position[:2] = [.1, -.2]
        self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK, PARK_SPEED='4'))
        actions = self.model.prepare_actions
        self.assertEqual([a['axis'] for a in actions if a['kind']=='home'], [])
        self.assertEqual([a['axis'] for a in actions if a['kind']=='home_release'], [])
        heat = next(i for i,a in enumerate(actions) if a['kind']=='heat')
        bin_move = [a for a in actions[:heat] if a['kind']=='move'][-1]
        self.assertEqual((bin_move['x'], bin_move['y']), (82.8,152.5))
        select = next(i for i,a in enumerate(actions) if a.get('leg')=='selector_press')
        wipe = next(i for i,a in enumerate(actions) if a.get('name')=='clean')
        pad = next(i for i,a in enumerate(actions) if a.get('leg')=='z_contact_pad')
        self.assertLess(heat, select)
        self.assertLess(select, wipe)
        self.assertLess(wipe, pad)
        self.assertEqual((actions[pad]['x'],actions[pad]['y']), (82.8,68.))
        calls = self.model.calls
        bottom = next(i for i,c in enumerate(calls) if c[:2]==('G28','Z'))
        pad_call = next(i for i,c in enumerate(calls) if c[:2]==('service_move','z_contact_pad'))
        self.assertLess(bottom,pad_call)
        self.assertEqual([c[1] for c in calls if c[0]=='G28'], ['Z','Y','X','Z'])
        self.assertFalse([c for c in calls if c[0]=='service_home'])
        start = next(c for c in calls if c[0]=='refine' and c[2])
        self.assertEqual(start[1][7], 802)
        self.assertGreaterEqual(start[1][8], 1_000_000/(4*802))
        self.assertEqual(self.adapter.last_result['handoff_clearance_mm'], 1.)
        self.assertEqual(self.model.position[2], 10.)
        self.assertEqual(self.model.base_position[:2], [.1, -.2])
        self.assertAlmostEqual(self.model.physical_z-self.model.threshold, 10.+1/802)

    def test_incomplete_local_retreat_cannot_publish_a_native_zero(self):
        self.use_mcu_reference()
        self.operator_menu()
        self.model.refine_reply_overrides['clearance_steps'] = 801
        with self.assertRaisesRegex(ValueError, 'valid native Z release'):
            self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
        self.assertFalse(self.adapter.last_result['coordinate_saved'])
        self.assertNotIn('z', self.model.axes)
        self.assertEqual(self.model.heater_target, 0.)

    def test_preparation_stop_or_failure_never_starts_native_motion(self):
        for stage in ('phase', 'heat', 'move', 'done'):
            with self.subTest(stage=stage):
                self.setUp()
                self.operator_menu()
                self.model.prepare_failure = stage
                with self.assertRaisesRegex(ValueError, 'T0 selection and cleaning|handoff receipt'):
                    self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
                self.assertFalse(any(c[0] in ('reference', 'refine') for c in self.model.calls))
                self.assertEqual([c[1] for c in self.model.calls if c[0]=='G28'],
                                 ['Z','Y','X'])
                self.assertEqual(self.model.mode_requests, ['SERVICE'])
                self.assertEqual(self.model.axes, '')
                self.assertEqual(self.model.heater_target, 0.)
                self.assertEqual(self.adapter.last_result['terminal'], 'failed')
                self.assertFalse(self.adapter.sequence_running)

    def test_preparation_suspends_native_targets_and_restores_only_after_success(self):
        for fail in (None, 'heat'):
            with self.subTest(fail=fail):
                self.setUp()
                self.operator_menu()
                second = mock.Mock(last_pwm_value=0.)
                second.get_temp.return_value = (100., 180.)
                self.model.heaters['inventure_t1'] = second
                self.model.heater_target = 200.
                self.model.prepare_failure = fail
                if fail:
                    with self.assertRaises(ValueError):
                        self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
                else:
                    self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
                second.set_temp.assert_has_calls([mock.call(0.), mock.call(0. if fail else 180.)])
                self.assertEqual(self.model.heater_target, 0. if fail else 200.)

    def test_mcu_without_heater_handoff_is_refused_before_preparation(self):
        self.operator_menu()
        self.model.mcu.get_constants = lambda: {}
        with self.assertRaisesRegex(ValueError, 'MCU R15'):
            self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
        self.assertEqual((self.model.calls, self.model.mode_requests, self.model.prepare_actions),
                         ([], [], []))
        for missing in ('native_xy_handoff_abi', 'refine_clearance'):
            with self.subTest(missing=missing):
                self.setUp()
                self.operator_menu()
                setattr(self.model if missing=='native_xy_handoff_abi' else self.adapter, missing, None)
                with self.assertRaisesRegex(ValueError, 'MCU R16'):
                    self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
                self.assertEqual((self.model.calls, self.model.mode_requests, self.model.prepare_actions),
                                 ([], [], []))

    def test_prepare_t0_uses_real_r20_factory_and_requires_mcu_handoff(self):
        from test_orange_pi_z_home_host import ZHomeHostTests
        from test_orange_pi_load_test import Wire
        r20 = _load_r20_mcu()
        for deny_handoff in (False, True):
            with self.subTest(deny_handoff=deny_handoff):
                self.setUp()
                self.model.axes = 'xy'
                self.model.position[:2] = [0., 0.]
                board = ZHomeHostTests().host()
                board.device.z = 0  # native Klipper has reached PE12
                real_handoff = board.device.native_handoff
                def handoff(args):
                    row = real_handoff(args)
                    return dict(row, reject=2) if deny_handoff else row
                board.commands['factory_native_handoff'] = Wire(handoff)
                r20_factory_start = types.MethodType(r20.InventureMCU._factory_start, board)
                def factory_start(*args, **kwargs):
                    # Match the installed R20 host; legacy source test package
                    # still exposes the pinned pre-R19 ZHomeProgram otherwise.
                    with mock.patch.dict(sys.modules, {
                            'orange_pi.inventure_z_home': _load_r19_factory()}):
                        return r20_factory_start(*args, **kwargs)
                board._factory_start = factory_start
                board._factory_action = types.MethodType(r20.InventureMCU._factory_action, board)
                board.printer.objects['inventure_native_z'] = self.adapter
                board.printer.objects['heaters'] = self.model
                self.adapter.board = board
                self.adapter.reactor = board.reactor
                self.adapter.sequence_running = True
                if deny_handoff:
                    with self.assertRaisesRegex(ValueError, 'T0 selection and cleaning|handoff'):
                        self.adapter._prepare_t0(Command(), self.model, 200.,
                                                 prehomed_xy=True)
                else:
                    result = self.adapter._prepare_t0(Command(), self.model, 200.,
                                                      prehomed_xy=True)
                    self.assertEqual(result['native_xy_offsets_mm'], [0.,0.])
                    self.assertEqual(result['service_xy_mm'], [self.adapter.pad_x,self.adapter.pad_y])
                    self.assertEqual(result['native_handoff']['mode'], 'NATIVE')
                    self.assertTrue(result['native_handoff']['verified'])
                    factory = board.load_test_last['factory']
                    self.assertTrue(factory['cleaning_done'])
                    self.assertEqual(factory['motion_legs']['selector_press']['target'], [154.5,154.5])
                    self.assertEqual(factory['motion_legs']['z_contact_pad']['target'],
                                     [self.adapter.pad_x,self.adapter.pad_y])
                    self.assertFalse(any(row.get('name') in ('home','home_xy','z_initial_clearance')
                                         for row in factory.get('module_history', [])))
                self.assertIsNone(board.load_test_context)
                self.assertFalse(board.device.active())

    def test_service_preflight_allows_only_its_own_homing_preparation(self):
        from test_orange_pi_factory_host import FactoryHostTests
        board, _, _ = FactoryHostTests().host(fast_motion=True)
        board.printer.objects['inventure_native_z'] = self.adapter
        self.adapter.sequence_running = True
        with self.assertRaisesRegex(ValueError, 'homing'):
            board._operator_service_preflight()
        with self.assertRaisesRegex(ValueError, 'homing'):
            board._operator_service_preflight(homing=object())
        board._operator_service_preflight(homing=self.adapter)
        self.adapter.running = True
        with self.assertRaisesRegex(ValueError, 'homing'):
            board._operator_service_preflight(homing=self.adapter)

    def test_shutdown_readback_uses_only_ownerless_status_and_matches_the_failed_run(self):
        self.use_mcu_reference()
        self.operator_menu()
        gcode = Command()
        board = self.model.mcu
        raw = board._serial = mock.Mock()
        board.get_constants = lambda: {'INVENTURE_NATIVE_Z_REFINE_ABI': '1',
                                      'INVENTURE_NATIVE_HEATER_HANDOFF_ABI': '1'}
        board.get_name = lambda: 'mcu'
        board.is_fileoutput = lambda: False
        cmd = mock.Mock()
        board.lookup_command = mock.Mock(return_value=cmd)
        self.model.register_callback = mock.Mock()
        self.model.register_async_callback = lambda cb: cb(0.)
        self.model.get_state_message = lambda: ('MCU shutdown', 'shutdown')
        self.model.lookup_object('gcode').respond_info = gcode.respond_info
        original_query = self.model._thermal_query_send
        def poll(command, data, **kwargs):
            if command in (self.model.refine, self.model.refine_clearance) and data[0] == 0:
                self.model.invoke_shutdown('Inventure V9 local safety fault')
                raise ValueError('No communication with MCU')
            return original_query(command, data, **kwargs)
        self.model._thermal_query_send = poll
        with self.assertRaisesRegex(ValueError, 'No communication'):
            self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
        cmd.send.assert_called_once_with([0]*7)
        self.assertEqual(raw.register_response.call_args.args[1], 'inventure_native_z_refine_state')
        callback = raw.register_response.call_args.args[0]
        owner, token = self.adapter.refine_identity
        row = dict(owner=owner, token=token, status=2, active=0, reject=0, net_steps=8020,
                   best_steps=0, cycles=0, non_improvements=0, up_steps=8020, down_steps=0)
        callback(row)
        self.assertEqual(self.adapter.last_result['mcu_refinement']['status'], 'no contact')
        self.assertTrue(self.adapter.retained_refinement['matches_failed_run'])
        self.assertFalse(self.adapter.last_result['coordinate_saved'])
        self.assertIn('V9_NATIVE_Z_RETAINED', gcode.message)

        # A freshly connected host has no run identity. Report the retained
        # counters independently even if Klipper could not configure the MCU.
        self.model.get_state_message = lambda: ('Cannot configure MCU', 'error')
        self.adapter.last_result = self.adapter.refine_identity = None
        self.adapter._connect_error('MCU error during connect', {
            'error': "Can not update MCU 'mcu' config as it is shutdown"})
        raw.register_response.call_args.args[0](row)
        self.assertIsNone(self.adapter.retained_refinement['matches_failed_run'])
        self.assertIsNone(self.adapter.last_result)
        self.adapter.cmd_home_status(gcode)
        raw.register_response.call_args.args[0](dict(row, owner=0, token=0, status=0))
        self.assertIsNone(self.adapter.retained_refinement)
        self.assertIn('no native probe record', gcode.message)
        self.adapter.cmd_home_status(gcode)
        self.model.register_callback.call_args.args[0](self.model.time + 2.)
        self.assertIn('within two seconds', gcode.message)
        self.assertEqual(cmd.send.call_args_list, [mock.call([0]*7)] * 4)

    def test_bare_home_all_from_fluidd_or_oled_then_confirm(self):
        for mode in ('OFF', 'SERVICE', 'NATIVE'):
            for origin in ('fluidd', 'oled'):
                with self.subTest(mode=mode, origin=origin):
                    self.setUp()
                    model = self.model
                    model.native_mode, model.inactive_mode = mode == 'NATIVE', mode
                    menu, lines = self.operator_menu()
                    if origin == 'fluidd':
                        self.adapter.cmd_request_home(Command(AXES='XYZ', TARGET='205', PARK_Z='8'))
                        self.assertTrue(any('Yes / Start|INVENTURE_ZORTRAX' in line for line in lines))
                        self.assertIn(('Nozzle target: 205 C', None), menu.view.options)
                    else:
                        menu.open('home_xyz')
                    self.assertEqual((model.calls, model.mode_requests), ([], []))
                    token = menu.view.token
                    menu.choose(token, confirm=True)
                    self.assertEqual(menu.view.title, 'Home XYZ complete')
                    self.assertEqual(model.mode_requests,
                                     ['SERVICE'] if mode == 'NATIVE' else ['NATIVE','SERVICE'])
                    self.assertEqual([c[1] for c in model.calls if c[0] == 'G28'],
                                     ['Z','Y','X','Z'])
                    self.assertFalse([c for c in model.calls if c[0] == 'service_home'])
                    reference, = [c[1] for c in model.calls if c[0] == 'reference']
                    self.assertEqual(reference['ACK'], Z_HOME_ACK)
                    self.assertEqual(float(reference.get('TARGET', self.adapter.target)),
                                     205. if origin == 'fluidd' else self.adapter.target)
                    self.assertEqual(model.get_position()[2], 8. if origin == 'fluidd' else 10.)
                    before = list(model.calls)
                    with self.assertRaises(ValueError):
                        menu.choose(token, confirm=True)
                    self.assertEqual(before, model.calls)

    def test_operator_cold_axes_and_explicit_z_keep_script_behavior(self):
        for axes in ('X', 'Y', 'XY', 'Z', 'XYZ'):
            with self.subTest(axes=axes):
                self.setUp()
                self.model.axes = 'xy'
                self.model.native_mode = False
                menu, lines = self.operator_menu()
                options = {'ACK': Z_HOME_ACK} if 'Z' in axes else {}
                self.adapter.cmd_request_home(Command(AXES=axes, **options))
                self.assertEqual(self.model.mode_requests,
                                 ['NATIVE','SERVICE'] if 'Z' in axes else ['NATIVE'])
                self.assertEqual(menu.view, None)
                self.assertFalse(any('prompt_begin' in line for line in lines))
                self.assertEqual(any(c[0] == 'reference' for c in self.model.calls), 'Z' in axes)
                self.assertEqual(self.model.heater_target, 0.)

    def test_home_review_back_timeout_stop_or_new_guard_never_moves(self):
        for change in ('back', 'timeout', 'stop', 'tray', 'latch', 'print', 'service', 'jam'):
            with self.subTest(change=change):
                self.setUp()
                self.model.native_mode = False
                menu, lines = self.operator_menu()
                self.adapter.cmd_request_home(Command(AXES='XYZ'))
                token = menu.view.token
                if change == 'back':
                    menu.choose(token, index=0)
                elif change == 'timeout':
                    menu.view.deadline = menu.reactor.monotonic()
                elif change == 'stop':
                    menu.board.cmd_control_stop(Command())
                elif change == 'tray':
                    self.model.tray = 1
                elif change == 'latch':
                    self.model.control_latched = True
                elif change == 'service':
                    self.model.service_active = True
                elif change == 'jam':
                    self.model.filament_jam = types.SimpleNamespace(active=True)
                else:
                    self.model.print_state = 'paused'
                with self.assertRaises(ValueError):
                    menu.choose(token, confirm=True)
                self.assertEqual((self.model.calls, self.model.mode_requests), ([], []))

    def test_invalid_ack_parameters_and_busy_controller_do_not_arm(self):
        for options, settings in ((dict(ACK='typo'), {}), (dict(TARGET='nan'), {}),
                                  (dict(PARK_Z='200'), {}), ({}, dict(control_fault=True)),
                                  ({}, dict(service_active=True))):
            with self.subTest(options=options, settings=settings):
                self.setUp()
                self.model.native_mode = False
                self.operator_menu()
                for key, value in settings.items():
                    setattr(self.model, key, value)
                with self.assertRaises(ValueError):
                    self.adapter.cmd_request_home(Command(AXES='XYZ', **options))
                self.assertEqual((self.model.calls, self.model.mode_requests), ([], []))

    def test_refused_native_transition_and_loss_mid_sequence_do_not_rearm(self):
        self.operator_menu()
        self.model.native_mode = False
        self.model.control_reject = 'sensor_or_door_guard'
        with self.assertRaisesRegex(ValueError, 'guard'):
            self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
        self.assertFalse(any(c[0] in ('G28','reference','refine') for c in self.model.calls))
        self.assertEqual(self.model.mode_requests, ['NATIVE'])
        self.model.calls.clear()
        self.model.mode_requests.clear()
        self.model.control_reject = None
        home_bottom = self.adapter._home_bottom
        def lose_control():
            home_bottom()
            self.model.native_mode = False
        self.adapter._home_bottom = lose_control
        with self.assertRaisesRegex(ValueError, 'NATIVE'):
            self.adapter.cmd_request_home(Command(AXES='XYZ', ACK=Z_HOME_ACK))
        self.assertEqual(self.model.mode_requests, ['NATIVE'])
        self.assertEqual([c[1] for c in self.model.calls if c[0] == 'G28'], ['Z'])

    def test_xy_uses_y_then_x_with_temporary_clearance_and_scaled_speeds(self):
        self.adapter.cmd_home_axes(Command(AXES='XY'))
        self.assertEqual([call for call in self.model.calls if call[0] == 'G28'],
                         [('G28', 'Z'), ('G28', 'Y'), ('G28', 'X')])
        self.assertEqual(self.model.speeds[1:], [('y', 7.5, 3., 7.5), ('x', 7.5, 3., 7.5)])
        self.assertEqual(self.model.axes, 'xy')
        self.assertEqual(self.model.rails[0].homing_speed, 5.)
        self.assertEqual(self.model.rails[1].second_homing_speed, 2.)
        self.assertFalse(any(call[0] == 'heat' for call in self.model.calls))

    def test_x_and_y_can_be_homed_independently_with_known_clearance(self):
        for axis in ('X', 'Y'):
            with self.subTest(axis=axis):
                self.model.calls = []
                self.model.axes = 'z'
                self.adapter.last_result = dict(reference_established=True, coordinate_saved=True)
                self.adapter.cmd_home_axes(Command(AXES=axis, XY_SPEED_FACTOR='1.2'))
                self.assertEqual([call for call in self.model.calls if call[0] == 'G28'],
                                 [('G28', axis)])
                self.assertEqual(self.model.speeds[-1], (axis.lower(), 6., 2.4, 6.))
                self.assertIn('z', self.model.axes)

    def test_xyz_uses_native_reference_then_parks_absolutely_and_restores_heat(self):
        self.model.heater_target = 180.
        self.adapter.cmd_home_axes(Command(AXES='XYZ', ACK=Z_HOME_ACK, TARGET='205',
                                           PARK_Z='12', PARK_SPEED='4', PAD_SPEED='65',
                                           Z_APPROACH_MM='142', Z_PROBE_MM='3'))
        self.assertEqual([call for call in self.model.calls if call[0] == 'G28'],
                         [('G28', 'Z'), ('G28', 'Y'), ('G28', 'X')])
        calls = self.model.calls
        reference = next(i for i, call in enumerate(calls) if call[0] == 'reference')
        self.assertEqual(calls[reference][1]['ACK'], Z_HOME_ACK)
        self.assertEqual(float(calls[reference][1]['Z_APPROACH_MM']), 142.)
        self.assertEqual(float(calls[reference][1]['Z_PROBE_MM']), 3.)
        self.assertTrue(any(move[0] == 'move' and move[1][:2] == (82.8, 68.)
                            and move[2] == 65. for move in calls[:reference]))
        self.assertIn(('heat', 205., False), calls[:reference])
        self.assertIn(('move', (None, None, 12.), 4.), calls[reference + 1:])
        self.assertEqual(self.model.position, [82.8, 68., 12., 0.])
        self.assertEqual(self.model.axes, 'xyz')
        self.assertEqual(self.model.heater_target, 180.)

    def test_z_preserves_xy_homing_and_parks_ten_mm_without_changing_reference(self):
        self.model.axes = 'xy'
        self.adapter.cmd_home_axes(Command(AXES='Z', ACK=Z_HOME_ACK))
        self.assertEqual([call for call in self.model.calls if call[0] == 'G28'], [('G28', 'Z')])
        self.assertEqual(self.model.position, [82.8, 68., 10., 0.])
        self.assertAlmostEqual(self.model.physical_z,
                               self.model.threshold + 1. / Z_STEPS_PER_MM + 10.)
        self.assertTrue(self.adapter.last_result['coordinate_saved'])
        self.assertEqual(self.model.heater_target, 0.)

    def test_bad_preflight_inputs_leave_motion_and_heating_untouched(self):
        cases = [
            (Command(AXES='Z', ACK=Z_HOME_ACK), {}),
            (Command(AXES='XYZ'), {}),
            (Command(AXES='XYZ', ACK=Z_HOME_ACK, PARK_Z='200'), {}),
            (Command(AXES='XYZ', ACK=Z_HOME_ACK, TARGET='nan'), {}),
            (Command(AXES='XY', XY_SPEED_FACTOR='nan'), {}),
            (Command(AXES='XYZ', ACK=Z_HOME_ACK, PAD_SPEED='nan'), {}),
            (Command(AXES='Y'), {'native_mode': False}),
            (Command(AXES='X'), {'print_state': 'printing'}),
        ]
        for command, options in cases:
            with self.subTest(command=command, options=options):
                self.setUp()
                for key, value in options.items():
                    setattr(self.model, key, value)
                with self.assertRaises(ValueError):
                    self.adapter.cmd_home_axes(command)
                self.assertEqual(self.model.calls, [])

    def test_native_endstop_failure_restores_speeds_and_stops_composite(self):
        self.model.fail_axis = 'y'
        with self.assertRaises(ValueError):
            self.adapter.cmd_home_axes(Command(AXES='XYZ', ACK=Z_HOME_ACK))
        self.assertEqual([call for call in self.model.calls if call[0] == 'G28'],
                         [('G28', 'Z'), ('G28', 'Y')])
        self.assertEqual(self.model.rails[1].homing_speed, 5.)
        self.assertEqual(self.model.rails[1].second_homing_speed, 2.)
        self.assertEqual(self.model.rails[1].homing_retract_speed, 5.)
        self.assertFalse(any(call[0] in ('heat', 'reference') for call in self.model.calls))

    def test_closed_contact_at_bottom_blocks_lateral_homing(self):
        self.adapter.endstop = types.SimpleNamespace(query_endstop=lambda _: True)
        with self.assertRaisesRegex(ValueError, 'PC14'):
            self.adapter.cmd_home_axes(Command(AXES='XY'))
        self.assertEqual([call for call in self.model.calls if call[0] == 'G28'], [('G28', 'Z')])
        self.assertNotIn('z', self.model.axes)

    def test_probe_failure_clears_heat_skips_post_reference_move(self):
        self.model.axes = 'xy'
        self.model.heater_target = 180.
        self.adapter.cmd_home = mock.Mock(side_effect=ValueError('Test probe failed'))
        with self.assertRaises(ValueError):
            self.adapter.cmd_home_axes(Command(AXES='Z', ACK=Z_HOME_ACK))
        self.adapter.cmd_home.assert_called_once()
        self.assertEqual(self.model.heater_target, 0.)
        self.assertFalse(any(call[0] == 'move' and call[1][:2] == (None, None)
                             for call in self.model.calls))

    def test_heat_timeout_cools_without_running_contact_refinement(self):
        self.model.axes = 'xy'
        self.model.heater_busy = True
        self.adapter.heat_timeout = 2.
        with self.assertRaisesRegex(ValueError, 'temperature|heat|timeout'):
            self.adapter.cmd_home_axes(Command(AXES='Z', ACK=Z_HOME_ACK))
        self.assertFalse(any(call[0] == 'reference' for call in self.model.calls))
        self.assertEqual(self.model.heater_target, 0.)


if __name__ == '__main__':
    unittest.main()
