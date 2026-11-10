"""Exercise the real host file installer with an isolated systemd/proc model."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('orange_pi_update',ROOT/'install-orange-pi.py')
update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)


class OrangePiUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.live=self.root/'inventure-rc72-stage/klipper-inventure'
        self.extras=self.live/'klippy/extras';self.extras.mkdir(parents=True)
        self.script=self.live/'klippy/klippy.py';self.script.write_text('# active stage\n')
        self.config=self.root/'printer_data/config/printer.cfg';self.config.parent.mkdir(parents=True)
        self.config.write_text('[mcu]\nserial: /dev/ttyS1\nbaud: 750000\n[printer]\nkinematics: none\n')
        self.original_config=self.config.read_bytes()
        self.old=(ROOT/'tests/fixtures/inventure_mcu_chamber75_r3.py').read_bytes()
        self.dest=self.extras/'inventure_mcu.py';self.dest.write_bytes(self.old)
        self.factory_dest=self.extras/'inventure_factory_core.py'
        for name in ('inventure_oled.py','serialhdl.py'):
            p=self.live/'klippy/serialhdl.py' if name=='serialhdl.py' else self.extras/name
            p.write_bytes((ROOT/'orange_pi'/name).read_bytes())
        self.other=self.root/'klipper/klippy/extras/inventure_mcu.py'
        self.other.parent.mkdir(parents=True);self.other.write_text('# unrelated checkout\n')
        self.proc=self.root/'proc';self.process=self.proc/'421';self.process.mkdir(parents=True)
        self.argv=['/usr/bin/python3',str(self.script),str(self.config),'-l',str(self.root/'klippy.log')]
        (self.process/'cmdline').write_bytes(b'\0'.join(a.encode() for a in self.argv)+b'\0')
        self.active=True;self.calls=[]
        original_target=update.core.running_klipper_target
        original_config=update.core.running_printer_config
        for obj,name,value in ((update.core,'running_klipper_target',lambda:original_target(proc_root=self.proc)),
                               (update.core,'running_printer_config',lambda:original_config(proc_root=self.proc)),
                               (update.subprocess,'run',self.run_system),
                               (update,'check_print_job',lambda:None),
                               (update.os,'geteuid',lambda:1000)):
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)

    def run_system(self,args,check=False,**kw):
        self.calls.append(tuple(args))
        if args[:2]==['systemctl','show']:
            return types.SimpleNamespace(returncode=0,stdout='421\n')
        if args==['sudo','-v']:rc=0
        elif args[-3:]==['systemctl','stop','klipper']:self.active=False;rc=0
        elif args[-3:]==['systemctl','start','klipper']:self.active=True;rc=0
        elif args==['systemctl','is-active','--quiet','klipper']:rc=0 if self.active else 3
        else:raise AssertionError('Unexpected system request: '+str(args))
        if check and rc:raise subprocess.CalledProcessError(rc,args)
        return types.SimpleNamespace(returncode=rc)

    def factory_package(self):
        """Exercise the new-module path even while release hashes are staged."""
        package=self.root/'package';package.mkdir()
        metadata=json.loads((ROOT/'orange_pi/THERMAL_HOST_MANIFEST.json').read_text())
        for name,key in (('inventure_mcu.py','module_sha256'),
                         ('inventure_oled.py','oled_module_sha256'),('serialhdl.py','serialhdl_sha256')):
            data=(ROOT/'orange_pi'/name).read_bytes();(package/name).write_bytes(data)
            metadata[key]=hashlib.sha256(data).hexdigest()
        core=ROOT/'orange_pi/inventure_factory_core.py'
        data=core.read_bytes() if core.is_file() else b'# isolated installer factory-core fixture\n'
        (package/'inventure_factory_core.py').write_bytes(data)
        metadata['factory_core_sha256']=hashlib.sha256(data).hexdigest()
        old=(ROOT/'tests/fixtures/inventure_mcu_t1.py').read_bytes()
        metadata['allowed_existing_sha256'].append(hashlib.sha256(old).hexdigest())
        (package/'THERMAL_HOST_MANIFEST.json').write_text(json.dumps(metadata))
        return package

    def test_upgrade_targets_the_running_stage_and_preserves_config_and_backup(self):
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertEqual(self.other.read_text(),'# unrelated checkout\n')
        backups=list((self.live/'.inventure-thermal-test-r2-backups').glob('*/inventure_mcu.py'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),self.old)
        self.assertIn(('sudo','systemctl','stop','klipper'),self.calls)
        self.assertTrue(self.active)

    def test_motor_r1_adapter_and_oled_upgrade_together_in_the_active_stage(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_motor_r1.py').read_bytes())
        oled=self.extras/'inventure_oled.py'
        oled.write_bytes((ROOT/'tests/fixtures/inventure_oled_motor_r1.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(oled.read_bytes(),(ROOT/'orange_pi/inventure_oled.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_motor_r2_upgrade_accepts_the_exact_installed_door_policy_module(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_motor_r2.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_motor_r3_upgrade_accepts_the_physically_validated_seek_module(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_motor_r3.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_motor_r4_upgrade_preserves_config_and_reloads_python(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_motor_r4.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_dry_run_does_not_stop_service_or_write_files(self):
        with patch('sys.stdout',new=io.StringIO()):update.update(True)
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertFalse(any('stop' in a for a in self.calls))
        self.assertFalse((self.live/'.inventure-thermal-test-r2-backups').exists())

    def test_zlimit_host_upgrades_to_service_without_config_or_mcu_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_zlimit.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_service_host_upgrades_to_sensor_readbacks_without_config_or_mcu_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_service.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_sensors_host_upgrades_to_realtime_feeder_run_without_changing_config(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_sensors.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_feedrun_host_upgrades_to_50_second_limit_without_changing_config(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_feedrun.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_control_r5_upgrade_preserves_mcu_config_and_reloads_python(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_feed50_host_upgrades_to_load230_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_feed50.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_load230_upgrades_to_R2_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_load230.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_load230_R2_upgrades_to_PID_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_load230_r2.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_load230_R3_upgrades_to_LOAD15_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_load230_r3.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_LOAD15_upgrades_to_cassette_load_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_load15.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_CASSETTE1_upgrades_to_smooth_host_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_cassette1.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_smooth_upgrades_to_retraction_without_changing_MCU_or_config(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_smooth.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_retract_upgrades_to_both_tools_without_changing_MCU_or_config(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_retract.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_T1_upgrade_adds_verified_factory_core_and_records_previous_absence(self):
        previous=(ROOT/'tests/fixtures/inventure_mcu_t1.py').read_bytes()
        self.dest.write_bytes(previous);package=self.factory_package()
        with patch.object(update.core,'ROOT',package),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.factory_dest.read_bytes(),(package/'inventure_factory_core.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        backups=list((self.live/'.inventure-thermal-test-r2-backups').glob('*/INSTALL_MANIFEST.json'))
        self.assertEqual(len(backups),1)
        state=json.loads(backups[0].read_text())['files']['inventure_factory_core.py']
        self.assertEqual(state,{'exists':False,'sha256':None})
        self.assertEqual((backups[0].parent/'inventure_mcu.py').read_bytes(),previous)
        self.assertFalse((backups[0].parent/'inventure_factory_core.py').exists())

    def test_factory_upgrade_rollback_restores_exact_previous_module_and_missing_core(self):
        previous=(ROOT/'tests/fixtures/inventure_mcu_t1.py').read_bytes()
        self.dest.write_bytes(previous);package=self.factory_package();self.active=False
        with patch.object(update.core,'ROOT',package),patch('sys.stdout',new=io.StringIO()):
            result=update.core.install(self.live,apply=True)
            self.assertTrue(self.factory_dest.is_file())
            update.core.install(self.live,apply=True,rollback=Path(result['backup']))
        self.assertEqual(self.dest.read_bytes(),previous)
        self.assertFalse(self.factory_dest.exists())
        self.assertEqual(self.config.read_bytes(),self.original_config)

    def test_existing_verified_factory_core_survives_rollback(self):
        package=self.factory_package();expected=(package/'inventure_factory_core.py').read_bytes()
        self.factory_dest.write_bytes(expected);self.active=False
        with patch.object(update.core,'ROOT',package),patch('sys.stdout',new=io.StringIO()):
            result=update.core.install(self.live,apply=True)
            update.core.install(self.live,apply=True,rollback=Path(result['backup']))
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertEqual(self.factory_dest.read_bytes(),expected)

    def test_unknown_existing_factory_core_is_refused_before_stopping(self):
        package=self.factory_package();self.factory_dest.write_text('# local change\n')
        with patch.object(update.core,'ROOT',package):
            with self.assertRaisesRegex(ValueError,'factory_core.py differs'):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertEqual(self.factory_dest.read_text(),'# local change\n')
        self.assertFalse(any('stop' in args for args in self.calls))

    def test_interruption_at_factory_module_restores_old_files_and_previous_absence(self):
        package=self.factory_package();original=Path.replace
        def interrupt_core(path,other):
            if path.name=='.inventure_factory_core.py.thermal-r2.new':raise KeyboardInterrupt()
            return original(path,other)
        with patch.object(update.core,'ROOT',package),patch.object(Path,'replace',interrupt_core):
            with self.assertRaises(KeyboardInterrupt),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertFalse(self.factory_dest.exists());self.assertTrue(self.active)
        self.assertEqual(self.config.read_bytes(),self.original_config)

    def test_backup_absence_marker_cannot_hide_an_unexpected_core_file(self):
        package=self.factory_package();self.active=False
        with patch.object(update.core,'ROOT',package),patch('sys.stdout',new=io.StringIO()):
            result=update.core.install(self.live,apply=True)
            backup=Path(result['backup']);(backup.parent/'inventure_factory_core.py').write_bytes((package/'inventure_factory_core.py').read_bytes())
            with self.assertRaisesRegex(ValueError,'Backup presence differs'):
                update.core.install(self.live,apply=True,rollback=backup)
        self.assertEqual(self.dest.read_bytes(),(package/'inventure_mcu.py').read_bytes())
        self.assertEqual(self.factory_dest.read_bytes(),(package/'inventure_factory_core.py').read_bytes())

    def test_same_disk_version_restarts_python_without_rewriting_modules(self):
        expected=(ROOT/'orange_pi/inventure_mcu.py').read_bytes()
        self.dest.write_bytes(expected)
        if 'factory_core_sha256' in json.loads((ROOT/'orange_pi/THERMAL_HOST_MANIFEST.json').read_text()):
            self.factory_dest.write_bytes((ROOT/'orange_pi/inventure_factory_core.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),expected)
        self.assertIn(('sudo','systemctl','stop','klipper'),self.calls)
        self.assertTrue(self.active)
        self.assertFalse((self.live/'.inventure-thermal-test-r2-backups').exists())

    def test_unknown_local_changes_fail_before_stopping(self):
        modified=self.old+b'\n# local user change\n';self.dest.write_bytes(modified)
        with self.assertRaisesRegex(ValueError,'differs'):update.update()
        self.assertEqual(self.dest.read_bytes(),modified)
        self.assertFalse(any('stop' in a for a in self.calls))

    def test_print_in_progress_fails_before_stopping(self):
        with patch.object(update,'check_print_job',side_effect=ValueError('current print')):
            with self.assertRaisesRegex(ValueError,'current print'),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertFalse(any('stop' in a for a in self.calls))

    def test_failed_start_rolls_back_files_and_restarts_old_host(self):
        base=self.run_system;failed=False
        def fail_first_start(args,**kw):
            nonlocal failed
            if args[-3:]==['systemctl','start','klipper'] and not failed:
                failed=True;raise subprocess.CalledProcessError(1,args)
            return base(args,**kw)
        with patch.object(update.subprocess,'run',side_effect=fail_first_start):
            with self.assertRaises(subprocess.CalledProcessError),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old);self.assertTrue(self.active)

    def test_interrupted_partial_copy_is_restored_before_service_restart(self):
        original=Path.replace
        def interrupt_second(path,other):
            if path.name=='.inventure_oled.py.thermal-r2.new':raise KeyboardInterrupt()
            return original(path,other)
        with patch.object(Path,'replace',interrupt_second):
            with self.assertRaises(KeyboardInterrupt),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old);self.assertTrue(self.active)
        self.assertEqual(self.config.read_bytes(),self.original_config)

    def test_ambiguous_running_process_is_refused_before_write(self):
        (self.process/'cmdline').write_bytes(b'/usr/bin/python3\0wrapper.py\0')
        with self.assertRaisesRegex(ValueError,'klippy.py'):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old)


class PrintGuardTests(unittest.TestCase):
    def test_printing_and_paused_states_refuse_updates(self):
        for state in ('printing','paused'):
            data=json.dumps({'result':{'status':{'print_stats':{'state':state}}}}).encode()
            with patch.object(update.urllib.request,'urlopen',return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError,'current print'):update.check_print_job()


if __name__=='__main__':unittest.main(verbosity=2)
