"""Exercise the five-point backend at its real Klipper command boundary."""
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from orange_pi import inventure_leveling_transport as transport
from orange_pi.inventure_factory_core import FactoryError, defaults
from test_orange_pi_door_policy import Command
from test_orange_pi_z_home_core import PlanModel
import test_orange_pi_z_home_host as z_host


def saved_pad_record():
    # Schema written by R23 ACTION=SAVE before measurement_settings was added.
    # The three measurements distinguish the completed FULL run from CENTER.
    t0 = dict(center_xy_mm=[81.7, 68.3625], z_release_mm_from_pe12=139.4089775561,
              z_release_spread_mm=0., xy_center_repeat_mm=.0328125,
              scan_method='SWEEP+TAP', edges_mm=[[77.9109375, 85.4890625], [64.53828125, 72.1859375]],
              electrical_diameter_mm=[7.578125, 7.64765625])
    t1 = dict(t0, center_xy_mm=[70.75, 68.7953125], z_release_mm_from_pe12=137.7880299252)
    return dict(schema=1, run_id='6fa661efce2343d9be8f294437391a65',
                prep='EMPTY', scan='SWEEP', measurement_target_c=100.,
                measurements={'T0': t0, 'T1': t1, 'T0_RETURN': dict(t0, z_release_mm_from_pe12=139.4289276808)},
                candidate=dict(t1_offset_mm={'X': 10.95, 'Y': -.432812},
                               t1_lower_by_mm=1.630923, t0_reseat_mm={'X': 0., 'Y': 0., 'Z': .01995},
                               t1_carriage_shift_mm={'X': -10.95, 'Y': .432812}))


class PlatformTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name) / 'printer.cfg'
        self.record_path = self.config.with_name('inventure_nozzle_pad.json')

    def write_record(self, record):
        self.record_path.write_text(json.dumps(record))

    def host(self):
        board = z_host.ZHomeHostTests().host(edges=True)
        board.allow_actuators = True
        board.nozzle_z_dual_abi = 1
        board.printer.get_start_args = lambda: {'config_file': str(self.config)}
        self.write_record(saved_pad_record())
        return board

    def test_real_saved_t0_center_is_translated_once_for_all_five_ids(self):
        center, z = transport.platform_reference(saved_pad_record(), defaults(0))
        self.assertAlmostEqual(center[0], 81.7)
        self.assertAlmostEqual(center[1], 68.3625)
        self.assertTrue(111800 <= z <= 111825)
        pairs = (((127.5, 13.), (141.7, 15.8625)),
                 ((7.5, 13.), (21.7, 15.8625)),
                 ((7.5, 118.), (21.7, 120.8625)),
                 ((127.5, 118.), (141.7, 120.8625)),
                 ((67.5, 65.5), (81.7, 68.3625)))
        for point, expected in pairs:
            with self.subTest(point=point):
                xy = transport.raw_point(point, center)
                self.assertAlmostEqual(xy[0], expected[0])
                self.assertAlmostEqual(xy[1], expected[1])

    def test_invalid_saved_results_are_rejected_before_planning(self):
        def invalid(record, path, value):
            for key in path[:-1]:
                record = record[key]
            record[path[-1]] = value
        for path, value in ((('schema',), 18), (('schema',), True), (('candidate',), None),
                            (('measurement_settings',), [1]),
                            (('measurement_settings',), {'xy_repeat_mm': math.nan}),
                            (('measurement_settings',), {'z_repeat_mm': False}),
                            (('measurements',), {}),
                            (('measurements', 'T1'), None),
                            (('measurements', 'T0_RETURN'), {}),
                            (('measurements', 'T0_RETURN', 'center_xy_mm'), [85., 68.3625]),
                            (('candidate', 't1_lower_by_mm'), 9.0),
                            (('measurements', 'T0', 'center_xy_mm'), [81.7]),
                            (('measurements', 'T0', 'center_xy_mm'), [81.7, math.nan]),
                            (('measurements', 'T0', 'center_xy_mm'), [False, 68.36]),
                            (('measurements', 'T0', 'z_release_mm_from_pe12'), math.inf)):
            with self.subTest(path=path, value=value):
                record = saved_pad_record()
                invalid(record, path, value)
                with self.assertRaises(ValueError):
                    transport.platform_reference(record, defaults(0))
        record = saved_pad_record()
        record['measurements'].pop('T1')
        with self.assertRaises(ValueError):
            transport.platform_reference(record, defaults(0))

    def test_entire_tray_must_fit_inside_service_limits(self):
        record = saved_pad_record()
        # Center fits the envelope; the two right pads would be outside it.
        for sample in record['measurements'].values():
            x, y = sample['center_xy_mm']
            sample['center_xy_mm'] = [x + 21.3, y]
        with self.assertRaisesRegex(ValueError, '(?i)(zakres|envelope)'):
            transport.platform_reference(record, defaults(0))

    def assert_start_rejected_without_actuation(self, board, reason, **values):
        with self.assertRaisesRegex(RuntimeError, reason):
            board.cmd_platform_level(Command(**{'ACTION': 'START', **values}))
        actuations = [p for p in board.device.packets
                      if p[0] in ('motion', 'output') and p[1] == 1]
        self.assertEqual(actuations, [])
        self.assertFalse(board.device.edge_starts)
        self.assertEqual(board.platform_level_status()['phase'], 'idle')

    def test_active_print_and_paused_print_cannot_start(self):
        for state in ('printing', 'paused'):
            with self.subTest(state=state):
                board = self.host()
                board.printer.objects['print_stats'] = SimpleNamespace(get_status=lambda now: {'state': state})
                self.assert_start_rejected_without_actuation(board, 'print|druk')

    def test_existing_owners_survive_rejected_start(self):
        for owner in ('load_test_context', 'feeder_run_context', 'nozzle_z_context'):
            with self.subTest(owner=owner):
                board = self.host()
                existing = {}
                setattr(board, owner, existing)
                self.assert_start_rejected_without_actuation(board, '(?i)(active|procedure|busy|owns|in progress|aktywn)')
                self.assertIs(getattr(board, owner), existing)

    def test_missing_pad_record_and_open_tray_cannot_start(self):
        board = self.host()
        self.record_path.unlink()
        self.assert_start_rejected_without_actuation(board, '(?i)(pad|reference)')
        board = self.host()
        board.device.tray = False
        self.assert_start_rejected_without_actuation(board, '(?i)(tray|PC13|tack)')

    def test_pending_native_heater_at_zero_duty_cannot_be_stolen(self):
        board = self.host()
        hotend = SimpleNamespace(target_temp=260., last_pwm_value=0.)
        board.printer.objects['heaters'] = SimpleNamespace(heaters={'extruder': hotend})
        self.assert_start_rejected_without_actuation(board, '(?i)(heater|grzał|cele Klippera)')
        self.assertEqual(hotend.target_temp, 260.)

    def test_t1_native_limit_rejects_both_nozzle_start_without_leaving_a_timer(self):
        board = self.host()
        board.shared_adc_sensors = ((), (SimpleNamespace(maximum=180.),))
        self.assert_start_rejected_without_actuation(board, '(?i)(T1|200)', ACTIVE_TOOL='T0')
        self.assertIsNone(board.load_test_context)
        board.reactor.pause(board.reactor.now + 1.)
        self.assertFalse(board.device.edge_starts)
        self.assertEqual(board.device.packets, [])

    def test_only_a_verified_native_binding_identifies_the_initial_nozzle(self):
        board = self.host()
        ops = board.printer.objects['inventure_operations']
        ops.state.selector_tool = 1
        self.assertEqual(transport._initial_tool(board), 'UNKNOWN')
        board.printer.objects['inventure_tools'] = SimpleNamespace(enabled=True, require_binding=lambda: None)
        self.assertEqual(transport._initial_tool(board), 'T1')
        ops.state.selector_tool = 0
        self.assertEqual(transport._initial_tool(board), 'T0')

        def stale_binding():
            raise RuntimeError('Native binding lost')

        board.printer.command_error = RuntimeError
        board.printer.objects['inventure_tools'].require_binding = stale_binding
        self.assertEqual(transport._initial_tool(board), 'UNKNOWN')

    def test_unknown_selector_requires_dual_heat_support_before_any_motion(self):
        board = self.host()
        board.nozzle_z_dual_abi = 0
        self.assert_start_rejected_without_actuation(board, 'dual-nozzle')

    def test_common_park_is_inside_both_configured_purge_paths(self):
        t0 = dict(defaults(0), center_x=88., center_y=152.5, purge_span=20.)
        t1 = dict(t0, center_x=80., purge_span=12.)
        self.assertEqual(transport._initial_bin('UNKNOWN', t0, t1), (82., 152.5))
        for profile in (dict(t1, center_x=66.), dict(t1, center_y=154.5)):
            with self.assertRaisesRegex(ValueError, 'waste-bin'):
                transport._initial_bin('UNKNOWN', t0, profile)

    def test_unknown_selector_honors_each_configured_nozzle_power(self):
        board = self.host()
        board.procedure_profiles = ({}, {'max_power': 40.}, {'max_power': 25.})
        board.cmd_platform_level(Command(ACTION='START'))
        heaters = board.load_test_context['factory']['dual_heaters']
        self.assertEqual([heater['power'] for heater in heaters], [40., 25.])

    def test_incompatible_custom_purge_paths_fail_before_starting(self):
        board = self.host()
        board.procedure_profiles = ({}, {}, {'center_y': 149.})
        self.assert_start_rejected_without_actuation(board, 'waste-bin')

    def test_open_doors_with_global_interlock_enabled_block_start(self):
        board = self.host()
        board.door_policy_global = True
        board._door_policy_query = lambda: dict(enabled=True, front_door_closed=False,
                                                lid_closed=True, reject=None)
        self.assert_start_rejected_without_actuation(board, '(?i)(door|drzwi)')

    def test_front_right_probe_waits_for_all_151_releases_after_the_last_minimum(self):
        for later_minimum in (False, True):
            with self.subTest(later_minimum=later_minimum):
                model = PlanModel(SPEED_PROFILE='OEM')
                program = transport.PlatformLevelTransport(model.p, model.settings, (81.7, 68.34),
                    111800, {'initial_tool': 'T0'}, (82.8, 152.5))
                self.assertIsNone(program.settings['z_max_cycles'])
                self.assertEqual(program.settings['z_stable_cycles'], 101)
                model.program = program
                program.history.append('LEVEL_POINT')
                model.position = program.z_steps = 109344
                model.contact_at, model.release_at = 111860, 111856
                # Construct an edge trace consistent with the reported minimum
                # of 111851 on cycle 428 and the next 83 observed releases.
                model.change_after_release = {119: 111855, 239: 111854, 359: 111853, 427: 111852}
                if later_minimum:
                    model.change_after_release[539] = 111851
                search = program._reference(15, 151)
                response, progress = None, {}
                with self.assertRaises(StopIteration) as done:
                    while True:
                        action = search.send(response)
                        response = None
                        if action['kind'] == 'z_contact_edge':
                            response = model._edge(action)
                        elif action['kind'] == 'z_progress':
                            progress[action['qualified_cycles']] = action['non_improvements']
                self.assertEqual([progress[n] for n in (471, 493, 511, 512)], [43, 65, 83, 84])
                last = 691 if later_minimum else 579
                self.assertEqual((model.releases, progress[last]), (last, 151))
                self.assertEqual(done.exception.value, 111850 if later_minimum else 111851)

    def test_ordinary_oem_z_home_still_stops_at_512_cycles(self):
        model = PlanModel(SPEED_PROFILE='OEM')
        model.change_after_release = {n: 2486 - n // 50 for n in range(50, 512, 50)}
        with self.assertRaisesRegex(FactoryError, 'z_contact_cycle_limit') as error:
            model.run()
        self.assertEqual((error.exception.details['cycles'], model.releases), (512, 512))

    def test_oem_search_reversing_across_the_mcu_leg_limit(self):
        # A 15 mm search in both directions may request a 30 mm return.
        # The R13 wire caps a single leg at 16040 steps. A success on the
        # shorter second leg must account for the complete first leg too.
        program = transport.PlatformLevelTransport.__new__(transport.PlatformLevelTransport)
        program.z_steps = 111800
        program.settings = {'z_contact_interval_us': 4000}
        search = program._z_edge(1, 21000)
        first = next(search)
        self.assertEqual((first['start_steps'], first['steps']), (111800, 16040))
        second = search.send(dict(status=2, sent_steps=16040, end_raw=0))
        self.assertEqual((second['start_steps'], second['steps']), (95760, 4960))
        with self.assertRaises(StopIteration) as done:
            search.send(dict(status=1, sent_steps=307, end_raw=1))
        self.assertEqual(done.exception.value, (True, 16347))
        self.assertEqual(program.z_steps, 95453)

    def paused_with_global_interlock(self, while_stopping=None):
        from test_orange_pi_leveling_sequence import PlatformDevice
        from test_orange_pi_load_test import Wire

        board = self.host()
        device = board.device = PlatformDevice(board)
        board._fresh_inputs = device.inputs
        pending = [while_stopping] if while_stopping else []

        def control(args):
            if args[0] == 2 and pending and board.platform_level_run['phase'] == 'await_knobs':
                pending.pop()(board)
            return device.control(args)

        board.commands.update(control=Wire(control), output=Wire(device.out),
                              motion=Wire(device.move), motion_driver=Wire(device.driver),
                              factory_session=Wire(device.query_factory), z_edge=Wire(device.z_edge))
        doors = dict(enabled=True, front_door_closed=True, lid_closed=True,
                     motor_busy=False, lock_command_mask=0, reject=None)
        board.door_policy_global = True
        board._door_policy_query = lambda: dict(doors)
        board.cmd_platform_level(Command(ACTION='START'))
        limit = board.reactor.now + 200.
        while not board.platform_level_status().get('door_pause'):
            self.assertIsNotNone(board.load_test_context, board.platform_level_status())
            self.assertLess(board.reactor.now, limit, board.platform_level_status())
            board.reactor.pause(board.reactor.now + .25)
        state = board.platform_level_status()
        self.assertTrue(state['awaiting_operator'], state)
        self.assertEqual(state['phase'], 'await_knobs')
        self.assertFalse(device.active())
        self.assertEqual(device.output(6)['duty'], 0)
        self.assertEqual(device.output(7)['duty'], 0)
        return board, device, doors, dict(ACTION='NEXT', RUN_ID=state['run_id'], PROMPT_ID=state['prompt_id'])

    @staticmethod
    def actuation_counters(device):
        return (len(device.z_moves), len(device.edge_starts),
                sum(p[0] != 0 and p[0] != 3 for p in device.factory_packets),
                sum(p[:2] in (('output', 1), ('motion', 1)) for p in device.packets))

    def test_interlocked_operator_pause_refuses_next_while_open_and_cancel_has_no_restart(self):
        board, device, doors, next_prompt = self.paused_with_global_interlock()
        frozen = self.actuation_counters(device)
        doors['front_door_closed'] = False
        with self.assertRaisesRegex(RuntimeError, '(?i)(door|drzwi|zamkn)'):
            board.cmd_platform_level(Command(**next_prompt))
        self.assertTrue(board.platform_level_status()['awaiting_operator'])
        board.reactor.pause(board.reactor.now + 5.)
        self.assertEqual(self.actuation_counters(device), frozen)
        board.cmd_platform_level(Command(ACTION='CANCEL', RUN_ID=next_prompt['RUN_ID']))
        doors['front_door_closed'] = True
        with self.assertRaises(RuntimeError):
            board.cmd_platform_level(Command(**next_prompt))
        board.reactor.pause(board.reactor.now + 3.)
        self.assertEqual(self.actuation_counters(device), frozen)
        self.assertEqual(board.platform_level_status()['phase'], 'cancelled')

    def test_interlocked_prompt_is_private_until_stop_ack_and_cancel_wins_over_next(self):
        def stop_in_flight(board):
            status = board.platform_level_status()
            self.assertFalse(status['awaiting_operator'])
            with self.assertRaises(RuntimeError):
                board.cmd_platform_level(Command(ACTION='NEXT', RUN_ID=status['run_id'],
                                                 PROMPT_ID=status['prompt_id']))

        board, device, doors, next_prompt = self.paused_with_global_interlock(stop_in_flight)
        frozen = self.actuation_counters(device)

        def cancel_during_readback():
            board._door_policy_query = lambda: dict(doors)
            board.cmd_platform_level(Command(ACTION='CANCEL', RUN_ID=next_prompt['RUN_ID']))
            return dict(doors)

        board._door_policy_query = cancel_during_readback
        with self.assertRaises(RuntimeError):
            board.cmd_platform_level(Command(**next_prompt))
        self.assertEqual(board.platform_level_status()['phase'], 'cancelled')
        board.reactor.pause(board.reactor.now + 3.)
        self.assertEqual(self.actuation_counters(device), frozen)

    def test_interlocked_timeout_or_removed_tray_during_access_cannot_resume(self):
        for fault in ('timeout', 'removed_tray', 'disconnect'):
            with self.subTest(fault=fault):
                board, device, doors, next_prompt = self.paused_with_global_interlock()
                frozen = self.actuation_counters(device)
                doors['lid_closed'] = False
                if fault == 'timeout':
                    board.load_test_context['factory']['total_deadline'] = board.reactor.now + .15
                elif fault == 'removed_tray':
                    device.tray = False
                else:
                    board.connected = False
                board.reactor.pause(board.reactor.now + 2.)
                self.assertEqual(board.platform_level_status()['phase'], 'failed', board.platform_level_status())
                self.assertIsNone(board.load_test_context)
                doors['lid_closed'] = True
                board.connected, device.tray = True, True
                with self.assertRaises(RuntimeError):
                    board.cmd_platform_level(Command(**next_prompt))
                board.reactor.pause(board.reactor.now + 2.)
                self.assertEqual(self.actuation_counters(device), frozen)
                self.assertFalse(device.active())
