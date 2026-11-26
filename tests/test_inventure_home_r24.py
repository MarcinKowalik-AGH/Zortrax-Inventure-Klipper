"""The installed R24 route must retain accepted engines and conditional XY."""
import ast
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import inventure_bootstrap as bootstrap

ROOT=Path(__file__).resolve().parents[1]

class OperatorHomeR24Tests(unittest.TestCase):
    def test_installed_fluidd_and_oled_independent_axes_then_z(self):
        with tempfile.TemporaryDirectory(prefix='r24-operator-home-test-') as temp:
            output=Path(temp)/'verified'
            result=subprocess.run([sys.executable,str(ROOT/'ci/check_inventure_home_r24.py'),
                                   '--destination',str(output)],capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads((output/'R24_HOME_CYCLES.json').read_text())
            self.assertEqual(report['status'],'PASS_INSTALLED_R24_HOME_CYCLES')
            self.assertEqual(report['case_count'],126)
            self.assertEqual(report['refusal_count'],42)
            for row in report['cases']:
                self.assertEqual(row['xy_seek'],row['xy_release'])
                self.assertEqual(row['xy_seek'],row['xy_backoff'])
                self.assertEqual(row['xy_backoff_mm'],5.)
                self.assertTrue(row['enable_held'])
                self.assertTrue(row['targets_unchanged'])
            after_xy=[row for row in report['cases'] if row['sequence']==['X','Y','Z'] and row['request']=='Z']
            self.assertEqual(len(after_xy),6)
            self.assertTrue(all(row['xy_seek']==[] for row in after_xy))
            missing=[row for row in report['cases'] if row['sequence']==['Z']]
            self.assertTrue(all(row['xy_seek']==['Y','X'] for row in missing))
            self.assertTrue(report['configuration_unchanged'])
            self.assertFalse(report['hardware_tested'])

    def test_rc1_upgrade_changes_only_operator_helper_and_manifest_and_rolls_back_exactly(self):
        with tempfile.TemporaryDirectory(prefix='inventure-r24-rc2-upgrade-') as temporary:
            home=Path(temporary)
            host=home/'host'
            _,old,metadata=bootstrap._plan(ROOT,procedure_profile='operator-home-r24',operator_home_revision='rc1')
            # Generated independently from the immutable published RC1 commit
            # 2c8d2807b5c408da0c9a0aa537615825616a95a4.
            self.assertEqual(sha256(bootstrap._metadata_bytes(metadata)).hexdigest(),
                             '9a62f3171da978d6cf7842eeecd5a07a2a348f69da7c5b5f89ada7937598ab92')
            _,new,_=bootstrap._plan(ROOT,procedure_profile='operator-home-r24')
            self.assertEqual({name for name in old if old[name]!=new[name]},
                             {'klippy/extras/inventure_operator_home.py'})
            # In particular, every byte of native Z, its generator and the
            # T0/factory driver is identical to the preceding release.
            for name in ('inventure_native_z.py','inventure_z_home.py','inventure_mcu.py'):
                self.assertEqual(old['klippy/extras/'+name],new['klippy/extras/'+name])
            def unchanged_methods(code):
                cls=next(n for n in ast.parse(code).body if isinstance(n,ast.ClassDef))
                return {n.name:ast.dump(n,include_attributes=False) for n in cls.body
                        if isinstance(n,ast.FunctionDef) and n.name!='_home_limit'}
            self.assertEqual(unchanged_methods(old['klippy/extras/inventure_operator_home.py'][0]),
                             unchanged_methods(new['klippy/extras/inventure_operator_home.py'][0]))
            bootstrap._write_files(host,old)
            marker=host/bootstrap.MANIFEST_NAME
            marker.write_bytes(bootstrap._metadata_bytes(metadata));marker.chmod(0o644)
            original={p.relative_to(host):p.read_bytes() for p in host.rglob('*') if p.is_file()}
            spec=importlib.util.spec_from_file_location('r24_upgrade_installer',ROOT/'install-new-host.py')
            installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
            with bootstrap.procedure_profile('operator-home-r24'):
                staged,provenance,updates,before=installer.stage_runtime(host,home)
                self.assertEqual(before['status'],'VERIFIED_LEGACY_HOST')
                self.assertEqual(provenance['status'],'VERIFIED_HOST')
                self.assertNotEqual(staged,host)
                self.assertEqual({p.relative_to(host).as_posix() for p in updates},
                                 {'klippy/extras/inventure_operator_home.py',bootstrap.MANIFEST_NAME})
                self.assertEqual({p.relative_to(host):p.read_bytes() for p in host.rglob('*') if p.is_file()},original)
                for path,(_,after) in updates.items():path.write_bytes(after)
                self.assertEqual(bootstrap.verify_host(host)['status'],'VERIFIED_HOST')
                for path,(before,_) in updates.items():path.write_bytes(before)
                self.assertEqual(bootstrap.verify_host(host,allow_legacy=True)['status'],'VERIFIED_LEGACY_HOST')
            self.assertEqual({p.relative_to(host):p.read_bytes() for p in host.rglob('*') if p.is_file()},original)

    def test_oem_profile_can_stage_the_new_helper_and_restore_absence_on_rollback(self):
        with tempfile.TemporaryDirectory(prefix='inventure-r24-first-upgrade-') as temporary:
            host=Path(temporary)/'host'
            with bootstrap.procedure_profile('oem-home-r38.6'):
                bootstrap.prepare_host(host)
            original={p.relative_to(host):p.read_bytes() for p in host.rglob('*') if p.is_file()}
            helper=host/'klippy/extras/inventure_operator_home.py'
            self.assertFalse(helper.exists())
            with bootstrap.procedure_profile('operator-home-r24'):
                update=bootstrap.prepare_runtime_update(host)
                self.assertIsNone(update['files'][helper][0])
                for path,(_,after) in update['files'].items():path.write_bytes(after)
                self.assertEqual(bootstrap.verify_host(host)['status'],'VERIFIED_HOST')
                for path,(before,_) in update['files'].items():
                    if before is None:path.unlink()
                    else:path.write_bytes(before)
            self.assertEqual({p.relative_to(host):p.read_bytes() for p in host.rglob('*') if p.is_file()},original)
            with bootstrap.procedure_profile('oem-home-r38.6'):
                self.assertEqual(bootstrap.verify_host(host)['status'],'VERIFIED_HOST')

    def test_held_handoff_is_checked_in_actual_mcu_c(self):
        result=subprocess.run([sys.executable,str(ROOT/'ci/check_inventure_home_r24_mcu.py')],
                              capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(report['status'],'PASS_R24_HELD_SERVICE_REGISTER_TESTS')
        self.assertFalse(report['hardware_tested'])

    def test_installer_accepts_r24_only_with_its_capability_and_retains_old_validation(self):
        spec=importlib.util.spec_from_file_location('r24_mcu_identity_installer',ROOT/'install-new-host.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from test_install_new_host import mcu_identity
        # The existing R23 validator is exercised using the same fixture,
        # then R24 adds its independent held-entry capability requirement.
        fixture=mcu_identity()['mcu_constants']
        fixture.update(INVENTURE_NATIVE_SERVICE_HANDOFF_ABI=1,
            INVENTURE_NATIVE_THERMAL_PROFILE_ABI=1,INVENTURE_FAULT_REASON_ABI=1,
            INVENTURE_SCHEDULER_ABI=1,INVENTURE_SCHEDULER_NATIVE_HZ=10000,
            INVENTURE_SCHEDULER_SERVICE_HZ=20000,INVENTURE_SCHEDULER_GUARD_MS=10,
            INVENTURE_SERVICE_NATIVE_SPLIT_ABI=1,INVENTURE_POLICY_STATUS_ABI=1,
            INVENTURE_DISPLAY_ERROR_ABI=1,INVENTURE_LOCAL_OLED_ANIMATION_ABI=1,
            INVENTURE_LOCAL_OLED_NATIVE_GRAY4=1,INVENTURE_LOCAL_SPLASH_ABI=1,
            INVENTURE_OLED_HOSTLINK_TIMEOUT_MS=1500)
        data=dict(mcu_version='r24',mcu_constants=dict(fixture,
            INVENTURE_MCU_RELEASE='RC8-CONTROL-R7-HOME-R24-OLED',INVENTURE_NATIVE_HELD_SERVICE_ABI=1))
        self.assertEqual(module.require_mcu(data)['native_held_service_abi'],1)
        data['mcu_constants'].pop('INVENTURE_NATIVE_HELD_SERVICE_ABI')
        with self.assertRaisesRegex(ValueError,'held-service'):module.require_mcu(data)

if __name__=='__main__':unittest.main()
