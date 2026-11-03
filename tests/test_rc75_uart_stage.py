# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib,subprocess,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import inventure_p4_baud_stage as p4
CFG='''[mcu]\nserial: /dev/ttyS1\nbaud: 250000 # current P4\nrestart_method: command\n\n[printer]\nkinematics: none\n\n[inventure_oled]\nrefresh_interval: 0.2\n'''
class UARTTests(unittest.TestCase):
    def test_step_250_to_1m(self):
        new,old=p4.parse_and_plan(CFG,1000000)
        self.assertEqual(old,250000)
        self.assertIn('baud: 1000000 # current P4',new)
        self.assertEqual(new.replace('baud: 1000000','baud: 250000'),CFG)
        newer,old=p4.parse_and_plan(new,2000000)
        self.assertEqual(old,1000000)
        self.assertIn('baud: 2000000',newer)
    def test_reject_direct_250_to_2m(self):
        with self.assertRaises(ValueError):p4.parse_and_plan(CFG,2000000)
    def test_reject_wrong_port(self):
        with self.assertRaises(ValueError):p4.parse_and_plan(CFG.replace('/dev/ttyS1','/dev/ttyS3'),1000000)
    def test_reject_duplicate_baud(self):
        with self.assertRaises(ValueError):p4.parse_and_plan(CFG.replace('baud: 250000','baud: 250000\nbaud: 250000'),1000000)
    def test_reject_nonqual_rate(self):
        with self.assertRaises(ValueError):p4.parse_and_plan(CFG,1750000)
    def test_zero3_24mhz_exact_and_incompatible_speeds(self):
        for baud in (250000,500000,1500000):
            p4.validate_clock(baud,24000000)
        for baud in (1000000,2000000):
            with self.assertRaises(ValueError):
                p4.validate_clock(baud,24000000)
        with self.assertRaises(ValueError):
            p4.validate_clock(1500000,None)
        for baud in (1000000,2000000):
            p4.validate_clock(baud,64000000)
        with self.assertRaises(ValueError):
            p4.validate_clock(3000000,24000000)
        p4.validate_clock(3000000,48000000)
    def test_transition_from_500k_to_1500k_and_recovery(self):
        base,_=p4.parse_and_plan(CFG,500000)
        lab,old=p4.parse_and_plan(base,1500000)
        self.assertEqual(old,500000)
        self.assertIn('baud: 1500000 # current P4',lab)
        self.assertEqual(p4.parse_and_plan(lab,500000),(base,1500000))
        fast,old=p4.parse_and_plan(lab,3000000)
        self.assertEqual(old,1500000)
        self.assertEqual(p4.parse_and_plan(fast,1500000),(lab,3000000))
    def test_cli_uses_the_measured_uart_clock(self):
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'printer.cfg'
            path.write_text(CFG)
            for baud,expected in ((1000000,1),(2000000,1),(1500000,0)):
                result=subprocess.run([sys.executable,p4.__file__,'--printer-cfg',str(path),
                                       '--baud',str(baud),'--uart-clock-hz','24000000'],
                                      capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,expected,result.stdout+result.stderr)
            self.assertEqual(path.read_text(),CFG)

    def test_restore_host_cfg_does_not_touch_mcu(self):
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'printer.cfg';path.write_text(CFG)
            target,_=p4.parse_and_plan(CFG,1000000)
            p4.active_service=lambda:False
            p4.apply(path,CFG,target,250000,1000000)
            backup=next((path.parent/'.inventure-p4-backups').iterdir())
            self.assertEqual(path.read_text(),target)
            p4.rollback(path,backup)
            self.assertEqual(path.read_text(),CFG)

if __name__=='__main__':unittest.main()
