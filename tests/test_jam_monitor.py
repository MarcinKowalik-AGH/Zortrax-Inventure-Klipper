import types
import unittest
from unittest import mock

from orange_pi.inventure_jam_monitor import JamMonitor, classify


def row(**overrides):
    r = dict(channel=0, clock=2000, last_pulse_clock=1995, pulses=67, starts=10,
             a=1, b=0, live=1, mode=1, running=0, fault=0, budget=492,
             forward=100, reverse=2)
    r.update(overrides)
    return r


class Reactor:
    NEVER = 1.e12

    def __init__(self):
        self.now = 5.
        self.timers = {}

    def monotonic(self):
        return self.now

    def register_timer(self, cb):
        self.timers[cb] = self.NEVER
        return cb

    def update_timer(self, cb, when):
        self.timers[cb] = when

    def pause(self, when):
        self.now = when


class JamMonitorTest(unittest.TestCase):
    def setUp(self):
        self.rt = Reactor()
        self.objs = {}
        printer = types.SimpleNamespace(lookup_object=self.objs.get,
                                        register_event_handler=lambda event, cb: None,
                                        command_error=ValueError)
        self.board = types.SimpleNamespace(printer=printer, reactor=self.rt,
            connected=True, status_received=5., inputs_received=5.,
            inputs={'raw': {'t0_a': 1, 't0_b': 0, 't1_a': 0, 't1_b': 1}},
            status={'fault': 0, 'armed': 1}, allow_actuators=True)
        self.monitor = JamMonitor(self.board)
        self.tools = types.SimpleNamespace(enabled=True, running=False, bound_tool=0,
                                          require_binding=lambda: None)
        self.objs['inventure_tools'] = self.tools
        self.sd = types.SimpleNamespace(is_active=lambda: True, current_file=object())
        self.objs['virtual_sdcard'] = self.sd
        self.objs['print_stats'] = types.SimpleNamespace(get_status=lambda now: dict(state='printing'))

    def test_cassette_faults_signed_actual_budget_and_uint32_wrap(self):
        self.assertIsNone(classify('CASSETTE', row(forward=4, budget=-200), 4))
        self.assertIsNone(classify('CASSETTE', row(budget=0), 12))
        self.assertEqual(classify('CASSETTE', row(fault=3), 100), 'CASSETTE_JAM')
        self.assertEqual(classify('CASSETTE', row(forward=1, budget=-1), 0xffffffff), 'EXTRUDER_JAM')
        self.assertEqual(classify('CASSETTE', row(fault=2), None, True), 'RUNOUT')
        self.assertEqual(classify('CASSETTE', row(budget=-1, reverse=3), 100, previous_reverse=2),
                         'EXTRUDER_JAM')
        with self.assertRaisesRegex(ValueError, 'counter'):
            classify('CASSETTE', row(forward=10), 410)

    def test_sources_and_unknown_balance(self):
        for low, reason in [(None, 'MATERIAL_MISSING'), (True, 'RUNOUT'), (False, 'CARTRIDGE_ERROR')]:
            self.assertEqual(classify('CASSETTE', row(a=0), 91, low), reason)
        self.monitor.set_source('T0', 'CASSETTE')
        self.monitor.set_source('T1', 'EXTERNAL')
        self.tools.bound_tool = 1
        self.assertEqual(self.monitor.poll(5.), dict(tool='T1', source='EXTERNAL', reason='RUNOUT',
                         trigger='inlet_clear', evidence={'a': 0, 'b': 1}))
        self.assertIsNone(self.monitor.poll(5.1))
        self.assertEqual(self.monitor.commands, {})
        self.assertIsNone(self.monitor.owned_tool)

    def test_stock_allowance_on_tool_change_initial_feed_first_portion_and_retraction(self):
        m = self.monitor
        for tool, portion in [('T0', 20160), ('T1', 51840)]:
            with self.subTest(tool=tool):
                baseline = row(starts=0xffffffff, forward=500, reverse=15, budget=portion)
                first, _ = m._print_sample(tool, baseline, before_auto=True)
                self.assertEqual(first['budget'], 96000)
                moved, _ = m._print_sample(tool, dict(baseline, forward=96500))
                self.assertEqual(moved['budget'], 0)
                portion_start = row(starts=0, forward=96509, reverse=15, budget=portion-9)
                measured, _ = m._print_sample(tool, portion_start)
                self.assertEqual(measured['budget'], portion-9)
                at_limit = dict(portion_start, forward=96500+portion+3, reverse=18)
                measured, prev = m._print_sample(tool, at_limit)
                self.assertEqual(measured['budget'], 0)
                self.assertIsNone(classify('CASSETTE', measured, prev['forward']))
                measured, prev = m._print_sample(tool, dict(at_limit, forward=at_limit['forward']+1))
                self.assertEqual(measured['budget'], -1)
                self.assertEqual(classify('CASSETTE', measured, prev['forward']), 'EXTRUDER_JAM')
        # An inactive tool's allowance survives selection and a re-sent R13 profile.
        resumed, _ = m._print_sample('T0', dict(m.allowances[0], budget=20160), before_auto=True)
        self.assertEqual(resumed['budget'], -1)
        self.sd.current_file = object()
        new, _ = m._print_sample('T0', row(budget=20160), before_auto=True)
        self.assertEqual(new['budget'], 96000)

    def test_p13_print_baseline_counts_first_move_on_initially_inactive_tool(self):
        baseline = row(forward=10, reverse=3, starts=5)
        self.objs['inventure_filament'] = types.SimpleNamespace(
            enabled=True, print_baselines=(row(), baseline))
        sample, prev = self.monitor._print_sample('T1', dict(baseline, forward=96011))
        self.assertEqual(sample['budget'], -1)
        self.assertEqual(classify('CASSETTE', sample, prev['forward']), 'EXTRUDER_JAM')

    def test_manual_repair_before_first_print_poll_keeps_the_replenished_budget(self):
        m = self.monitor
        m.set_source('T0', 'CASSETTE')
        self.board._control_query = lambda: dict(mode='NATIVE', armed=1, fault=0, latched=0)
        m.commands = {name: mock.Mock() for name in ('select', 'lease', 'profile', 'mode')}
        counters = row(starts=0, forward=0, reverse=0, budget=20160)
        m.commands['profile'].send_wait_ack.side_effect = lambda args: counters.update(budget=args[3])
        with mock.patch.object(m, 'sample', side_effect=lambda tool: dict(counters)):
            m._start('T0', True)
            self.assertEqual(m.allowances[0]['budget'], 96000)
            counters.update(starts=3, forward=500, budget=20100)
            m.stop_recovery()
            m._start('T0', False)
        self.assertEqual(m.allowances[0]['budget'], 20100)

    def test_source_never_deduced_from_present_or_stale_inputs(self):
        self.assertEqual(self.monitor.source('T0'), 'UNKNOWN')
        self.assertIsNone(self.monitor.poll(5.))
        self.monitor.set_source('T0', 'EXTERNAL')
        self.assertIsNone(self.monitor.poll(5.))
        self.assertEqual(self.monitor.poll(6.)['reason'], 'MONITOR_ERROR')
        self.assertEqual(self.monitor.commands, {})

    def test_empty_threshold_accepts_only_current_identity_and_bound_ledger(self):
        self.objs['inventure_materials'] = types.SimpleNamespace(enabled=True, confirmed=True, stopped=False,
            pair=[types.SimpleNamespace(source='cartridge', material_id=3)] * 2)
        tag = types.SimpleNamespace(nominal_mm=1000, remaining_mm=51, identity=lambda: 'same')
        rfid = types.SimpleNamespace(enabled=True, error_for=lambda idx, mid: None,
                                     channels=[{'tag': tag}] * 2)
        self.objs['inventure_rfid'] = rfid
        self.assertFalse(self.monitor._nearly_empty(0))
        tag.remaining_mm = 50
        self.assertTrue(self.monitor._nearly_empty(0))
        estimate = types.SimpleNamespace(uncertain=False, intent=None, identity='same',
                                         target_mm=60, last_timestamp=5.)
        self.objs['inventure_consumption'] = types.SimpleNamespace(enabled=True, max_gap=.5,
            ledger=types.SimpleNamespace(channels=[estimate, None]))
        self.assertFalse(self.monitor._nearly_empty(0))
        estimate.target_mm = 40
        self.assertTrue(self.monitor._nearly_empty(0))
        estimate.identity = 'old tag'
        self.assertIsNone(self.monitor._nearly_empty(0))
        rfid.error_for = lambda idx, mid: 'stale'
        self.assertIsNone(self.monitor._nearly_empty(0))

    def test_confirmed_material_selection_has_priority(self):
        self.monitor.set_source('T0', 'EXTERNAL')
        self.objs['inventure_materials'] = types.SimpleNamespace(enabled=True, stopped=False,
            confirmed=True, pair=(types.SimpleNamespace(source='cartridge'),
                                  types.SimpleNamespace(source='spool')))
        self.assertEqual(self.monitor.source('T0'), 'CASSETTE')
        self.assertEqual(self.monitor.source('T1'), 'EXTERNAL')
        with self.assertRaisesRegex(ValueError, 'confirmed material source'):
            self.monitor.set_source('T0', 'EXTERNAL')

    def test_fresh_edges_and_aged_pulse_over_wrapping_mcu_clock(self):
        m = self.monitor
        m.clock_hz = 10000
        m._deliver(row(clock=0xfffffffa, last_pulse_clock=0xfffffff0), 5., 5.)
        m._deliver(row(clock=14, last_pulse_clock=0xfffffff0), 5.01, 5.01)
        self.assertEqual(m.reports[0]['pulse_age_ms'], 3.)
        m._deliver(row(clock=10), 5.02, 5.02)
        m._deliver(row(clock=20), 4., 5.02)
        self.assertEqual(m.reports[0]['clock'], 14)

    def test_lost_print_lease_stops_and_never_renews_from_old_state(self):
        m = self.monitor
        m.owned_tool = 'T0'
        m.lease_time = 4.
        m.commands = {'lease': mock.Mock(), 'mode': mock.Mock()}
        self.assertEqual(m._renew(5.), self.rt.NEVER)
        m.commands['mode'].send.assert_called_once_with([0, 0, 0])
        m.commands['lease'].send.assert_not_called()
        self.assertIsNone(m.owned_tool)

    def test_legacy_fault_delegation_is_scoped_to_its_exact_live_print(self):
        m = self.monitor
        m.set_source('T0', 'CASSETTE')
        backend = types.SimpleNamespace(enabled=True, stopped=False, selected=0, running=False,
            ops=types.SimpleNamespace(state=types.SimpleNamespace(
                owner=types.SimpleNamespace(kind='print'), fatal=None)), received=[5., 5.])
        self.objs['inventure_filament'] = backend
        self.board.filament_jam = jam = types.SimpleNamespace(
            state={}, active=False, running=True, job=None)
        def start(event):
            jam.active, jam.state, jam.job = True, event, self.sd.current_file
        jam._start = start
        report = row(fault=3)
        self.assertFalse(m.legacy_fault_managed(0, report))
        m.observe_legacy_fault(0, report)
        self.assertTrue(m.legacy_fault_managed(0, report))
        self.assertEqual(jam.state['reason'], 'CASSETTE_JAM')
        for changed in [dict(channel=1), dict(live=0), dict(fault=4)]:
            self.assertFalse(m.legacy_fault_managed(0, dict(report, **changed)))
        self.rt.now += .6
        self.assertFalse(m.legacy_fault_managed(0, report))
        self.rt.now -= .6
        self.sd.current_file = object()
        self.assertFalse(m.legacy_fault_managed(0, report))

    def test_legacy_recovery_samples_pending_current_report_and_releases_only_its_tool(self):
        m = self.monitor
        m.set_source('T0', 'CASSETTE')
        initial = row()
        backend = types.SimpleNamespace(reports=[initial, None], commands={'query': mock.Mock(), 'mode': mock.Mock()},
                                        modes=[(2, 0), (0, 0)], received=[5., None], pulse_ages=[.7, 0.])
        backend.commands['query'].send.side_effect = lambda args: backend.reports.__setitem__(0, row(pulses=70))
        m.borrowed, m.owned_tool, m.recovery = backend, 'T0', True
        self.assertTrue(m.legacy_mode_delegated(0))
        self.assertFalse(m.legacy_mode_delegated(1))
        report = m.sample('T0')
        self.assertEqual(report['pulses'], 70)
        self.assertEqual(report['pulse_age_ms'], 700.)
        m.stop_recovery()
        backend.commands['mode'].send.assert_called_once_with([0, 0, 0])
        self.assertFalse(m.legacy_mode_delegated(0))

    def test_real_p13_and_p14_keep_counting_during_owned_cassette_fault(self):
        import importlib.util
        from pathlib import Path
        from test_jam_motion import pinned
        from host.inventure_consumption import InventureConsumption
        from host.inventure_consumption_core import Ledger
        pinned()  # Pin the native modules imported by the real P13 overlay.
        file = Path(__file__).resolve().parents[1] / 'host/inventure_filament.py'
        spec = importlib.util.spec_from_file_location('orange_pi.jam_test_p13', file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        p13 = object.__new__(module.InventureFilament)
        p13.board, p13.printer, p13.reactor = self.board, self.board.printer, self.rt
        p13.enabled, p13.stopped, p13.running, p13.selected = True, False, False, 0
        p13.generation, p13.rejected = 0, 0
        p13.reports, p13.received, p13.pulse_ages = [None, None], [None, None], [0., 0.]
        p13.mcu = types.SimpleNamespace(get_constant_float=lambda _: 1.e6)
        p13.ops = types.SimpleNamespace(state=types.SimpleNamespace(
            owner=types.SimpleNamespace(kind='print'), fatal=None), fatal=mock.Mock())
        self.objs['inventure_filament'] = p13
        self.monitor.set_source('T0', 'CASSETTE')
        self.board.filament_jam = jam = types.SimpleNamespace(
            monitor=self.monitor, active=False, running=True, job=None, state={})
        def accept(event):
            jam.active, jam.state, jam.job = True, event, self.sd.current_file
        jam._start = accept
        p14 = object.__new__(InventureConsumption)
        p14.board, p14.reactor, p14.ops = self.board, self.rt, p13.ops
        p14.enabled, p14.stopped, p14.clock_hz, p14.max_gap = True, False, 1.e6, .5
        p14.materials = types.SimpleNamespace(pair=[types.SimpleNamespace(source='cartridge', material_id=3)])
        tag = types.SimpleNamespace(identity=lambda: 'tag', remaining_mm=10000)
        p14.rfid = types.SimpleNamespace(channels=[dict(tag=tag, uncertain=False)])
        p14.ledger = Ledger()
        p14.ledger.bind(0, 'cartridge', 3, 'tag', 10000)
        p14._save = mock.Mock()
        self.board.printer.send_event = lambda event, idx, p: p14._report(idx, p)
        fields = dict(retry=0, direction=1, assist=0, locks=0)
        p13._deliver(row(**fields), 5., 0, 5.)
        self.rt.now += .1
        p13._deliver(row(fault=3, clock=102000, forward=1060, **fields), 5.1, 0, 5.1)
        self.assertEqual(jam.state['reason'], 'CASSETTE_JAM')
        self.assertIsNone(p14.ledger.error)
        self.assertEqual(p14.ledger.channels[0].target_mm, 9999)
        self.assertEqual(p13._sample(0)['fault'], 3)
        p13.ops.fatal.assert_not_called()
        self.rt.now += .6
        with self.assertRaises(ValueError):
            p13._sample(0)
        p13.ops.fatal.assert_called_once()

    def test_native_sample_timeout_refuses_old_snapshot(self):
        m = self.monitor
        m.set_source('T0', 'CASSETTE')
        m.owned_tool = 'T0'
        m.reports[0] = row(received=5.)
        m.commands['query'] = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'fresh'):
            m.sample('T0')


if __name__ == '__main__':
    unittest.main()
