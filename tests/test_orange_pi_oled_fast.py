"""Port M300 pacing to actual Inventure ABI and real host renderer/adapter."""
import importlib
import importlib.util
import ctypes
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from test_orange_pi_door_policy import adapter
from test_orange_pi_control import msgproto

ROOT=Path(__file__).resolve().parents[1]
t=importlib.import_module(adapter.__package__+'.inventure_oled_transport')
o=importlib.import_module(adapter.__package__+'.inventure_oled')
core=importlib.import_module(adapter.__package__+'.inventure_menu_core')
menu=importlib.import_module(adapter.__package__+'.inventure_oem_menu')


def decode(args):
    dc,encoding,length,data=args
    assert 0<len(data)<=32 and 0<length<=256
    if not encoding:
        assert len(data)==length
        return bytes(data)
    result=bytearray();offset=0
    while offset<len(data):
        code=data[offset];offset+=1;count=(code&127)+1
        if code&128:
            result.extend(bytes([data[offset]])*count);offset+=1
        else:
            result.extend(data[offset:offset+count]);offset+=count
    assert len(result)==length
    return bytes(result)


class Panel:
    """SSD1322 addressing model: execute the actual burst operations."""
    def __init__(self,wire=bytes(8192)):
        self.ram=bytearray(wire);self.command=None;self.cols=(0,63);self.rows=(0,63)
        self.x=self.y=0
    def apply(self,operation):
        kind,token,args=operation
        if kind!='burst':return
        dc=args[0];data=decode(args)
        if not dc:
            self.command=data[0]
            if self.command==0x5c:self.x,self.y=self.cols[0],self.rows[0]
        elif self.command==0x15:self.cols=(data[0]-0x1c,data[1]-0x1c)
        elif self.command==0x75:self.rows=tuple(data)
        elif self.command==0x5c:
            # The window's column is two bytes, not one. Retain half-column.
            for value in data:
                if not hasattr(self,'half'):self.half=0
                self.ram[self.y*128+self.x*2+self.half]=value;self.half+=1
                if self.half==2:
                    self.half=0;self.x+=1
                    if self.x>self.cols[1]:
                        self.x=self.cols[0];self.y+=1
                        if self.y>self.rows[1]:self.y=self.rows[0]


def ready_stream(wire=bytes(8192),**kw):
    s=t.FastOledStream(**kw);s.start(0.)
    s.operations=None;s.pending=None;s.inflight.clear();s.ready_after=0.
    s.previous=s.latest=s.target=s.last_frame_bytes=wire
    s.phase='ready';s.generation_frames=1;s.completed_frames=1
    s.display_mode=s.requested_mode='awake';s._initializing=False
    return s


def run_frame(stream,now=1.,panel=None):
    panel=Panel(stream.previous or bytes(8192)) if panel is None else panel
    operations=[]
    frames=stream.completed_frames
    for _ in range(20000):
        op=stream.step(now)
        if op is not None:
            operations.append(op);panel.apply(op)
            now+=.0001
            assert stream.ack(op[1],now,stream.generation)
        elif stream.operations is None and not stream.inflight and (
                stream.completed_frames>frames or stream.previous==stream.latest):
            return now,panel,operations
        else:
            now=max(now+.0001,stream.next_wakeup(now))
    raise AssertionError('OLED stream did not finish')


