"""R5 public command routing, single START, protocol limits and native PWM."""
import json
import types
import unittest
from test_orange_pi_door_policy import adapter as mod,Wire as BaseWire
from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("pinned_msgproto",ROOT/"tests/fixtures/pinned_msgproto.py");msgproto=importlib.util.module_from_spec(spec);spec.loader.exec_module(msgproto)
spec=importlib.util.spec_from_file_location("pinned_gcode",ROOT/"tests/fixtures/pinned_gcode.py");gcode=importlib.util.module_from_spec(spec);spec.loader.exec_module(gcode)
class Reactor:
    NEVER=float("inf")
    def __init__(self):self.updates=[]
    def monotonic(self):return 100.
    def update_timer(self,timer,when):self.updates.append((timer,when))
from contextlib import nullcontext
class Printer:
    def get_start_args(self):return {}
    def get_reactor(self):return self
    def mutex(self):return nullcontext()
    def register_event_handler(self,*args):pass
    def config_error(self,message):return RuntimeError(message)
    def get_state_message(self):return ("Printer is ready","ready")
    def send_event(self,*args):return []
    def invoke_shutdown(self,message):raise AssertionError(message)
from test_orange_pi_door_policy import Command

class Wire(BaseWire):
    def __init__(self,reply):super().__init__(reply);self.actions=self.calls;self.retries=[]
    def send_with_preface(self,heartbeat,preface,args,**kw):
        heartbeat.send(preface);self.retries.append(kw.get('retry'))
        return self.send(args,**kw)

class DriverWire(Wire):
    def __init__(self,counts):super().__init__({});self.counts=counts
    def send(self,args,**kw):
        self.actions.append(tuple(args));i=args[0]
        return dict(run=1,channel=i,planned=abs(self.counts[i]),sent=0,direction=int(self.counts[i]>0))

class OutputWire(Wire):
    def send(self,args,**kw):
        self.actions.append(tuple(args));action,channel,duty,ms,limit,token=args
        return dict(channel=channel,duty=duty if action==1 else 0,
            remaining_ms=ms if action==1 else 0,token=token,reject=0,reason=1 if action==1 else 2)

