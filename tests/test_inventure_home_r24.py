"""The installed R24 route must retain accepted engines and conditional XY."""
import ast
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import inventure_bootstrap as bootstrap

ROOT=Path(__file__).resolve().parents[1]
UI_FILES={'klippy/extras/'+name for name in
          ('inventure_menu_service.py','inventure_oled_graphics.py','inventure_oem_menu.py',
           'inventure_menu_core.py','inventure_oled.py','inventure_home_ui.py','inventure_home_sprites.json')}

class OperatorHomeR24Tests(unittest.TestCase):
    def test_installed_fluidd_and_oled_independent_axes_then_z(self):
        with tempfile.TemporaryDirectory(prefix='r24-operator-home-test-') as temp:
            output=Path(temp)/'verified'
            result=subprocess.run([sys.executable,str(ROOT/'ci/check_inventure_home_r24.py'),
                                   '--destination',str(output)],capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads((output/'R24_HOME_CYCLES.json').read_text())
            self.assertEqual(report['status'],'PASS_INSTALLED_R24_HOME_CYCLES')
            self.assertEqual(report['case_count'],800)
            self.assertEqual(report['refusal_count'],184)
            for row in report['cases']:
                self.assertEqual(row['xy_seek'],row['xy_release'])
                self.assertEqual(row['xy_seek'],row['xy_backoff'])
                self.assertEqual(row['xy_backoff_mm'],5.)
                self.assertTrue(row['enable_held'])
                self.assertTrue(row['targets_unchanged'])
                self.assertTrue(row['release_origin_dynamic'])
                self.assertTrue(row['physical_z_pad_unchanged'])
                self.assertTrue(row['actual_corexy_limits_checked'])
                self.assertFalse(row['initial_review'])
                self.assertTrue(row['oled_progress_rendered'])
                self.assertTrue(row['m300_axis_animation'])
                self.assertTrue(row['oled_delivered_during_thermal_hold'])
                self.assertEqual(row['home_ui_release'],'RC8-HOME-MENU-R24.4')
            self.assertEqual((output/'R24_HOME_OLED.png').read_bytes()[:8],b'\x89PNG\r\n\x1a\n')
            after_xy=[row for row in report['cases'] if row['sequence']==['X','Y','Z'] and row['request']=='Z']
            self.assertEqual(len(after_xy),20)
            self.assertTrue(all(row['xy_seek']==[] for row in after_xy))
            combined=[row for row in report['cases'] if row['sequence']==['X','Y','ALL'] and row['request']=='ALL']
            self.assertEqual(len(combined),20)
            self.assertTrue(all(row['xy_seek']==[] for row in combined))
            for axis,missing in (('X','Y'),('Y','X')):
                partial=[row for row in report['cases'] if row['sequence']==[axis,'ALL'] and row['request']=='ALL']
                self.assertEqual(len(partial),20)
                self.assertTrue(all(row['xy_seek']==[missing] for row in partial))
            missing=[row for row in report['cases'] if row['sequence']==['Z']]
            self.assertTrue(all(row['xy_seek']==['Y','X'] for row in missing))
            self.assertTrue(report['configuration_unchanged'])
            self.assertFalse(report['hardware_tested'])
            self.assertEqual({row['machine'] for row in report['cases']},{'A','B'})
            for axis in 'XY':
                measured={row['measured_release_steps'][axis] for row in report['cases']
                          if axis in row['measured_release_steps']}
                self.assertGreaterEqual(len(measured),4)

    def check_prior_upgrade(self,revision,manifest_hash):
        with tempfile.TemporaryDirectory(prefix='inventure-r24-rc2-upgrade-') as temporary:
            home=Path(temporary)
            host=home/'host'
            _,old,metadata=bootstrap._plan(ROOT,procedure_profile='operator-home-r24',operator_home_revision=revision)
            # Generated independently from the immutable preceding releases.
            self.assertEqual(sha256(bootstrap._metadata_bytes(metadata)).hexdigest(),
                             manifest_hash)
            _,new,_=bootstrap._plan(ROOT,procedure_profile='operator-home-r24')
            changed=UI_FILES|({'klippy/extras/inventure_operator_home.py'} if revision!='rc5' else set())
            self.assertEqual({name for name in old.keys()|new.keys() if old.get(name)!=new.get(name)},changed)
            # In particular, every byte of native Z, its generator and the
            # T0/factory driver is identical to the preceding release.
            for name in ('inventure_native_z.py','inventure_z_home.py','inventure_mcu.py'):
                self.assertEqual(old['klippy/extras/'+name],new['klippy/extras/'+name])
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
                                 changed|{bootstrap.MANIFEST_NAME})
                self.assertEqual({p.relative_to(host):p.read_bytes() for p in host.rglob('*') if p.is_file()},original)
                for path,(before,after) in updates.items():
                    installer.updater.atomic_config_write(path,after,create=before is None)
                self.assertEqual(bootstrap.verify_host(host)['status'],'VERIFIED_HOST')
                for path,(before,_) in updates.items():
                    if before is None:path.unlink()
                    else:path.write_bytes(before)
                self.assertEqual(bootstrap.verify_host(host,allow_legacy=True)['status'],'VERIFIED_LEGACY_HOST')
            self.assertEqual({p.relative_to(host):p.read_bytes() for p in host.rglob('*') if p.is_file()},original)

    def test_rc1_upgrade_changes_only_operator_routing_and_ui_and_rolls_back_exactly(self):
        self.check_prior_upgrade('rc1','9a62f3171da978d6cf7842eeecd5a07a2a348f69da7c5b5f89ada7937598ab92')

    def test_rc3_upgrade_changes_only_operator_routing_and_ui_and_rolls_back_exactly(self):
        self.check_prior_upgrade('rc2','f78fcb822c91c1111f181655ad205722b42e373b8220739ae3e3b15b52ee9ca0')

    def test_physically_confirmed_rc4_upgrade_and_exact_rollback(self):
        self.check_prior_upgrade('rc4','dd427a00e334c679f7aee69075f960e1d91db6c5294529a63bcd38c6e1bdbd44')

    def test_rc5_upgrade_changes_only_ui_and_manifest_and_rolls_back_exactly(self):
        self.check_prior_upgrade('rc5','3e5eb8242f65d5a341397b04f486642ca8f342651a143d55943ce4ccbc142dc6')

    def test_rc4_homing_implementation_changes_only_the_requested_operator_routing(self):
        frozen=(ROOT/'restored/operator_home_r24/rc4/inventure_operator_home.py').read_bytes()
        self.assertEqual(sha256(frozen).hexdigest(),
                         'a8db616130e7bf4f90e791af275e073b5369714295136d6485e9044720f47132')
        current=(ROOT/'restored/operator_home_r24/inventure_operator_home.py').read_bytes()
        def methods(code):
            cls=next(n for n in ast.parse(code).body if isinstance(n,ast.ClassDef))
            return {(n.name,tuple(ast.dump(d,include_attributes=False) for d in n.decorator_list)):
                    ast.dump(n,include_attributes=False) for n in cls.body if isinstance(n,ast.FunctionDef)}
        old,new=methods(frozen),methods(current)
        self.assertEqual(set(old),set(new))
        self.assertEqual({name for name,decorators in old if old[name,decorators]!=new[name,decorators]},
                         {'_install_g28','_home_axes','_prepare_t0'})

    def test_oem_profile_can_stage_the_new_helper_and_restore_absence_on_rollback(self):
        from test_install_new_host import installer
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
                # Exercise the installer's real atomic creation, including
                # its explicit mode, under a restrictive host umask.
                previous_umask=os.umask(0o077)
                try:
                    for path,(before,after) in update['files'].items():
                        installer.updater.atomic_config_write(path,after,create=before is None)
                finally:
                    os.umask(previous_umask)
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
        from test_install_new_host import r24_mcu_identity
        # The existing R23 validator is exercised using the same fixture,
        # then R24 adds its independent held-entry capability requirement.
        data=r24_mcu_identity()
        self.assertEqual(module.require_mcu(data)['native_held_service_abi'],1)
        data['mcu_constants'].pop('INVENTURE_NATIVE_HELD_SERVICE_ABI')
        with self.assertRaisesRegex(ValueError,'held-service'):module.require_mcu(data)

if __name__=='__main__':unittest.main()
