"""Verify source-to-artifact integration and reversible live-host installation."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ci'))
from apply_gnu_ctr_fix import main as gnu_fix
from apply_rc7_dict_fix import apply as dictionary_fix
from apply_rc75_oled import apply as oled_fix
from prepare_kit import prepare

spec=importlib.util.spec_from_file_location('rc74install',ROOT/'install_rc75_oled.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

SAMPLE='''[mcu]
serial: /dev/ttyS1
baud: 250000

[printer]
kinematics: none
max_velocity: 1
max_accel: 1

[inventure_oled]
spi_speed: 2000000
chunk_interval: 0.020
refresh_interval: 0.5
ack_timeout: 1.0
oem_menu: True
oem_language: pl

[inventure_mcu]
allow_actuators: False
'''

def archive_bytes():
    return b''.join(p.read_bytes() for p in sorted((ROOT/'source/parts').glob('part*.bin')))

class Integration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.kit=self.root/'kit'

    def baseline(self):
        with zipfile.ZipFile(io.BytesIO(archive_bytes())) as z:z.extractall(self.kit)
        gnu_fix(self.kit);dictionary_fix(self.kit)

    def test_prepared_build_and_readable_sources_match(self):
        prepare(self.kit)
        for path in (ROOT/'host').glob('*.py'):
            self.assertEqual(path.read_bytes(),(self.kit/'host'/path.name).read_bytes())
        for path in (ROOT/'mcu').iterdir():
            if path.is_file():self.assertEqual(path.read_bytes(),(self.kit/'mcu'/path.name).read_bytes())
        hashes=json.loads((self.kit/'baseline/expected_hashes.json').read_text())
        for key,record in json.loads((ROOT/'ci/rc75_manifest.json').read_text())['host'].items():
            self.assertEqual(hashes[key]['new_sha256'],record['new_sha256'])
            self.assertEqual((self.kit/'regression_tests/overlay'/key).read_bytes(),
                             (ROOT/'host'/Path(key).name).read_bytes())

    def test_unknown_host_rejected_before_any_write(self):
        self.baseline();old=(self.kit/'host/inventure_oled.py').read_bytes()
        (self.kit/'host/inventure_oled_transport.py').write_text('unknown')
        with self.assertRaisesRegex(ValueError,'Unknown baseline host'):oled_fix(self.kit)
        self.assertEqual((self.kit/'host/inventure_oled.py').read_bytes(),old)

    def test_changed_protected_contract_is_rejected(self):
        self.baseline();old=(self.kit/'host/inventure_oled.py').read_bytes()
        path=self.kit/'regression_tests/config/p0_module_contracts.json'
        path.write_text(path.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'Unknown kit input'):oled_fix(self.kit)
        self.assertEqual((self.kit/'host/inventure_oled.py').read_bytes(),old)

    def test_preparation_does_not_overwrite_existing_directory(self):
        self.kit.mkdir();(self.kit/'sentinel').write_text('keep')
        with self.assertRaisesRegex(ValueError,'already exists'):prepare(self.kit)
        self.assertEqual((self.kit/'sentinel').read_text(),'keep')


class Installer(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.host=self.root/'klipper'
        self.extras=self.host/'klippy/extras';self.extras.mkdir(parents=True)
        (self.host/'klippy/klippy.py').write_text('')
        with zipfile.ZipFile(io.BytesIO(archive_bytes())) as z:
            self.original={name:z.read('host/'+name) for name in installer.FILES}
        for name,data in self.original.items():(self.extras/name).write_bytes(data)
        self.cfg=self.root/'printer.cfg';self.cfg.write_text(SAMPLE)
        self.guard=patch.object(installer,'service_stopped',return_value=None)
        self.guard.start();self.addCleanup(self.guard.stop)

    def test_precise_update_idempotence_and_bitwise_rollback(self):
        items,old,new=installer.make_plan(self.host,self.cfg)
        self.assertEqual(len(items),4)
        self.assertIn('chunk_interval: 0.002',new);self.assertIn('refresh_interval: 0.2',new)
        self.assertIn('kinematics: none',new);self.assertIn('allow_actuators: False',new)
        installer.apply(self.host,self.cfg,items,old,new)
        items2,old2,new2=installer.make_plan(self.host,self.cfg)
        self.assertEqual(old2,new2);self.assertTrue(all(i['action']=='same' for i in items2))
        backup=next((self.host/'.inventure-rc75-backups').iterdir())
        installer.rollback(self.host,self.cfg,backup)
        self.assertEqual(self.cfg.read_text(),SAMPLE)
        for name,data in self.original.items():self.assertEqual((self.extras/name).read_bytes(),data)

    def test_unknown_file_refused_without_config_write(self):
        (self.extras/'inventure_oled.py').write_text('unknown')
        with self.assertRaisesRegex(ValueError,'unknown existing SHA'):
            installer.make_plan(self.host,self.cfg)
        self.assertEqual(self.cfg.read_text(),SAMPLE)

    def test_duplicate_options_and_sections_refused(self):
        for text in (SAMPLE+'\n[inventure_oled]\noem_menu: True\n',
                     SAMPLE.replace('chunk_interval: 0.020','chunk_interval: 0.020\nchunk_interval: 0.002')):
            with self.subTest(text=text):
                self.cfg.write_text(text)
                with self.assertRaises(ValueError):installer.make_plan(self.host,self.cfg)

    def test_release_config_selects_klipper_start(self):
        self.cfg.write_text(SAMPLE.replace('oem_language: pl','oem_language: pl\noem_start_screen: menu'))
        self.assertIn('oem_start_screen: klipper',installer.make_plan(self.host,self.cfg)[2])

    def test_unknown_pacing_is_refused(self):
        self.cfg.write_text(SAMPLE.replace('chunk_interval: 0.020','chunk_interval: 0.050'))
        with self.assertRaisesRegex(ValueError,'Unexpected chunk_interval'):
            installer.make_plan(self.host,self.cfg)

    def test_concurrent_change_cannot_partially_install(self):
        items,old,new=installer.make_plan(self.host,self.cfg)
        (self.extras/'inventure_oem_menu.py').write_text('concurrent change')
        with self.assertRaisesRegex(ValueError,'Concurrent modification'):
            installer.apply(self.host,self.cfg,items,old,new)
        self.assertEqual((self.extras/'inventure_oled.py').read_bytes(),self.original['inventure_oled.py'])
        self.assertEqual(self.cfg.read_text(),SAMPLE)

    def test_symlink_file_refused(self):
        target=self.extras/'inventure_oled.py';target.unlink()
        target.symlink_to(self.cfg)
        with self.assertRaisesRegex(ValueError,'symlink'):installer.make_plan(self.host,self.cfg)


if __name__=='__main__':unittest.main()
