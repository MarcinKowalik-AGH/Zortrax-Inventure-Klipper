"""Exercise both real OLED pixel producers and defer optional factory reads."""
from hashlib import sha256
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import inventure_bootstrap as bootstrap
from test_orange_pi_oled_fast import Config, Printer, o, ready_stream
from test_inventure_home_ui import ui as historical_ui

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('communication_guard_test',
    ROOT/'restored/information_r25/inventure_communication_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardedOLED(guard.CommunicationQuietOLED, historical_ui.HomeOLEDUI, o.InventureOLED):
    pass


class CommunicationGuardTests(unittest.TestCase):
    def fixture(self):
        printer = Printer()
        oled = GuardedOLED(Config(printer))
        oled._build_config()
        oled.connected = True
        oled.stream = ready_stream()
        oled.next_query = oled.render_at = 0.
        oled._render = lambda now: oled.stream.request(b'\x55'*8192, True)
        oled.keys = []
        oled._held_keys = oled.keys.append
        printer.objects['inventure_oled'] = oled
        return printer, oled

    def event(self, printer, name, *args):
        for handler in printer.events[name]:
            handler(*args)

    def test_native_homing_ticks_keep_buttons_and_lease_but_send_no_pixels(self):
        printer, oled = self.fixture()
        move = object()
        self.event(printer, 'homing:homing_move_begin', move)
        for tick in range(101):
            now = 10.+tick*.1
            printer.reactor.now = now
            self.assertAlmostEqual(oled._tick(now), now+.1)
            self.assertAlmostEqual(oled._next_tick(now), now+.1)
        names = [name for name, _, _ in printer.mcu.calls]
        self.assertEqual(set(names), {'inventure_oled_ping', 'inventure_oled_query'})
        self.assertEqual(names.count('inventure_oled_ping'), 21)
        self.assertEqual(len(oled.keys), 101)
        self.assertTrue(oled.get_status(20.)['native_homing_pixels_paused'])
        self.event(printer, 'homing:homing_move_end', move)
        self.assertFalse(oled.get_status(20.)['native_homing_pixels_paused'])
        self.assertAlmostEqual(oled.get_status(20.)['native_homing_quiet_seconds'], 10.)
        oled._pump(20.)
        self.assertTrue(any(name == 'inventure_oled_burst' for name, _, _ in printer.mcu.calls))

    def test_real_ack_callback_cannot_restart_pixels_or_create_a_busy_loop(self):
        printer, oled = self.fixture()
        oled.stream.request(b'\x55'*8192, True)
        oled._pump(10.)
        sent = len(printer.mcu.calls)
        pending = list(oled.stream.inflight)
        self.assertGreater(len(pending), 0)
        self.event(printer, 'homing:homing_move_begin', object())
        for token, _, _, _ in pending:
            oled._ack(dict(generation_hi=oled.stream.generation >> 32,
                generation_lo=oled.stream.generation & 0xffffffff,
                token=token, result=0, **{'#receive_time': 10.001}))
            printer.reactor.flush()
            self.assertEqual(len(printer.mcu.calls), sent)
            self.assertGreaterEqual(oled.timer.when, 10.1)
        self.assertTrue(oled.stream.enabled)
        self.assertIsNone(oled.stream.failure)

    def test_entire_home_including_local_pc14_is_quiet_without_native_move_events(self):
        printer, oled = self.fixture()
        native = types.SimpleNamespace(sequence_running=True, running=False)
        printer.objects['inventure_native_z'] = native
        oled._render(10.)
        # Heat/SERVICE, the operator's MCU-local PC14 phase, and direct local Z.
        for sequence_running, running in ((True, False), (True, True), (False, True)):
            native.sequence_running, native.running = sequence_running, running
            for now in (10., 10.5, 11.):
                self.assertEqual(oled._tick(now), now+.1)
                self.assertFalse(oled._send_one(now))
                self.assertEqual(oled._next_tick(now), now+.1)
            self.assertTrue(oled.get_status(11.)['home_sequence_pixels_paused'])
            self.assertFalse(oled.get_status(11.)['native_homing_pixels_paused'])
        self.assertFalse(any(name == 'inventure_oled_burst' for name, _, _ in printer.mcu.calls))
        native.sequence_running = native.running = False
        oled._pump(12.)
        self.assertTrue(any(name == 'inventure_oled_burst' for name, _, _ in printer.mcu.calls))

    def test_guard_precedes_the_frozen_home_wrapper_that_resumed_pixels_during_hold(self):
        printer, oled = self.fixture()
        home = types.SimpleNamespace(busy=True)
        printer.board.zortrax = types.SimpleNamespace(home_ui=home)
        oled.thermal_test_hold = True
        self.assertEqual(oled._tick(10.), 10.1)
        self.assertFalse(oled._send_one(10.))
        self.assertTrue(oled.thermal_test_hold)
        self.assertFalse(any(name == 'inventure_oled_burst' for name, _, _ in printer.mcu.calls))
        home.busy = False
        self.assertFalse(oled._home_pixels_paused())

    def test_nested_moves_and_shutdown_leave_no_stale_pause_on_reconnect(self):
        printer, oled = self.fixture()
        a, b = object(), object()
        self.event(printer, 'homing:homing_move_begin', a)
        self.event(printer, 'homing:homing_move_begin', b)
        self.event(printer, 'homing:homing_move_end', a)
        self.assertFalse(oled._send_one(10.))
        self.assertEqual(oled._quiet_homing_moves, {b})
        # Run just the guard cleanup; production shutdown retains its existing
        # safety/display owner, independently tested by the original adapter.
        for handler in printer.events['klippy:shutdown']:
            if handler.__name__ == '_quiet_homing_reset':
                handler()
        self.assertFalse(oled._quiet_homing_moves)
        self.assertEqual(oled._quiet_homing_count, 1)

    def test_cached_render_failure_does_not_abort_the_homing_event(self):
        printer, oled = self.fixture()
        move = object()
        self.event(printer, 'homing:homing_move_begin', move)
        with patch.object(oled, '_render', side_effect=RuntimeError('renderer failed')), \
             patch.object(guard.logging, 'exception'):
            self.assertEqual(oled._tick(10.), 10.1)
        self.assertEqual(oled._quiet_homing_moves, {move})
        self.assertFalse(any(name == 'inventure_oled_burst' for name, _, _ in printer.mcu.calls))

    def test_background_info_does_no_query_journal_or_commit_while_home_runs(self):
        calls = []
        class OriginalInfo:
            def synchronize(self, now):
                calls.append(now)
                return now+5.
            def get_status(self, now):
                return {'information_release': 'RC8-INFORMATION-R25.2'}
        class Info(guard.CommunicationQuietInformation, OriginalInfo):
            pass
        printer, oled = self.fixture()
        printer.is_shutdown = lambda: False
        info = Info()
        info.printer, info.board, info.ready, info.transport = printer, printer.board, True, object()
        native = types.SimpleNamespace(get_status=lambda now: {'running': True})
        printer.objects['inventure_native_z'] = native
        for now in (10., 15., 20.):
            self.assertEqual(info.synchronize(now), now+5.)
        self.assertEqual(calls, [])
        self.assertEqual(info.wait_reason, 'inventure_native_z')
        printer.objects.pop('inventure_native_z')
        self.event(printer, 'homing:homing_move_begin', object())
        info.synchronize(25.)
        self.assertEqual(info.wait_reason, 'native_homing')
        oled._quiet_homing_reset()
        printer.board.load_test_context = {}
        info.synchronize(30.)
        self.assertEqual(info.wait_reason, 'service')
        printer.board.load_test_context = None
        info.synchronize(35.)
        self.assertEqual(calls, [35.])
        self.assertEqual(info.get_status(35.)['communication_guard_release'], guard.RELEASE)

    def test_rc3_upgrade_preserves_all_accepted_home_and_info_storage_engines(self):
        from test_install_new_host import installer
        with tempfile.TemporaryDirectory() as temp, bootstrap.procedure_profile('operator-home-r24'):
            host = Path(temp)/'host'
            _, old, metadata = bootstrap._plan(ROOT, information_revision='rc3')
            self.assertEqual(sha256(bootstrap._metadata_bytes(metadata)).hexdigest(),
                '8b738e864f877e176094fd02d698a3e4050bf2dfbc27ab1e1703bb6a3ac3dba3')
            _, new, _ = bootstrap._plan(ROOT)
            changed = {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)}
            self.assertEqual(changed, {'klippy/extras/'+name for name in
                ('inventure_oled.py', 'inventure_information.py', 'inventure_communication_guard.py')})
            for name in ('inventure_native_z.py', 'inventure_operator_home.py', 'inventure_mcu.py',
                         'inventure_z_home.py', 'inventure_home_ui.py', 'inventure_info_store.py',
                         'inventure_counter_sync.py', 'inventure_runtime.py'):
                self.assertEqual(old['klippy/extras/'+name], new['klippy/extras/'+name])
            bootstrap._write_files(host, old)
            marker = host/bootstrap.MANIFEST_NAME
            marker.write_bytes(bootstrap._metadata_bytes(metadata))
            marker.chmod(0o644)
            update = bootstrap.prepare_runtime_update(host)
            self.assertEqual({p.relative_to(host).as_posix() for p in update['files']},
                             changed | {bootstrap.MANIFEST_NAME})
            for path, (before, after) in update['files'].items():
                installer.updater.atomic_config_write(path, after, create=before is None)
            self.assertEqual(bootstrap.verify_host(host)['status'], 'VERIFIED_HOST')
            for path, (before, _) in update['files'].items():
                if before is None:
                    path.unlink()
                else:
                    path.write_bytes(before)
            self.assertEqual(bootstrap.verify_host(host, allow_legacy=True)['status'], 'VERIFIED_LEGACY_HOST')


if __name__ == '__main__':
    unittest.main()
