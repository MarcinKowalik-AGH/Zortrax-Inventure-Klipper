"""Run the actual R13 factory executor for each idle recovery path."""
import unittest

import test_orange_pi_factory_host as factory
from test_orange_pi_door_policy import Command
from test_orange_pi_factory_core import Executor, core, short_feedback


class JamBenchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = factory.FactoryHostTests()

    def start(self, tool, operation, scenario='short'):
        h, _, _ = self.fixture.host(scenario, fast_motion=True, factory_motion_tools=3)
        h.cmd_procedure(Command(ACTION=operation, TOOL=tool, TARGET=230, SPEED_PROFILE='OEM'))
        return h

    def test_both_sources_both_tools_stop_heating_and_park_over_bin(self):
        for tool in ('T0', 'T1'):
            for source, expected in (('CASSETTE', 'CASSETTE_R2'), ('EXTERNAL', 'EXTERNAL_CHECK_FLOW')):
                with self.subTest(tool=tool, source=source):
                    h = self.start(tool, 'JAM_' + source)
                    last = self.fixture.finish(h)
                    f = last['factory']
                    self.assertEqual(last['phase'], 'done', last['reason'])
                    self.assertEqual(f['result']['terminal'], expected)
                    self.assertFalse(f['result']['flow_confirmed'])
                    self.assertFalse(f['result']['print_resumed'])
                    self.assertEqual(f['result']['transport_confirmed'], source == 'CASSETTE')
                    self.assertEqual(f['position'][:2], [f['parameters']['center_x'], f['parameters']['center_y']])
                    self.assertTrue(f['cleaning_done'])
                    self.assertEqual(h.device.output(6 + int(tool[-1]))['duty'], 0)
                    if source == 'EXTERNAL':
                        self.assertFalse(any(h.device.mode_history))
                    else:
                        self.assertLessEqual(f['e_steps_by_phase']['JAM_R1'], 48000)
                        self.assertLessEqual(f['e_steps_by_phase']['JAM_R2'], 48000)

    def test_r1_failure_ends_without_reloading_or_wiping(self):
        h = self.start('T1', 'JAM_CASSETTE', 'no_edges')
        last = self.fixture.finish(h)
        self.assertEqual(last['phase'], 'failed')
        self.assertIn('ten recent', last['reason'])
        self.assertNotIn('JAM_R2', last['factory']['history'])
        self.assertFalse(last['factory']['cleaning_done'])

    def test_r2_failure_reuses_cassette_loader_once(self):
        def no_starts(ex, action, result):
            short_feedback(ex, action, result)
            if action['kind'] == 'feed_until_starts':
                result.update(ok=False)
        p = core.parameters({'TOOL': 'T0', 'SPEED_PROFILE': 'OEM', 'TARGET': 230})
        ex = Executor(p, hook=no_starts, operation='JAM_CASSETTE').run()
        self.assertEqual(ex.done['terminal'], 'CASSETTE_RELOAD_L1')
        self.assertEqual(ex.program.history.count('L1'), 1)

    def test_material_temperature_and_oem_selection_required_before_actuation(self):
        for params in ({'TARGET': 230}, {'SPEED_PROFILE': 'OEM'}):
            h, _, _ = self.fixture.host(fast_motion=True, factory_motion_tools=3)
            with self.assertRaisesRegex(RuntimeError, 'requires SPEED_PROFILE=OEM'):
                h.cmd_procedure(Command(ACTION='JAM_EXTERNAL', **params))
            self.assertIsNone(h.load_test_context)
            self.assertEqual(h.device.packets, [])
