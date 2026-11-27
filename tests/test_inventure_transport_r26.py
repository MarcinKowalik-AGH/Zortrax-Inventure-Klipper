"""Exercise the actual F207 DMA worker, rather than only its RLE decoder."""
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ci'))
from apply_inventure_transport_r26 import overlay
from apply_inventure_information_r25 import overlay as old_overlay
from prepare_kit import prepare
import inventure_bootstrap as bootstrap
import types

class PairedHostR26Tests(unittest.TestCase):
    def test_matched_mcu_keeps_home_animation_and_reports_uart_counters(self):
        from test_inventure_communication_guard import GuardedOLED,guard
        from test_orange_pi_oled_fast import Printer,Config,ready_stream
        p=Printer();original=p.mcu.get_constants
        p.mcu.get_constants=lambda:dict(original(),INVENTURE_OLED_COEXISTENCE_ABI=1,INVENTURE_UART_TIMER_WINDOW_ABI=1)
        oled=GuardedOLED(Config(p));oled._build_config();oled.connected=True;oled.stream=ready_stream()
        p.board.zortrax=types.SimpleNamespace(home_ui=types.SimpleNamespace(busy=True),view=None)
        oled.thermal_test_hold=True;oled.next_query=oled.render_at=0.
        oled._render=lambda now:oled.stream.request(b'\x55'*8192,True)
        oled._held_keys=lambda now:None
        oled._quiet_homing_begin(object())
        with self.assertNoLogs(level='ERROR'):
            oled._tick(10.)
        names=[name for name,_,_ in p.mcu.calls]
        self.assertIn('inventure_oled_burst',names)
        self.assertIn('inventure_oled_coexist_query',names)
        self.assertTrue(oled.thermal_test_hold)
        state=oled.get_status(10.)
        self.assertEqual(state['oled_transport_mode'],'cooperative_dma')
        self.assertFalse(state['home_pixels_paused'])
        packet=dict(irq_ticks=120,blocks=4,wire_bytes=128,errors=0,peak=4,
                    uart_overruns=1,uart_noise=2,uart_framing=3,uart_rx_overflows=0)
        oled._coexist_response(dict(packet,**{'#receive_time':10.}))
        p.reactor.flush()
        self.assertEqual(oled.get_status(10.)['transport_health'],packet)
        self.assertEqual(oled.get_status(10.)['communication_guard_release'],guard.RELEASE)

    def test_exact_rc4_upgrade_changes_only_communication_adapter_and_manifest(self):
        from test_install_new_host import installer
        with tempfile.TemporaryDirectory() as temp,bootstrap.procedure_profile('operator-home-r24'):
            host=Path(temp)/'host'
            _,old,meta=bootstrap._plan(ROOT,information_revision='rc4')
            self.assertEqual(sha256(bootstrap._metadata_bytes(meta)).hexdigest(),
                '6969c019d484f6d98800d47081098adf9ce086defb003e33942d545aa0b3cf87')
            _,new,_=bootstrap._plan(ROOT)
            self.assertEqual({n for n in old.keys()|new.keys() if old.get(n)!=new.get(n)},
                {'klippy/extras/inventure_communication_guard.py'})
            bootstrap._write_files(host,old);marker=host/bootstrap.MANIFEST_NAME
            marker.write_bytes(bootstrap._metadata_bytes(meta));marker.chmod(0o644)
            plan=bootstrap.prepare_runtime_update(host)
            self.assertEqual({p.relative_to(host).as_posix() for p in plan['files']},
                {bootstrap.MANIFEST_NAME,'klippy/extras/inventure_communication_guard.py'})
            for path,(before,after) in plan['files'].items():
                installer.updater.atomic_config_write(path,after,create=before is None)
            self.assertEqual(bootstrap.verify_host(host)['status'],'VERIFIED_HOST')
            for path,(before,after) in plan['files'].items():path.write_bytes(before)
            self.assertEqual(bootstrap.verify_host(host,allow_legacy=True)['status'],'VERIFIED_LEGACY_HOST')

    def test_installer_accepts_r26_only_with_the_paired_transport_capability(self):
        from test_install_new_host import installer,r24_mcu_identity
        d=r24_mcu_identity();d['mcu_constants'].update(INVENTURE_MCU_RELEASE='RC8-CONTROL-R7-HOME-R26-TRANSPORT-OLED',
            INVENTURE_OLED_COEXISTENCE_ABI=1,INVENTURE_FACTORY_INFO_ABI=1,INVENTURE_FACTORY_COUNTER_ABI=1)
        self.assertEqual(installer.require_mcu(d)['native_held_service_abi'],1)
        d['mcu_constants'].pop('INVENTURE_OLED_COEXISTENCE_ABI')
        with self.assertRaisesRegex(ValueError,'cooperative DMA'):installer.require_mcu(d)

