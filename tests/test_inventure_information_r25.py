"""OEM instruction oracle, Flash fault recovery and durable Info accounting."""
import ctypes
from hashlib import sha256
import importlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'restored/information_r25'
package = types.ModuleType('information_r25_test')
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package
store_module = importlib.import_module(package.__name__+'.inventure_info_store')
counter_module = importlib.import_module(package.__name__+'.inventure_counter_sync')
runtime = importlib.import_module(package.__name__+'.inventure_runtime')
service_module = importlib.import_module(package.__name__+'.inventure_information')
host_marker = types.ModuleType(package.__name__+'.inventure_mcu')
host_marker.HOST_REVISION = 'RC8-CONTROL-R7-HOMING-R38.6'
sys.modules[host_marker.__name__] = host_marker


class NativeFactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        code = '''
#include "inventure_factory_info.h"
static uint8_t memory[32768];static int calls,cut,torn,busy;
static int begin(void *c){(void)c;return !busy;}static void end(void*c){(void)c;}
static int erase(void*c,unsigned bank){(void)c;calls++;if(calls==cut){if(torn)memset(memory+bank*16384,255,11);return 0;}memset(memory+bank*16384,255,16384);return 1;}
static int program(void*c,unsigned bank,unsigned off,uint16_t value){(void)c;calls++;if(calls==cut){if(torn){memory[bank*16384+off]&=value|0xaa;memory[bank*16384+off+1]&=(value>>8)|0xaa;}return 0;}memory[bank*16384+off]&=value;memory[bank*16384+off+1]&=value>>8;return 1;}
void load(const uint8_t *p){memcpy(memory,p,32768);}void readback(uint8_t*p){memcpy(p,memory,32768);}
uint32_t crc(unsigned bank){struct info_bank b;info_decode(memory+bank*16384,&b);return info_settings_crc(&b);}
int write_counter(unsigned source,uint32_t expected,uint32_t target,uint32_t crc,int failure,int partial,int blocked){
calls=0;cut=failure;torn=partial;busy=blocked;struct info_io io={{memory,memory+16384},begin,end,erase,program,0};return info_commit(&io,source,expected,target,crc);}
int operations(void){return calls;}
int identity(const uint8_t *otp,const uint8_t *locks,uint32_t *serial,int *hardware){return info_identity(otp,locks,serial,hardware);}
'''
        (root/'test.c').write_text(code)
        compiled=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fPIC','-shared','-I',str(SOURCE),
                        str(root/'test.c'),'-o',str(root/'test.so')],capture_output=True,text=True)
        if compiled.returncode:raise AssertionError(compiled.stderr)
        cls.lib = ctypes.CDLL(str(root/'test.so'))
        cls.lib.crc.restype = ctypes.c_uint32
        cls.lib.write_counter.argtypes = [ctypes.c_uint32]*4+[ctypes.c_int]*3

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def image(self, minutes=100):
        from test_inventure_leveling_oled_r23 import package as installed
        codec = importlib.import_module(installed.__name__+'.inventure_nvm_core')
        records = {i:bytes((i+j)&255 for j in range(n)) for i,n in enumerate(codec.LENGTHS)}
        records[0] = struct.pack('<I',minutes)
        return codec,records,codec.encode_bank(records)+b'\xff'*16384

    def read(self):
        result=ctypes.create_string_buffer(32768);self.lib.readback(result);return result.raw

    def test_every_erase_and_program_failure_recovers_without_losing_any_setting(self):
        codec,records,image=self.image()
        self.lib.load(image);crc=self.lib.crc(0)
        self.assertEqual(self.lib.write_counter(0,100,102,crc,0,0,0),0)
        count=self.lib.operations()
        for partial in (0,1):
            for cut in range(1,count+1):
                self.lib.load(image)
                self.assertEqual(self.lib.write_counter(0,100,102,crc,cut,partial,0),4,(cut,partial))
                self.assertIn(self.lib.write_counter(0,100,102,crc,0,0,0),(0,1),(cut,partial))
                expected=dict(records);expected[0]=struct.pack('<I',102)
                self.assertEqual(codec.decode_dump(self.read())['records'],expected,(cut,partial))
                self.assertEqual(self.lib.write_counter(0,100,102,crc,0,0,0),1)

    def test_busy_stale_settings_and_counter_regression_make_no_flash_write(self):
        codec,records,image=self.image();self.lib.load(image);crc=self.lib.crc(0)
        for expected,target,checksum,blocked,result in ((100,101,crc,1,2),(99,101,crc,0,3),
                (100,101,crc^1,0,3),(100,99,crc,0,3),(100,0xffffffff,crc,0,3)):
            self.lib.load(image)
            self.assertEqual(self.lib.write_counter(0,expected,target,checksum,0,0,blocked),result)
            self.assertEqual(self.read(),image)
            self.assertEqual(self.lib.operations(),0)

    def test_otp_matches_the_original_arm_reader_and_preserves_oem_byte_order(self):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_LR, UC_ARM_REG_SP
        proof=json.loads((SOURCE/'oem_information_evidence.json').read_text())
        self.assertEqual(proof['application_sha256'],'9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268')
        uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB)
        uc.mem_map(0x08010000,65536);uc.mem_map(0x1fff7000,4096);uc.mem_map(0x20000000,8192)
        uc.mem_write(int(proof['otp_reader_base'],16),bytes.fromhex(proof['otp_reader_hex']))
        otp=bytes((i*37+11)&255 for i in range(512))
        uc.mem_write(0x1fff7800,otp)
        for first in range(16):
            locks=bytes(0xff if i<first else 0 for i in range(16))
            uc.mem_write(0x1fff7a00,locks)
            uc.reg_write(UC_ARM_REG_SP,0x20001f00);uc.reg_write(UC_ARM_REG_LR,0x0801ff01)
            uc.reg_write(UC_ARM_REG_R0,0x20001000);uc.reg_write(UC_ARM_REG_R1,0)
            uc.emu_start(int(proof['otp_reader_entry'],16)|1,0x0801ff00,count=2000)
            serial=ctypes.c_uint32();hardware=ctypes.c_int()
            valid=self.lib.identity(otp,locks,ctypes.byref(serial),ctypes.byref(hardware))
            self.assertEqual(valid,uc.reg_read(UC_ARM_REG_R0))
            if valid:
                raw=bytes(uc.mem_read(0x20001000,5))
                self.assertEqual(serial.value,int.from_bytes(raw[:4],'big'))
                self.assertEqual(hardware.value,raw[4]&15 if 0xe0<=raw[4]<=0xef else -1)


