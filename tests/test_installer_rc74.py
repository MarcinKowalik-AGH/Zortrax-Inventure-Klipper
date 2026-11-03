import hashlib,importlib.util,os,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rc74install',ROOT/'install_rc74_oled.py')
mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
CLEAN=(ROOT/'TESTS/baseline_host' if (ROOT/'TESTS/baseline_host').is_dir() else ROOT/'tests/baseline_host')
SAMPLE='''[mcu]\nserial: /dev/ttyS1\nbaud: 250000\n\n[printer]\nkinematics: none\nmax_velocity: 1\nmax_accel: 1\n\n[inventure_oled]\nspi_speed: 2000000\nchunk_interval: 0.020\nrefresh_interval: 0.5\nack_timeout: 1.0\noem_menu: True\noem_language: pl\n\n[inventure_mcu]\nallow_actuators: False\n'''
class InstallerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.host=self.root/'klipper';extras=self.host/'klippy/extras';extras.mkdir(parents=True)
  (self.host/'klippy/klippy.py').write_text('')
  for p in mod.FILES:(extras/p).write_bytes((CLEAN/p).read_bytes())
  self.cfg=self.root/'printer.cfg';self.cfg.write_text(SAMPLE)
 def test_precise_preflight_and_rollback(self):
  items,orig,patched=mod.make_plan(self.host,self.cfg)
  self.assertEqual(len(items),3)
  self.assertEqual(orig,SAMPLE)
  self.assertIn('chunk_interval: 0.002',patched)
  self.assertIn('refresh_interval: 0.2',patched)
  self.assertIn('oem_start_screen: klipper',patched)
  self.assertIn('kinematics: none',patched)
  self.assertIn('allow_actuators: False',patched)
  mod.apply(self.host,self.cfg,items,orig,patched)
  backups=sorted((self.host/'.inventure-rc74-backups').iterdir());self.assertEqual(len(backups),1)
  items2,orig2,patched2=mod.make_plan(self.host,self.cfg)
  self.assertEqual(orig2,patched2)
  self.assertTrue(all(i['action']=='same' for i in items2))
  mod.rollback(self.host,self.cfg,backups[0])
  self.assertEqual(self.cfg.read_text(),SAMPLE)
  for p in mod.FILES:self.assertEqual((self.host/'klippy/extras'/p).read_bytes(),(CLEAN/p).read_bytes())
 def test_unknown_modified_file_rejected_without_any_write(self):
  p=self.host/'klippy/extras/inventure_oled.py';p.write_text('untrusted')
  with self.assertRaisesRegex(ValueError,'unknown existing SHA'):mod.make_plan(self.host,self.cfg)
  self.assertEqual(self.cfg.read_text(),SAMPLE)
 def test_duplicate_section_and_parameter_rejected(self):
  self.cfg.write_text(SAMPLE+'\n[inventure_oled]\noem_menu: True\n')
  with self.assertRaisesRegex(ValueError,'Exactly one'):mod.make_plan(self.host,self.cfg)
  self.cfg.write_text(SAMPLE.replace('chunk_interval: 0.020','chunk_interval: 0.020\nchunk_interval: 0.002'))
  with self.assertRaisesRegex(ValueError,'Duplicate'):mod.make_plan(self.host,self.cfg)
 def test_opt_in_direct_menu_respected(self):
  self.cfg.write_text(SAMPLE.replace('oem_language: pl','oem_language: pl\noem_start_screen: menu'))
  _,orig,patched=mod.make_plan(self.host,self.cfg)
  self.assertIn('oem_start_screen: menu',patched)
 def test_unknown_interval_refused(self):
  self.cfg.write_text(SAMPLE.replace('chunk_interval: 0.020','chunk_interval: 0.050'))
  with self.assertRaisesRegex(ValueError,'Unexpected chunk_interval'):mod.make_plan(self.host,self.cfg)
if __name__=='__main__':unittest.main()