class FastTransportTests(unittest.TestCase):
    def test_actual_compiled_MCU_decoder_accepts_every_host_packet(self):
        source=(ROOT/'service_mcu/control_r6/SOURCE_MCU/inventure_panel.c').read_text()
        body=source.split('oled_fast_decode(',1)[1].split('\nstatic void\noled_fast_fail',1)[0]
        c='#include <stdint.h>\n#include <string.h>\n#define OLED_FAST_WIRE 256\nint decode('+body
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'decode.c').write_text(c)
            subprocess.run(['gcc','-std=c11','-shared','-fPIC','-O2',str(path/'decode.c'),'-o',str(path/'decode.so')],check=True,capture_output=True)
            library=ctypes.CDLL(str(path/'decode.so'))
            fn=library.decode;fn.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint]
            rnd=random.Random(44)
            patterns=[bytes(8192),b'\xff'*8192,bytes(range(256))*32]
            patterns += [bytes([rnd.randrange(256)])*rnd.randrange(1,500)+bytes(range(31)) for _ in range(40)]
            patterns += [bytes(rnd.randrange(256) for _ in range(8192))]
            for data in patterns:
                actual=bytearray()
                for _,args,_ in t.fast_blocks(data):
                    dc,encoding,length,packed=args;out=ctypes.create_string_buffer(258)
                    self.assertEqual(fn(out,length,encoding,packed,len(packed)),1)
                    actual.extend(out.raw[:length]);self.assertEqual(out.raw[256:],b'\x00\x00')
                self.assertEqual(actual,data)

    def test_byte_budget_covers_real_R7_dictionary_and_native_envelopes(self):
        protocol=json.loads((ROOT/'tests/fixtures/inventure_oled_protocol.json').read_text())
        parser=msgproto.MessageParser();parser.process_identify(json.dumps(protocol).encode(),decompress=False)
        burst=parser.lookup_command(next(f for f in protocol['commands'] if f.startswith('inventure_oled_burst ')))
        ack=parser.lookup_command(next(iter(protocol['responses'])))
        for hi,lo,token in ((0,1,1),(0x7fffffff,0xffffffff,4096),(0xffffffff,0xffffffff,0xffffffff)):
            for _,args,_ in t.fast_blocks(bytes(range(256))*2):
                sent=len(burst.encode([hi,lo,token,*args]))
                received=len(ack.encode([hi,lo,token,0]))
                self.assertLessEqual(sent+received+2*msgproto.MESSAGE_MIN,len(args[3])+52)
                self.assertLessEqual(sent,msgproto.MESSAGE_PAYLOAD_MAX)
    def test_actual_modules_import_complete_transport_and_sleep_contract(self):
        self.assertTrue(hasattr(t,'FastOledStream'))
        self.assertEqual(len(o.sleep_wire()),8192)
        self.assertEqual(t.FastOledStream().window,4)

    def test_packbits_matches_MCU_bounds_and_random_full_byte_values(self):
        rnd=random.Random(104)
        patterns=[bytes(8192),b'\xff'*8192,bytes(range(256))*32]
        patterns += [bytes(rnd.randrange(256) for _ in range(size)) for size in (1,31,32,33,255,256,257,8192)]
        patterns += [bytes([rnd.randrange(256)])*rnd.randrange(1,600)+bytes(range(32)) for _ in range(50)]
        for data in patterns:
            self.assertEqual(b''.join(decode(args) for _,args,_ in t.fast_blocks(data)),data)

    def test_optimized_windows_reconstruct_exact_frame_and_never_cost_more(self):
        rnd=random.Random(11)
        before=bytes(8192)
        for _ in range(35):
            after=bytearray(before)
            for n in range(35):after[rnd.randrange(8192)]=rnd.randrange(256)
            regions=t.diff_regions(before,after)
            original=tuple(op for w in regions for op in t.fast_frame_operations(after,*w))
            optimized=t.optimized_frame_operations(before,bytes(after),regions)
            self.assertLessEqual(len(optimized),len(original))
            self.assertLessEqual(sum(a[2] for _,a,_ in optimized),sum(a[2] for _,a,_ in original))
            self.assertLessEqual(sum(len(a[3])+36 for _,a,_ in optimized),sum(len(a[3])+36 for _,a,_ in original))
            p=Panel(before)
            for index,(kind,args,delay) in enumerate(optimized):p.apply((kind,index,args))
            self.assertEqual(p.ram,after);before=bytes(after)

    def test_real_OEM_menu_cursor_frames_are_lossless(self):
        m=menu.OEMMenu('en',root_page='launcher',bench=True,operational=True)
        for page in ('root','maintenance','material','settings','information','service_tests'):
            m.page=page;m.selected=0;m.generation+=1
            s=ready_stream(o.Frame().oem_menu(m.view()).wire_bytes())
            for _ in range(min(7,len(m.items())-1)):
                m.move(1);wire=o.Frame().oem_menu(m.view()).wire_bytes()
                s.request(wire,urgent=True);_,p,_=run_frame(s)
                self.assertEqual(s.previous,wire);self.assertEqual(p.ram,wire)
                self.assertTrue(s.ready)

    def test_four_DMA_credits_and_commit_only_after_last_ack(self):
        s=ready_stream();s.request(b'\xff'*8192,True)
        ops=[s.step(1.) for _ in range(4)]
        self.assertTrue(all(ops));self.assertIsNone(s.step(1.))
        self.assertEqual(len(s.inflight),4);self.assertEqual(s.previous,bytes(8192))
        self.assertEqual(s.peak_pending,4)
        for op in ops:self.assertTrue(s.ack(op[1],1.001,s.generation))
        self.assertEqual(s.previous,bytes(8192))
        _,_,remaining=run_frame(s,1.002)
        self.assertEqual(s.previous,b'\xff'*8192);self.assertGreater(len(remaining),0)

    def test_successful_FIFO_ack_covers_only_predecessors_in_same_generation(self):
        s=ready_stream();s.request(b'\x77'*8192,True)
        ops=[s.step(1.) for _ in range(4)]
        self.assertFalse(s.ack(ops[2][1],1.001,s.generation-1))
        self.assertTrue(s.ack(ops[2][1],1.001,s.generation))
        self.assertEqual(s.acked,3);self.assertEqual(s.recovered_acks,2)
        self.assertEqual(len(s.inflight),1)
        self.assertFalse(s.ack(ops[0][1],1.002,s.generation))

    def test_error_and_late_native_receipt_cannot_commit_or_replay_pixels(self):
        for result,received,processed in ((2,1.001,1.001),(0,2.01,2.01),(0,1.01,2.01)):
            s=ready_stream();s.request(b'\xff'*8192,True);op=s.step(1.)
            self.assertFalse(s.ack(op[1],received,s.generation,result,processed))
            self.assertFalse(s.enabled);self.assertIsNone(s.previous)
            self.assertEqual(s.sent,1)

    def test_fast_bursts_are_never_retried_on_missing_ACK(self):
        s=ready_stream(timeout=.1);s.request(b'\xff'*8192,True)
        ops=[s.step(1.) for _ in range(4)];sent=s.sent
        self.assertIsNone(s.step(1.07));self.assertEqual(s.sent,sent)
        with self.assertRaises(TimeoutError):s.step(1.101)
        self.assertFalse(s.ready);self.assertEqual(s.timeout_context['sent'],sent)

    def test_new_generation_fences_old_ACK_and_redraws_whole_image(self):
        s=ready_stream();s.request(b'\x11'*8192,True);op=s.step(1.);old=s.generation
        s.start(1.1);begin=s.step(1.1)
        self.assertEqual(begin[0],'begin');self.assertGreater(s.generation,old)
        self.assertFalse(s.ack(op[1],1.11,old));self.assertEqual(len(s.inflight),1)
        self.assertFalse(s.ready);self.assertIsNone(s.previous)

    def test_init_and_control_delays_are_barriers(self):
        s=t.FastOledStream();s.start(1.)
        op=s.step(1.);self.assertEqual(op[0],'begin');self.assertIsNone(s.step(1.001))
        s.ack(op[1],1.002,s.generation)
        self.assertIsNone(s.step(1.4));op=s.step(1.503);self.assertEqual(op[0],'control')
        self.assertIsNone(s.step(1.504))

    def test_latest_frame_replaces_thousand_pending_views_without_queue_growth(self):
        s=ready_stream();s.request(b'\x11'*8192,True);s.step(1.)
        for i in range(1000):s.request(bytes([2+i%200])*8192,True)
        self.assertEqual(len(s.inflight),1);self.assertGreater(s.coalesced_requests,900)
        token=s.inflight[0][0];s.ack(token,1.001,s.generation)
        now,_,_=run_frame(s,1.002)
        self.assertNotEqual(s.previous,s.latest)
        now,_,_=run_frame(s,now+.021)
        self.assertEqual(s.previous,s.latest);self.assertLessEqual(len(s.frame_samples),128)

    def test_work_budget_applies_to_heated_service_without_per_tick_status_calls(self):
        s=ready_stream(work_budget=1000.);s.active_work=True
        s.request(bytes(range(256))*32,True)
        sent=[]
        for tick in range(1000):
            now=1.+tick*.001
            for _ in range(4):
                op=s.step(now)
                if op is None:break
                sent.append(op);s.ack(op[1],now+.000001,s.generation)
        self.assertLessEqual(sum(len(args[3])+52 for _,_,args in sent),1337)
        self.assertFalse(s.ready and s.previous==s.latest)
        self.assertEqual(s.optimized_frames,0)

    def test_waiting_for_ACK_uses_deadline_not_two_millisecond_spin(self):
        s=ready_stream();s.request(b'\x33'*8192,True)
        for _ in range(4):s.step(1.)
        self.assertEqual(s.next_wakeup(1.001),2.)
        self.assertEqual(s.sent,4)

    def test_sleep_and_wake_modes_commit_after_ack_without_claiming_new_pixels(self):
        s=ready_stream();count=s.completed_frames
        for mode in ('dim','off','awake'):
            old=s.display_mode;s.set_display_mode(mode);op=s.step(1.)
            self.assertEqual(s.display_mode,old)
            s.ack(op[1],1.001,s.generation);run_frame(s,1.002)
            self.assertEqual(s.display_mode,mode);self.assertEqual(s.completed_frames,count)
        self.assertEqual(s.last_frame_bytes,bytes(8192))

    def test_sequence_exhaustion_stops_without_wrapping(self):
        s=ready_stream();s.token=t.MAX_TOKEN;s.request(b'\x66'*8192,True)
        self.assertIsNone(s.step(1.));self.assertFalse(s.enabled)


