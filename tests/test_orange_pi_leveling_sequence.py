"""Real R26 command dispatcher with an independent R13 wire/pad model."""
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from test_orange_pi_door_policy import Command, policy
from test_orange_pi_load_test import Wire
from test_orange_pi_leveling_transport import saved_pad_record
import test_orange_pi_z_home_host as z_host


class PlatformDevice(z_host.ZDevice):
    # Locations physically established by the R23 results supplied to this test.
    pads = ((141.7, 15.8625), (21.7, 15.8625), (81.7, 68.3625),
            (141.7, 120.8625), (21.7, 120.8625))

    def __init__(self, host):
        super().__init__(host)
        self.xy = (0., 0.)
        self.z = 1000
        self.contact_at = None
        self.edge_immediate = True
        self.physical_tool = None
        self.first_heating_xy = None
        self.press_temperatures = []
        self.sample_points = []
        self.phase_at_power_on = []
        self.rear_adjusted = False
        self.right_low = False
        self.fail_final_stop = False
        self.doors_closed = True
        self.door_lock_enabled = True
        self.release_shift = 0
        self.z_rising = False

    def door_policy(self, args):
        assert args[0] == 0, 'Platform leveling must preserve the global door policy'
        busy = self.active()
        return policy(enabled=int(self.door_lock_enabled), disable_bit=int(not self.door_lock_enabled),
                      closed_mask=3 if self.doors_closed else 0, motor_busy=int(busy))

    def inputs(self):
        row = super().inputs()
        for name in ('front_door', 'lid'):
            if self.doors_closed:
                row['raw_mask'] |= 1 << self.h.INPUT_NAMES.index(name)
            row['raw'][name] = int(self.doors_closed)
        return row

    def _raw_z(self, position, contact):
        raw = super()._raw_z(position, contact)
        if self.z_rising and 0 <= position <= self.release_shift:
            raw |= self.BOTTOM
        return raw

    def control(self, args):
        reply = super().control(args)
        if args[0] == 2 and self.fail_final_stop and self.h.platform_level_run['phase'] == 'finishing':
            # The reply to the final stop arrives after all five points.
            assert self.h.platform_level_status()['candidate_depth_um'] is None
            self.fail_final_stop = False
            return dict(reply, active=1)
        return reply

    def out(self, args):
        if args[0] == 1 and args[2]:
            assert self.doors_closed or not self.door_lock_enabled
            if self.first_heating_xy is None:
                self.first_heating_xy = self.xy
            self.phase_at_power_on.append((args[1], self.h.platform_level_run['phase']))
        return super().out(args)

    def move(self, args):
        if args[0] != 1:
            return super().move(args)
        assert self.doors_closed or not self.door_lock_enabled
        _, directions, a, b, z, e, _, _, _, mask, _, _, _ = args
        if self.edge and 'terminal' in self.edge:
            self.edge = None
            self.motion = None  # The separate edge engine now owns the true Z position.
        if z:
            self.z_rising = bool(directions & self.BOTTOM)
        if not (a or b):
            return super().move(args)
        assert not self.contact and not e
        if mask & 3:
            self.xy = tuple(0. if mask & (1 << i) else coord
                            for i, coord in enumerate(self.xy))
        else:
            assert not mask & self.CONTACT or not self.contact
            da = a if directions & 1 else -a
            db = b if directions & 2 else -b
            self.xy = (self.xy[0] + (da + db) / 1280.,
                       self.xy[1] + (da - db) / 1280.)
            if math.dist(self.xy, (154.5, 154.5)) < .01:
                self.physical_tool = 'T0'
                self.press_temperatures.append(tuple(self.h.temperatures[:2]))
        # Retain physical guards; only an endstop that can actually change on
        # this XY leg is an eligible hit in the common fixture's move engine.
        return super().move([*args[:9], mask & 3, args[10] & 3, *args[11:]])

    def z_edge(self, args):
        if args[0] == 1:
            assert self.doors_closed or not self.door_lock_enabled
            assert self.session and self.session['active'] and not self.session['dual']
            assert self.session['tool'] == 0 and not self.h.platform_level_run['operator_confirmed']
            assert self.physical_tool == 'T0' and self.press_temperatures, 'No T0 press before probing'
            distances = [math.dist(self.xy, xy) for xy in self.pads]
            nearest = distances.index(min(distances))
            assert distances[nearest] < .01, 'PC14 sensing away from conductive pad: %r' % (self.xy,)
            # Rear left is lower until the operator follows its prompt.
            low = (nearest == 4 and not self.rear_adjusted) or (nearest == 3 and self.right_low)
            self.contact_at = 111808 + self.release_shift + (250 if low else 0)
            if args[3] == 2:
                self.sample_points.append(nearest)
        return super().z_edge(args)


