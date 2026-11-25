"""Identify the real saved module without importing or changing printer code."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('home_reference_audit', ROOT/'tools/inventure_home_reference_audit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HomeReferenceAuditTests(unittest.TestCase):
    def test_reads_actual_prior_and_current_modules_and_preserves_every_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            runtime = home/'klipper-inventure-r38.6'
            current = (ROOT/'restored/configuration_audit_r23/inventure_native_z.py').read_bytes()
            prior = (ROOT/'restored/scheduler_r21/inventure_native_z.py').read_bytes()
            native = runtime/module.NATIVE
            native.parent.mkdir(parents=True)
            native.write_bytes(current)
            manifest = {'files': {module.NATIVE.as_posix(): {'sha256': module.fingerprint(current)['sha256']}}}
            (runtime/'.inventure-bootstrap.json').write_text(json.dumps(manifest))
            for name, data in (('before_r23_5', prior), ('after_r23_5', current)):
                folder = home/'inventure-installations'/name
                (folder/'before').mkdir(parents=True)
                saved = folder/'before/2'
                saved.write_bytes(data)
                (folder/'private-config').write_text('PRIVATE_CALIBRATION_CANARY')
                (folder/'backup.json').write_text(json.dumps({'files': {
                    str(native): str(saved), '/config/printer.cfg': str(folder/'private-config')}}))
            before = {str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()}
            report = module.audit(home)
            self.assertEqual(report['current']['git_blob'], module.R23_5_BLOB)
            self.assertTrue(report['current']['matches_runtime_manifest'])
            self.assertEqual({row['git_blob'] for row in report['backups']},
                             {module.PRE_AUDIT_BLOB, module.R23_5_BLOB})
            self.assertTrue(report['pre_R23_5_reference_backup_found'])
            self.assertFalse(report['physically_accepted_bytes_identified'])
            self.assertEqual(report['errors'], [])
            self.assertNotIn('PRIVATE_CALIBRATION_CANARY', json.dumps(report))
            self.assertEqual({str(p): p.read_bytes() for p in home.rglob('*') if p.is_file()}, before)

    def test_rejects_a_backup_redirected_outside_its_transaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            outside = home/'unrelated.py'
            outside.write_bytes((ROOT/'restored/scheduler_r21/inventure_native_z.py').read_bytes())
            folder = home/'inventure-installations/one'
            folder.mkdir(parents=True)
            record = folder/'backup.json'
            for saved in (outside, folder/'linked.py'):
                if saved != outside:
                    saved.symlink_to(outside)
                record.write_text(json.dumps({'files': {'/host/klippy/extras/inventure_native_z.py': str(saved)}}))
                report = module.audit(home)
                self.assertFalse(report['pre_R23_5_reference_backup_found'])
                self.assertEqual(report['backups'], [])
                self.assertTrue(any(row['scope'] == 'one' for row in report['errors']))

    def test_missing_runtime_and_backups_do_not_invent_an_accepted_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = module.audit(Path(temporary))
            self.assertIsNone(report['current'])
            self.assertEqual(report['backups'], [])
            self.assertFalse(report['pre_R23_5_reference_backup_found'])
            self.assertFalse(report['physically_accepted_bytes_identified'])


if __name__ == '__main__':
    unittest.main()
