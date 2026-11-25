"""Exercise the real installation transaction without contacting a printer.

Configuration backups, atomic writes and Fluidd directory exchanges use real
temporary files.  Only service/process introspection and HTTP boundaries are
replaced; the dashboard and readiness checks execute their production code.
"""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_new_host_installer', ROOT/'install-new-host.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def mcu_identity():
    return dict(mcu_version='test-R16', mcu_constants={
        'INVENTURE_MCU_RELEASE': installer.MCU_RELEASE,
        'INVENTURE_NATIVE_XY_HANDOFF_ABI': 1,
        'INVENTURE_NATIVE_Z_CLEARANCE_ABI': 1,
    })


class PureGuardsTests(unittest.TestCase):
    def test_systemd_arguments_preserve_spaces_quotes_expansions_and_empty_args(self):
        data = installer.unit_text([
            '/home/klipper/python env/bin/python', '-B', '/host%R/klippy.py',
            '/config/printer "lab".cfg', r'/tmp/l\og$HOME.log', '',
        ]).decode()
        self.assertEqual(data, installer.UNIT_MARKER + '[Service]\nExecStart=\n'
            'ExecStart="/home/klipper/python env/bin/python" "-B" "/host%%R/klippy.py" '
            '"/config/printer \\"lab\\".cfg" "/tmp/l\\\\og$$HOME.log" ""\n')
        for argv in ([], ['python3'], ['/python', 'bad\narg'], ['/python', 'bad\x00arg'],
                     ['/python', 'bad\targ'], ['/python', 'bad\x7farg']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                installer.unit_text(argv)

    def test_r17_native_service_handoff_capability_checked_without_disabling_r16(self):
        legacy = mcu_identity()
        self.assertEqual(installer.require_mcu(legacy)['native_service_handoff_abi'], 0)
        modern = deepcopy(legacy)
        modern['mcu_constants']['INVENTURE_MCU_RELEASE'] = installer.MCU_SERVICE_HANDOFF_RELEASE
        modern['mcu_constants']['INVENTURE_NATIVE_SERVICE_HANDOFF_ABI'] = 1
        self.assertEqual(installer.require_mcu(modern)['native_service_handoff_abi'], 1)
        modern['mcu_constants']['INVENTURE_NATIVE_SERVICE_HANDOFF_ABI'] = 0
        with self.assertRaisesRegex(ValueError, 'R17'):
            installer.require_mcu(modern)

    def test_r18_requires_independent_native_thermal_profile_and_fault_reason(self):
        modern = mcu_identity()
        modern['mcu_constants']['INVENTURE_MCU_RELEASE'] = installer.MCU_NATIVE_PROFILE_RELEASE
        modern['mcu_constants']['INVENTURE_NATIVE_SERVICE_HANDOFF_ABI'] = 1
        modern['mcu_constants']['INVENTURE_NATIVE_THERMAL_PROFILE_ABI'] = 1
        modern['mcu_constants']['INVENTURE_FAULT_REASON_ABI'] = 1
        self.assertEqual(installer.require_mcu(modern)['native_thermal_profile_abi'], 1)
        for key in ('INVENTURE_NATIVE_THERMAL_PROFILE_ABI', 'INVENTURE_FAULT_REASON_ABI'):
            broken = deepcopy(modern)
            broken['mcu_constants'].pop(key)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                installer.require_mcu(broken)

    def test_r23_display_requires_its_caps_and_all_r22_safety_caps(self):
        modern=mcu_identity()
        modern['mcu_constants'].update(
            INVENTURE_MCU_RELEASE=installer.MCU_DISPLAY_RELEASE,
            INVENTURE_NATIVE_SERVICE_HANDOFF_ABI=1,
            INVENTURE_NATIVE_THERMAL_PROFILE_ABI=1,INVENTURE_FAULT_REASON_ABI=1,
            INVENTURE_SCHEDULER_ABI=1,INVENTURE_SCHEDULER_NATIVE_HZ=10000,
            INVENTURE_SCHEDULER_SERVICE_HZ=20000,INVENTURE_SCHEDULER_GUARD_MS=10,
            INVENTURE_SERVICE_NATIVE_SPLIT_ABI=1,INVENTURE_POLICY_STATUS_ABI=1,
            INVENTURE_DISPLAY_ERROR_ABI=1,INVENTURE_LOCAL_OLED_ANIMATION_ABI=1,
            INVENTURE_LOCAL_OLED_NATIVE_GRAY4=1,INVENTURE_LOCAL_SPLASH_ABI=1,
            INVENTURE_OLED_HOSTLINK_TIMEOUT_MS=1500)
        self.assertEqual(installer.require_mcu(modern)['mcu_release'],installer.MCU_DISPLAY_RELEASE)
        for key in ('INVENTURE_SERVICE_NATIVE_SPLIT_ABI','INVENTURE_POLICY_STATUS_ABI',
                    'INVENTURE_NATIVE_SERVICE_HANDOFF_ABI','INVENTURE_DISPLAY_ERROR_ABI',
                    'INVENTURE_LOCAL_OLED_ANIMATION_ABI','INVENTURE_LOCAL_OLED_NATIVE_GRAY4',
                    'INVENTURE_LOCAL_SPLASH_ABI','INVENTURE_OLED_HOSTLINK_TIMEOUT_MS'):
            broken=deepcopy(modern);broken['mcu_constants'].pop(key)
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,key):
                installer.require_mcu(broken)

    def test_r22_service_native_policy_abi_required_and_r21_remains_compatible(self):
        modern = mcu_identity()
        modern['mcu_constants']['INVENTURE_MCU_RELEASE'] = installer.MCU_SAFETY_POLICY_RELEASE
        modern['mcu_constants'].update(
            INVENTURE_NATIVE_SERVICE_HANDOFF_ABI=1,
            INVENTURE_NATIVE_THERMAL_PROFILE_ABI=1,
            INVENTURE_FAULT_REASON_ABI=1,
            INVENTURE_SCHEDULER_ABI=1,
            INVENTURE_SCHEDULER_NATIVE_HZ=10000,
            INVENTURE_SCHEDULER_SERVICE_HZ=20000,
            INVENTURE_SCHEDULER_GUARD_MS=10,
            INVENTURE_SERVICE_NATIVE_SPLIT_ABI=1,
            INVENTURE_POLICY_STATUS_ABI=1)
        profile=installer.require_mcu(modern)
        self.assertEqual(profile['service_native_split_abi'],1)
        self.assertEqual(profile['policy_status_abi'],1)
        for key in ('INVENTURE_SERVICE_NATIVE_SPLIT_ABI','INVENTURE_POLICY_STATUS_ABI'):
            broken = deepcopy(modern)
            broken['mcu_constants'].pop(key)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,key):
                installer.require_mcu(broken)
        predecessor=deepcopy(modern)
        predecessor['mcu_constants']['INVENTURE_MCU_RELEASE']=installer.MCU_SCHEDULER_RELEASE
        predecessor['mcu_constants'].pop('INVENTURE_SERVICE_NATIVE_SPLIT_ABI')
        predecessor['mcu_constants'].pop('INVENTURE_POLICY_STATUS_ABI')
        self.assertEqual(installer.require_mcu(predecessor)['policy_status_abi'],0)

    def test_r21_scheduler_release_requires_scheduler_abi_and_clock_profiles(self):
        modern = mcu_identity()
        modern['mcu_constants']['INVENTURE_MCU_RELEASE'] = installer.MCU_SCHEDULER_RELEASE
        modern['mcu_constants'].update(
            INVENTURE_NATIVE_SERVICE_HANDOFF_ABI=1,
            INVENTURE_NATIVE_THERMAL_PROFILE_ABI=1,
            INVENTURE_FAULT_REASON_ABI=1,
            INVENTURE_SCHEDULER_ABI=1,
            INVENTURE_SCHEDULER_NATIVE_HZ=10000,
            INVENTURE_SCHEDULER_SERVICE_HZ=20000,
            INVENTURE_SCHEDULER_GUARD_MS=10)
        self.assertEqual(installer.require_mcu(modern)['scheduler_abi'],1)
        for field in ('INVENTURE_SCHEDULER_ABI', 'INVENTURE_SCHEDULER_NATIVE_HZ',
                      'INVENTURE_SCHEDULER_SERVICE_HZ','INVENTURE_SCHEDULER_GUARD_MS'):
            damaged = deepcopy(modern)
            damaged['mcu_constants'][field] = 0
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,field):
                installer.require_mcu(damaged)

    def test_mcu_release_and_both_abis_are_required(self):
        good = mcu_identity()
        self.assertEqual(installer.require_mcu(good)['native_z_clearance_abi'], 1)
        for key in good['mcu_constants']:
            for value in (None, 'R15', 0):
                broken = deepcopy(good)
                broken['mcu_constants'][key] = value
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'R16'):
                    installer.require_mcu(broken)
        strings = deepcopy(good)
        strings['mcu_constants']['INVENTURE_NATIVE_Z_CLEARANCE_ABI'] = '1'
        strings['mcu_constants']['INVENTURE_NATIVE_XY_HANDOFF_ABI'] = '1'
        installer.require_mcu(strings)

    def test_snapshot_detects_changes_creation_removal_and_symlinks_without_writes(self):
        with tempfile.TemporaryDirectory(prefix='inventure-snapshot-') as temporary:
            root = Path(temporary)
            existing, missing = root/'printer.cfg', root/'settings.json'
            existing.write_bytes(b'operator calibration\n')
            snapshot = {str(existing): sha256(existing.read_bytes()).hexdigest(), str(missing): None}
            installer.verify_snapshot(snapshot)
            existing.write_bytes(b'changed by operator\n')
            with self.assertRaisesRegex(ValueError, 'changed during preparation'):
                installer.verify_snapshot(snapshot)
            self.assertEqual(existing.read_bytes(), b'changed by operator\n')
            existing.write_bytes(b'operator calibration\n')
            missing.write_bytes(b'new state')
            with self.assertRaises(ValueError):
                installer.verify_snapshot(snapshot)
            self.assertEqual(missing.read_bytes(), b'new state')
            missing.unlink()
            existing.unlink()
            with self.assertRaises(ValueError):
                installer.verify_snapshot(snapshot)
            existing.symlink_to(root/'absent')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                installer.verify_snapshot(snapshot)
            self.assertTrue(existing.is_symlink())


class TransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='inventure-transaction-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = self.root/'report'
        self.report.mkdir()
        self.config = self.root/'config/printer.cfg'
        self.config.parent.mkdir()
        self.before_config = b'[printer]\nkinematics: none\n# local original\n'
        self.config.write_bytes(self.before_config)
        self.config.chmod(0o640)
        self.moon = self.config.parent/'moonraker.conf'
        self.moon.write_bytes(b'[server]\n# previous Moonraker\n')
        self.before_moon = self.moon.read_bytes()
        self.include = self.config.parent/'inventure_services.cfg'
        self.dropin = self.root/'systemd/50-inventure-release.conf'
        self.dropin.parent.mkdir()
        self.host = self.root/'new-host'
        (self.host/'klippy').mkdir(parents=True)
        self.script = self.host/'klippy/klippy.py'
        self.script.write_bytes(b'# No code is executed by this process fixture.\n')
        self.argv = [sys.executable, str(self.script), str(self.config)]
        self.unit = installer.unit_text(self.argv)
        self.web = self.root/'web'
        self.source = self.root/'web-source'
        for directory, version in ((self.web, 'old'), (self.source, 'new')):
            (directory/'assets').mkdir(parents=True)
            (directory/'index.html').write_text('<title>Fluidd</title><script src="assets/'+version+'.js"></script>')
            (directory/'assets'/f'{version}.js').write_text(version)
        (self.web/'config.json').write_bytes(b'{"operator": "retained"}')
        (self.source/installer.fluidd_web.MARKER).write_text(installer.fluidd_web.TAG+'\n')
        self.before_web = self.web_files()
        self.db = {'macros': {'categories': [], 'stored': []}, 'uiSettings.general.locale': 'pl'}
        self.before_db = deepcopy(self.db)
        self.http_calls, self.service_calls, self.start_snapshots = [], [], []
        self.state, self.message = 'ready', 'Printer is ready'
        self.mcu = mcu_identity()
        self.board = {'host_revision': installer.bootstrap.HOST_REVISION, 'armed': 0, 'fault': 0,
                      'connected': True, 'status_age': 0.1,
                      'host_module':str(self.host/'klippy/extras/inventure_mcu.py')}
        self.native_z, self.oled = {'running': False,
            'host_module':str(self.host/'klippy/extras/inventure_native_z.py'),
            'homing_safety_release':installer.LEVELING_UI_RELEASE}, {
            'ui_transport': {'active_work': False},
            'procedure_ui_release': installer.LEVELING_UI_RELEASE,
            'operator_menu_root':'launcher','operator_menu_roots':['Klipper','Zortrax Inventure']}
        self.serial, self.baud = '/dev/ttyS5', 750000
        self.macros = {'INVENTURE_DIAGNOSTICS', 'INVENTURE_HOME_ALL', 'PAUSE'}
        self.fail_locale_once = self.fail_web = self.fail_after_web = False
        self.fail_stop_once = self.break_restore = False
        self.unit_write_failure_once = False
        self.process = SimpleNamespace(name='81', stat=lambda: SimpleNamespace(st_uid=1000))
        self.enterContext(patch.object(installer.dashboard, 'request', side_effect=self.request))
        self.enterContext(patch.object(installer.updater, 'service', side_effect=self.service))
        self.enterContext(patch.object(installer, 'write_unit', side_effect=self.write_unit))
        self.enterContext(patch.object(installer, 'reload_units'))
        self.enterContext(patch.object(installer.fluidd_web, 'activate', side_effect=self.deploy_web))
        self.enterContext(patch.object(installer, 'restore_web', side_effect=lambda root, before:
            installer.fluidd_web.exchange(root, Path(before))))
        self.enterContext(patch.object(installer.core, 'running_service_script',
            return_value=(self.process, self.argv, 1, self.script)))
        self.enterContext(patch.object(installer.core, 'running_printer_config', return_value=self.config))

    def web_files(self):
        return {str(p.relative_to(self.web)): p.read_bytes() for p in self.web.rglob('*') if p.is_file()}

    def info(self):
        return dict(process_id=81, klipper_path=str(self.host), config_file=str(self.config),
                    software_version=installer.bootstrap.VERSION,
                    state=self.state, state_message=self.message)

    def request(self, base, endpoint, data=None):
        self.http_calls.append((endpoint, deepcopy(data)))
        path, query = urlsplit(endpoint).path, parse_qs(urlsplit(endpoint).query, keep_blank_values=True)
        if path == '/printer/info':
            return self.info()
        if path == '/server/info':
            return {'klippy_connected': True}
        if path == '/printer/objects/list':
            return {'objects': ['mcu', 'inventure_mcu', 'print_stats', 'idle_timeout', 'extruder',
                                'inventure_native_z', 'inventure_oled']}
        if path == '/printer/objects/query':
            values = {
                'webhooks': {'state': self.state, 'state_message': self.message},
                'configfile': {'config': dict(
                    [('gcode_macro '+name, {}) for name in self.macros]
                    + [('mcu', {'serial': self.serial, 'baud': str(self.baud)})])},
                'inventure_mcu': self.board,
                'inventure_native_z': self.native_z, 'inventure_oled': self.oled,
                'mcu': self.mcu, 'print_stats': {'state': 'complete'},
                'idle_timeout': {'state': 'Ready'}, 'extruder': {'target': 0},
            }
            return {'status': deepcopy({name: values[name] for name in query})}
        if path == '/server/database/item':
            key = data['key'] if data else query['key'][0]
            if data:
                if key == 'uiSettings.general.locale' and self.fail_locale_once:
                    self.fail_locale_once = False
                    raise urllib.error.HTTPError(endpoint, 503, 'Injected locale write failure', {}, None)
                self.db[key] = deepcopy(data['value'])
            return {'value': deepcopy(self.db[key])}
        raise AssertionError('Unexpected HTTP request: '+endpoint)

    def service(self, action, name='klipper'):
        self.service_calls.append((action, name))
        if action == 'stop' and self.fail_stop_once:
            self.fail_stop_once = False
            raise subprocess.TimeoutExpired(['systemctl', 'stop', name], 45)
        if action == 'stop' and self.break_restore and self.include.exists():
            self.break_restore = False
            self.config.unlink()
            self.config.mkdir()  # Actual filesystem collision during rollback.
        if action == 'start':
            self.start_snapshots.append((installer.regular_bytes(self.dropin), self.config.read_bytes()))

    def write_unit(self, data, directory, dropin):
        if self.unit_write_failure_once:
            self.unit_write_failure_once = False
            dropin.write_bytes(b'partially replaced unit')
            raise OSError('Injected privileged installation failure')
        if data is None:
            dropin.unlink(missing_ok=True)
        else:
            dropin.write_bytes(data)

    def deploy_web(self, source, root):
        if self.fail_web:
            raise OSError('Injected web deployment failure')
        previous = installer.fluidd_web.deploy(source, root)
        if self.fail_after_web:
            self.state, self.message = 'error', 'Injected final configuration failure'
        return str(previous) if previous else None

    def transaction(self, moon_changed=True, runtime_updates=None, before_start=None):
        updates = {
            self.config: (self.before_config, b'[include inventure_services.cfg]\n[printer]\nkinematics: corexy\n'),
            self.include: (None, b'[gcode_macro INVENTURE_DIAGNOSTICS]\ngcode: INVENTURE_VERSION\n'),
        }
        if moon_changed:
            updates[self.moon] = self.before_moon, b'[server]\n# selected Inventure web\n'
        updates.update(runtime_updates or {})
        return installer.InstallTransaction(self.report, updates, self.unit, moon_changed,
                                             self.web, dropin=self.dropin, before_start=before_start)

    def activate(self, transaction, **kwargs):
        return installer.activate(transaction, self.host, self.config, self.source, self.macros, **kwargs)

    def assert_restored(self, previous_unit=None):
        self.assertEqual(self.config.read_bytes(), self.before_config)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.moon.read_bytes(), self.before_moon)
        self.assertFalse(self.include.exists())
        self.assertEqual(installer.regular_bytes(self.dropin), previous_unit)
        self.assertEqual(self.db, self.before_db)
        self.assertEqual(self.web_files(), self.before_web)
        self.assertEqual(self.start_snapshots[-1], (previous_unit, self.before_config))
        self.assertEqual(json.loads((self.report/'rollback.json').read_text())['status'], 'ROLLED_BACK')

    def test_invalid_mcu_apply_exits_before_creating_any_installation_or_stopping_service(self):
        self.mcu['mcu_constants']['INVENTURE_MCU_RELEASE'] = 'RC8-CONTROL-R7-HOMING-R15'
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        with patch.object(installer.os, 'geteuid', return_value=1000), \
             patch.object(Path, 'home', side_effect=AssertionError('No staging before the MCU gate')), \
             self.assertRaisesRegex(ValueError, 'R16'):
            installer.run(SimpleNamespace(apply=True))
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(self.service_calls, [])
        self.assertFalse(any(data for _, data in self.http_calls))

    def test_complete_activation_uses_real_dashboard_web_and_file_writes(self):
        transaction = self.transaction()
        final, groups = self.activate(transaction, serial=self.serial, baud=self.baud)
        self.assertFalse(final['firmware_restart_required'])
        self.assertEqual(groups['status'], 'GROUPS_INSTALLED')
        self.assertEqual(self.dropin.read_bytes(), self.unit)
        self.assertEqual(self.config.read_bytes(), transaction.updates[self.config][1])
        self.assertEqual(self.db['uiSettings.general.locale'], 'en')
        self.assertTrue(installer.fluidd_web.same_build(self.source, self.web))
        self.assertEqual((self.web/'config.json').read_bytes(), b'{"operator": "retained"}')
        self.assertFalse((self.report/'rollback.json').exists())
        self.assertEqual(self.service_calls.count(('start', 'klipper')), 1)

    def test_armed_controller_is_rejected_before_staging_and_service_stop(self):
        self.board['armed'] = 1
        with patch.object(installer.os, 'geteuid', return_value=1000), \
             patch.object(Path, 'home', side_effect=AssertionError('No staging while armed')), \
             self.assertRaisesRegex(ValueError, 'remains armed'):
            installer.run(SimpleNamespace(apply=True))
        self.assertEqual(self.service_calls, [])
        self.assertEqual(self.config.read_bytes(), self.before_config)
        self.assertFalse(any(data for _, data in self.http_calls))

    def test_active_safety_fault_is_not_treated_as_a_restart_instruction(self):
        self.board.update(armed=1, fault=1)
        with self.assertRaisesRegex(ValueError, 'Inspect its cause'):
            installer.idle_mcu()
        self.assertEqual(self.service_calls, [])
        self.assertFalse(any('gcode' in endpoint for endpoint, _ in self.http_calls))

    def test_missing_or_stale_board_status_does_not_permit_a_service_stop(self):
        healthy = dict(self.board)
        cases = [{}, dict(healthy, connected=False), dict(healthy, status_age=2),
                 dict(healthy, status_age=-1), dict(healthy, status_age=float('nan')),
                 dict(healthy, status_age=float('inf')), dict(healthy, armed=None),
                 dict(healthy, fault=None)]
        for state in cases:
            with self.subTest(state=state):
                self.board = state
                with patch.object(installer.os, 'geteuid', return_value=1000), \
                     patch.object(Path, 'home', side_effect=AssertionError('No staging before MCU check')), \
                     self.assertRaisesRegex(ValueError, 'fresh Inventure MCU status'):
                    installer.run(SimpleNamespace(apply=True))
        self.assertEqual(self.service_calls, [])
        self.assertEqual(self.config.read_bytes(), self.before_config)

    def test_service_home_or_clean_is_blocked_even_with_native_targets_off_and_unarmed(self):
        for native, oled in ((True, False), (False, True)):
            with self.subTest(native_z=native, oled_service=oled):
                self.native_z['running'] = native
                self.oled['ui_transport']['active_work'] = oled
                with self.assertRaisesRegex(ValueError, 'active Inventure service procedure'):
                    installer.idle_mcu()
        self.assertEqual(self.service_calls, [])

    def runtime_update_files(self):
        renderer = self.host/'klippy/extras/inventure_oled_render.py'
        renderer.parent.mkdir()
        marker = self.host/installer.bootstrap.MANIFEST_NAME
        before = {renderer: b'# original generic renderer\n', marker: b'{"original": true}\n'}
        after = {renderer: b'# authenticated dual thermal renderer\n', marker: b'{"restored": true}\n'}
        for path, data in before.items():
            path.write_bytes(data)
            path.chmod(0o644)
        return before, after, {path: (before[path], after[path]) for path in before}

    def test_runtime_files_are_verified_after_stop_and_before_new_process_starts(self):
        before, after, changes = self.runtime_update_files()
        verified = []
        def verify_runtime():
            self.assertIn(('stop', 'klipper'), self.service_calls)
            self.assertNotIn(('start', 'klipper'), self.service_calls)
            self.assertEqual({path: path.read_bytes() for path in after}, after)
            verified.append(True)
        transaction = self.transaction(runtime_updates=changes, before_start=verify_runtime)
        self.activate(transaction)
        self.assertEqual(verified, [True])
        self.assertEqual({path: transaction.backups[path].read_bytes() for path in before}, before)
        self.assertEqual({path: path.read_bytes() for path in after}, after)

    def test_failed_runtime_verification_restores_renderer_manifest_and_configuration(self):
        before, _, changes = self.runtime_update_files()
        def reject_runtime():
            raise ValueError('Injected complete runtime verification failure')
        transaction = self.transaction(runtime_updates=changes, before_start=reject_runtime)
        with self.assertRaisesRegex(RuntimeError, 'complete runtime verification failure'):
            self.activate(transaction)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assert_restored()
        self.assertEqual(self.service_calls.count(('start', 'klipper')), 1)  # Restored host only.

    def test_unclassified_mcu_safety_fault_rolls_back_the_renderer_as_well(self):
        before, _, changes = self.runtime_update_files()
        transaction = self.transaction(runtime_updates=changes)
        self.state, self.message = 'shutdown', "MCU 'mcu' shutdown: Inventure V9 local safety fault"
        with self.assertRaisesRegex(RuntimeError, 'local safety fault'):
            self.activate(transaction)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assert_restored()

    def test_first_ready_failure_restores_real_config_and_removes_new_service_override(self):
        transaction = self.transaction()
        self.state, self.message = 'error', 'Invalid new configuration'
        with self.assertRaisesRegex(RuntimeError, 'previous host/configuration restored'):
            self.activate(transaction)
        self.assert_restored()
        self.assertFalse(transaction.settings_changed)

    def test_ready_process_with_old_oled_ui_is_refused_and_rolled_back(self):
        transaction=self.transaction()
        self.oled.pop('procedure_ui_release')
        with self.assertRaisesRegex(RuntimeError,'did not load the matching OLED procedure UI'):
            self.activate(transaction)
        self.assert_restored()
        self.assertFalse(transaction.settings_changed)

    def test_ready_ui_with_wrong_launcher_is_refused_and_rolled_back(self):
        transaction=self.transaction()
        self.oled['operator_menu_root']='root'
        with self.assertRaisesRegex(RuntimeError,'requested Klipper / Zortrax Inventure menu'):
            self.activate(transaction)
        self.assert_restored()

    def test_ready_with_a_home_module_from_candidate_runtime_is_rolled_back(self):
        self.native_z['host_module']='/home/klipper/klipper-inventure-candidate/klippy/extras/inventure_native_z.py'
        with self.assertRaisesRegex(RuntimeError,'different Inventure MCU or Home module'):
            self.activate(self.transaction())
        self.assert_restored()

    def test_missing_driver_recovery_validates_ready_mcu_inside_transaction(self):
        result,_=self.activate(self.transaction(),recover_startup=True)
        self.assertIn('recovered_mcu',result)
        self.assertEqual(result['recovered_mcu']['mcu_release'],installer.MCU_RELEASE)

    def test_startup_recovery_rejects_a_stale_mcu_status_and_rolls_back(self):
        self.board['status_age']=2.0
        with self.assertRaisesRegex(RuntimeError,'fresh Inventure MCU status'):
            self.activate(self.transaction(),recover_startup=True)
        self.assert_restored()

    def test_dashboard_http_failure_restores_settings_and_existing_service_override(self):
        previous_unit = installer.UNIT_MARKER.encode()+b'[Service]\n# previous managed override\n'
        self.dropin.write_bytes(previous_unit)
        transaction = self.transaction()
        self.fail_locale_once = True
        with self.assertRaisesRegex(RuntimeError, 'locale write failure'):
            self.activate(transaction)
        self.assert_restored(previous_unit)

    def test_web_deployment_failure_restores_config_and_dashboard_changes(self):
        transaction = self.transaction()
        self.fail_web = True
        with self.assertRaisesRegex(RuntimeError, 'web deployment failure'):
            self.activate(transaction)
        self.assert_restored()

    def test_final_ready_failure_exchanges_the_actual_web_backup_back(self):
        transaction = self.transaction()
        self.fail_after_web = True
        with self.assertRaisesRegex(RuntimeError, 'final configuration failure'):
            self.activate(transaction)
        self.assertIsNotNone(transaction.web_previous)
        self.assert_restored()

    def test_active_uart_mismatch_is_inside_the_rollback_boundary(self):
        transaction = self.transaction()
        with self.assertRaisesRegex(RuntimeError, 'Active UART differs'):
            self.activate(transaction, serial='/dev/ttyS2', baud=750000)
        self.assert_restored()

    def test_known_mcu_shutdown_latch_keeps_the_valid_installation_without_reset(self):
        installer.require_mcu(self.mcu)
        transaction = self.transaction()
        self.state, self.message = 'shutdown', installer.SHUTDOWN_LATCH+'\nReview the original cause.'
        final, groups = self.activate(transaction, serial=self.serial, baud=self.baud)
        self.assertTrue(final['firmware_restart_required'])
        self.assertTrue(groups['firmware_restart_required'])
        self.assertEqual(groups['macro_source'], 'installed_klipper_config')
        self.assertEqual(self.dropin.read_bytes(), self.unit)
        self.assertTrue(self.include.exists())
        self.assertEqual(self.service_calls.count(('start', 'klipper')), 1)
        self.assertFalse((self.report/'rollback.json').exists())
        self.assertFalse(any('gcode' in endpoint or 'restart' in endpoint for endpoint, _ in self.http_calls))

    def test_config_change_after_backup_is_preserved_without_service_or_database_mutation(self):
        transaction = self.transaction()
        self.config.write_bytes(b'new operator configuration')
        self.db['uiSettings.general.locale'] = 'de'  # A concurrent UI preference is not ours to undo.
        with self.assertRaisesRegex(RuntimeError, 'File changed immediately before activation'):
            self.activate(transaction)
        self.assertEqual(self.config.read_bytes(), b'new operator configuration')
        self.assertEqual(self.db['uiSettings.general.locale'], 'de')
        self.assertFalse(self.include.exists())
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.service_calls, [])
        self.assertFalse(any(data for _, data in self.http_calls))

    def test_database_change_during_preparation_is_not_rolled_back_over_operator_edit(self):
        transaction = self.transaction()
        self.db['uiSettings.general.locale'] = 'de'
        with self.assertRaisesRegex(RuntimeError, 'Fluidd settings changed during preparation'):
            self.activate(transaction)
        self.assertEqual(self.db['uiSettings.general.locale'], 'de')
        self.assertEqual(self.config.read_bytes(), self.before_config)
        self.assertEqual(self.service_calls, [])
        self.assertFalse(any(data for _, data in self.http_calls))

    def test_create_collision_during_atomic_link_preserves_the_other_writers_file(self):
        transaction = self.transaction()
        original_write = installer.updater.atomic_config_write
        concurrent_data = b'# Created concurrently by the operator, not this installation.\n'

        def competing_write(path, data, *, create=False):
            if path == self.include and create:
                path.write_bytes(concurrent_data)
            return original_write(path, data, create=create)

        with patch.object(installer.updater, 'atomic_config_write', side_effect=competing_write), \
             self.assertRaisesRegex(RuntimeError, 'File exists'):
            self.activate(transaction)
        self.assertEqual(self.include.read_bytes(), concurrent_data)
        self.assertNotIn(self.include, transaction.written)
        self.assertEqual(self.config.read_bytes(), self.before_config)
        self.assertEqual(self.moon.read_bytes(), self.before_moon)
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.db, self.before_db)
        self.assertEqual(self.start_snapshots[-1], (None, self.before_config))
        self.assertEqual(json.loads((self.report/'rollback.json').read_text())['status'], 'ROLLED_BACK')

    def test_stop_timeout_still_attempts_to_restart_the_unchanged_previous_host(self):
        transaction = self.transaction()
        self.fail_stop_once = True
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.activate(transaction)
        self.assert_restored()
        self.assertEqual(self.service_calls.count(('stop', 'klipper')), 2)
        self.assertEqual(self.service_calls.count(('start', 'klipper')), 1)

    def test_rollback_stop_timeout_continues_only_after_systemd_confirms_no_process(self):
        transaction = self.transaction()
        self.activate(transaction)
        self.fail_stop_once = True
        with patch.object(installer.subprocess, 'run', return_value=SimpleNamespace(
                stdout='MainPID=0\nActiveState=failed\n')) as systemctl:
            self.assertEqual(transaction.rollback(), [])
        self.assertEqual(systemctl.call_args.args[0], [
            'systemctl', 'show', 'klipper', '--property=MainPID', '--property=ActiveState'])
        self.assert_restored()

    def test_rollback_stop_timeout_with_live_process_leaves_all_installed_files_intact(self):
        transaction = self.transaction()
        self.activate(transaction)
        installed = {path: path.read_bytes() for path in (
            self.config, self.moon, self.include, self.dropin)}
        installed_web, installed_db = self.web_files(), deepcopy(self.db)
        calls_before = len(self.service_calls)
        self.fail_stop_once = True
        with patch.object(installer.subprocess, 'run', return_value=SimpleNamespace(
                stdout='MainPID=81\nActiveState=active\n')):
            errors = transaction.rollback()
        self.assertTrue(any('Could not confirm that Klipper stopped' in error for error in errors))
        self.assertEqual({path: path.read_bytes() for path in installed}, installed)
        self.assertEqual(self.web_files(), installed_web)
        self.assertEqual(self.db, installed_db)
        self.assertEqual(self.service_calls[calls_before:], [('stop', 'klipper')])
        report = json.loads((self.report/'rollback.json').read_text())
        self.assertEqual(report['status'], 'ROLLBACK_NEEDS_ATTENTION')
        self.assertFalse(report['service_restart_attempted'])

    def test_partial_unit_install_failure_restores_previous_unit_and_configuration(self):
        previous_unit = installer.UNIT_MARKER.encode()+b'[Service]\n# saved override\n'
        self.dropin.write_bytes(previous_unit)
        transaction = self.transaction()
        self.unit_write_failure_once = True
        with self.assertRaisesRegex(RuntimeError, 'privileged installation failure'):
            self.activate(transaction)
        self.assert_restored(previous_unit)

    def test_filesystem_restore_failure_does_not_start_a_mixed_configuration(self):
        transaction = self.transaction()
        self.state, self.message = 'error', 'Bad new configuration'
        self.break_restore = True
        with self.assertRaisesRegex(RuntimeError, 'rollback needs attention'):
            self.activate(transaction)
        report = json.loads((self.report/'rollback.json').read_text())
        self.assertEqual(report['status'], 'ROLLBACK_NEEDS_ATTENTION')
        self.assertTrue(any(str(self.config) in error for error in report['errors']))
        self.assertFalse(report['service_restart_attempted'])
        self.assertEqual(self.service_calls.count(('start', 'klipper')), 1)  # Initial new-host start only.
        self.assertEqual(transaction.backups[self.config].read_bytes(), self.before_config)
        self.assertFalse(self.include.exists())
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.moon.read_bytes(), self.before_moon)


if __name__ == '__main__':
    unittest.main()
