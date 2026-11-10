"""Hot load-stage behavior: bounded heat leases, E ownership and GPIO evidence."""
from contextlib import contextmanager
import json
import unittest
import test_orange_pi_control as control
import test_orange_pi_feeder_run as feeder
import test_orange_pi_feeder_service as service
from test_orange_pi_door_policy import adapter as mod, Command


class Device:
    def __init__(self,h):
        self.h=h;self.packets=[];self.outputs={};self.motion=None;self.run=0
    def now(self):return self.h.reactor.monotonic()
    def active_motion(self):return self.motion is not None and self.now()<self.motion['until']
    def output(self,i):
        row=self.outputs.get(i,dict(duty=0,until=0.,token=0,reason=0))
        if row['duty'] and self.now()>=row['until']:
            row=dict(row,duty=0,reason=3);self.outputs[i]=row
        return row
    def active(self):return self.active_motion() or any(self.output(i)['duty'] for i in range(10))
    def control(self,args):
        action,mode=args
        if action in (1,2):self.packets.append(('control',*args))
        if action==1:self.h.control_reply.update(mode=mode,armed=int(mode==1))
        if action==2 or action==1 and mode==0:
            for i,row in list(self.outputs.items()):self.outputs[i]=dict(row,duty=0,reason=2)
            if self.motion:self.motion.update(until=self.now(),stopped=True)
        return dict(self.h.control_reply,active=int(self.active()))
    def out(self,args):
        action,i,duty,ms,limit,token=args
        if action:
            self.packets.append(('output',*args))
            if action==1 and self.active_motion():
                row=self.output(i)
                return dict(channel=i,duty=row['duty'],remaining_ms=0,token=row['token'],reject=2,reason=row['reason'])
            if action==1:
                self.outputs[i]=dict(duty=duty,until=self.now()+ms/1000.,token=token,reason=1)
            else:
                old=self.output(i);self.outputs[i]=dict(old,duty=0,reason=2,until=self.now())
        row=self.output(i)
        return dict(channel=i,duty=row['duty'],remaining_ms=max(0,round((row['until']-self.now())*1000)) if row['duty'] else 0,
            token=row['token'],reject=0,reason=row['reason'])
    def move(self,args):
        action,directions,a,b,z,e,rate,pulse,ms,mask,value,hold,token=args
        if action==1:
            self.packets.append(('motion',*args));self.run+=1
            self.motion=dict(token=token,counts=(a,b,z,e),directions=directions,
                until=self.now()+max(a,b,z,e)/rate+.02,started=self.now(),stopped=False)
        if self.motion is None:
            return dict(run=0,token=0,phase=0,reason=0,reject=0,active=0,events=0,elapsed_ms=0,hit=0)
        row=self.motion;active=self.active_motion();stopped=row['stopped']
        return dict(run=self.run,token=row['token'],phase=1 if active else 4 if stopped else 3,
            reason=0 if active else 3 if stopped else 1,reject=0,active=int(active),
            events=0 if active or stopped else max(row['counts']),
            elapsed_ms=round((self.now()-row['started'])*1000),hit=0)
    def driver(self,args):
        i=args[0];row=self.motion;n=row['counts'][i]
        return dict(run=self.run,channel=i,planned=n,sent=0 if self.active_motion() or row['stopped'] else n,
            direction=(row['directions']>>i)&1)


class Wire(control.Wire):
    def __init__(self,callback):super().__init__({});self.callback=callback
    def send(self,args,**kw):self.calls.append(tuple(args));return self.callback(args)


class Reactor(feeder.Reactor):
    def __init__(self,h):super().__init__();self.h=h
    def pause(self,target):
        while True:
            due=[t for t in self.timers if t.when<=target]
            if not due:break
            timer=min(due,key=lambda t:t.when);self.now=timer.when;self.h.refresh()
            when=timer.callback(self.now)
            if timer in self.timers:timer.when=when
        self.now=target;self.h.refresh();return self.now