class DurableInformationTests(unittest.TestCase):
    def test_a_checkpoint_wake_during_a_yielded_query_does_not_start_a_second_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=service_module.InventureInformation.__new__(service_module.InventureInformation)
            owner.store=store_module.InfoStore(Path(directory)/'info.json')
            owner.store.bind_identity('12345678'*3,'Z1234ABCD',9)
            owner.journal=counter_module.CounterJournal(owner.store);owner.sync=counter_module.CounterSync()
            owner.ready=owner.verified_identity=True;owner.synchronizing=False
            owner.printer=types.SimpleNamespace(is_shutdown=lambda:False)
            owner.reactor=types.SimpleNamespace(monotonic=lambda:10.,NEVER=1.e30)
            owner.executor=types.SimpleNamespace(submit=lambda fn,*args:fn(*args))
            owner.blocker=lambda now:None
            calls=[]
            def query():
                calls.append('query')
                self.assertEqual(owner.synchronize(10.),10.+runtime.DRAIN_POLL_SECONDS)
                return dict(serial=0x1234abcd,state=1,source=0,print_minutes=100,settings_crc=42)
            owner.transport=types.SimpleNamespace(query=query)
            owner.synchronize(10.)
            self.assertEqual(calls,['query'])
            self.assertFalse(owner.synchronizing)
            self.assertEqual(owner.sync.status,'CURRENT')

    def test_observers_call_original_commands_once_and_count_only_identified_file_printing(self):
        now=[0.];calls=[];position=types.SimpleNamespace(last_position=[0.,0.,0.,0.])
        stats=types.SimpleNamespace(state='standby')
        stats.get_status=lambda eventtime:dict(state=stats.state)
        stats.note_pause=lambda:setattr(stats,'state','paused')
        stats.note_complete=lambda:setattr(stats,'state','complete')
        stats.reset=lambda:setattr(stats,'state','standby')
        def original(command):calls.append(command);position.last_position=list(command)
        commands={'G0':original,'G1':original}
        def register(name, command):
            previous=commands.pop(name,None)
            if command is not None:commands[name]=command
            return previous
        objects=dict(gcode_move=position,print_stats=stats,virtual_sdcard=types.SimpleNamespace(is_active=lambda:True),
                     toolhead=types.SimpleNamespace(check_busy=lambda eventtime:(eventtime,eventtime,True)))
        owner=service_module.InventureInformation.__new__(service_module.InventureInformation)
        owner.printer=types.SimpleNamespace(lookup_object=lambda name,default=None:objects.get(name,default))
        owner.gcode=types.SimpleNamespace(register_command=register)
        updates=[]
        owner.reactor=types.SimpleNamespace(monotonic=lambda:now[0],update_timer=lambda *a:updates.append(a),NOW=0.)
        owner.clock=runtime.PrintClock();owner.runtime_timer=None;owner.verified_identity=True
        owner.observers_installed=owner.job_deposited=owner.job_completed=False
        owner.install_observers();owner.install_observers()
        commands['G1']([1,0,0,1]);self.assertIsNone(owner.clock.since)
        stats.state='printing';now[0]=10.;commands['G1']([1,0,0,2])
        self.assertIsNone(owner.clock.since) # Extrusion-only maintenance is not deposition.
        commands['G1']([2,0,0,3]);self.assertEqual(owner.clock.since,10.)
        commands['G1']([2.5,0,0,3.5]);self.assertEqual(len(updates),1)
        now[0]=20.;stats.note_pause();owner.clock.drain(20.,objects['toolhead'])
        now[0]=50.;commands['G1']([3,0,0,4]);self.assertEqual(owner.clock.snapshot(50.),(10.,0))
        stats.state='printing';commands['G0']([4,0,0,5]);now[0]=60.
        stats.note_complete();stats.note_complete();owner.clock.drain(60.,objects['toolhead'])
        self.assertEqual(owner.clock.snapshot(100.),(20.,1))
        self.assertEqual(len(calls),6)

    def test_new_transport_rejects_old_wrong_kind_and_wrong_token_responses(self):
        class Completion:
            result=None
            def wait(self, timeout):return self.result
        reactor=types.SimpleNamespace(monotonic=lambda:100.,completion=Completion,
            async_complete=lambda completion, packet:setattr(completion,'result',packet))
        callbacks={};sent=[]
        def command(fmt):
            def send(args):
                sent.append((fmt,args))
                callback=callbacks[service_module.INFO_FORMAT]
                callback(dict(token=args[0]-1, valid=1, **{'#receive_time':100.}))
                callback(dict(token=args[0], valid=1, **{'#receive_time':99.}))
                callback(dict(token=args[0], valid=1, serial=123, **{'#receive_time':100.}))
            return types.SimpleNamespace(send=send)
        mcu=types.SimpleNamespace(lookup_command=command,
            register_serial_response=lambda callback,fmt:callbacks.update({fmt:callback}))
        transport=service_module.Transport(mcu,reactor)
        self.assertEqual(transport.query('identity')['serial'],123)
        self.assertEqual(len(sent),1)
        self.assertIsNone(transport.pending)

    def test_identity_and_printing_time_survive_restart_and_refuse_another_printer(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'info.json';store=store_module.InfoStore(path)
            store.bind_identity('12345678'*3,'Z1234ABCD',9)
            store.update(lambda data:data['counters'].update(print_seconds=123.75,completed_jobs=2))
            restarted=store_module.InfoStore(path)
            self.assertEqual(restarted.snapshot(),store.snapshot())
            with self.assertRaisesRegex(ValueError,'another MCU'):
                restarted.bind_identity('87654321'*3,'Z87654321',8)
            self.assertEqual(store_module.InfoStore(path).snapshot(),store.snapshot())
            path.unlink()
            self.assertEqual(store_module.InfoStore(path).snapshot(),store.snapshot())

    def test_counter_ack_loss_and_host_receipt_failure_never_double_add_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            store=store_module.InfoStore(Path(directory)/'info.json');journal=counter_module.CounterJournal(store)
            sync=counter_module.CounterSync();state=dict(serial=0x1234abcd,state=1,source=0,print_minutes=100,settings_crc=42)
            snapshot=dict(identity=dict(machine_id='uid'),counters=dict(print_seconds=0.))
            def query():return dict(state)
            def commit(args):
                serial,source,expected,target,crc=args
                if state['print_minutes']==expected:state.update(source=1-source,print_minutes=target)
                raise TimeoutError('Lost reply after Flash commit')
            sync.run(snapshot,journal.load,journal.save,query,commit,runtime=True)
            snapshot['counters']['print_seconds']=125.
            with self.assertRaises(counter_module.CounterRecoveryError):
                sync.run(snapshot,journal.load,journal.save,query,commit,runtime=True)
            self.assertEqual(state['print_minutes'],102)
            reboot=counter_module.CounterSync()
            reboot.run(snapshot,journal.load,journal.save,query,lambda args:dict(status=1),runtime=True)
            self.assertEqual(reboot.committed_seconds,120)
            self.assertEqual(journal.load()['pending'],None)
            self.assertEqual(state['print_minutes']*60+125-reboot.committed_seconds,6125)

    def test_recovery_when_the_mcu_committed_but_only_one_host_receipt_copy_was_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            store=store_module.InfoStore(Path(directory)/'info.json');journal=counter_module.CounterJournal(store)
            sync=counter_module.CounterSync();state=dict(serial=0x1234abcd,state=1,source=0,print_minutes=100,settings_crc=42)
            snapshot=dict(identity=dict(machine_id='uid'),counters=dict(print_seconds=0.))
            def query():return dict(state)
            def commit(args):state.update(source=1-args[1],print_minutes=args[3]);return dict(status=0)
            sync.run(snapshot,journal.load,journal.save,query,commit,runtime=True)
            atomic=store._atomic_replace
            def fail_after_first_receipt(path,raw):
                atomic(path,raw)
                data=json.loads(raw)['data']
                if path==journal.path and data['committed'] and data['pending'] is None:
                    raise OSError('Power failed after first receipt rename')
            snapshot['counters']['print_seconds']=125.
            with patch.object(journal,'atomic_replace',side_effect=fail_after_first_receipt):
                with self.assertRaises(counter_module.CounterRecoveryError):
                    sync.run(snapshot,journal.load,journal.save,query,commit,runtime=True)
            restarted=counter_module.CounterSync();commands=[]
            restarted.run(snapshot,lambda:journal.load_for_sync(True),journal.save,query,
                          lambda args:commands.append(args) or dict(status=0),runtime=True)
            self.assertEqual(commands,[])
            self.assertEqual(state['print_minutes'],102)
            self.assertEqual(restarted.committed_seconds,120)
            self.assertEqual(journal._read(journal.path),journal._read(journal.checkpoint))

    def test_absolute_host_checkpoint_retry_does_not_double_elapsed_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=service_module.InventureInformation.__new__(service_module.InventureInformation)
            owner.store=store_module.InfoStore(Path(directory)/'info.json')
            owner.printer=types.SimpleNamespace(lookup_object=lambda name,default=None:None)
            owner.reactor=types.SimpleNamespace(monotonic=lambda:10.,NEVER=1.e30,NOW=0.)
            owner.executor=types.SimpleNamespace(submit=lambda fn,*args:fn(*args))
            owner.clock=runtime.PrintClock();owner.clock.start(0.,None)
            owner.saved=(0.,0);owner.target=owner.error=None;owner.writing=owner.ready=False;owner.transport=None
            atomic=owner.store._atomic_replace
            def fail(path,raw):atomic(path,raw);raise OSError('Checkpoint confirmation lost')
            with patch.object(owner.store,'_atomic_replace',side_effect=fail):owner.checkpoint(10.)
            self.assertEqual(owner.store.load()['counters']['print_seconds'],10.)
            owner.checkpoint(10.)
            self.assertEqual(owner.store.snapshot()['counters']['print_seconds'],10.)
            self.assertEqual(owner.saved,(10.,0))

    def test_data_corruption_is_reported_instead_of_resetting_lifetime_to_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'info.json';store=store_module.InfoStore(path)
            store.update(lambda data:data['counters'].update(print_seconds=200))
            raw=json.loads(path.read_text());raw['data']['counters']['print_seconds']=0
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'checksum'):
                store_module.InfoStore(path)

    def test_info_render_uses_cached_identity_and_does_no_query_or_disk_read(self):
        with tempfile.TemporaryDirectory() as directory:
            store=store_module.InfoStore(Path(directory)/'info.json')
            store.bind_identity('12345678'*3,'Z1234ABCD',9)
            store.update(lambda d:d['counters'].update(print_seconds=125.,completed_jobs=2))
            owner=service_module.InventureInformation.__new__(service_module.InventureInformation)
            owner.store=store;owner.clock=runtime.PrintClock();owner.saved=(0.,0)
            owner.sync=counter_module.CounterSync();owner.sync.minutes=102;owner.sync.committed_seconds=120
            owner.verified_identity=True;owner.board=types.SimpleNamespace(mcu_release='RC8-CONTROL-R7-HOME-R25-INFO-OLED')
            owner.error=owner.wait_reason=None
            class Locked:
                def __enter__(self):raise AssertionError('Paint waited for filesystem lock')
                def __exit__(self,*args):pass
            with patch.object(store,'load',side_effect=AssertionError('Paint read disk')),patch.object(store,'lock',Locked()):
                status=owner.get_status(10.)
            self.assertEqual(status['serial_number'],'Z1234ABCD')
            self.assertEqual(status['hardware_version'],9)
            self.assertEqual(status['total_print_seconds'],6125.)
            self.assertEqual(status['total_printing_time'],'1 h 42 min')

    def test_clock_excludes_heating_pause_and_service_and_drains_queued_print_moves(self):
        clock=runtime.PrintClock();toolhead=types.SimpleNamespace(check_busy=lambda now:(8.,10.,True))
        self.assertEqual(clock.snapshot(100),(0.,0))
        clock.start(100,toolhead);clock.request_stop(120);clock.drain(123,toolhead)
        self.assertEqual(clock.snapshot(150),(21.,0))
        clock.start(200,toolhead);clock.stop(210)
        self.assertEqual(clock.snapshot(1000),(31.,0))
        self.assertFalse(runtime.deposited([0,0,0,0],[0,0,0,20]))
        self.assertTrue(runtime.deposited([0,0,0,0],[1,0,0,1]))