class TransportR26Tests(unittest.TestCase):
    def test_actual_uart_frame_copy_keeps_rx_irqs_open_and_publishes_complete_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);kit=prepare(root/'kit')
            script='''import sys,json
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/scripts',sys.argv[2]+'/ci']
import build_firmware_on_mac as b
from apply_inventure_transport_r26 import patch_uart
root=Path(sys.argv[3]);r,old=b.extract_pinned(root,json.loads(b.HIST.read_text()));b.apply_rc7(r,old,json.loads(b.HIST.read_text()))
p=r/'src/generic/serial_irq.c';(root/'old.c').write_bytes(p.read_bytes());patch_uart(r);(root/'new.c').write_bytes(p.read_bytes())
'''
            result=subprocess.run([sys.executable,'-c',script,str(kit),str(ROOT),str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            import re
            for variant in ('new','old'):
                text=(root/(variant+'.c')).read_text()
                def function(signature):
                    start=text.index(signature);end=text.index('\n}',start)+2
                    return text[start:end]
                copy=function('void\nconsole_sendf(')
                copy,n=re.subn(r'transmit_buf\[\(head\s*\+\s*i\)\s*&\s*TX_RING_MASK\]\s*=\s*frame\[i\];',
                    'test_store((head+i)&TX_RING_MASK,frame[i]);',copy)
                self.assertEqual(n,1)
                code='''#include <stdint.h>
#include <stdarg.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#define TX_RING_SIZE 1024u
#define TX_RING_MASK 1023u
#define MESSAGE_MAX 64
typedef unsigned irqstatus_t;
static uint8_t transmit_buf[1024],expected[150000];
static volatile uint16_t transmit_head,transmit_tail,transmit_highwater;
static volatile uint32_t transmit_dropped;
static unsigned masked,pause_consumer,produced,consumed,uart_irqs,native_irqs;
struct command_encoder{unsigned len,seed;};
int serial_get_tx_byte(uint8_t *out);
static void drain(void){uint8_t byte;if(pause_consumer)return;while(!serial_get_tx_byte(&byte)){assert(consumed<produced);assert(byte==expected[consumed++]);}}
static irqstatus_t irq_save(void){unsigned old=masked;masked=1;return old;}
static void irq_restore(irqstatus_t old){masked=old;if(!masked)drain();}
static void serial_enable_tx_irq(void){}
static void test_store(unsigned index,uint8_t byte){assert(!masked);uart_irqs++;native_irqs++;drain();transmit_buf[index]=byte;}
static unsigned command_encode_and_frame(uint8_t*out,unsigned cap,const struct command_encoder *ce,va_list ap){
 (void)ap;assert(!masked && ce->len<=cap);for(unsigned i=0;i<ce->len;i++){out[i]=(ce->seed+i)&255;expected[produced++]=out[i];}return ce->len;
}
'''+function('int\nserial_get_tx_byte(')+'\n'+copy+'''
static void emit(const struct command_encoder *ce,...){va_list ap;va_start(ap,ce);console_sendf(ce,ap);va_end(ap);}
int main(void){
 transmit_head=transmit_tail=65520;
 for(unsigned i=0;i<2000;i++){struct command_encoder ce={1+i%64,i};emit(&ce);}
 assert(produced==consumed && !transmit_dropped && uart_irqs==produced && native_irqs==produced);
 produced=consumed=0;transmit_head=transmit_tail=0;pause_consumer=1;
 for(unsigned i=0;i<17;i++){struct command_encoder ce={64,i};emit(&ce);}
 assert(transmit_dropped==1 && (uint16_t)(transmit_head-transmit_tail)==1024);
 pause_consumer=0;drain();assert(consumed==1024 && !masked);
 puts("PASS_R26_UART_IRQ_PREEMPTION_2000_FRAMES_RING_WRAP_FULL_AND_ATOMIC_PUBLICATION");
}
'''
                source=root/(variant+'-test.c');source.write_text(code);binary=root/(variant+'-test')
                result=subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                result=subprocess.run([str(binary)],capture_output=True,text=True)
                if variant=='new':self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                else:
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('!masked',result.stderr)

    def test_actual_dma_worker_is_interruptible_and_cancellation_never_restarts_dma(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);kit=prepare(root/'kit');new=overlay(root/'new');old=old_overlay(root/'old')
            for name in ('inventure_board.c','inventure_control.inc','inventure_z_home.inc','inventure_held_service.inc'):
                before=(old/name).read_text().replace('RC8-CONTROL-R7-HOME-R25-INFO-OLED','RC8-CONTROL-R7-HOME-R26-TRANSPORT-OLED')
                self.assertEqual((new/name).read_text(),before,name)
            for source,expected in ((new,0),(old,1)):
                binary=root/(source.name+'-dma')
                command=['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-pointer-to-int-cast',
                    '-I',str(ROOT/'tests/mcu/mock'),'-I',str(source),'-I',str(kit/'mcu'),
                    str(ROOT/'tests/mcu/test_r26_oled_dma.c'),'-o',str(binary)]
                result=subprocess.run(command,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                result=subprocess.run([str(binary)],capture_output=True,text=True)
                if expected:
                    self.assertNotEqual(result.returncode,0,'Old MCU unexpectedly allowed IRQ preemption')
                    self.assertIn('!irq_depth',result.stderr)
                else:
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertIn('PASS_R26_F207_DMA',result.stdout)

if __name__=='__main__':unittest.main()
