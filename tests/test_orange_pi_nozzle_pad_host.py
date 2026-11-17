"""Exercise pad XYZ calibration against timed heater and contact models."""
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from orange_pi.inventure_z_home import Z_STEPS_PER_MM
from test_orange_pi_door_policy import Command
from test_orange_pi_load_test import Wire
from test_orange_pi_nozzle_z import DualNozzleDevice
import test_orange_pi_z_home_host as z_host_support


class PadDevice(DualNozzleDevice):
    centers = ((82.74, 68.11), (71.95, 67.96))
    touch_heights = (round(137.10 * Z_STEPS_PER_MM), round(136.98 * Z_STEPS_PER_MM))

    def __init__(self, host):
        super().__init__(host)
        self.calls = []
        self.e_moves = []
        self.pad_moves = []
        self.sweep_moves = []
        self.contact_radius_mm = 2.5
        self.first_sweep_open_steps = None
        self.unreliable_sweep_seed = None
        self.cold_switches = []
        self.xy = (0., 0.)
        self.z = 20 * Z_STEPS_PER_MM
        self.hot = (240., 235.)
        self.wipe_moves = []
        self.ceiling = None
        self.sealed = False
        self.heater_update_time = self.now()

    def refresh_heaters(self, context):
        now = self.now()
        elapsed = now - self.heater_update_time
        self.heater_update_time = now
        for heater in (context.get('factory') or {}).get('dual_heaters', ()):
            tool = heater['tool']
            target = heater['target']
            previous = self.h.temperatures[tool]
            if previous > target:
                temperature = max(target, previous - 9. * elapsed)
            elif self.output(6 + tool)['duty']:
                temperature = target
            else:
                continue
            self.h.temperatures[tool] = temperature
            self.h.raw_adc[tool] = round(temperature * 4095. / 660.)

    def _settle(self):
        super()._settle()
        row = self.motion
        if row is not None and 'xy_start' in row:
            fraction = self.fraction()
            self.xy = tuple(p + d * fraction for p, d in zip(row['xy_start'], row['xy_delta']))
            if 'sweep_steps' in row and self.now() >= row['until'] and not row['stopped']:
                self.contact = not bool(row['hit'] & self.CONTACT)

    def fraction(self):
        fraction = super().fraction()
        row = self.motion
        if row is not None and 'sweep_steps' in row and not row['stopped']:
            return fraction * row['sweep_steps'] / row['counts'][0]
        return fraction

    def driver(self, args):
        result = super().driver(args)
        if 'sweep_steps' in self.motion and self.now() >= self.motion['until'] and not self.motion['stopped']:
            result['sent'] = self.motion['sweep_steps'] if args[0] in (0, 1) else 0
        return result

    def _begin_sweep(self, args):
        _, directions, a, b, z, e, rate, _, _, _, _, _, token = args
        tool = self.session['tool']
        assert self.sealed and self.contact and self.tray and a == b > 0 and not (z or e)
        sign_a = 1 if directions & 1 else -1
        sign_b = 1 if directions & 2 else -1
        step_x = (sign_a + sign_b) / 1280.
        step_y = (sign_a - sign_b) / 1280.
        distance, hit = a, 0
        for sent in range(1, a + 1):
            point = (self.xy[0] + sent * step_x, self.xy[1] + sent * step_y)
            if math.dist(point, self.centers[tool]) > self.contact_radius_mm:
                distance, hit = sent, self.CONTACT
                break
        if not self.sweep_moves and self.first_sweep_open_steps is not None:
            distance = self.first_sweep_open_steps
            hit = self.CONTACT
        duration = distance / rate + .02
        self.packets.append(('motion', *args))
        self.run += 1
        self.motion = dict(token=token, counts=(a, b, 0, 0), directions=directions,
            xy_start=self.xy, xy_delta=(a * step_x, b * step_y),
            sweep_steps=distance, until=self.now() + duration, started=self.now(),
            duration=duration, stopped=False, home=False, hit=hit)
        self.sweep_moves.append(dict(tool=tool, start=self.xy, sent=distance, planned=a, direction=directions,
                                     hit=hit, z=self.z))
        self.motion_starts.append(dict(t=self.now(), arguments=tuple(args),
            duty=(self.output(6)['duty'], self.output(7)['duty']),
            temperatures=tuple(self.h.temperatures[:2])))
        return super().move([0, *args[1:]])

    def query_factory(self, args):
        self.calls.append(tuple(args))
        action, tool, mode, monitor, _, budget, _, low, high, token, seq = args
        assert mode == monitor == 0
        if action == 8:
            assert not self.output(6)['duty'] and not self.output(7)['duty']
            self.cold = (low, high) == (0, 50)
            if self.cold:
                assert tool == 0 or self.h.nozzle_pad_cold_abi == 2
                assert not budget and all(0. <= temp < 50. for temp in self.h.temperatures[:2])
            else:
                assert tool == 0 and low == 155 and high == max(self.hot) + 10
            wire = [4, *args[1:]]
        elif action in (9, 10):
            self._settle()
            assert self.session['active'] and token == self.session['token']
            assert seq == self.session['sequence'] + 1
            assert not self.active_motion() and not self.contact
            if action == 9:
                assert not self.cold
                assert self.session['tool'] != tool
                if self.sealed:
                    assert 0 <= self.z <= 10 * Z_STEPS_PER_MM
                if self.z:
                    retreat = self.z_moves[-1]
                    assert retreat['direction'] == -1 and retreat['sent'] == 10 * Z_STEPS_PER_MM
                self.session['tool'] = tool
                self.ceiling = None
                if max(self.h.temperatures[:2]) <= 162.:
                    self.cold_switches.append(tool)
            else:
                assert self.session['tool'] == tool
                lower, upper = (0., 49.99) if self.cold else (158., 162.)
                assert all(lower <= temp <= upper for temp in self.h.temperatures[:2])
                self.sealed = True
                self.ceiling = budget
            wire = [2, *args[1:]]
        else:
            wire = args
        return super().query_factory(wire)

    def move(self, args):
        if args[0] != 1:
            return super().move(args)
        self._settle()
        factory = self.h.load_test_context['factory']
        original = args
        _, directions, a, b, z, e, _, _, _, mask, value, _, _ = args
        origin = self.xy
        tool = self.session['tool']
        if (a or b) and mask == value == self.CONTACT | self.TRAY:
            return self._begin_sweep(args)
        inside_pad = math.dist(origin, self.centers[tool]) <= self.contact_radius_mm
        if self.sweep_moves and self.unreliable_sweep_seed is not None:
            inside_pad = inside_pad and math.dist(origin, self.unreliable_sweep_seed) > .001
        if e:
            assert not self.sealed and not self.contact and not any((a, b, z))
            assert self.h.temperatures[tool] >= self.hot[tool] - 2.
            # Positive wire direction is a retract for T0 and negative for T1.
            assert bool(directions & 8) == (tool == 0)
            self.e_moves.append((tool, e))
        if z:
            self.contact_at = self.touch_heights[tool] if inside_pad else None
            if directions & self.BOTTOM and self.sealed:
                assert self.ceiling is not None and self.z + z <= self.ceiling
                if mask & self.CONTACT:
                    assert not value & self.CONTACT and not self.contact
                    self.pad_moves.append((tool, origin, self.z + z, inside_pad))
        if a or b:
            assert not self.contact
            if 'clean' in factory['module_stack']:
                self.wipe_moves.append((tool, tuple(self.h.temperatures[:2])))
            if mask & (self.CONTACT | self.TRAY):
                # The stock fixture treats any mask on XY as an endstop hit.
                # This model keeps tray seated and contact open while in XY.
                args = [*args[:9], mask & 3, value & 3, *args[11:]]
        reply = super().move(args)
        if a or b:
            if mask == 1 or mask == 2:
                axis = 0 if mask == 1 else 1
                delta = tuple(-x if i == axis else 0. for i, x in enumerate(origin))
            else:
                motor_a = a if directions & 1 else -a
                motor_b = b if directions & 2 else -b
                delta = ((motor_a + motor_b) / 1280., (motor_a - motor_b) / 1280.)
            self.motion.update(xy_start=origin, xy_delta=delta)
        self.motion_starts[-1]['arguments'] = tuple(original)
        return reply


