"""Freeze the backed-up Home implementation while retaining current menu/status."""
import ast
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import inventure_accepted_home_restore as restoration
import inventure_bootstrap as bootstrap
from inventure_bootstrap_config import stage_config, _set_scalar

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('accepted_home_installer', ROOT/'install-new-host.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class AcceptedHomeRestoreTests(unittest.TestCase):
    def test_every_executable_procedure_node_matches_the_saved_reference(self):
        baseline = (ROOT/'restored/scheduler_r21/inventure_native_z.py').read_bytes()
        candidate = (ROOT/restoration.SOURCE).read_bytes()
        self.assertEqual(restoration.blob(baseline), restoration.BASELINE_BLOB)
        self.assertEqual(restoration.execution_ast(candidate), restoration.execution_ast(baseline))
        current = (ROOT/'restored/configuration_audit_r23/inventure_native_z.py').read_bytes()
        updates, proof = restoration.restored_accepted_home(ROOT, {restoration.TARGET: (current, 0o644)})
        self.assertEqual(updates[restoration.TARGET], (candidate, 0o644))
        self.assertTrue(proof['only_status_differs_from_baseline'])
        self.assertNotEqual(restoration.execution_ast(current), restoration.execution_ast(baseline))
        mutated = candidate.replace(b'self._home_limit(\'Y\', factor)', b'self._home_limit(\'X\', factor)', 1)
        self.assertNotEqual(mutated, candidate)
        self.assertNotEqual(restoration.execution_ast(mutated), restoration.execution_ast(baseline))
        changed_status = candidate.replace(b'    def get_status(self, eventtime):\n',
            b'    def get_status(self, eventtime):\n        self.cmd_home(eventtime)\n', 1)
        with self.assertRaisesRegex(ValueError, 'read-only dictionary'):
            restoration.execution_ast(changed_status)

    def test_rc7_to_restored_home_changes_only_native_module_and_manifest_and_rolls_back(self):
        with tempfile.TemporaryDirectory(prefix='inventure-accepted-home-') as temporary:
            host = Path(temporary)/'host'
            _, files, metadata = bootstrap._plan(ROOT, restore_accepted_home=False)
            bootstrap._write_files(host, files)
            marker = host/bootstrap.MANIFEST_NAME
            marker.write_bytes(bootstrap._metadata_bytes(metadata))
            marker.chmod(0o644)
            before = {str(p.relative_to(host)): p.read_bytes() for p in host.rglob('*') if p.is_file()}
            self.assertTrue(bootstrap.verify_host(host, allow_legacy=True)['runtime_restoration_required'])
            update = bootstrap.prepare_runtime_update(host)
            self.assertEqual({p.relative_to(host).as_posix() for p in update['files']},
                             {restoration.TARGET, bootstrap.MANIFEST_NAME})
            for path, (_, after) in update['files'].items():
                path.write_bytes(after)
            self.assertEqual(bootstrap.verify_host(host)['status'], 'VERIFIED_HOST')
            proof = update['accepted_home_restoration']
            self.assertEqual(proof['baseline_blob'], restoration.BASELINE_BLOB)
            for path, (original, _) in update['files'].items():
                path.write_bytes(original)
            self.assertEqual({str(p.relative_to(host)): p.read_bytes() for p in host.rglob('*') if p.is_file()}, before)
            self.assertEqual(bootstrap.verify_host(host, allow_legacy=True)['status'], 'VERIFIED_LEGACY_HOST')

    def test_retained_backup_must_match_the_current_runtime_and_exact_reference_bytes(self):
        with tempfile.TemporaryDirectory(prefix='inventure-home-backup-') as temporary:
            home = Path(temporary)
            host = home/'klipper-inventure-r38.6'
            folder = home/'inventure-installations/20261125T135716.311355Z'
            (folder/'before').mkdir(parents=True)
            saved = folder/'before/2'
            saved.write_bytes((ROOT/'restored/scheduler_r21/inventure_native_z.py').read_bytes())
            (folder/'backup.json').write_text(json.dumps({'files': {str(host/restoration.TARGET): str(saved)}}))
            result = restoration.require_reference_backup(ROOT, home, host)
            self.assertEqual(result['saved_file'], str(saved))
            self.assertEqual(result['git_blob'], restoration.BASELINE_BLOB)
            saved.write_bytes(b'unverified replacement')
            with self.assertRaisesRegex(ValueError, 'No retained installer backup'):
                restoration.require_reference_backup(ROOT, home, host)

    def test_restoration_configuration_guard_accepts_role_moves_and_rejects_motion_changes(self):
        with tempfile.TemporaryDirectory(prefix='inventure-home-settings-') as temporary:
            root = Path(temporary)
            host = bootstrap.prepare_host(root/'host')
            source = root/'original'
            shutil.copytree(ROOT/'tests/fixtures/field_config_20261125', source)
            gcodes = root/'gcodes'; gcodes.mkdir()
            module = installer.updater.klipper_config_module(host)
            original = source/'printer.cfg'
            _set_scalar(module, original, 'virtual_sdcard', 'path', str(gcodes))
            plan = stage_config(host, root/'staged', original, serial='/dev/ttyS5', baud=750000,
                                gcodes_path=gcodes, restore_field_profile=True)
            staged = Path(plan['config_path'])
            installer.require_identical_configuration(host, original, staged)
            _set_scalar(module, staged, 'stepper_y', 'homing_speed', '4')
            with self.assertRaisesRegex(ValueError, 'any effective printer configuration change'):
                installer.require_identical_configuration(host, original, staged)


if __name__ == '__main__':
    unittest.main()
