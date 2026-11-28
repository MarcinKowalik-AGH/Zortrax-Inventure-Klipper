"""Check the paired host's cassette traffic, E/Z routing and runtime upgrade."""
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import inventure_bootstrap as bootstrap
from inventure_cassette_windows_restore import TARGET
import test_orange_pi_factory_host as factory_test
from test_install_new_host import installer, r24_mcu_identity
import test_inventure_xy_step_r30 as r30


class Host:
    def _build_config(self):
        pass

    def _factory_session(self, context, mode=None, monitor=None, next_tool=None, ceiling_steps=None):
        self.sessions.append(mode)
        return dict(context['factory']['snapshot'])

    def _factory_action(self, context, action):
        self.moves.append(action)
        if action['kind'] == 'mode':
            return self._factory_session(context, action['mode'], action['monitor'])
        return action


class CassetteWindowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        profile = bootstrap.ROOT/'restored/cassette_windows_r31/inventure_mcu_profile.py'
        namespace = dict(InventureMCU=Host, math=math)
        exec(compile(profile.read_bytes(), str(profile), 'exec'), namespace)
        cls.driver = namespace['InventureMCU']

    def setUp(self):
        self.now = 100.
        self.starts, self.keepalives, self.callbacks = [], [], {}
        self.host = self.driver()
        self.host.sessions, self.host.moves = [], []
        self.host.connected = True
        self.host.commands = {'factory_session': object()}
        self.host.reactor = SimpleNamespace(monotonic=lambda: self.now,
                                            register_async_callback=lambda cb: cb(self.now))

        def lookup(fmt):
            return SimpleNamespace(send=(self.starts if fmt == self.host._FEED_START
                                         else self.keepalives).append)

        self.host.mcu = SimpleNamespace(get_constants=lambda: {'INVENTURE_FACTORY_FEED_WINDOW_ABI': 1},
                                       lookup_command=lookup,
                                       register_serial_response=lambda cb, fmt: self.callbacks.update({fmt: cb}))
        self.host._build_config()
        self.factory = dict(snapshot=dict(a=0, b=0), phase='L2', operation='AUTO', move=None,
                            token=42, parameters=dict(a_timeout=5., unload_a_timeout=6.))
        self.context = self.host.load_test_context = dict(factory=self.factory)

    def deliver(self, state, run=1, owner=42, a=0, b=0):
        self.callbacks[self.host._FEED_STATE](dict(owner=owner, run=run, state=state, a=a, b=b))

    def mode(self, mode):
        return self.host._factory_action(self.context, dict(kind='mode', mode=mode, monitor=0))

    def test_loading_waits_on_the_mcu_with_one_start_and_lightweight_renewals(self):
        self.assertIsNone(self.mode(2))
        self.assertEqual(self.starts, [[42, 1, 1, 5000]])
        self.deliver(2, owner=43, a=1)
        self.assertIsNone(self.mode(2))
        self.deliver(1)
        self.assertEqual(self.mode(2)['mode'], 2)
        wait = dict(kind='wait_a', timeout_s=5.)
        for step in range(1, 49):
            self.now = 100. + step / 10.
            self.host._factory_session(self.context)
            self.assertIsNone(self.host._factory_action(self.context, wait))
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(self.host.sessions, [])
        self.assertTrue(8 <= len(self.keepalives) <= 12)
        self.assertTrue(all(args == [42, 1] for args in self.keepalives))
        self.deliver(2, a=1)
        self.deliver(1, a=0)  # late RUN report after the latched completion
        self.assertEqual(self.host._factory_action(self.context, wait)['a'], 1)
        self.mode(0)
        self.assertEqual(self.host.sessions, [0])
        self.assertNotIn('cassette_window', self.factory)

    def test_unload_leaves_precise_e_and_z_actions_in_the_existing_dispatcher(self):
        self.factory.update(phase='U0', operation='UNLOAD')
        self.assertIsNone(self.mode(3))
        self.deliver(2, a=1, b=1)
        self.mode(3)
        self.assertTrue(self.host._factory_action(self.context, dict(kind='wait_unload'))['ok'])
        self.mode(4)
        self.factory['phase'] = 'U3'
        self.assertIsNone(self.mode(3))
        self.deliver(1, run=2, a=1, b=1)
        self.mode(3)
        self.assertEqual(self.starts, [[42, 1, 2, 10000], [42, 2, 3, 6000]])
        e = dict(kind='retract_until_a', timeout_s=6., speed=1.)
        z = dict(kind='home', axis='Z', max_mm=250., speed=2.)
        self.assertIs(self.host._factory_action(self.context, e), e)
        self.assertIs(self.host._factory_action(self.context, z), z)
        self.assertEqual(self.host.moves[-2:], [e, z])

    def test_lost_report_is_requested_once_with_the_original_descriptor(self):
        self.mode(2)
        self.deliver(1)
        self.now += 5.31
        self.host._factory_session(self.context)
        self.assertEqual(self.starts, [[42, 1, 1, 5000], [42, 1, 1, 5000]])
        self.now += .51
        with self.assertRaisesRegex(ValueError, 'result timed out'):
            self.host._factory_session(self.context)

    def test_expired_and_rejected_windows_are_distinguished(self):
        self.mode(2)
        self.deliver(3)
        self.assertFalse(self.host._factory_action(self.context, dict(kind='wait_a'))['ok'])
        self.mode(0)
        self.mode(2)
        self.deliver(5, run=2)
        with self.assertRaisesRegex(ValueError, 'rejected'):
            self.host._factory_session(self.context)

    def test_r30_remains_on_the_original_host_protocol(self):
        self.host.mcu.get_constants = lambda: {}
        self.host._build_config()
        self.mode(2)
        self.assertEqual(self.host.sessions, [2])
        self.assertEqual(self.starts, [])

    def _factory_flow(self, scenario, drop_u3_report=False):
        fixture = factory_test.FactoryHostTests()
        host, _, _ = fixture.host(scenario, fast_motion=True, factory_motion_tools=3)
        profile = bootstrap.ROOT/'restored/cassette_windows_r31/inventure_mcu_profile.py'
        namespace = dict(InventureMCU=type(host), math=math)
        exec(compile(profile.read_bytes(), str(profile), 'exec'), namespace)
        host.__class__ = namespace['InventureMCU']
        device = host.device
        original_refresh = host.refresh
        trace = []
        active = {}

        def refresh():
            original_refresh()
            if not active or not device.session['active'] or device.session['mode'] != active['mode']:
                return
            condition = active['condition']
            reached = device.a if condition == 1 else not device.a or (condition == 2 and device.b)
            if reached and active['state'] == 1:
                active['state'] = 2
                if condition != 3 or not drop_u3_report:
                    host._feed_deliver(dict(owner=active['owner'], run=active['run'], state=2,
                                            a=device.a, b=device.b))
            if active['state'] == 2:
                device.running = False

        def start(args):
            owner, run, condition, timeout = args
            trace.append((owner, run, condition, timeout))
            mode = 2 if condition == 1 else 3
            device.session['mode'] = mode
            active.update(owner=owner, run=run, mode=mode, condition=condition, state=1)
            host._feed_deliver(dict(owner=owner, run=run, state=1, a=device.a, b=device.b))
            refresh()

        def keepalive(identity):
            self.assertEqual(identity, [active['owner'], active['run']])
            device.session['deadline'] = host.reactor.monotonic() + 1.5

        host.refresh = refresh
        host._feed_start = SimpleNamespace(send=start)
        host._feed_keepalive = SimpleNamespace(send=keepalive)
        return fixture, host, trace

    def test_full_load_reaches_l4_with_the_original_e_segment(self):
        fixture, host, starts = self._factory_flow('wait_a')
        fixture.start(host, 1, SPEED_PROFILE='OEM')
        fixture.wait_phase(host, 'L2')
        replies = len(host.device.factory_packets)
        host.reactor.pause(host.reactor.now + .60)
        self.assertEqual(len(host.device.factory_packets), replies)
        host.device.a = 1
        last = fixture.finish(host)
        self.assertEqual(last['phase'], 'done', last)
        self.assertEqual(last['factory']['result']['terminal'], 'L4')
        self.assertEqual([(case, limit) for _, _, case, limit in starts], [(1, 5000)])
        e = [p[6:9] for p in host.device.packets if p[:2] == ('motion', 1) and p[6] and not (p[3] or p[4])]
        self.assertEqual(e, [(250*960, 1536, 10)])

    def test_full_unload_keeps_e_budgets_and_releases_on_a_even_if_the_u3_report_is_lost(self):
        fixture, host, starts = self._factory_flow('unload_hold_a', drop_u3_report=True)
        host.cmd_procedure(factory_test.Command(ACTION='UNLOAD', TOOL='T1', SPEED_PROFILE='OEM',
                                                MATERIAL='Z-SUPPORT'))
        fixture.wait_phase(host, 'U3')
        host.reactor.pause(host.reactor.now + .50)
        host.device.a = 0
        released = host.reactor.now
        last = fixture.finish(host)
        self.assertEqual(last['phase'], 'done', last)
        self.assertLess(host.reactor.now - released, .50)
        self.assertEqual(last['factory']['result']['terminal'], 'UNLOAD')
        self.assertEqual([(case, limit) for _, _, case, limit in starts], [(2, 10000), (3, 6000)])
        e = [p[6] for p in host.device.packets if p[:2] == ('motion', 1) and p[6]]
        self.assertEqual(e, [50*960, 1, 6*960])

    def test_installer_requires_the_matched_cassette_abi(self):
        identity = r24_mcu_identity()
        constants = identity['mcu_constants']
        constants.update(r30.CAPABILITIES, INVENTURE_MCU_RELEASE=installer.MCU_CASSETTE_WINDOWS_RELEASE,
                         INVENTURE_FACTORY_INFO_ABI=1, INVENTURE_FACTORY_COUNTER_ABI=1,
                         INVENTURE_OLED_COEXISTENCE_ABI=1, INVENTURE_UART_TIMER_WINDOW_ABI=1,
                         INVENTURE_FACTORY_FEED_WINDOW_ABI=1)
        self.assertEqual(installer.require_mcu(identity)['mcu_release'], installer.MCU_CASSETTE_WINDOWS_RELEASE)
        del constants['INVENTURE_FACTORY_FEED_WINDOW_ABI']
        with self.assertRaisesRegex(ValueError, 'cassette feed phase capability'):
            installer.require_mcu(identity)

    def test_r30_r31_upgrade_and_rollback_touch_only_the_board_adapter(self):
        with TemporaryDirectory() as directory, bootstrap.procedure_profile('operator-home-r24'):
            with bootstrap.xy_step_timers('r30'):
                host = Path(directory)/'host'
                _, old_files, old_meta = bootstrap._plan(bootstrap.ROOT)
                bootstrap._write_files(host, old_files)
                marker = host/bootstrap.MANIFEST_NAME
                marker.write_bytes(bootstrap._metadata_bytes(old_meta))
                marker.chmod(0o644)
                for enable in (True, False):
                    with bootstrap.cassette_windows(enable):
                        update = bootstrap.prepare_runtime_update(host)
                        self.assertEqual(set(update['files']), {host/TARGET, marker})
                        for path, (_, new) in update['files'].items():
                            path.write_bytes(new)
                        self.assertEqual(bootstrap.verify_host(host)['status'], 'VERIFIED_HOST')
                self.assertEqual(marker.read_bytes(), bootstrap._metadata_bytes(old_meta))
                self.assertEqual((host/TARGET).read_bytes(), old_files[TARGET][0])


if __name__ == '__main__':
    unittest.main()