class Reactor:
    NOW=0.;NEVER=float('inf')
    def __init__(self):self.now=10.;self.callbacks=[];self.timers=[]
    def monotonic(self):return self.now
    def register_async_callback(self,cb):self.callbacks.append(cb)
    def register_timer(self,cb):
        timer=types.SimpleNamespace(cb=cb,when=self.NEVER);self.timers.append(timer);return timer
    def update_timer(self,timer,when):timer.when=when
    def flush(self):
        callbacks,self.callbacks=self.callbacks,[]
        for cb in callbacks:cb(self.now)


class MCU:
    def __init__(self):self.config_callbacks=[];self.responses={};self.calls=[]
    def get_constants(self):return {'INVENTURE_ABI':3,'INVENTURE_OLED_ABI':2,'INVENTURE_OLED_FAST_ABI':1,'INVENTURE_OLED_FAST_QUEUE':4,'INVENTURE_OLED_FAST_WIRE':256}
    def get_constant_float(self,name):return self.get_constants()[name]
    def alloc_command_queue(self):return object()
    def register_config_callback(self,cb):self.config_callbacks.append(cb)
    def check_valid_response(self,fmt):return True
    def register_serial_response(self,cb,fmt):self.responses[fmt.split()[0]]=cb
    def lookup_command(self,fmt,cq=None):
        def send(args,**kw):self.calls.append((fmt.split()[0],list(args),kw))
        return types.SimpleNamespace(send=send)
    def add_config_cmd(self,cmd):pass