class FlashIsolationTests(unittest.TestCase):
    def test_actual_mcu_idle_gate_refuses_every_active_owner_and_held_drives(self):
        text=(SOURCE/'inventure_information.inc').read_text()
        function=text[text.index('static uint8_t info_idle'):text.index('static int info_target_supported')]
        scalars=('fault','shutdown_value','armed_value','control_latched','control_mode','held_service_pending',
                 'held_service_live','native_z_guard_active','native_z_refine_held','native_z_refine_conflict',
                 'zh_value','xy_value','heat_value','fan_value','transport_live')
        settings=list(scalars)+['gpio.ODR']+[x+'.active' for x in ('cm','fs','sl')]+['motor_test.running']
        settings += [f'{name}[{i}]' for name in ('feeder_running','locks_on','locks_pulling','assist_pending') for i in range(2)]
        settings += ['control_heater_duty[%d]'%i for i in range(4)]+['co[%d].duty'%i for i in range(10)]
        prefix='''#include <stdint.h>
#define BIT(n) (1u<<(n))
#define CT_OFF 0
struct {unsigned ODR;} gpio;
#define GPIOE (&gpio)
struct {unsigned active;} cm,fs,sl;struct{unsigned running;}motor_test;
struct{unsigned duty;}co[10];
static unsigned feeder_running[2],locks_on[2],locks_pulling[2],assist_pending[2],control_heater_duty[4];
static int queue_idle=1;
static int inventure_native_stepper_idle(void){return queue_idle;}
'''
        prefix+='static unsigned '+','.join(scalars)+';\n'
        for name,value in (('sched_is_shutdown','shutdown_value'),('inventure_is_armed','armed_value'),
                           ('zh_active','zh_value'),('xy_seek_busy','xy_value'),('t0_heat_busy','heat_value'),('fan_manual_active','fan_value')):
            prefix+='static int %s(void){return %s;}\n'%(name,value)
        code=prefix+function+'\nint check(unsigned index){\n'
        code+=''.join(name+'=0;\n' for name in settings)
        code+='queue_idle=1;switch(index){\n'
        code+=''.join('case %d:%s=%du;break;\n'%(i+1,name,1024 if name=='gpio.ODR' else 1) for i,name in enumerate(settings))
        code+='case 999:queue_idle=0;break;}return info_idle();}\n'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'gate.c').write_text(code)
            result=subprocess.run(['cc','-Wall','-Wextra','-Werror','-shared','-fPIC',str(root/'gate.c'),'-o',str(root/'gate.so')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            lib=ctypes.CDLL(str(root/'gate.so'))
            self.assertEqual(lib.check(0),1)
            for i,name in enumerate(settings):self.assertEqual(lib.check(i+1),0,name)
            self.assertEqual(lib.check(999),0)

    def test_flash_scheduler_helper_preserves_callbacks_and_relative_deadlines_at_wrap(self):
        sys.path.insert(0,str(ROOT/'ci'))
        from apply_inventure_information_r25 import patch_scheduler
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'src').mkdir();(root/'src/sched.c').write_text('')
            patch_scheduler(root)
            functions=(root/'src/sched.c').read_text()
            prefix='''#include <stdint.h>
struct timer{uint32_t waketime;struct timer*next;void*func;};
static struct timer sentinel_timer,first,periodic_timer;
static struct {struct timer*timer_list;}SchedStatus;
static uint32_t clock;static int kicks;
static unsigned timer_from_us(unsigned n){return n*120;}
static uint32_t timer_read_time(void){return clock;}
static int sched_is_shutdown(void){return 0;}
static void timer_kick(void){kicks++;}
'''
            suffix='''
int exercise(uint32_t started,uint32_t elapsed){
first=(struct timer){started+2400,&periodic_timer,(void*)1};
periodic_timer=(struct timer){started+12000000,&sentinel_timer,(void*)2};
sentinel_timer=(struct timer){periodic_timer.waketime+0x80000000u,&periodic_timer,(void*)3};
SchedStatus.timer_list=&first;clock=started;kicks=0;
if(!sched_inventure_flash_ready(started))return 1;
clock+=elapsed;sched_inventure_flash_rebase(started);
if(first.waketime!=clock+2400||periodic_timer.waketime!=clock+12000000
 ||sentinel_timer.waketime!=periodic_timer.waketime+0x80000000u)return 2;
if(first.func!=(void*)1||periodic_timer.func!=(void*)2||sentinel_timer.func!=(void*)3||kicks!=1)return 3;
return 0;}
'''
            (root/'scheduler.c').write_text(prefix+functions+suffix)
            result=subprocess.run(['cc','-Wall','-Wextra','-Werror','-fPIC','-shared',str(root/'scheduler.c'),'-o',str(root/'scheduler.so')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            lib=ctypes.CDLL(str(root/'scheduler.so'));lib.exercise.argtypes=[ctypes.c_uint32]*2
            for start in (0,1,0xfffff000):
                for elapsed in (1,12000,120000000,240000000):self.assertEqual(lib.exercise(start,elapsed),0)


if __name__=='__main__':unittest.main()
