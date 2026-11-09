"""Exercise the real host file installer with an isolated systemd/proc model."""
import importlib.util
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

    def test_upgrade_targets_the_running_stage_and_preserves_config_and_backup(self):
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertEqual(self.other.read_text(),'# unrelated checkout\n')
        backups=list((self.live/'.inventure-thermal-test-r2-backups').glob('*/inventure_mcu.py'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),self.old)
        self.assertIn(('sudo','systemctl','stop','klipper'),self.calls)
        self.assertTrue(self.active)

    def test_dry_run_does_not_stop_service_or_write_files(self):
        with patch('sys.stdout',new=io.StringIO()):update.update(True)
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertFalse(any('stop' in a for a in self.calls))
        self.assertFalse((self.live/'.inventure-thermal-test-r2-backups').exists())

    def test_same_disk_version_restarts_python_without_rewriting_modules(self):
        expected=(ROOT/'orange_pi/inventure_mcu.py').read_bytes()
        self.dest.write_bytes(expected)
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