class Printer:
    def __init__(self):
        self.reactor=Reactor();self.objects={};self.events={}
        self.gcode=types.SimpleNamespace(register_command=lambda *a,**kw:None)
        self.mcu=MCU();self.mcu._conn_helper=types.SimpleNamespace(get_serialport=lambda:('/dev/ttyS1',750000))
        self.board=types.SimpleNamespace(mcu=self.mcu,connected=True,load_test_context=None,
            motion_last={},get_status=lambda now:{'status_age':0.,'fault':0,'temperatures':[25.]*8,'adc_ages':[0.]*8})
    def get_reactor(self):return self.reactor
    def load_object(self,cfg,name):return self.board
    def lookup_object(self,name,default=None):
        return {'gcode':self.gcode,'inventure_mcu':self.board}.get(name,self.objects.get(name,default))
    def register_event_handler(self,name,cb):self.events.setdefault(name,[]).append(cb)
    def config_error(self,msg):return RuntimeError(msg)
    def send_event(self,*args):pass


class Config:
    def __init__(self,p):self.printer=p
    def get_printer(self):return self.printer
    def get(self,name,default=None):return default
    def getint(self,name,default=None,**kw):return default
    def getfloat(self,name,default=None,**kw):return default
    def getboolean(self,name,default=None):return default
    def error(self,msg):return RuntimeError(msg)


