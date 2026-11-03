"""Compile/run actual board and panel C against register/API doubles."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys
import importlib.util
import io
import json
import zipfile
import zlib
from unittest.mock import Mock, patch
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
    def run_c(self,name,*options):
        cc=shutil.which('cc') or shutil.which('gcc')
        self.assertIsNotNone(cc,'native C compiler is required')
        out=Path(self.tmp.name)/name
        command=[cc,'-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-pointer-to-int-cast',
            *options,'-I',ROOT/'tests/mcu/mock','-I',self.kit/'mcu',
            '-I',self.kit/'regression_tests/overlay/src/stm32',ROOT/'tests/mcu'/f'{name}.c','-o',out]
        p=subprocess.run(list(map(str,command)),capture_output=True,text=True,timeout=30)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        p=subprocess.run([out],capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        print(p.stdout,end='')
    def test_local_cooling_adc_ownership_faults_and_fast_paths(self):self.run_c('test_rc8_board')
    def test_oled_row_oracle_and_local_recovery(self):
        self.run_c('test_rc8_panel')
        self.run_c('test_rc8_panel','-DCONFIG_SERIAL_BAUD=1500000')
    def test_full_host_accepts_oled_versions_supported_by_launcher(self):
        spec=importlib.util.spec_from_file_location('launcher_install',ROOT/'install_rc75_oled.py')
        launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
        history=json.loads((self.kit/'baseline/expected_hashes.json').read_text())
        for name,(accepted,current) in launcher.FILES.items():
            entry=history['klippy/extras/'+name]
            allowed={entry['sha256'],entry['new_sha256'],*entry.get('allowed_existing',[])}
            self.assertLessEqual(set(accepted)|{current},allowed,name)

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
        serial=next(item for item in items if item['path']=='klippy/serialhdl.py')
        self.assertEqual(serial['action'],'replace')
        with patch.object(module,'assert_service_stopped',return_value=None):
            module.install(target,extras,items,True)
            self.assertEqual((target/serial['path']).read_bytes(),
                             (ROOT/'host_core/serialhdl.py').read_bytes())
            backup=next((target/'.inventure-rc7_1-backups').iterdir())
            module.rollback(target,backup,True)
        for rel,data in original.items():self.assertEqual((target/rel).read_bytes(),data)

    def test_p4_uart_session_sets_wire_rate_after_identify(self):
        with zipfile.ZipFile(self.kit/'baseline/SOURCE_FULL_KLIPPER_PINNED_P17_RC2.zip') as z:
            parser_path=z.extract('klipper-inventure/klippy/msgproto.py',self.tmp.name)
        spec=importlib.util.spec_from_file_location('msgproto',parser_path)
        parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)
        spec=importlib.util.spec_from_file_location('p4_serial',self.kit/'host_core/serialhdl.py')
        serial=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,msgproto=parser,serial=Mock(),chelper=Mock(),util=Mock()):
            serial_helper=sys.modules['chelper']
            spec.loader.exec_module(serial)

        p4={'INVENTURE_P4_BOOT_JOURNAL_ABI':1,'SERIAL_BAUD':250000}
        cases=[(rate,b'u',p4,rate) for rate in (500000,750000,1000000,2000000)]
        cases.extend([
            (1500000,b'u',dict(p4,SERIAL_BAUD=1500000,INVENTURE_P4_FORCED_BAUD=1500000),1500000),
            (1500000,b'u',{'SERIAL_BAUD':115200},115200),
            (None,b'c',dict(p4,CANBUS_FREQUENCY=1000000),1000000),
            (1750000,b'u',p4,None),
        ])
        for baud,transport,config,expected in cases:
            with self.subTest(baud=baud,transport=transport,config=config):
                serial_helper.get_ffi.return_value=(Mock(),Mock())
                reactor=Mock()
                reactor.monotonic.return_value=0.
                raw=zlib.compress(json.dumps(dict(commands={},responses={},config=config)).encode())
                reactor.register_callback.return_value.wait.return_value=raw
                reader=serial.SerialReader(reactor)
                device=Mock(baudrate=baud)
                with patch.object(reader,'_bg_thread'):
                    if expected is None:
                        with self.assertRaisesRegex(serial.error,'P4 baud not supported'):
                            reader._start_session(device,transport)
                        device.close.assert_called_once()
                        self.assertIsNone(reader.serialqueue)
                    else:
                        self.assertTrue(reader._start_session(device,transport))
                        reader.ffi_lib.serialqueue_set_wire_frequency.assert_called_once_with(
                            reader.serialqueue,float(expected))
                        reader.disconnect()

if __name__=='__main__':unittest.main()
