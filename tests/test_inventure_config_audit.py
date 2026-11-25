"""The supplied field config, single shared Home route and failed startup repair."""
from contextlib import redirect_stdout
from hashlib import sha256
import importlib.util
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import inventure_bootstrap as bootstrap
from inventure_bootstrap_config import stage_config
import inventure_config_layout as layout
from inventure_config_cleanup import normalize_layout

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/field_config_20261125'
spec=importlib.util.spec_from_file_location('audit_installer',ROOT/'install-new-host.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class FieldConfigAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='inventure-field-config-audit-')
        cls.root=Path(cls.temp.name)
        cls.host=bootstrap.prepare_host(cls.root/'host')
        cls.module=installer.updater.klipper_config_module(cls.host)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_normalization_keeps_all_settings_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'config';shutil.copytree(FIXTURE,target)
            main=target/'printer.cfg';before,_=layout.active_klipper(self.module,main,{})
            changes={};proof=normalize_layout(self.module,main,changes)
            after,_=layout.active_klipper(self.module,main,changes)
            self.assertEqual(layout.config_values(before),layout.config_values(after))
            self.assertTrue(proof['effective_settings_preserved'])
            for path,(_,data) in changes.items():path.write_bytes(data)
            self.assertLess(len(main.read_text().splitlines()),35)
            changes={};normalize_layout(self.module,main,changes)
            self.assertEqual(changes,{})

    def test_migration_preserves_saved_tail_and_calibration_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'original';shutil.copytree(FIXTURE,source)
            tail=self.module.AUTOSAVE_HEADER+'#*# [extruder]\n#*# control = pid\n#*# pid_kp = 18.125\n#*# pid_ki = 0.25\n#*# pid_kd = 90.5\n'
            main=source/'printer.cfg';main.write_text(main.read_text()+tail)
            state=b'{"preserved_field_calibration":true}\n'
            (source/'inventure_platform_level.json').write_bytes(state)
            plan=stage_config(self.host,root/'staged',main,serial='/dev/ttyS5',baud=750000,gcodes_path=root/'gcodes',restore_field_profile=True)
            staged=Path(plan['config_path'])
            self.assertTrue(staged.read_bytes().endswith(tail.encode()))
            self.assertEqual((staged.parent/'inventure_platform_level.json').read_bytes(),state)
            self.assertTrue(plan['configuration_cleanup']['save_config_preserved'])


class StartupRepairContract(unittest.TestCase):
    def test_only_configuration_registration_errors_allow_recovery(self):
        for state,message,allowed in (
                ('error',"Unknown pin chip name 'inventure_r5'",True),
                ('error',"Unknown temperature sensor 'Inventure R5 ADC'",True),
                ('shutdown',"Unknown pin chip name 'inventure_r5'",False),
                ('error','MCU unable to connect',False),
                ('error',"Unknown pin chip name 'mcu'",False),
                ('ready','Printer is ready',False)):
            self.assertEqual(installer.recoverable_startup(dict(state=state,state_message=message)),allowed)

    def test_override_is_published_only_after_complete_temporary_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);dropin=root/'50-inventure-release.conf'
            calls=[]
            def execute(args,**kwargs):
                calls.append(args)
                if 'install' in args:shutil.copyfile(args[-2],args[-1])
                elif 'mv' in args:Path(args[-2]).replace(args[-1])
                elif 'rm' in args:Path(args[-1]).unlink(missing_ok=True)
            with patch.object(installer.subprocess,'run',side_effect=execute):
                installer.write_unit(installer.unit_text(['/python','/verified/klippy.py','/printer.cfg']),root,dropin)
            self.assertEqual(calls[0][-1],str(dropin)+'.new')
            self.assertEqual(calls[1][-2:],[str(dropin)+'.new',str(dropin)])
            self.assertEqual(calls[2][-1],str(dropin)+'.new')
            self.assertNotIn('$KLIPPER_ARGS',(root/'service-dropin.conf').read_text())

    def test_interrupted_temporary_write_never_truncates_the_active_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=root/'50-inventure-release.conf';target.write_bytes(b'old pinned runtime')
            def fail(args,**kwargs):
                if 'install' in args:raise OSError('Interrupted temporary install')
            with patch.object(installer.subprocess,'run',side_effect=fail):
                with self.assertRaises(OSError):installer.write_unit(b'new pinned runtime',root,target)
            self.assertEqual(target.read_bytes(),b'old pinned runtime')


if __name__=='__main__':unittest.main()