class SequenceTests(unittest.TestCase):
    def simulate(self, active, fail_final_stop=False, door_lock=False, both_rear=False,
                 open_throughout=False, binding=True):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'printer.cfg'
            saved = json.dumps(saved_pad_record())
            pad_file = config.with_name('inventure_nozzle_pad.json')
            pad_file.write_text(saved)
            board = z_host.ZHomeHostTests().host(edges=True)
            board.allow_actuators = True
            board.printer.get_start_args = lambda: {'config_file': str(config)}
            board.nozzle_z_dual_abi = 1
            board.device = device = PlatformDevice(board)
            device.physical_tool = active
            if binding:
                board.printer.objects['inventure_tools'] = SimpleNamespace(enabled=True, require_binding=lambda: None)
                board.printer.objects['inventure_operations'].state.selector_tool = int(active[1])
            device.door_lock_enabled = door_lock
            device.doors_closed = not open_throughout
            device.fail_final_stop = fail_final_stop
            device.right_low = both_rear
            board._fresh_inputs = device.inputs
            board.commands.update(control=Wire(device.control), output=Wire(device.out),
                                  motion=Wire(device.move), motion_driver=Wire(device.driver),
                                  factory_session=Wire(device.query_factory), z_edge=Wire(device.z_edge))
            board.door_policy_global = True
            board.commands['door_policy'] = Wire(device.door_policy)
            board.cmd_platform_level(Command(ACTION='START'))
            prompts = []
            previous_rear = None
            until = board.reactor.now + 3605.
            while board.load_test_context is not None:
                run = board.platform_level_status()
                if run['awaiting_operator']:
                    prompts.append(run['phase'])
                    if door_lock:
                        self.assertTrue(run['door_pause'])
                        self.assertFalse(device.active())
                        self.assertEqual([device.output(i)['duty'] for i in (6, 7)], [0, 0])
                        self.assertEqual(device.door_policy((0, 1))['lock_command_mask'], 0)
                        device.doors_closed = False
                        before = len(device.packets), len(device.edge_starts)
                        with self.assertRaisesRegex(RuntimeError, 'owns the actuators'):
                            board.cmd_door_policy(Command(ENABLE=0))
                        self.assertEqual(device.door_policy((0, 1))['enabled'], 1)
                        with self.assertRaisesRegex(RuntimeError, '(?i)(drzwi|pokryw|door|lid)'):
                            board.cmd_platform_level(Command(ACTION='NEXT', RUN_ID=run['run_id'],
                                                             PROMPT_ID=run['prompt_id']))
                        board.reactor.pause(board.reactor.now + 10.)
                        self.assertTrue(board.platform_level_status()['awaiting_operator'])
                        self.assertEqual((len(device.packets), len(device.edge_starts)), before)
                        device.doors_closed = True
                    elif open_throughout:
                        self.assertFalse(run['door_pause'])
                        contacts = device.inputs()['raw']
                        self.assertEqual((contacts['front_door'], contacts['lid']), (0, 0))
                    if run['phase'] == 'adjust':
                        if door_lock:
                            # A PE12 release that shifted by 31 electrical steps after
                            # re-homing also shifts pad coordinates in the new raw frame.
                            device.release_shift = 31
                        instruction = run['instructions'][0]
                        self.assertEqual(instruction['direction'], 'clockwise')
                        if previous_rear is None:
                            self.assertEqual(instruction['knob'], 'rear_left')
                            device.rear_adjusted = True
                            previous_rear = (len(device.z_moves), len(device.edge_starts),
                                             device.run, len(device.phase_at_power_on), run['prompt_id'])
                        else:
                            self.assertTrue(both_rear)
                            self.assertEqual(instruction['knob'], 'rear_right')
                            device.right_low = False
                            self.assertEqual(previous_rear,
                                             (len(device.z_moves), len(device.edge_starts), device.run,
                                              len(device.phase_at_power_on), run['prompt_id'] - 1))
                    board.cmd_platform_level(Command(ACTION='NEXT', RUN_ID=run['run_id'],
                                                     PROMPT_ID=run['prompt_id']))
                self.assertLess(board.reactor.now, until)
                board.reactor.pause(board.reactor.now + .25)
            run = board.platform_level_status()
            if fail_final_stop:
                self.assertEqual(run['phase'], 'failed', run)
                self.assertIsNone(run['candidate_depth_um'])
                self.assertEqual(run['measurement_count'], 7)
                self.assertIn('confirm', run['error'])
                with self.assertRaisesRegex(RuntimeError, 'candidate'):
                    board.cmd_platform_level(Command(ACTION='SAVE', RUN_ID=run['run_id']))
                self.assertFalse(config.with_name('inventure_platform_level.json').exists())
                self.assertEqual(pad_file.read_text(), saved)
                return
            self.assertEqual(run['phase'], 'candidate', (run.get('error'), board.load_test_last.get('reason')))
            self.assertEqual(run['reference_steps'], 0)
            self.assertEqual(set(run['heights_steps'].values()), {0})
            self.assertEqual(prompts, ['await_knobs', 'adjust'] + (['adjust'] if both_rear else []))
            self.assertEqual(run['instruction_count'], 2 if both_rear else 1)
            self.assertEqual(device.sample_points, [2, 0, 1, 4, 3, 3, 4, 2])
            self.assertEqual(len(device.press_temperatures), 2 if door_lock else 1)
            if door_lock:
                self.assertEqual([row['difference_steps'] for row in run['resume_datums']], [31])
                self.assertGreaterEqual(device.press_temperatures[-1][0], 195.)
            temperature = device.press_temperatures[0]
            self.assertGreaterEqual(temperature[0], 195.)
            expected_bin_x = (82.8 if active == 'T0' else 72.) if binding else 77.4
            self.assertLess(math.dist(device.first_heating_xy, (expected_bin_x, 152.5)), .01)
            if active == 'T1' or not binding:
                self.assertGreaterEqual(temperature[1], 195.)
            else:
                self.assertLess(temperature[1], 50.)
            self.assertFalse(any(phase in ('preparing', 'await_knobs')
                                 for channel, phase in device.phase_at_power_on))
            self.assertEqual({channel for channel, _ in device.phase_at_power_on},
                             {6, 7} if active == 'T1' or not binding else {6})
            self.assertEqual(device.z, 0)
            self.assertLess(math.dist(device.xy, (82.8, 152.5)), .01)
            self.assertEqual([device.output(i)['duty'] for i in (6, 7)], [0, 0])
            board.cmd_platform_level(Command(ACTION='SAVE', RUN_ID=run['run_id']))
            self.assertEqual(board.platform_level_status()['phase'], 'saved')
            self.assertEqual(pad_file.read_text(), saved)
            saved_level = json.loads(config.with_name('inventure_platform_level.json').read_text())
            self.assertGreater(saved_level['depth_um'], 130000)
            self.assertEqual(saved_level['initial_tool'], active if binding else 'UNKNOWN')
            if door_lock:
                self.assertEqual(saved_level['resume_datums'], run['resume_datums'])
                self.assertTrue(saved_level['global_door_enabled'])

    def test_from_t0(self):
        self.simulate('T0')

    def test_from_t1(self):
        self.simulate('T1')

    def test_unknown_native_binding_with_either_nozzle_physically_lowered(self):
        for active in ('T0', 'T1'):
            with self.subTest(initial_tool=active):
                self.simulate(active, binding=False)

    def test_terminal_stop_must_succeed_before_save(self):
        self.simulate('T0', fail_final_stop=True)

    def test_disabled_global_door_policy_allows_open_doors_and_lid_throughout(self):
        for active in ('T0', 'T1'):
            with self.subTest(initial_tool=active):
                self.simulate(active, open_throughout=True)

    def test_global_lock_manual_access_then_closed_doors_and_measured_datum(self):
        self.simulate('T1', door_lock=True)

    def test_lock_waits_for_both_rear_knobs_before_reheating_or_moving(self):
        self.simulate('T0', door_lock=True, both_rear=True)