class ControlHostTests(unittest.TestCase):
    def host(self,counts=(640,640,0,0)):
        h=mod.InventureMCU.__new__(mod.InventureMCU)
        h.connected=True;h.control_abi=1;h.control_token=10;h.control_last={}
        h.motion_last={};h.output_last={};h.guard_last={};h.control_timer=object()
        h.allow_actuators=False;h.reactor=Reactor();h.gcode=Command()
        h.commands={'control':Wire(dict(mode=2,armed=0,active=0,latched=0,fault=0,guard_mask=127,reject=0)),
            'motion':Wire(dict(run=1,token=11,phase=1,reason=0,reject=0,active=1,events=0,elapsed_ms=0,hit=0)),
            'motion_driver':DriverWire(counts),'output':OutputWire({}),'heartbeat':Wire({}),
            'feeder':Wire({}),'lock':Wire({})}
        return h

    def test_signed_xy_corexy_mapping_and_minimum_switch_defaults(self):
        for axis,steps,counts,dirs,mask in (
                ('X',640,(640,640,0,0),3,0),('X',-640,(-640,-640,0,0),0,1),
                ('Y',640,(640,-640,0,0),1,0),('Y',-640,(-640,640,0,0),2,2)):
            h=self.host(counts);h.cmd_move(Command(AXIS=axis,STEPS=steps,SPEED=2.))
            packet=h.commands['motion'].actions[0]
            self.assertEqual(packet,(1,dirs,640,640,0,0,1280,10,30000,mask,mask,0,11))
            self.assertFalse(h.motion_last['establishes_position'])

    def test_all_main_drivers_have_signed_raw_steps_and_direction_override(self):
        for axis,index in (('A',0),('B',1),('Z',2),('E',3)):
            counts=[0]*4;counts[index]=-80
            h=self.host(counts);h.cmd_move(Command(AXIS=axis,STEPS=80,DIR=0,RATE=600,
                STOP_MASK=12,STOP_VALUE=4,HOLD_MS=50,MAX_MS=5000))
            p=h.commands['motion'].actions[0]
            self.assertEqual(p[1],0);self.assertEqual(p[2+index],80)
            self.assertEqual(p[6:],(600,10,5000,12,4,50,11))

    def test_vector_motion_retains_independent_counts_for_four_drivers(self):
        counts=(640,-320,80,-96);h=self.host(counts)
        h.cmd_motion(Command(A=640,B=-320,Z=80,E=-96,RATE=900))
        self.assertEqual(h.commands['motion'].actions[0][1:6],(5,640,320,80,96))

    def test_motion_start_is_not_retried_and_poll_only_reads(self):
        h=self.host();h.cmd_move(Command());self.assertEqual(len(h.commands['motion'].actions),1)
        self.assertEqual(h.commands['motion'].retries,[False])
        h._control_poll(100.)
        self.assertEqual([p[0] for p in h.commands['motion'].actions],[1,0])
        self.assertEqual(h.commands['motion'].retries,[False,True])
        self.assertEqual(h.commands['motion_driver'].actions,[(0,),(1,),(2,),(3,)]*2)

    def test_busy_request_does_not_stop_an_existing_run(self):
        h=self.host();h.commands['motion'].reply['reject']=2
        with self.assertRaisesRegex(RuntimeError,'another_owner'):h.cmd_move(Command())
        self.assertEqual(h.commands['control'].actions,[(0,0)])
        self.assertEqual([a[0] for a in h.commands['motion'].actions],[1])

    def test_malformed_or_lost_start_requests_common_stop(self):
        for mode in ('missing','wrong_token','wrong_driver'):
            h=self.host()
            if mode=='missing':h.commands['motion'].reply.pop('phase')
            if mode=='wrong_token':h.commands['motion'].reply['token']=99
            if mode=='wrong_driver':h.commands['motion_driver'].counts=(1,1,1,1)
            with self.assertRaises(RuntimeError):h.cmd_move(Command())
            self.assertEqual(h.commands['control'].actions[-1],(2,0))
            self.assertEqual(len(h.commands['motion'].actions),1)

    def test_non_service_mode_refuses_without_stopping_native(self):
        h=self.host();h.commands['control'].reply.update(mode=1,armed=1)
        with self.assertRaisesRegex(RuntimeError,'MODE=SERVICE'):h.cmd_move(Command())
        self.assertEqual(h.commands['motion'].actions,[])
        self.assertEqual(h.commands['control'].actions,[(0,0)])

    def test_invalid_motion_parameters_emit_no_start(self):
        for params in ({'DISTANCE':'nan'},{'SPEED':0},{'STEPS':0},{'RATE':20001},
                {'PULSE_US':100,'RATE':20000},{'STEPS':2000001},{'MAX_MS':600001},{'DIR':1}):
            h=self.host()
            with self.assertRaises(RuntimeError):h.cmd_move(Command(**params))
            self.assertEqual(h.commands['motion'].actions,[])

    def test_each_output_routes_its_channel_power_time_and_cutoff(self):
        for channel,name in enumerate(mod.OUTPUT_NAMES):
            h=self.host();h.cmd_output(Command(CHANNEL=name,ACTION='SET',POWER=75,DURATION=180,LIMIT=50))
            self.assertEqual(h.commands['output'].actions,[(1,channel,192,180000,50,11)])
            self.assertEqual(h.output_last[name]['power_pct'],75.)
            h._control_poll(101.)
            self.assertEqual(h.commands['output'].actions[-1][0],0)
            self.assertEqual(sum(p[0]==1 for p in h.commands['output'].actions),1)

    def test_output_invalid_parameters_do_not_energize(self):
        for params in ({'POWER':101},{'POWER':'nan'},{'DURATION':0},{'DURATION':601},{'LIMIT':30}):
            h=self.host();p=dict(CHANNEL='T0',ACTION='SET',POWER=25);p.update(params)
            with self.assertRaises(RuntimeError):h.cmd_output(Command(**p))
            self.assertEqual(h.commands['output'].actions,[])

    def test_stop_does_not_require_permission_mode_or_connected_state(self):
        h=self.host();h.connected=False;h.commands['control'].reply.update(mode=0,latched=1)
        h.cmd_control_stop(Command())
        self.assertEqual(h.commands['control'].actions,[(2,0)])
        self.assertEqual(h.reactor.updates[-1][1],h.reactor.NEVER)
        h.cmd_output(Command(CHANNEL='T0',ACTION='OFF'))
        self.assertEqual(h.commands['output'].actions[-1][0],2)

    def test_two_feeders_and_locks_are_available_with_existing_allow_false(self):
        h=self.host();h.printer=types.SimpleNamespace(lookup_object=lambda *a:None)
        for i in range(2):
            h.cmd_feeder(Command(CHANNEL=i,DIRECTION=0,DURATION_MS=100))
            h.cmd_lock(Command(CHANNEL=i,VALUE=1));h.cmd_lock(Command(CHANNEL=i,VALUE=0))
        self.assertEqual(h.commands['feeder'].actions,[(0,0,100),(1,0,100)])
        self.assertEqual(h.commands['lock'].actions,[(0,1),(0,0),(1,1),(1,0)])

    def test_guard_query_never_silently_changes_limits(self):
        h=self.host();h.commands['guard']=Wire(dict(sensor=4,enabled=1,min_c=-20,max_c=80,reject=0))
        with self.assertRaisesRegex(RuntimeError,'explicit ENABLE'):h.cmd_guard(Command(SENSOR='PA4'))
        self.assertEqual(h.commands['guard'].actions,[])
        h.cmd_guard(Command(SENSOR='PA4',ENABLE=1,MAX=80))
        self.assertEqual(h.commands['guard'].actions,[(4,1,-20,80)])

    def test_real_extended_gcode_parser_accepts_names_with_digit_values(self):
        h=self.host();dispatch=gcode.GCodeDispatch(Printer());dispatch.register_output_handler(lambda x:None)
        for name,callback in [('INVENTURE_MOVE',h.cmd_move),('INVENTURE_OUTPUT',h.cmd_output),
                ('INVENTURE_STOP',h.cmd_control_stop)]:dispatch.register_command(name,callback,True)
        dispatch._handle_ready()
        dispatch.run_script_from_command('INVENTURE_MOVE AXIS=X DISTANCE=1 SPEED=2')
        self.assertEqual(h.commands['motion'].actions[0][2:4],(640,640))
        dispatch.run_script_from_command('INVENTURE_OUTPUT CHANNEL=PC9 ACTION=SET POWER=75 DURATION=180 LIMIT=50')
        self.assertEqual(h.commands['output'].actions[0][1:5],(8,192,180000,50))
        dispatch.run_script_from_command('INVENTURE_STOP');self.assertEqual(h.commands['control'].actions[-1],(2,0))

    def test_r5_maximum_wire_frames_roundtrip_in_real_compiled_dictionary(self):
        parser=msgproto.MessageParser();parser.process_identify((ROOT/'tests/fixtures/control_r5.dict').read_bytes(),decompress=False)
        samples=[(mod.MOTION_COMMAND,dict(action=1,directions=15,a=2000000,b=2000000,z=2000000,e=2000000,
            rate=20000,pulse_us=100,max_ms=600000,stop_mask=65535,stop_value=65535,hold_ms=60000,token=0xffffffff)),
            (mod.MOTION_RESPONSE,dict(run=0xffffffff,token=0xffffffff,phase=5,reason=7,reject=5,active=0,
                events=2000000,elapsed_ms=600000,hit=65535)),
            (mod.OUTPUT_RESPONSE,dict(channel=9,duty=256,remaining_ms=600000,token=0xffffffff,reject=5,reason=6)),
            (mod.CONTROL_RESPONSE,dict(mode=2,armed=0,active=1,latched=1,fault=255,guard_mask=255,reject=5)),
            (mod.GUARD_RESPONSE,dict(sensor=7,enabled=1,min_c=-30,max_c=310,reject=2))]
        for fmt,params in samples:
            codec=parser.lookup_command(fmt);data=codec.encode_by_name(**params)
            self.assertLessEqual(len(data),msgproto.MESSAGE_PAYLOAD_MAX)
            result,end=codec.parse(data,0);self.assertEqual(result,params);self.assertEqual(end,len(data))

    def test_native_fixed_pwm_has_active_low_nozzles_and_keeps_shared_adc_owner(self):
        h=self.host();h.native_outputs={};h.mcu_name='mcu';created=[]
        physical=types.SimpleNamespace(setup_cycle_time=lambda *a,**kw:created.append((a,kw)))
        pins=types.SimpleNamespace(setup_pin=lambda typ,desc:(created.append((typ,desc)) or physical))
        h.printer=types.SimpleNamespace(lookup_object=lambda name:pins,config_error=RuntimeError)
        for name in ('HEATER_T0','HEATER_T1','HEATER_PC9','HEATER_PC8'):
            out=h.setup_pin('pwm',{'pin':name,'invert':False})
            out.setup_cycle_time(out.ticks/120000000.)
            self.assertTrue(created[-2][1].startswith('!mcu:'))
            self.assertTrue(created[-1][1]['hardware_pwm'])
        bad=mod.R5FixedPWM(h,'HEATER_T0',physical,5632,True)
        with self.assertRaisesRegex(RuntimeError,'pwm_cycle_time'):bad.setup_cycle_time(.1)

    def test_native_guard_rejects_old_noninverted_or_software_heater(self):
        h=self.host();h.mcu=object()
        for invert,hardware in ((False,True),(True,False)):
            physical=types.SimpleNamespace(_pin='PA0',_invert=invert,_hardware_pwm=hardware,get_mcu=lambda:h.mcu)
            h.printer=types.SimpleNamespace(lookup_objects=lambda:[('extruder',types.SimpleNamespace(mcu_pwm=physical))])
            with self.assertRaisesRegex(ValueError,'active-LOW'):h._native_preflight()

if __name__=='__main__':unittest.main(verbosity=2)