class NozzlePadHostTests(unittest.TestCase):
    settings = dict(SCAN='TAP', ACK='PAD_GEOMETRY_MEASURED', T0_Z_MAX=137.30, T1_Z_MAX=137.20,
                    T0_TARGET=240, T1_TARGET=235, T0_RETRACT_MM=20, T1_RETRACT_MM=15)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Path(directory.name) / 'printer.cfg'
        self.saved = self.config.with_name('inventure_nozzle_pad.json')

    def host(self):
        host = z_host_support.ZHomeHostTests().host()
        host.device = device = PadDevice(host)
        host._fresh_inputs = device.inputs
        host.printer.get_start_args = lambda: {'config_file': str(self.config)}
        host.allow_actuators = True
        host.nozzle_pad_abi = 1
        host.commands.update(control=Wire(device.control), output=Wire(device.out),
            motion=Wire(device.move), motion_driver=Wire(device.driver),
            factory_session=Wire(device.query_factory))
        return host

    def cmd(self, host, action, **kwargs):
        command = Command(ACTION=action, **kwargs)
        host.cmd_nozzle_pad_calibrate(command)
        rows = [line for line in command.lines if line.startswith('V9_NOZZLE_PAD ')]
        return json.loads(rows[-1].split(' ', 1)[1]) if rows else None

    def advance(self, host, predicate, timeout=900.):
        limit = host.reactor.now + timeout
        while not predicate():
            self.assertLess(host.reactor.now, limit, self.cmd(host, 'STATUS'))
            host.reactor.pause(host.reactor.now + .5)

    def await_t0_confirmation(self, host):
        self.advance(host, lambda: host.nozzle_z_context is None or
                     host.nozzle_pad_run['phase'] == 'awaiting_t0_confirmation')
        status = self.cmd(host, 'STATUS')
        self.assertEqual(status['phase'], 'awaiting_t0_confirmation', status)
        return status

    def test_cold_center_seeks_four_edges_and_emits_one_line(self):
        host = self.host()
        host.nozzle_pad_sweep_abi = host.nozzle_pad_cold_abi = 1
        device = host.device
        device.contact_radius_mm = 7.54 / 2.
        device.mode = 0
        display_holds = []
        host.printer.objects['inventure_oled'] = SimpleNamespace(set_thermal_test_hold=display_holds.append)
        command = Command(ACTION='CENTER', T0_Z_MAX=137.30)
        host.cmd_nozzle_pad_calibrate(command)
        self.assertEqual(command.lines, [])
        self.advance(host, lambda: host.nozzle_z_context is None)
        run = host.nozzle_pad_run
        self.assertEqual(run['phase'], 'verified', run)
        self.assertEqual(len(command.lines), 1, command.lines)
        self.assertIn('środek X=', command.lines[0])
        self.assertIn('X−=', command.lines[0])
        self.assertIn('Y+=', command.lines[0])
        self.assertEqual(host.commands['output'].calls, [])
        self.assertEqual([s['direction'] for s in device.sweep_moves], [3, 0, 1, 2])
        self.assertEqual(device.e_moves, [])
        self.assertEqual(device.wipe_moves, [])
        self.assertEqual(device.ceiling, int(137.30 * Z_STEPS_PER_MM))
        self.assertFalse(device.contact)
        point = run['measurements']['T0']['center_xy_mm']
        self.assertLess(math.dist(point, device.centers[0]), .005)
        self.assertLess(math.dist(device.xy, point), .003)
        self.assertFalse(self.saved.exists())
        self.assertEqual(display_holds, [True, False])

    def test_cold_t1_center_uses_its_own_selector_ceil_and_result(self):
        host = self.host()
        host.nozzle_pad_sweep_abi, host.nozzle_pad_cold_abi = 1, 2
        device = host.device
        device.mode = 0
        device.contact_radius_mm = 7.54 / 2.
        command = Command(ACTION='CENTER', TOOL='T1', T1_Z_MAX=137.20, T0_PAD_X=81.7, T0_PAD_Y=68.2)
        host.cmd_nozzle_pad_calibrate(command)
        factory = host.load_test_context['factory']
        self.advance(host, lambda: host.nozzle_z_context is None)
        run = host.nozzle_pad_run
        self.assertEqual(run['phase'], 'verified', run)
        stroke = factory['motion_legs']['selector_press']
        self.assertEqual((stroke['start'], stroke['target']), ([15., 154.5], [0., 154.5]))
        self.assertEqual((device.calls[0][0], device.calls[0][1]), (8, 1))
        self.assertTrue(all(row[0] != 9 for row in device.calls))
        self.assertEqual([row['tool'] for row in device.sweep_moves], [1]*4)
        self.assertAlmostEqual(device.sweep_moves[0]['start'][0], 70.9, places=3)
        self.assertAlmostEqual(device.sweep_moves[0]['start'][1], 68.2, places=3)
        self.assertEqual(device.ceiling, int(137.20 * Z_STEPS_PER_MM))
        self.assertTrue(all(tool == 1 and z <= device.ceiling for tool, _, z, _ in device.pad_moves))
        self.assertEqual(list(run['measurements']), ['T1'])
        self.assertLess(math.dist(run['measurements']['T1']['center_xy_mm'], device.centers[1]), .03)
        self.assertEqual(len(command.lines), 1)
        self.assertTrue(command.lines[0].startswith('Pad T1 | środek'))
        self.assertEqual(host.commands['output'].calls, [])
        self.assertEqual(device.e_moves, [])
        self.assertFalse(self.saved.exists())

    def test_cold_t1_requires_its_own_ceiling_and_new_mcu_before_motion(self):
        host = self.host()
        host.nozzle_pad_sweep_abi = host.nozzle_pad_cold_abi = 1
        with self.assertRaisesRegex(RuntimeError, 'requires T1_Z_MAX'):
            host.cmd_nozzle_pad_calibrate(Command(ACTION='CENTER', TOOL='T1', T0_Z_MAX=137.30))
        with self.assertRaisesRegex(RuntimeError, 'CENTER TOOL=T1 wymaga firmware MCU'):
            host.cmd_nozzle_pad_calibrate(Command(ACTION='CENTER', TOOL='T1', T1_Z_MAX=137.20))
        self.assertIsNone(getattr(host, 'nozzle_z_context', None))
        self.assertFalse(host.device.calls)
        self.assertFalse(host.device.motion_starts)

    def test_cold_center_retries_fresh_pc14_opening_before_y_sweep(self):
        host = self.host()
        host.nozzle_pad_sweep_abi = host.nozzle_pad_cold_abi = 1
        device = host.device
        device.contact_radius_mm = 7.54 / 2.
        injected = []

        def inputs():
            sample = device.inputs()
            factory = host.load_test_context['factory'] if host.load_test_context else {}
            action = factory.get('action') or {}
            if (factory.get('phase') == 'Y+' and action.get('stage') == 'pad_sweep'
                    and factory['move'] is None and not injected):
                injected.append(sample['raw']['nozzle_contact'])
                sample['raw']['nozzle_contact'] = 1
            return sample

        host._fresh_inputs = inputs
        command = Command(ACTION='CENTER', T0_Z_MAX=137.30)
        host.cmd_nozzle_pad_calibrate(command)
        self.advance(host, lambda: host.nozzle_z_context is None)
        self.assertEqual(injected, [0])
        self.assertEqual(host.nozzle_pad_run['phase'], 'verified', host.nozzle_pad_run)
        self.assertEqual(len(command.lines), 1, command.lines)
        self.assertEqual([m['direction'] for m in device.sweep_moves], [3, 0, 1, 2])
        self.assertTrue(all(z <= device.ceiling for _, _, z, _ in device.pad_moves))

    def test_cold_center_recovers_eight_step_open_and_missing_seed_contact(self):
        host = self.host()
        host.nozzle_pad_sweep_abi = host.nozzle_pad_cold_abi = 1
        device = host.device
        device.first_sweep_open_steps = 8
        device.unreliable_sweep_seed = (82.8, 68.)
        command = Command(ACTION='CENTER', T0_Z_MAX=137.30, EDGE_TOLERANCE_MM=.02, PAD_DIAMETER_MM=5.)
        host.cmd_nozzle_pad_calibrate(command)
        self.advance(host, lambda: host.nozzle_z_context is None)
        run = host.nozzle_pad_run
        self.assertEqual(run['phase'], 'verified', run)
        self.assertEqual(len(command.lines), 1, command.lines)
        self.assertEqual(device.sweep_moves[0]['sent'], 8)
        self.assertEqual([m['direction'] for m in device.sweep_moves], [3, 1, 2])
        result = run['measurements']['T0']
        self.assertEqual(result['scan_method'], 'SWEEP+TAP')
        self.assertLess(math.dist(result['center_xy_mm'], device.centers[0]), .02)
        self.assertTrue(all(z <= device.ceiling for _, _, z, _ in device.pad_moves))
        self.assertFalse(device.contact)

    def test_cold_center_skips_an_unstable_left_tap_and_reports_tenths(self):
        host = self.host()
        host.nozzle_pad_sweep_abi = host.nozzle_pad_cold_abi = 1
        device = host.device
        device.first_sweep_open_steps = 8
        device.unreliable_sweep_seed = (82.8, 68.)
        interrupted_taps = []

        def inputs():
            sample = device.inputs()
            factory = (host.load_test_context or {}).get('factory', {})
            action = factory.get('action') or {}
            if (factory.get('phase') == 'X-_TOUCH' and action.get('kind') == 'z_verify_closed'
                    and math.isclose(device.xy[0], 80.33125, abs_tol=.001)
                    and sample['raw']['nozzle_contact'] == 0):
                interrupted_taps.append(device.xy[0])
                sample['raw']['nozzle_contact'] = 1
            return sample

        host._fresh_inputs = inputs
        command = Command(ACTION='CENTER', T0_Z_MAX=137.30, PAD_DIAMETER_MM=5.)
        host.cmd_nozzle_pad_calibrate(command)
        self.advance(host, lambda: host.nozzle_z_context is None)
        run = host.nozzle_pad_run
        self.assertEqual(len(interrupted_taps), 2)
        self.assertEqual(run['phase'], 'verified', run)
        self.assertEqual(len(command.lines), 1)
        x, y = run['measurements']['T0']['center_xy_mm']
        self.assertIn('środek X=%.1f Y=%.1f mm |' % (x, y), command.lines[0])
        self.assertLess(abs(x - device.centers[0][0]), .05)
        self.assertTrue(all(z <= device.ceiling for _, _, z, _ in device.pad_moves))
        self.assertFalse(device.contact)

    def test_cold_center_requires_fresh_cold_nozzles_before_motion(self):
        host = self.host()
        host.nozzle_pad_sweep_abi = host.nozzle_pad_cold_abi = 1
        host.temperatures[1] = 50.
        with self.assertRaisesRegex(RuntimeError, '50 C'):
            host.cmd_nozzle_pad_calibrate(Command(ACTION='CENTER', T0_Z_MAX=137.30))
        self.assertIsNone(host.load_test_context)
        self.assertFalse(host.device.motion_starts)

    def test_empty_verify_positions_t0_and_waits_at_pe12_before_heat(self):
        host = self.host()
        host.nozzle_pad_sweep_abi = 1
        device = host.device
        device.contact_radius_mm = 7.54 / 2.
        device.hot = (160., 160.)
        self.cmd(host, 'START', MODE='VERIFY_T0', PREP='EMPTY',
                 ACK='PAD_GEOMETRY_MEASURED', T0_Z_MAX=137.30)
        with self.assertRaisesRegex(RuntimeError, 'waiting for physical tool confirmation'):
            self.cmd(host, 'CONFIRM_T0')
        status = self.await_t0_confirmation(host)
        self.assertFalse(status['t0_confirmed'])
        factory = host.load_test_context['factory']
        self.assertEqual(factory['motion_legs']['selector_press']['reason'], 'completed')
        self.assertEqual(factory['position'], [82.8, 68., 0.])
        self.assertEqual(device.z, 0)
        host.reactor.pause(host.reactor.now + 2.)
        self.assertEqual((device.output(6)['duty'], device.output(7)['duty']), (0, 0))
        self.assertFalse(factory['heat_started'])
        self.assertFalse(device.pad_moves)
        self.assertTrue(self.cmd(host, 'CONFIRM_T0')['t0_confirmed'])
        with self.assertRaisesRegex(RuntimeError, 'waiting for physical tool confirmation'):
            self.cmd(host, 'CONFIRM_T0')
        self.advance(host, lambda: host.nozzle_z_context is None)
        result = self.cmd(host, 'STATUS')
        self.assertEqual(result['phase'], 'verified', result)
        self.assertEqual([m['state'] for m in factory['module_history'] if m['name'] == 'select_tool'],
                         ['start', 'done'])
        self.assertEqual(len(device.sweep_moves), 8)

    def test_prepares_both_nozzles_and_measures_t0_t1_t0_under_one_owner(self):
        host = self.host()
        started = self.cmd(host, 'START', **self.settings)
        self.assertEqual(started['phase'], 'running')
        self.advance(host, lambda: host.nozzle_z_context is None)
        result = self.cmd(host, 'STATUS')
        device = host.device
        self.assertEqual(result['phase'], 'candidate', result)
        self.assertEqual(result['run_id'], started['run_id'])
        self.assertEqual(result['hot_targets_c'], {'T0': 240., 'T1': 235.})
        self.assertEqual(result['measurement_target_c'], 160.)
        samples = result['sample_temperatures_c']
        self.assertEqual([sample['tool'] for sample in samples], ['T0', 'T1', 'T0'])
        for sample in samples:
            for tool in ('T0', 'T1'):
                self.assertAlmostEqual(sample[tool], 160., delta=2.)
        self.assertEqual([row[1] for row in device.calls if row[0] == 9], [1, 0, 1, 0])
        self.assertEqual([row[1] for row in device.calls if row[0] == 10], [0, 1, 0])
        self.assertEqual([row[5] for row in device.calls if row[0] == 10],
            [int(self.settings['T%d_Z_MAX' % tool] * Z_STEPS_PER_MM) for tool in (0, 1, 0)])
        self.assertEqual({row[9] for row in device.calls}, {device.calls[0][9]})
        self.assertEqual(device.e_moves, [(0, 19200), (1, 14400)])
        self.assertEqual(device.calls[0][5], 33600)
        hot_wipes = [(tool, temps) for tool, temps in device.wipe_moves if temps[tool] > 180.]
        cold_wipes = [(tool, temps) for tool, temps in device.wipe_moves if temps[tool] < 180.]
        self.assertEqual({tool for tool, _ in hot_wipes}, {0, 1})
        self.assertEqual({tool for tool, _ in cold_wipes}, {0, 1})
        self.assertTrue(all(abs(temps[tool] - device.hot[tool]) <= 2. for tool, temps in hot_wipes))
        self.assertTrue(all(all(158. <= t <= 162. for t in temps) for _, temps in cold_wipes))
        homing = [m['arguments'][9] for m in device.motion_starts
                  if m['arguments'][9] == m['arguments'][10] and m['arguments'][9] in (1, 2, 4)]
        self.assertEqual(homing, [2, 1, 4])
        self.assertTrue(any(row[-1] for row in device.pad_moves))
        self.assertTrue(any(not row[-1] for row in device.pad_moves))
        for tool in (0, 1):
            self.assertLessEqual(max(m[2] for m in device.pad_moves if m[0] == tool),
                                 self.settings['T%d_Z_MAX' % tool] * Z_STEPS_PER_MM)
        self.assertEqual(device.cold_switches, [0, 1, 0])
        self.assertEqual((device.output(6)['duty'], device.output(7)['duty']), (0, 0))
        self.assertFalse(device.stepper_enabled)
        candidate = result['candidate']
        self.assertAlmostEqual(candidate['t1_offset_mm']['X'], 10.79, delta=.035)
        self.assertAlmostEqual(candidate['t1_offset_mm']['Y'], .15, delta=.035)
        self.assertAlmostEqual(candidate['t1_lower_by_mm'], .12, delta=.02)
        self.assertFalse(self.saved.exists())
        with self.assertRaisesRegex(RuntimeError, 'RUN_ID'):
            self.cmd(host, 'SAVE', ACK='PAD_RESULT_REVIEWED', RUN_ID='stale')
        with self.assertRaisesRegex(RuntimeError, 'ACK'):
            self.cmd(host, 'SAVE', RUN_ID=result['run_id'])
        saved = self.cmd(host, 'SAVE', ACK='PAD_RESULT_REVIEWED', RUN_ID=result['run_id'])
        self.assertEqual(saved['phase'], 'saved')
        record = json.loads(self.saved.read_text())
        self.assertEqual(record['candidate'], candidate)
        self.assertEqual(record['sample_temperatures_c'], samples)

    def test_requires_surveyed_limits_before_any_motion_or_heating(self):
        host = self.host()
        settings = dict(self.settings)
        del settings['T1_Z_MAX']
        with self.assertRaisesRegex(RuntimeError, 'T1_Z_MAX'):
            self.cmd(host, 'START', **settings)
        self.assertEqual(host.device.calls, [])
        self.assertEqual(host.device.motion_starts, [])
        self.assertFalse(host.device.output(6)['duty'])
        self.assertFalse(host.device.output(7)['duty'])

    def test_empty_nozzles_heat_directly_to_measurement_temperature_without_e_motion(self):
        host = self.host()
        device = host.device
        device.hot = (160., 160.)
        started = self.cmd(host, 'START', SCAN='TAP', PREP='EMPTY', ACK='PAD_GEOMETRY_MEASURED',
                           T0_Z_MAX=137.30, T1_Z_MAX=137.20)
        self.assertEqual(started['hot_targets_c'], {'T0': 160., 'T1': 160.})
        self.assertEqual([heater['target'] for heater in host.load_test_context['factory']['dual_heaters']],
                         [160., 160.])
        self.advance(host, lambda: host.nozzle_z_context is None)
        result = self.cmd(host, 'STATUS')
        self.assertEqual((result['phase'], result['run_id']), ('candidate', started['run_id']), result)
        self.assertEqual([sample['tool'] for sample in result['sample_temperatures_c']],
                         ['T0', 'T1', 'T0'])
        self.assertEqual(result['retract_e_steps'], {'T0': 0, 'T1': 0})
        self.assertEqual(device.calls[0][5], 0)
        self.assertEqual(device.e_moves, [])
        self.assertEqual({tool for tool, _ in device.wipe_moves}, {0, 1})
        self.assertTrue(all(all(158. <= temp <= 162. for temp in temps)
                            for _, temps in device.wipe_moves))
        self.assertEqual((device.output(6)['duty'], device.output(7)['duty']), (0, 0))

    def test_empty_nozzles_reject_filament_preparation_arguments_before_actuation(self):
        settings = dict(SCAN='TAP', PREP='EMPTY', ACK='PAD_GEOMETRY_MEASURED',
                        T0_Z_MAX=137.30, T1_Z_MAX=137.20)
        for argument, value in (('T0_TARGET', 200), ('T1_TARGET', 160),
                                ('T0_RETRACT_MM', 0), ('T1_RETRACT_MM', 5)):
            with self.subTest(argument=argument):
                host = self.host()
                with self.assertRaisesRegex(RuntimeError, 'PREP=EMPTY'):
                    self.cmd(host, 'START', **settings, **{argument: value})
                self.assertEqual(host.device.calls, [])
                self.assertEqual(host.device.motion_starts, [])
                self.assertFalse(host.device.output(6)['duty'])
                self.assertFalse(host.device.output(7)['duty'])

    def test_verify_t0_rechecks_adc_age_after_factory_lease_renewal(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                host = self.host()
                host.nozzle_pad_sweep_abi = 1
                device = host.device
                device.contact_radius_mm = 7.54 / 2.
                device.hot = (160., 160.)
                delivered = []

                def renew(args):
                    row = device.query_factory(args)
                    factory = host.load_test_context['factory']
                    if args[0] == 2 and factory['heat_started'] and not delivered:
                        host.reactor.now += .02
                        # ADC callbacks can run while a serial query yields.
                        age = 1.1 if stale else 0.
                        host.adc_received[1] = host.reactor.monotonic() - age
                        delivered.append(True)
                    return row

                host.commands['factory_session'] = Wire(renew)
                self.cmd(host, 'START', MODE='VERIFY_T0', PREP='EMPTY',
                         ACK='PAD_GEOMETRY_MEASURED', T0_Z_MAX=137.30)
                self.await_t0_confirmation(host)
                self.cmd(host, 'CONFIRM_T0')
                self.advance(host, lambda: host.nozzle_z_context is None)
                self.assertTrue(delivered)
                result = self.cmd(host, 'STATUS')
                if stale:
                    self.assertEqual(result['phase'], 'failed', result)
                    self.assertIn('T1 ADC is stale', result['error'])
                    self.assertFalse(device.pad_moves)
                    self.assertEqual((device.output(6)['duty'], device.output(7)['duty']), (0, 0))
                else:
                    self.assertEqual(result['phase'], 'verified', result)
                    self.assertEqual(len(device.sweep_moves), 8)

    def test_pad_cooldown_reads_t1_after_t0_off_reply(self):
        host = self.host()
        output = host._load_test_output
        delivered = []

        def send_output(context, duty, heater=None):
            row = output(context, duty, heater)
            if (duty == 0 and heater and heater['tool'] == 0 and
                    context['factory'].get('pad_cooling') and not delivered):
                host.reactor.now += .02
                host.adc_received[1] = host.reactor.monotonic()
                delivered.append(True)
            return row

        host._load_test_output = send_output
        self.cmd(host, 'START', **dict(self.settings, MODE='VERIFY_T0'))
        self.await_t0_confirmation(host)
        self.cmd(host, 'CONFIRM_T0')
        self.advance(host, lambda: host.nozzle_z_context is None)
        self.assertTrue(delivered)
        result = self.cmd(host, 'STATUS')
        self.assertEqual(result['phase'], 'verified', result)

    def test_default_sweep_finds_edges_from_actual_partial_xy_steps(self):
        host = self.host()
        host.nozzle_pad_sweep_abi = 1
        device = host.device
        device.contact_radius_mm = 7.54 / 2.
        device.hot = (160., 160.)
        self.cmd(host, 'START', PREP='EMPTY', ACK='PAD_GEOMETRY_MEASURED',
                 T0_Z_MAX=137.30, T1_Z_MAX=137.20)
        self.advance(host, lambda: host.nozzle_z_context is None)
        result = self.cmd(host, 'STATUS')
        self.assertEqual(result['phase'], 'candidate', result)
        self.assertEqual(device.e_moves, [])
        self.assertTrue(device.pad_moves)
        self.assertTrue(all(inside for _, _, _, inside in device.pad_moves))
        self.assertEqual([move['tool'] for move in device.sweep_moves], [0]*8 + [1]*8 + [0]*8)
        self.assertTrue(all(move['hit'] == device.CONTACT and 0 < move['sent'] < move['planned']
                            and move['z'] == device.touch_heights[move['tool']]
                            for move in device.sweep_moves))
        for name, tool in (('T0', 0), ('T1', 1), ('T0_RETURN', 0)):
            center = result['measurements'][name]['center_xy_mm']
            self.assertLess(math.dist(center, device.centers[tool]), .005)
        self.assertAlmostEqual(result['candidate']['t1_offset_mm']['X'], 10.79, delta=.005)
        self.assertAlmostEqual(result['candidate']['t1_offset_mm']['Y'], .15, delta=.005)
        self.assertAlmostEqual(result['candidate']['t1_lower_by_mm'], .12, delta=.005)

    def test_full_warm_sweep_rechecks_an_early_opening_and_finishes_both_tools(self):
        host = self.host()
        host.nozzle_pad_sweep_abi = 1
        device = host.device
        device.contact_radius_mm = 7.6 / 2.
        device.centers = ((81.70, 68.20), (70.80, 68.70))
        device.touch_heights = (round(139.25 * Z_STEPS_PER_MM), round(137.65 * Z_STEPS_PER_MM))
        device.hot = (230., 235.)
        device.first_sweep_open_steps = 8
        self.cmd(host, 'START', ACK='PAD_GEOMETRY_MEASURED',
                 T0_PAD_X=81.70, T0_PAD_Y=68.20, T0_Z_MAX=139.50, T0_TARGET=230, T0_RETRACT_MM=100,
                 T1_PAD_X=70.80, T1_PAD_Y=68.70, T1_Z_MAX=137.95, T1_TARGET=235, T1_RETRACT_MM=100)
        self.advance(host, lambda: host.nozzle_z_context is None, timeout=1800.)
        result = self.cmd(host, 'STATUS')
        self.assertEqual(result['phase'], 'candidate', result)
        self.assertEqual(result['measurements']['T0']['scan_method'], 'SWEEP+TAP')
        self.assertEqual([row['tool'] for row in result['sample_temperatures_c']], ['T0', 'T1', 'T0'])
        self.assertEqual(device.e_moves, [(0, 96000), (1, 96000)])
        outer_taps = [z for tool, _, z, inside in device.pad_moves if tool == 0 and not inside]
        self.assertTrue(outer_taps)
        self.assertLessEqual(max(outer_taps), device.touch_heights[0] + round(.05 * Z_STEPS_PER_MM))
        self.assertAlmostEqual(result['candidate']['t1_carriage_shift_mm']['X'], -10.9, delta=.015)
        self.assertAlmostEqual(result['candidate']['t1_carriage_shift_mm']['Y'], .5, delta=.015)
        self.assertFalse(device.stepper_enabled)
        self.assertEqual((device.output(6)['duty'], device.output(7)['duty']), (0, 0))

    def test_sweep_requires_its_abi_before_actuation(self):
        host = self.host()
        with self.assertRaisesRegex(RuntimeError, 'SCAN=SWEEP requires'):
            self.cmd(host, 'START', PREP='EMPTY', ACK='PAD_GEOMETRY_MEASURED',
                     T0_Z_MAX=137.30, T1_Z_MAX=137.20)
        self.assertEqual(host.device.calls, [])
        self.assertEqual(host.device.motion_starts, [])
        self.assertFalse(host.device.output(6)['duty'])
        self.assertFalse(host.device.output(7)['duty'])

    def test_sweep_rejects_tray_stop_or_mismatched_counters_without_advancing_xy(self):
        host = self.host()
        move = {'counts': (1920, -1920, 0, 0)}
        for hit, sent_b in ((16, 960), (8, 959)):
            with self.subTest(hit=hit, sent_b=sent_b):
                factory = {'position': [82.8, 68., 0.], 'move': move}
                drivers = {'A': {'sent': 960, 'direction': 1},
                           'B': {'sent': sent_b, 'direction': 0},
                           'Z': {'sent': 0}, 'E': {'sent': 0}}
                row = dict(reason='stop_input', phase='done', hit=hit, drivers=drivers)
                with self.assertRaisesRegex(ValueError, 'Unexpected pad sweep terminal state'):
                    host._factory_pad_sweep_result(factory, move, row)
                self.assertEqual(factory['position'], [82.8, 68., 0.])

    def test_verify_t0_only_arms_the_surveyed_tool_and_cannot_save(self):
        host = self.host()
        settings = dict(self.settings, MODE='VERIFY_T0')
        del settings['T1_Z_MAX']
        self.cmd(host, 'START', **settings)
        self.await_t0_confirmation(host)
        self.cmd(host, 'CONFIRM_T0')
        self.advance(host, lambda: host.nozzle_z_context is None)
        result = self.cmd(host, 'STATUS')
        self.assertEqual(result['phase'], 'verified', result)
        self.assertEqual(set(result['measurements']), {'T0'})
        self.assertIsNone(result['candidate'])
        self.assertEqual([row[1] for row in host.device.calls if row[0] == 10], [0])
        self.assertEqual(host.device.e_moves, [(0, 19200), (1, 14400)])
        with self.assertRaisesRegex(RuntimeError, 'repeatable candidate'):
            self.cmd(host, 'SAVE', ACK='PAD_RESULT_REVIEWED', RUN_ID=result['run_id'])
        self.assertFalse(self.saved.exists())

    def test_stop_revokes_ownership_before_further_probe_or_e_motion(self):
        host = self.host()
        self.cmd(host, 'START', **self.settings)
        self.advance(host, lambda: host.device.pad_moves or host.nozzle_z_context is None)
        self.assertTrue(host.device.pad_moves, self.cmd(host, 'STATUS'))
        self.cmd(host, 'STOP')
        count = len(host.device.motion_starts)
        host.reactor.pause(host.reactor.now + 3.)
        status = self.cmd(host, 'STATUS')
        self.assertEqual((status['phase'], status['error']), ('stopped', 'operator_stop'))
        self.assertEqual(len(host.device.motion_starts), count)
        self.assertFalse(host.device.active_motion())
        self.assertFalse(host.device.stepper_enabled)
        self.assertIsNone(host.load_test_context)
        self.assertFalse(self.saved.exists())
