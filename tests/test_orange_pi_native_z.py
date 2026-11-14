"""Exercise the opt-in native Z adapter with a pinned Klipper API model."""
import importlib
import json
import sys
import types
import unittest
from unittest import mock

from orange_pi.inventure_z_home import Z_HOME_ACK, Z_STEPS_PER_MM


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
        action, owner, token, _, _, _, _ = data
        self.calls.append(('refine', tuple(data), start))
        if action == 1:
            assert self.guard and start
            self.refine_polls = 0
        else:
            assert action == 0 and not start
            self.refine_polls += 1
        if self.refine_polls < 2:
            cycles = self.refine_polls
            return dict(owner=owner, token=token, active=1, status=0, reject=0,
                        net_steps=0, best_steps=0, cycles=cycles,
                        non_improvements=0, up_steps=cycles, down_steps=cycles)
        self.refine_complete = True
        self.physical_z = self.threshold + 1. / Z_STEPS_PER_MM
        net = round((self.z - self.physical_z) * Z_STEPS_PER_MM)
        if self.contact_stays_closed:
            self.physical_z = self.threshold
        row = dict(owner=owner, token=token, active=0, status=1, reject=0,
                   net_steps=net, best_steps=net, cycles=self.refine_count + 1,
                   non_improvements=self.refine_count,
                   up_steps=net + self.refine_count, down_steps=self.refine_count)
        row.update(self.refine_reply_overrides)
        return row

    def _thermal_query_send(self, query, data, start=False):
        if query is self.refine:
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

    def get_float(self, key, default=None, **kwargs):
        return float(self.get(key, default))

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
            self.module = importlib.import_module('orange_pi.inventure_native_z')
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
                         [(23, 24, span, interval, 190, 208)] * 3)
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


if __name__ == '__main__':
    unittest.main()
