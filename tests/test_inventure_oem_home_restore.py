"""Recover the whole pre-R19 Home bundle without rewriting its procedures."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

import inventure_bootstrap as bootstrap
import inventure_oem_home_restore as restoration

ROOT = Path(__file__).resolve().parents[1]


class OemHomeRestoreTests(unittest.TestCase):
    def test_selected_profile_loads_the_complete_field_configuration_without_uart(self):
        with tempfile.TemporaryDirectory(prefix='inventure-oem-home-startup-') as temporary:
            destination=Path(temporary)/'verified'
            result=subprocess.run([sys.executable,str(ROOT/'ci/check_inventure_oem_home.py'),
                                   '--destination',str(destination)],capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads((destination/'OEM_HOME_RECOVERY.json').read_text())
            self.assertEqual(report['status'],'PASS_OEM_HOME_RECOVERY')
            self.assertTrue(report['configuration_values_unchanged'])
            self.assertTrue(report['offline_startup'])
            self.assertFalse(report['serial_opened'])
            self.assertFalse(report['firmware_flashed'])
            self.assertFalse(report['hardware_tested'])

    def test_profile_changes_exactly_the_three_reference_modules(self):
        _, current, _ = bootstrap._plan(ROOT)
        with bootstrap.procedure_profile(restoration.PROFILE):
            _, restored, metadata = bootstrap._plan(ROOT)
        expected = {'klippy/extras/' + name for name in restoration.REFERENCE}
        self.assertEqual({name for name in current if current[name] != restored[name]}, expected)
        for name, digest in restoration.REFERENCE.items():
            baseline = (ROOT/'orange_pi'/name).read_bytes()
            actual = restored['klippy/extras/' + name][0]
            self.assertEqual(restoration.blob(baseline), digest)
            if name == 'inventure_native_z.py':
                self.assertEqual(restoration.execution_ast(actual), restoration.execution_ast(baseline))
                tree = ast.parse(actual)
                cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'InventureNativeZ')
                status = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'get_status')
                calls = [n for n in ast.walk(status) if isinstance(n, ast.Call)]
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].func.id, 'dict')
            else:
                self.assertEqual(actual, baseline)
        proof = metadata['runtime_restorations']['oem_home_r38_6']
        self.assertTrue(proof['executable_home_ast_identical'])
        self.assertFalse(proof['new_hardware_tested'])
        self.assertFalse(proof['mcu_update_required'])
        self.assertEqual(bootstrap.current_procedure_profile(), 'current')

    def test_profile_selection_does_not_leak_after_an_exception(self):
        with self.assertRaisesRegex(RuntimeError, 'test interruption'):
            with bootstrap.procedure_profile(restoration.PROFILE):
                self.assertEqual(bootstrap.current_procedure_profile(), restoration.PROFILE)
                raise RuntimeError('test interruption')
        self.assertEqual(bootstrap.current_procedure_profile(), 'current')

    def test_upgrade_and_exact_rollback_preserve_all_other_runtime_files(self):
        with tempfile.TemporaryDirectory(prefix='inventure-oem-home-') as temporary:
            host = Path(temporary)/'host'
            bootstrap.prepare_host(host)
            original = {p.relative_to(host): p.read_bytes() for p in host.rglob('*') if p.is_file()}
            with bootstrap.procedure_profile(restoration.PROFILE):
                plan = bootstrap.prepare_runtime_update(host)
                self.assertEqual({p.relative_to(host).as_posix() for p in plan['files']},
                    {'klippy/extras/' + n for n in restoration.REFERENCE} | {bootstrap.MANIFEST_NAME})
                for path, (_, after) in plan['files'].items():
                    path.write_bytes(after)
                self.assertEqual(bootstrap.verify_host(host)['status'], 'VERIFIED_HOST')
            self.assertEqual(bootstrap.verify_host(host, allow_legacy=True)['status'], 'VERIFIED_LEGACY_HOST')
            for path, (before, _) in plan['files'].items():
                path.write_bytes(before)
            self.assertEqual({p.relative_to(host): p.read_bytes() for p in host.rglob('*') if p.is_file()}, original)
            self.assertEqual(bootstrap.verify_host(host)['status'], 'VERIFIED_HOST')

    def test_retained_original_backup_is_required_not_the_r21_backup(self):
        with tempfile.TemporaryDirectory(prefix='inventure-oem-home-backup-') as temporary:
            home = Path(temporary)
            host = home/'klipper-inventure-r38.6'
            folder = home/'inventure-installations/20261124T142803.217006Z'
            (folder/'before').mkdir(parents=True)
            saved = folder/'before/0'
            (folder/'backup.json').write_text(json.dumps({'files': {str(host/restoration.TARGET): str(saved)}}))
            saved.write_bytes((ROOT/'restored/scheduler_r21/inventure_native_z.py').read_bytes())
            with self.assertRaisesRegex(ValueError, 'No retained backup'):
                restoration.require_reference_backup(ROOT, home, host)
            saved.write_bytes((ROOT/'orange_pi/inventure_native_z.py').read_bytes())
            record = restoration.require_reference_backup(ROOT, home, host)
            self.assertEqual(record['git_blob'], restoration.BASELINE_BLOB)
            self.assertEqual(record['saved_file'], str(saved))

    def test_restored_generator_releases_each_xy_axis_and_uses_measured_offsets(self):
        from test_orange_pi_native_z import Command
        from orange_pi.inventure_factory_core import parameters
        from orange_pi.inventure_z_home import z_home_parameters
        path = ROOT/'orange_pi/inventure_z_home.py'
        spec = importlib.util.spec_from_file_location('orange_pi.oem_home_reference_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        command = Command(TOOL='T0', TARGET=200., SPEED_PROFILE='OEM')
        params = parameters(command, operation='CLEAN')
        settings = z_home_parameters(command, params, required=False)
        generator = module.ZHomeProgram(params, settings).prepare_native_home([82.8, 68.], 80.)
        actions, reply = [], None
        for _ in range(200):
            try:
                action = generator.send(reply)
            except StopIteration:
                break
            actions.append(action)
            reply = dict(ok=True, z_bottom=1, nozzle_contact=1,
                         a=1,b=0,pulses=0,pulse_age_ms=0,starts=0,fault=0)
            if action['kind'] == 'native_home_frame':
                reply = dict(native_xy_offsets_mm=[.25, .375])
        else:
            self.fail('Original Home preparation did not finish')
        xy = [a for a in actions if a['kind'] in ('home', 'home_release')]
        self.assertEqual([(a['kind'], a['axis']) for a in xy],
                         [('home','Y'),('home_release','Y'),('home','X'),('home_release','X')])
        self.assertEqual([(a['speed'], a['max_mm']) for a in xy if a['kind']=='home_release'],
                         [(1., 7.8125), (1., 7.8125)])
        pad, = [a for a in actions if a.get('leg') == 'z_contact_pad']
        self.assertEqual([pad['x'],pad['y']], [82.55,67.625])
        self.assertEqual(actions[-1]['native_xy_mm'], [82.8,68.])
        self.assertEqual(actions[-1]['native_xy_offsets_mm'], [.25,.375])

    def test_original_interpreter_confirms_actual_release_steps_before_pad_handoff(self):
        from test_orange_pi_z_home_host import ZHomeHostTests
        from test_orange_pi_load_test import Wire
        from test_orange_pi_native_z import Command
        fixture = ZHomeHostTests()
        board = fixture.host()
        board.commands['factory_native_handoff'] = Wire(board.device.native_handoff)
        command = Command(TOOL='T0',TARGET=200.,SPEED_PROFILE='OEM')
        context = board._factory_start(command, operation='CLEAN', native_homing=dict(
            pad_xy=[82.8,68.],endstops=[0.,0.],pad_speed=80.))
        last = fixture.finish(board)
        self.assertEqual(last['phase'], 'done')
        factory = context['factory']
        self.assertEqual(set(factory['native_homing']['release_mm']), {'X','Y'})
        self.assertTrue(all(v>0 for v in factory['native_homing']['release_mm'].values()))
        result = factory['result']
        self.assertTrue(result['success'])
        self.assertEqual(result['terminal'], 'HOME_PREPARE')
        self.assertEqual(result['native_xy_mm'], [82.8,68.])
        self.assertEqual(result['native_xy_offsets_mm'],
            [factory['native_homing']['release_mm'][axis] for axis in 'XY'])
        self.assertEqual(result['service_xy_mm'],
            [82.8-result['native_xy_offsets_mm'][0],68.-result['native_xy_offsets_mm'][1]])
        self.assertEqual(len(board.commands['factory_native_handoff'].calls),1)


if __name__ == '__main__':
    unittest.main()