def oled():
    p=Printer();x=o.InventureOLED(Config(p));x._build_config()
    x.connected=True;x.stream=ready_stream();x.render_at=20.;x.next_query=20.
    return p,x


class AdapterTests(unittest.TestCase):
    def test_button_receipt_before_frame_ack_cannot_become_a_later_confirmation(self):
        p,x=oled();calls=[];x._select=calls.append
        x._render(10.);receipt=p.board._oled_input_receipt
        self.assertFalse(receipt[4]);run_frame(x.stream,10.)
        x._publish_input_receipt();self.assertTrue(p.board._oled_input_receipt[4])
        x._buttons(10.2,4,1,10.,receipt)
        self.assertEqual(calls,[])
        x._buttons(10.3,0,2,10.3,p.board._oled_input_receipt)
        x._buttons(10.4,4,3,10.4,p.board._oled_input_receipt)
        self.assertEqual(calls,[10.4])

    def test_full_adapter_build_uses_existing_ABI_and_actual_saved_host_baud(self):
        p,x=oled();self.assertTrue(x.fast)
        self.assertEqual(x.speed,2000000);self.assertEqual(len(p.mcu.config_callbacks),1)
        self.assertIn('inventure_oled_ack',p.mcu.responses)
        self.assertFalse(any('motion' in c[0] for c in p.mcu.calls))

    def test_service_cursor_wakes_and_marks_frame_urgent(self):
        p,x=oled();view=core.View(10,'confirm','Run?', [('No',None),('Yes',None)])
        svc=types.SimpleNamespace(view=view,panel_move=view.move)
        p.board.zortrax=svc;x._navigate(1,10.)
        self.assertTrue(x._user_refresh_pending);self.assertEqual(x.render_at,0.)
        self.assertEqual(view.selected,1)

    def test_bulk_pixels_are_background_but_not_actuator_or_lease_commands(self):
        p,x=oled();x.stream.request(b'\x55'*8192,True);x._pump(10.)
        calls=[c for c in p.mcu.calls if c[0]=='inventure_oled_burst']
        self.assertEqual(len(calls),4)
        self.assertTrue(all(c[2]['reqclock']==o.BACKGROUND_PRIORITY_CLOCK for c in calls))
        self.assertFalse(any('motion' in c[0] or 'heater' in c[0] for c in p.mcu.calls))

    def test_waiting_frame_does_not_poll_every_two_ms(self):
        p,x=oled();x.stream.request(b'\x22'*8192,True);x._pump(10.)
        self.assertAlmostEqual(x._next_tick(10.001),10.051)

    def test_opposing_held_keys_do_not_create_a_past_deadline_busy_loop(self):
        p,x=oled();x.last_buttons=3;x.repeat_at={0:9.5,1:9.6}
        self.assertAlmostEqual(x._next_tick(10.),10.05)

    def test_budget_is_enabled_for_service_heating_and_manual_motion(self):
        p,x=oled();p.board.load_test_context={'phase':'HEAT'};self.assertTrue(x._active_work(10.))
        p.board.load_test_context=None;p.board.motion_last={'active':1};self.assertTrue(x._active_work(10.))
        p.board.motion_last={};self.assertFalse(x._active_work(10.))
        p.objects['print_stats']=types.SimpleNamespace(get_status=lambda now:{'state':'paused'})
        self.assertTrue(x._active_work(10.));p.objects.pop('print_stats')
        p.objects['heaters']=types.SimpleNamespace(get_all_heaters=lambda:['extruder'],
            lookup_heater=lambda name:types.SimpleNamespace(get_status=lambda now:{'target':230.}))
        self.assertTrue(x._active_work(10.))

    def test_queued_receipt_retains_the_actual_old_view_and_cannot_match_a_replacement(self):
        p,x=oled();view=core.View(10.,'confirm','Run?', [('No',None),('Yes',None)])
        svc=types.SimpleNamespace(view=view,observe_buttons=lambda *a:True)
        p.board.zortrax=svc;receipt=x._input_receipt()
        self.assertIs(receipt[1][1],view)
        svc.view=core.View(10.,'confirm','Run?', [('No',None),('Yes',None)])
        calls=[];x._select=calls.append
        x._buttons(10.1,4,1,10.,receipt)
        self.assertEqual(calls,[])

    def test_stale_queued_OK_cannot_select_new_cursor_and_release_is_consumed(self):
        p,x=oled();calls=[];x._select=calls.append
        receipt=x._input_receipt();x.selection+=1
        x._buttons(10.1,4,1,10.,receipt)
        self.assertEqual(calls,[]);self.assertEqual(x.last_buttons,0)
        x._buttons(10.2,0,2,10.2,x._input_receipt())
        x._buttons(10.3,4,3,10.3,x._input_receipt())
        self.assertEqual(calls,[10.3])

    def test_discarded_held_OK_cannot_reappear_when_an_arrow_changes(self):
        p,x=oled();calls=[];x._select=calls.append
        receipt=x._input_receipt()+(False,)
        x._buttons(10.,4,1,10.,receipt)
        current=x._input_receipt()+(True,)
        x._buttons(10.1,5,2,10.1,current)
        x._buttons(10.2,4,3,10.2,x._input_receipt()+(True,))
        self.assertEqual(calls,[])
        x._buttons(10.3,0,4,10.3,x._input_receipt()+(True,))
        x._buttons(10.4,4,5,10.4,x._input_receipt()+(True,))
        self.assertEqual(calls,[10.4])

    def test_arrow_and_OK_chord_navigates_but_requires_a_new_released_confirmation(self):
        p,x=oled();calls=[];x._select=calls.append
        x._buttons(10.,6,1,10.,x._input_receipt()+(True,))
        self.assertEqual(calls,[]);self.assertTrue(x._ok_blocked_until_release)
        x._buttons(10.1,0,2,10.1,x._input_receipt()+(True,))
        x._buttons(10.3,4,3,10.3,x._input_receipt()+(True,))
        self.assertEqual(calls,[10.3])

    def test_serial_receipt_age_not_callback_time_controls_acceptance(self):
        p,x=oled();calls=[];x._select=calls.append
        for stamp in (9.,float('nan'),True,11.):
            x._buttons(10.,4,1,stamp,x._input_receipt())
        self.assertEqual(calls,[]);self.assertEqual(x._stale_button_events,4)

    def test_multiple_cursor_events_coalesce_without_dropping_valid_navigation(self):
        p,x=oled();x.oem_menu=menu.OEMMenu('en',root_page='root');x.oem_display_mode='menu'
        receipt=x._input_receipt()
        for i in range(4):
            x._buttons(10.+.05*i,2,2*i,10.+.05*i,receipt)
            x._buttons(10.+.05*i+.01,0,2*i+1,10.+.05*i+.01,receipt)
        self.assertEqual(x.oem_menu.selected,4)
        self.assertTrue(x._user_refresh_pending)

    def test_actual_frame_still_requires_ack_for_hardware_confirmation(self):
        p,x=oled();view=core.View(10.,'confirm','Run?', [('No',None),('Yes',None)])
        p.board.zortrax=types.SimpleNamespace(view=view,view_status=lambda:view.render())
        x.oem_menu=menu.OEMMenu('en');x._render(10.)
        self.assertFalse(x._zortrax_acknowledged())
        run_frame(x.stream,10.)
        self.assertTrue(x._zortrax_acknowledged())
        view.move(1);self.assertFalse(x._zortrax_acknowledged())

    def test_ack_handler_uses_native_receipt_and_bounds_diagnostic_map(self):
        p,x=oled();x.stream.request(b'\x66'*8192,True);x._pump(10.)
        token=x.stream.inflight[-1][0];g=x.stream.generation
        x._ack({'generation_hi':g>>32,'generation_lo':g&0xffffffff,'token':token,'result':0,'#receive_time':10.001})
        p.reactor.now=10.002;p.reactor.flush()
        self.assertLessEqual(len(x._fast_tx),4)
        self.assertGreaterEqual(x.stream.recovered_acks,3)
        self.assertEqual(x.timer.when,p.reactor.NOW)

    def test_shutdown_cancels_pixels_and_disables_timer(self):
        p,x=oled();x._shutdown()
        self.assertFalse(x.stream.enabled);self.assertEqual(x.timer.when,p.reactor.NEVER)
        self.assertEqual(p.mcu.calls[-2][0],'inventure_oled_stop')


if __name__=='__main__':unittest.main()
