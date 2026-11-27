"""Authenticated R26 upgrade and explicit MCU/host pairing for R27."""
from hashlib import sha256
from pathlib import Path
import sys,tempfile,types,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'ci')]
import inventure_bootstrap as bootstrap

class UARTWindowHostTests(unittest.TestCase):
    def fixture(self,window):
        from test_inventure_communication_guard import GuardedOLED,Printer,Config,ready_stream
        p=Printer();original=p.mcu.get_constants
        p.mcu.get_constants=lambda:dict(original(),INVENTURE_OLED_COEXISTENCE_ABI=1,
            **({'INVENTURE_UART_TIMER_WINDOW_ABI':1} if window else {}))
        oled=GuardedOLED(Config(p));oled._build_config();oled.connected=True;oled.stream=ready_stream()
        p.board.zortrax=types.SimpleNamespace(home_ui=types.SimpleNamespace(busy=True),view=None)
        oled.thermal_test_hold=True;oled.next_query=oled.render_at=0.
        oled._render=lambda now:oled.stream.request(b'\x55'*8192,True)
        oled._held_keys=lambda now:None
        return p,oled

    def test_r26_without_uart_window_retains_quiet_home(self):
        p,oled=self.fixture(False)
        oled._tick(10.)
        self.assertEqual(oled.get_status(10.)['oled_transport_mode'],'legacy_quiet')
        self.assertFalse(any(name=='inventure_oled_burst' for name,_,_ in p.mcu.calls))

    def test_matched_window_is_queried_cached_and_logged_without_more_mcu_traffic(self):
        p,oled=self.fixture(True)
        with self.assertNoLogs(level='ERROR'):oled._tick(10.)
        self.assertTrue(any(name=='inventure_uart_window_query' for name,_,_ in p.mcu.calls))
        packet=dict(windows=100,peak_ticks=2200,rx=2,shutdowns=0,active=0)
        oled._uart_window_response(dict(packet,**{'#receive_time':10.}));p.reactor.flush()
        self.assertEqual(oled.get_status(10.)['uart_priority_window'],packet)
        sent=len(p.mcu.calls)
        with self.assertLogs(level='ERROR') as logs:oled._transport_snapshot()
        self.assertIn('"rx": 2',logs.output[0]);self.assertEqual(len(p.mcu.calls),sent)
        response=[];oled._transport_status(types.SimpleNamespace(respond_info=response.append))
        self.assertIn('uart_priority_window',response[0]);self.assertEqual(len(p.mcu.calls),sent)

    def test_installer_requires_window_on_r27(self):
        from test_install_new_host import installer,r24_mcu_identity
        d=r24_mcu_identity();d['mcu_constants'].update(INVENTURE_MCU_RELEASE='RC8-CONTROL-R7-HOME-R27-UART-OLED',
            INVENTURE_OLED_COEXISTENCE_ABI=1,INVENTURE_UART_TIMER_WINDOW_ABI=1,
            INVENTURE_FACTORY_INFO_ABI=1,INVENTURE_FACTORY_COUNTER_ABI=1)
        self.assertEqual(installer.require_mcu(d)['mcu_release'],'RC8-CONTROL-R7-HOME-R27-UART-OLED')
        d['mcu_constants'].pop('INVENTURE_UART_TIMER_WINDOW_ABI')
        with self.assertRaisesRegex(ValueError,'priority-window'):installer.require_mcu(d)

    def test_exact_r26_upgrade_changes_only_adapter_and_manifest_and_reverses(self):
        from test_install_new_host import installer
        with tempfile.TemporaryDirectory() as temp,bootstrap.procedure_profile('operator-home-r24'):
            host=Path(temp)/'host'
            _,before,meta=bootstrap._plan(ROOT,information_revision='r26')
            self.assertEqual(sha256(bootstrap._metadata_bytes(meta)).hexdigest(),
                '682f20cf7fb0013e484760b61676a10cf615d4540cdbd1932ab62e6a6335485d')
            _,after,_=bootstrap._plan(ROOT)
            changed={name for name in before.keys()|after.keys() if before.get(name)!=after.get(name)}
            self.assertEqual(changed,{'klippy/extras/inventure_communication_guard.py'})
            bootstrap._write_files(host,before);marker=host/bootstrap.MANIFEST_NAME
            marker.write_bytes(bootstrap._metadata_bytes(meta));marker.chmod(0o644)
            plan=bootstrap.prepare_runtime_update(host)
            self.assertEqual({p.relative_to(host).as_posix() for p in plan['files']},
                {bootstrap.MANIFEST_NAME,'klippy/extras/inventure_communication_guard.py'})
            for path,(old,new) in plan['files'].items():installer.updater.atomic_config_write(path,new,create=old is None)
            self.assertEqual(bootstrap.verify_host(host)['status'],'VERIFIED_HOST')
            for path,(old,new) in plan['files'].items():path.write_bytes(old)
            self.assertEqual(bootstrap.verify_host(host,allow_legacy=True)['status'],'VERIFIED_LEGACY_HOST')

    def test_sampler_body_and_all_motion_sources_are_preserved(self):
        from apply_inventure_transport_r26 import overlay as old_overlay
        from apply_inventure_uart_r27 import overlay
        with tempfile.TemporaryDirectory() as temp:
            old=old_overlay(Path(temp)/'old');new=overlay(Path(temp)/'new')
            for path in old.iterdir():
                if path.suffix not in ('.c','.inc','.h') or path.name in ('inventure_board.c','serial.c'):continue
                self.assertEqual((new/path.name).read_text().replace('RC8-CONTROL-R7-HOME-R27-UART-OLED','RC8-CONTROL-R7-HOME-R26-TRANSPORT-OLED'),path.read_text(),path.name)
            text=(new/'inventure_board.c').read_text().replace('RC8-CONTROL-R7-HOME-R27-UART-OLED','RC8-CONTROL-R7-HOME-R26-TRANSPORT-OLED')
            text=text.replace('static uint_fast8_t noinline sample_event_body(struct timer *t) {','static uint_fast8_t sample_event(struct timer *t) {')
            text=text.replace('\n#include "inventure_sampler_uart.inc"\n\nvoid inventure_board_init(void) {','\nvoid inventure_board_init(void) {')
            self.assertEqual(text,(old/'inventure_board.c').read_text())