class LoadTests(unittest.TestCase):
    def host(self):
        h,ops,p13=feeder.FeederRunTests().host();h.reactor=Reactor(h)
        h.load_test_context=None;h.load_test_last={'phase':'idle','load_completed':False}
        h.temperatures=[25.,25.,25.,25.,25.,25.,25.,150.]
        h.raw_adc=[155,155,3700,3700,3700,3700,3700,1180]
        h.adc_received=[100.]*8;h.stale_adc=set();h.stale_board=False
        h.fan_pulses=True;h.control_reply=dict(mode=2,armed=0,latched=0,fault=0,guard_mask=127,reject=0)
        h.device=Device(h)
        h.commands.update(control=Wire(h.device.control),output=Wire(h.device.out),
            motion=Wire(h.device.move),motion_driver=Wire(h.device.driver))
        def refresh():
            now=h.reactor.monotonic()
            for i in range(8):
                if i not in h.stale_adc:h.adc_received[i]=now
            if not h.stale_board:h.status_received=now
            if h.fan_pulses:h.status['p0']+=1
            h.board_reply.update(h.status)
        h.refresh=refresh;h.refresh()
        return h,ops,p13

    def heat(self,h,**params):h.cmd_load_test(Command(ACTION='HEAT',**params));h.reactor.pause(100.25)
    def ready(self,h):
        h.temperatures[0]=230.;h.reactor.pause(h.reactor.now+2.5)
        self.assertTrue(h.load_test_last['ready_for_jog'])
    def starts(self,h,kind='output'):
        return [p for p in h.device.packets if p[0]==kind and p[1]==1]

    def test_temperature_target_uses_short_power_leases_and_a_separate_250C_cutoff(self):
        h,_,_=self.host();self.heat(h)
        self.assertEqual(self.starts(h)[0][2:6],(6,102,1500,250))
        self.assertEqual(h.load_test_last['target_c'],230.)
        self.ready(h);self.assertEqual(h.device.output(6)['duty'],0)
        h.temperatures[0]=229.;h.reactor.pause(103.);self.assertEqual(h.device.output(6)['duty'],0)
        h.temperatures[0]=227.;h.reactor.pause(103.25);self.assertGreater(h.device.output(6)['duty'],0)
        self.assertTrue(all(p[4]==1500 and p[5]==250 for p in self.starts(h)))

    def test_jog_requires_hot_stable_nozzle_and_refusal_keeps_heating(self):
        h,_,_=self.host();self.heat(h)
        with self.assertRaisesRegex(RuntimeError,'ready_for_jog'):h.cmd_load_test(Command(ACTION='JOG',DIR=0))
        self.assertEqual(self.starts(h,'motion'),[]);self.assertIsNotNone(h.load_test_context)
        h.reactor.pause(101.);self.assertGreater(h.device.output(6)['duty'],0)

    def test_both_E_directions_have_no_XYZ_steps_and_heat_is_off_until_motion_ends(self):
        for direction in (0,1):
            h,_,_=self.host();self.heat(h);self.ready(h)
            h.temperatures[0]=227.;h.reactor.pause(103.)
            h.cmd_load_test(Command(ACTION='JOG',DIR=direction,STEPS=960,RATE=960))
            starts=self.starts(h,'motion');self.assertEqual(len(starts),1)
            p=starts[0];self.assertEqual(p[2:7],(8 if direction else 0,0,0,0,960))
            self.assertEqual(h.device.output(6)['duty'],0)
            before=len(self.starts(h));h.reactor.pause(103.75)
            self.assertEqual(len(self.starts(h)),before)
            h.commands['sensor_inputs'].reply['raw']=1<<8;h.board_reply['p4']=9;h.status['p4']=9
            h.commands['gpio_read'].words[mod.GPIO_BASE+3*0x400+0x10]=1<<10
            h.reactor.pause(104.5)
            self.assertGreater(len(self.starts(h)),before)
            self.assertEqual(h.load_test_context['comparison']['pulse_delta']['t0_pb12'],7)
            self.assertEqual(h.load_test_context['comparison']['filament_after']['t0_a'],1)
            self.assertEqual(h.load_test_context['comparison']['changes'][0]['pin'],'PD10')
            reports=[json.loads(s.split(' ',1)[1]) for s in h.gcode.lines if s.startswith('V9_LOAD_TEST ')]
            self.assertTrue(any(r['phase']=='jog_done' for r in reports))
            self.assertTrue(all(not r['load_completed'] for r in reports))

    def test_stop_off_and_dedicated_stop_prevent_later_heat_or_motion(self):
        for action in ('common','off','dedicated'):
            h,_,_=self.host();self.heat(h);self.ready(h)
            h.cmd_load_test(Command(ACTION='JOG',DIR=0))
            if action=='common':h.cmd_control_stop(Command())
            elif action=='off':h.cmd_control(Command(MODE='OFF'))
            else:h.cmd_load_test(Command(ACTION='STOP'))
            count=len(h.device.packets);h.reactor.pause(800.)
            self.assertEqual(len(h.device.packets),count);self.assertIsNone(h.load_test_context)
            self.assertFalse(h.device.active())

    def test_faults_stale_thermometry_cable_and_owner_abort_every_output(self):
        for cause in ('temperature','stale_adc','ground','board_age','cable','fault','owner','native','P13'):
            h,ops,p13=self.host();self.heat(h)
            if cause=='temperature':h.temperatures[0]=241.
            elif cause=='stale_adc':h.stale_adc.add(0);h.adc_received[0]=95.
            elif cause=='ground':h.raw_adc[0]=0
            elif cause=='board_age':h.stale_board=True;h.status_received=95.
            elif cause=='cable':h.status['cable']=1
            elif cause=='fault':h.control_reply['fault']=4
            elif cause=='owner':ops.state.owner=object()
            elif cause=='native':h.control_reply.update(mode=1,armed=1)
            else:p13.running=True
            h.reactor.pause(101.)
            self.assertEqual(h.load_test_last['phase'],'failed',cause)
            self.assertIsNone(h.load_test_context,cause);self.assertFalse(h.device.active(),cause)

    def test_fan_failure_and_failed_heating_stop_the_service(self):
        for cause in ('fan','rise','reach'):
            h,_,_=self.host();self.heat(h)
            if cause=='fan':h.temperatures[0]=100.;h.fan_pulses=False;until=112.
            elif cause=='rise':until=161.
            else:h.temperatures[0]=100.;until=401.
            h.reactor.pause(until)
            self.assertEqual(h.load_test_last['phase'],'failed')
            self.assertIn({'fan':'fan pulses','rise':'did not rise','reach':'did not reach'}[cause],h.load_test_last['reason'])
            self.assertFalse(h.device.active())

    def test_host_delay_cannot_silently_restart_an_expired_MCU_power_lease(self):
        h,_,_=self.host();self.heat(h)
        h.reactor.now=103.;h.refresh()
        h._load_test_tick(h.load_test_context,103.)
        self.assertEqual(len(self.starts(h)),1)
        self.assertIn('lease expired',h.load_test_last['reason']);self.assertFalse(h.device.active())

    def test_fixed_total_timeout_cannot_be_extended_by_status_or_sampling(self):
        h,_,_=self.host();self.heat(h,DURATION=10);self.ready(h)
        h.cmd_load_test(Command(ACTION='BASELINE'));h.cmd_load_test(Command(ACTION='COMPARE'))
        h.cmd_load_test(Command(ACTION='STATUS'));h.reactor.pause(111.)
        self.assertIsNone(h.load_test_context);self.assertEqual(h.load_test_last['reason'],'duration_finished')
        self.assertFalse(h.device.active())

    def test_manual_flow_confirmation_preserves_pending_cleaning_and_does_not_claim_load_success(self):
        h,_,_=self.host();self.heat(h);self.ready(h)
        h.cmd_load_test(Command(ACTION='CONFIRM',FLOW=1))
        self.assertTrue(h.load_test_last['flow_confirmed_by_operator'])
        self.assertFalse(h.load_test_last['load_completed']);self.assertTrue(h.load_test_last['cleaning_pending'])
        self.assertFalse(h.load_test_last['head_filament_sensor_identified'])
        self.assertEqual(self.starts(h,'motion'),[])

    def test_raw_motion_output_and_feeder_cannot_take_the_stage_owner(self):
        h,_,_=self.host();self.heat(h)
        for method,params in ((h.cmd_move,dict(AXIS='X')),
                (h.cmd_output,dict(CHANNEL='T1',ACTION='SET',POWER=25)),
                (h.cmd_feeder_run,dict(CHANNEL=0,DURATION=50)),
                (h.cmd_feeder_test,dict(CHANNEL=0,DURATION_MS=100))):
            with self.assertRaisesRegex(RuntimeError,'Load test'):method(Command(**params))
        self.assertEqual(self.starts(h,'motion'),[])
        self.assertTrue(all(p[2]==6 for p in self.starts(h)))

    def test_stop_while_waiting_for_query_mutex_discards_pending_actuator_request(self):
        h,_,_=self.host();self.heat(h);context=h.load_test_context
        @contextmanager
        def interrupt():
            h.query_mutex=None;h.cmd_control_stop(Command());yield
        h.query_mutex=interrupt()
        count=len(self.starts(h))
        with self.assertRaisesRegex(ValueError,'stopped before'):h._load_test_output(context,102)
        self.assertEqual(len(self.starts(h)),count);self.assertFalse(h.device.active())

    def test_limits_are_validated_before_actuation(self):
        for params in (dict(TARGET='nan'),dict(TARGET=241),dict(MAX_POWER=51),dict(DURATION=601),dict(TOOL='PC9')):
            h,_,_=self.host()
            with self.assertRaises(RuntimeError):h.cmd_load_test(Command(ACTION='HEAT',**params))
            self.assertEqual(h.device.packets,[])
        h,_,_=self.host();self.heat(h);self.ready(h)
        for params in (dict(STEPS=19201),dict(STEPS=19200,RATE=160),dict(DIR=2),dict(RATE=4801)):
            with self.assertRaises(RuntimeError):
                h.cmd_load_test(Command(ACTION='JOG',**dict({'DIR':0},**params)))
        self.assertEqual(self.starts(h,'motion'),[])

    def test_real_extended_parser_and_legacy_operation_wrapper_allow_the_owned_service_stage(self):
        h,ops,_=self.host();dispatch=control.gcode.GCodeDispatch(h.printer)
        dispatch.register_output_handler(lambda line:None)
        dispatch.register_command('INVENTURE_LOAD_TEST',h.cmd_load_test,True)
        original=service.legacy_dispatch();handler=dispatch.ready_gcode_handlers['INVENTURE_LOAD_TEST']
        def guarded(gcmd):
            return original(ops,'INVENTURE_LOAD_TEST',handler,gcmd,dispatch._get_extended_params(gcmd))
        dispatch.ready_gcode_handlers['INVENTURE_LOAD_TEST']=guarded
        dispatch.base_gcode_handlers['INVENTURE_LOAD_TEST']=guarded;dispatch._handle_ready()
        dispatch.run_script_from_command('INVENTURE_LOAD_TEST ACTION=HEAT TOOL=T0 TARGET=230')
        h.reactor.pause(100.25);self.ready(h)
        with self.assertRaisesRegex(control.gcode.CommandError,'DIR'):
            dispatch.run_script_from_command('INVENTURE_LOAD_TEST ACTION=JOG STEPS=960 RATE=960')
        dispatch.run_script_from_command('INVENTURE_LOAD_TEST ACTION=JOG STEPS=960 DIR=0 RATE=960')
        self.assertEqual(len(self.starts(h,'motion')),1)
        dispatch.run_script_from_command('INVENTURE_LOAD_TEST ACTION=STOP')
        self.assertFalse(h.device.active())


if __name__=='__main__':unittest.main(verbosity=2)
