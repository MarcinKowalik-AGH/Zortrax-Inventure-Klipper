"""Compile/run actual board and panel C against register/API doubles."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys
import importlib.util
import io
import zipfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ci'))
from prepare_kit import prepare

class McuLocalServices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.kit=Path(cls.tmp.name)/'kit'
        prepare(cls.kit)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def run_c(self,name):
        cc=shutil.which('cc') or shutil.which('gcc')
        self.assertIsNotNone(cc,'native C compiler is required')
        out=Path(self.tmp.name)/name
        command=[cc,'-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-pointer-to-int-cast',
            '-I',ROOT/'tests/mcu/mock','-I',self.kit/'mcu',
            '-I',self.kit/'regression_tests/overlay/src/stm32',ROOT/'tests/mcu'/f'{name}.c','-o',out]
        p=subprocess.run(list(map(str,command)),capture_output=True,text=True,timeout=30)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        p=subprocess.run([out],capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        print(p.stdout,end='')
    def test_local_cooling_adc_ownership_faults_and_fast_paths(self):self.run_c('test_rc8_board')
    def test_oled_row_oracle_and_local_recovery(self):self.run_c('test_rc8_panel')
    def test_matching_full_host_installs_and_rolls_back_rc72_sources(self):
        root=Path(self.tmp.name)/'host-upgrade'
        with zipfile.ZipFile(self.kit/'baseline/SOURCE_FULL_KLIPPER_PINNED_P17_RC2.zip') as z:
            z.extractall(root)
        host=root/'klipper-inventure'
        original={}
        parts=b''.join(p.read_bytes() for p in sorted((ROOT/'source/parts').glob('part*.bin')))
        with zipfile.ZipFile(io.BytesIO(parts)) as z:
            for path in (self.kit/'host').iterdir():
                if path.suffix not in ('.py','.json'):continue
                rel='klippy/extras/'+path.name;data=z.read('host/'+path.name)
                (host/rel).write_bytes(data);original[rel]=data
            rel='klippy/serialhdl.py';data=z.read('host_core/serialhdl.py')
            (host/rel).write_bytes(data);original[rel]=data
        spec=importlib.util.spec_from_file_location('rc8fullinstall',self.kit/'scripts/install_host.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        target,extras,history=module.safe_target(host);items=module.plan(target,extras,history)
        self.assertEqual(len(items),16)
        with patch.object(module,'assert_service_stopped',return_value=None):
            module.install(target,extras,items,True)
            backup=next((target/'.inventure-rc7_1-backups').iterdir())
            module.rollback(target,backup,True)
        for rel,data in original.items():self.assertEqual((target/rel).read_bytes(),data)

if __name__=='__main__':unittest.main()
