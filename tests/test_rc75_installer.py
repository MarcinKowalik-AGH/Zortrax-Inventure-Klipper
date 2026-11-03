# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util, pathlib, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rc75installer',ROOT/'install_rc75_oled.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
CFG='''[mcu]\nserial: /dev/ttyS1\nbaud: 250000\n\n[printer]\nkinematics: none\n\n[inventure_oled]\nspi_speed: 2000000\nchunk_interval: 0.002\nrefresh_interval: 0.2\noem_menu: True\noem_language: pl\noem_start_screen: klipper\n\n[extruder]\nmax_extrude_only_distance: 25\n'''

class InstallerTests(unittest.TestCase):
    def test_menu_profile_insert_does_not_change_serial_or_motion(self):
        new=installer.plan_config(CFG)
        self.assertIn('menu_profile: m300',new)
        self.assertIn('baud: 250000',new)
        self.assertEqual(new.replace('menu_profile: m300\n',''),CFG)
    def test_unknown_option_and_duplicate_rejected(self):
        with self.assertRaises(ValueError):installer.plan_config(CFG.replace('oem_menu: True','oem_menu: False'))
        with self.assertRaises(ValueError):installer.plan_config(CFG.replace('refresh_interval: 0.2','refresh_interval: 0.2\nrefresh_interval: 0.2'))
        with self.assertRaises(ValueError):installer.plan_config(CFG.replace('oem_start_screen: klipper','oem_start_screen: invalid'))
    def test_payload_shas_match_source(self):
        for name,(accepted,new) in installer.FILES.items():
            self.assertIn(name,('inventure_oled.py','inventure_oem_menu.py'))
            self.assertEqual(installer.SHA((ROOT/'HOST'/name).read_bytes()),new)
            self.assertEqual(len(accepted),1)
    def test_apply_rollback_with_sha_staged_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=pathlib.Path(tmp);host=base/'klipper';extras=host/'klippy/extras';extras.mkdir(parents=True)
            (host/'klippy/klippy.py').write_text('# fixture')
            cfg=base/'printer.cfg';cfg.write_text(CFG)
            source=base/'payload';source.mkdir()
            oldfiles={};newfiles={};accepted={}
            for name in installer.FILES:
                oldfiles[name]=(name+' old v7.4').encode()
                newfiles[name]=(name+' new v7.5').encode()
                (extras/name).write_bytes(oldfiles[name]);(source/name).write_bytes(newfiles[name])
                accepted[name]=((installer.SHA(oldfiles[name]),),installer.SHA(newfiles[name]))
            orig_files=installer.FILES;orig_payload=installer.PAYLOAD;orig_stopped=installer.service_stopped
            try:
                installer.FILES=accepted;installer.PAYLOAD=source
                installer.service_stopped=lambda:None
                items,old,new=installer.make_plan(host,cfg)
                self.assertEqual(len(items),2)
                installer.apply(host,cfg,items,old,new)
                self.assertIn('menu_profile: m300',cfg.read_text())
                bak=next((host/'.inventure-rc75-backups').iterdir())
                installer.rollback(host,cfg,bak)
                self.assertEqual(cfg.read_text(),CFG)
                for name in oldfiles:self.assertEqual((extras/name).read_bytes(),oldfiles[name])
            finally:
                installer.FILES=orig_files;installer.PAYLOAD=orig_payload
                installer.service_stopped=orig_stopped

if __name__=='__main__':unittest.main()
