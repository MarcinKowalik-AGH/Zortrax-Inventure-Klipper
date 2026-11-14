"""Exercise the opt-in native Z adapter with a pinned Klipper API model."""
import importlib
import sys
import types
import unittest
from unittest import mock

from orange_pi.inventure_z_home import Z_HOME_ACK, Z_STEPS_PER_MM


class Model:
    def __init__(self):
        self.z = 145.
        self.physical_z = self.z
        self.axes = 'xy'
        self.calls = []
        self.edge_calls = []
        self.timeout_edge = None
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

    def lookup_object(self, name, default=None):
        return self if name == 'gcode_move' else default

    def is_shutdown(self):
        return self.stopped

    def is_motor_enabled(self):
        return not self.stopped

    def wait_moves(self):
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
        return dict(mode='NATIVE', armed=not self.stopped, fault=0, latched=0)

    def _z_home_inputs(self, expected):
        if self.tray:
            raise ValueError('Tray absent')
        return {'z_bottom': int(self.bottom.query_endstop(0))}

    def panel_temperature(self, tool, now):
        return (200., 'valid')

    def _control_validate(self, row, limits):
        assert all(lo <= row[key] <= hi for key, (lo, hi) in limits.items())

    def _control_next_token(self):
        return 23

    def _thermal_query_send(self, query, data, start=False):
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
        return trigger


class Command(dict):
    @staticmethod
    def error(message):
        return ValueError(message)

    def get_float(self, key, default=None, **kwargs):
        return float(self.get(key, default))

    def respond_info(self, message):
        self.message = message


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
        self.adapter.running = self.adapter.interrupted = False
        self.adapter.last_result = None
        self.adapter.pad_x, self.adapter.pad_y = 67.5, 65.5
        self.adapter.bottom_position = 145.
        self.adapter.bottom_release = 5.
        self.adapter.coarse_speed = 2.
        self.adapter.fine_speed = .3
        self.adapter.target = 200.
        self.adapter.repeat_count = 2
        self.command = Command(ACK=Z_HOME_ACK, Z_APPROACH_MM=135, Z_PROBE_MM=8)

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


if __name__ == '__main__':
    unittest.main()
