"""Run the host XY calibration against a model of its owned MCU calls."""
import json
from pathlib import Path
import tempfile
import unittest

from orange_pi.inventure_z_home import Z_STEPS_PER_MM
from test_orange_pi_door_policy import Command
from test_orange_pi_load_test import Wire
from test_orange_pi_nozzle_z import DualNozzleDevice
import test_orange_pi_z_home_host as z_host_support


class XYDevice(DualNozzleDevice):
    def __init__(self, host):
        super().__init__(host)
        self.calls = []
        self.extrusion = []
        self.probes = []
        self.probe_state = None
        self.z = 20 * Z_STEPS_PER_MM

    def query_factory(self, args):
        args = list(args)
        self.calls.append(tuple(args))
        aliases = {6: 4, 7: 5}
        args[0] = aliases.get(args[0], args[0])
        return super().query_factory(args)

    def move(self, args):
        if args[0] == 1 and args[5]:
            e = args[5]
            tool = self.session['tool']
            assert self.session['active'] and not self.contact
            assert [row[0] for row in self.probes].count(tool) == (2, 3)[tool]
            assert self.probe_state['active'] == 0
            assert args[6] * e <= 9600 * max(args[2:6])
            self.extrusion.append((tool, tuple(args)))
        return super().move(args)

    def local_refine(self, args):
        action, owner, token, span, interval, repeats = args
        if action:
            self._settle()
            tool = self.session['tool']
            assert self.h.thermal_display_held
            assert owner == self.session['token'] and self.session['active']
            assert self.z == 137 * Z_STEPS_PER_MM
            assert not self.active_motion()
            assert all(abs(self.h.temperatures[i] - t) <= 2.
                       for i, t in enumerate((210., 215.)))
            self.probes.append((tool, token))
            self.probe_state = dict(owner=owner, token=token, tool=tool, active=1, status=0,
                                    reject=0, net_steps=0, best_steps=0, cycles=0,
                                    non_improvements=0, up_steps=0, down_steps=0)
        elif self.probe_state['active']:
            assert (owner, token) == (self.probe_state['owner'], self.probe_state['token'])
            rise = 250 - 40 * self.probe_state['tool']
            self.z += rise
            self.motion = None
            self.probe_state.update(active=0, status=1, net_steps=rise, best_steps=rise,
                                    cycles=202, non_improvements=201,
                                    up_steps=rise + 202, down_steps=202)
        return dict(self.probe_state)


class NozzleXYHostTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'printer.cfg'

    def host(self):
        host = z_host_support.ZHomeHostTests().host()
        host.device = device = XYDevice(host)
        host._fresh_inputs = device.inputs
        host.printer.get_start_args = lambda: {'config_file': str(self.path)}
        host.allow_actuators = True
        host.nozzle_xy_abi = 1
        host.commands.update(control=Wire(device.control), output=Wire(device.out),
            motion=Wire(device.move), motion_driver=Wire(device.driver),
            factory_session=Wire(device.query_factory), nozzle_z_test=Wire(device.local_refine))
        return host

    def cmd(self, host, action, **kwargs):
        command = Command(ACTION=action, **kwargs)
        host.cmd_nozzle_xy_test(command)
        return json.loads(command.lines[-1].split(' ', 1)[1]) if command.lines else None

    def start(self, host):
        return self.cmd(host, 'START', ACK='XY_PATTERN_SURFACE_READY')

    def advance(self, host, predicate):
        limit = host.reactor.now + 900.
        while not predicate():
            self.assertLess(host.reactor.now, limit, host.nozzle_xy_status())
            host.reactor.pause(host.reactor.now + .5)

    def test_homes_once_and_prints_both_patterns_under_one_owner(self):
        host = self.host()
        started = self.start(host)
        self.assertEqual(started['phase'], 'printing')
        run_id = started['run_id']
        with self.assertRaisesRegex(RuntimeError, 'after the pattern has printed'):
            self.cmd(host, 'LINE', RUN_ID=run_id, AXIS='Y', LINE=0)
        self.advance(host, lambda: host.nozzle_z_context is None)
        state = self.cmd(host, 'STATUS')
        self.assertEqual(state['phase'], 'select', state)
        self.assertEqual(state['run_id'], run_id)
        self.assertEqual(state['printed_segments'], {'T0': 28, 'T1': 28})
        self.assertEqual(state['targets_c'], {'T0': 210., 'T1': 215.})
        device = host.device
        self.assertEqual([row[0] for row in device.probes], [0, 0, 1, 1, 1])
        self.assertEqual({row[9] for row in device.calls}, {device.calls[0][9]})
        self.assertEqual([row[0] for row in device.calls if row[0] != 2], [6, 7])
        self.assertEqual({row[5] for row in device.calls}, {714240})
        self.assertEqual({(row[7], row[8]) for row in device.calls}, {(185, 225)})
        self.assertEqual(sum(args[5] for _, args in device.extrusion), 714240)
        for tool in (0, 1):
            draw = [args for t, args in device.extrusion if t == tool and any(args[2:4])]
            self.assertEqual(len(draw), 28)
            self.assertTrue(all(bool(args[1] & 8) == bool(tool) for args in draw))
        homing = [move['arguments'][9] for move in device.motion_starts
                  if move['arguments'][9] == move['arguments'][10] and move['arguments'][9] in (1, 2, 4)]
        self.assertEqual(homing, [2, 1, 4])
        self.assertEqual((device.output(6)['duty'], device.output(7)['duty']), (0, 0))
        self.assertFalse(host.thermal_display_held)

    def test_saved_correction_is_applied_to_the_next_pattern_baseline(self):
        host = self.host()
        run_id = self.start(host)['run_id']
        self.advance(host, lambda: host.nozzle_z_context is None)
        with self.assertRaisesRegex(RuntimeError, 'current RUN_ID'):
            self.cmd(host, 'LINE', RUN_ID='stale', AXIS='X', LINE=2)
        for line in (-7, 7):
            with self.subTest(line=line), self.assertRaisesRegex(RuntimeError, 'LINE'):
                self.cmd(host, 'LINE', RUN_ID=run_id, AXIS='Y', LINE=line)
        centered = self.cmd(host, 'LINE', RUN_ID=run_id, AXIS='Y', LINE=0)
        self.assertEqual(centered['lines'], {'X': None, 'Y': 0})
        self.assertEqual(centered['candidate']['residual_mm'], {'X': 0., 'Y': 0.})
        self.cmd(host, 'LINE', RUN_ID=run_id, AXIS='Y', LINE=-3)
        with self.assertRaisesRegex(RuntimeError, 'Choose both lines'):
            self.cmd(host, 'APPLY', RUN_ID=run_id, ACK='XY_PATTERN_READ')
        self.assertFalse(host._nozzle_xy_path().exists())
        self.cmd(host, 'LINE', RUN_ID=run_id, AXIS='X', LINE=2)
        applied = self.cmd(host, 'APPLY', RUN_ID=run_id, ACK='XY_PATTERN_READ')
        self.assertEqual(applied['phase'], 'applied')
        self.assertTrue(applied['coordinate_saved'])
        self.assertEqual(json.loads(host._nozzle_xy_path().read_text())['residual_mm'],
                         {'X': -.1, 'Y': .15})
        restarted = self.host()
        next_run = self.start(restarted)
        self.assertNotEqual(next_run['run_id'], run_id)
        self.assertEqual(next_run['residual_before_mm'], {'X': -.1, 'Y': .15})
        _, settings = restarted.nozzle_z_context['parameters'][1]
        self.assertAlmostEqual(settings['z_probe_x'], 72.1)
        self.assertAlmostEqual(settings['z_probe_y'], 67.85)
        with self.assertRaisesRegex(RuntimeError, 'current RUN_ID'):
            self.cmd(restarted, 'STOP', RUN_ID=run_id)
        self.assertEqual(restarted.nozzle_xy_status()['phase'], 'printing')
        self.cmd(restarted, 'STOP', RUN_ID=next_run['run_id'])
        self.assertEqual(restarted.nozzle_xy_status()['phase'], 'stopped')
        self.assertEqual(restarted._read_nozzle_xy_offsets(), {'X': -.1, 'Y': .15})

    def test_stop_while_printing_invalidates_the_run_and_motion(self):
        host = self.host()
        run_id = self.start(host)['run_id']
        self.advance(host, lambda: host.device.extrusion or host.nozzle_z_context is None)
        self.assertTrue(host.device.extrusion, host.nozzle_xy_status())
        self.cmd(host, 'STOP', RUN_ID=run_id)
        status = self.cmd(host, 'STATUS')
        self.assertEqual((status['phase'], status['error']), ('stopped', 'operator_stop'))
        self.assertFalse(host.device.active_motion())
        self.assertFalse(host.device.stepper_enabled)
        self.assertIsNone(host.load_test_context)
        before = len(host.device.extrusion)
        host.reactor.pause(host.reactor.now + 3.)
        self.assertEqual(len(host.device.extrusion), before)
        self.assertFalse(host._nozzle_xy_path().exists())
