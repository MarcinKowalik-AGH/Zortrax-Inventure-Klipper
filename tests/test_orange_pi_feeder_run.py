"""Exercise real-time feeder windows, completion, ownership and cancellation."""
import ast
import json
from pathlib import Path
import types
import unittest
import test_orange_pi_control as control
from test_orange_pi_door_policy import adapter as mod, Command
import test_orange_pi_feeder_service as service
import test_orange_pi_cassette_inputs as cassette

ROOT=Path(__file__).resolve().parents[1]

class Reactor:
    NEVER=float('inf');NOW=0.
    def __init__(self):self.now=100.;self.timers=[]
    def monotonic(self):return self.now
    def register_timer(self,callback,waketime=float('inf')):
        timer=types.SimpleNamespace(callback=callback,when=waketime)
        self.timers.append(timer);return timer
    def update_timer(self,timer,when):
        if hasattr(timer,'when'):timer.when=when
    def unregister_timer(self,timer):
        if timer in self.timers:self.timers.remove(timer)
    def register_async_callback(self,callback):callback(self.now)
    def pause(self,target):
        while True:
            due=[t for t in self.timers if t.when<=target]
            if not due:break
            timer=min(due,key=lambda t:t.when);self.now=timer.when
            when=timer.callback(self.now)
            if timer in self.timers:timer.when=when
        self.now=target;return self.now

class ControlWire(control.Wire):
    def __init__(self,host):
        super().__init__(dict(mode=2,armed=0,active=0,latched=0,fault=0,guard_mask=127,reject=0))
        self.host=host;self.hold_active=False
    def send(self,args,**kw):
        self.calls.append(tuple(args));action,mode=args
        if action==2:self.host.device_until=[0.,0.]
        if action==1:self.reply.update(mode=mode,armed=int(mode==1))
        return dict(self.reply,active=int(self.hold_active or any(self.host.reactor.monotonic()<t for t in self.host.device_until)))

class FeederWire:
    def __init__(self,host):self.host=host;self.calls=[]
    def send(self,args):
        self.calls.append(tuple(args));channel,direction,ms=args
        if not 0<=ms<=1500:raise AssertionError('Firmware window exceeded')
        self.host.device_until[channel]=self.host.reactor.monotonic()+ms/1000. if ms else 0.

class FeederRunTests(unittest.TestCase):
    def host(self):
        h=cassette.CassetteInputTests().host()
        owned,ops,filament=service.FeederServiceTests().host()
        h.printer=owned.printer;h.reactor=Reactor();h.gcode=Command()
        h.feeder_run_context=None;h.feeder_run_last={'phase':'idle'}
        h.feeder_test_pending_until=None;h.device_until=[0.,0.]
        h.commands['control']=ControlWire(h);h.commands['feeder']=FeederWire(h)
        h.timer=object()
        return h,ops,filament

    def starts(self,h):return [p for p in h.commands['feeder'].calls if p[2]]

    def test_long_run_returns_immediately_and_ends_only_after_real_mcu_windows(self):
        h,_,_=self.host();before=h.reactor.monotonic()
        h.cmd_feeder_run(Command(CHANNEL=0,DIRECTION=1,DURATION=3.1))
        self.assertEqual(h.reactor.monotonic(),before)
        self.assertEqual(self.starts(h),[(0,1,1500)])
        h.reactor.pause(before+1.4);self.assertEqual(len(self.starts(h)),1)
        h.board_reply['p4']=9
        h.commands['sensor_inputs'].reply['raw']=1<<8
        h.commands['gpio_read'].words[mod.GPIO_BASE+3*0x400+0x10]=1<<10
        h.reactor.pause(before+4.)
        self.assertIsNone(h.feeder_run_context)
        self.assertEqual(self.starts(h),[(0,1,1500),(0,1,1500),(0,1,100)])
        self.assertEqual(h.feeder_run_last['phase'],'done')
        self.assertEqual(h.feeder_run_last['completed_windows_s'],3.1)
        self.assertEqual(h.feeder_run_last['pulse_delta']['t0_pb12'],7)
        self.assertEqual(h.feeder_run_last['filament_after']['t0_a'],1)
        self.assertEqual(h.feeder_run_last['changes'][0]['pin'],'PD10')
        self.assertFalse(h.feeder_run_last['physical_motion_confirmed'])
        self.assertEqual(h.commands['motion'].calls,[]);self.assertEqual(h.commands['output'].calls,[])

    def test_50_seconds_is_thirty_four_bounded_requests_and_reports_progress(self):
        h,_,_=self.host();h.cmd_feeder_run(Command(CHANNEL=0,DURATION=50))
        h.reactor.pause(160.)
        packets=self.starts(h)
        self.assertEqual(len(packets),34);self.assertEqual(sum(p[2] for p in packets),50000)
        self.assertEqual(packets[:-1],[(0,1,1500)]*33)
        self.assertEqual(packets[-1],(0,1,500))
        self.assertEqual(h.feeder_run_last['phase'],'done')
        self.assertEqual(h.feeder_run_last['maximum_duration_s'],50)
        reports=[json.loads(s.split(' ',1)[1]) for s in h.gcode.lines]
        self.assertGreater(sum(r['phase']=='running' for r in reports),8)

    def test_global_stop_cancels_the_job_and_prevents_any_later_restart(self):
        h,_,_=self.host();h.cmd_feeder_run(Command(CHANNEL=0,DURATION=50))
        h.reactor.pause(100.25);h.cmd_control_stop(Command())
        self.assertEqual(h.feeder_run_last['phase'],'stopped')
        self.assertEqual(h.device_until,[0.,0.])
        h.reactor.pause(400.);self.assertEqual(len(self.starts(h)),1)
        self.assertEqual(h.reactor.timers,[])

    def test_dedicated_stop_and_short_test_stop_cancel_the_same_channel(self):
        for method in ('dedicated','short'):
            h,_,_=self.host();h.cmd_feeder_run(Command(CHANNEL=1,DIRECTION=0,DURATION=10))
            if method=='dedicated':h.cmd_feeder_run(Command(ACTION='STOP'))
            else:h.cmd_feeder_test(Command(CHANNEL=1,ACTION='STOP'))
            h.reactor.pause(120.)
            self.assertEqual(self.starts(h),[(1,0,1500)])
            self.assertEqual(h.feeder_run_last['phase'],'stopped')

    def test_owner_fault_and_native_transport_changes_abort_before_renewal(self):
        for case in ('owner','native_transport','fault','native_mode','lost_connection'):
            h,ops,filament=self.host();h.cmd_feeder_run(Command(CHANNEL=0,DURATION=10))
            if case=='owner':ops.state.owner=types.SimpleNamespace(kind='print')
            elif case=='native_transport':filament.running=True
            elif case=='fault':h.commands['control'].reply['fault']=4
            elif case=='native_mode':h.commands['control'].reply.update(mode=1,armed=1)
            else:h.connected=False
            h.reactor.pause(105.)
            self.assertEqual(h.feeder_run_last['phase'],'failed',case)
            self.assertEqual(len(self.starts(h)),1,case)

    def test_busy_mcu_after_window_times_out_without_starting_another_window(self):
        h,_,_=self.host();h.cmd_feeder_run(Command(CHANNEL=0,DURATION=10))
        h.commands['control'].hold_active=True;h.reactor.pause(105.)
        self.assertEqual(h.feeder_run_last['phase'],'failed')
        self.assertIn('did not become idle',h.feeder_run_last['reason'])
        self.assertEqual(len(self.starts(h)),1)

    def test_shutdown_removes_the_job_without_sending_new_requests(self):
        h,_,_=self.host();h.cmd_feeder_run(Command(CHANNEL=0,DURATION=10))
        calls=list(h.commands['feeder'].calls);h._shutdown();h.reactor.pause(105.)
        self.assertEqual(h.commands['feeder'].calls,calls)
        self.assertEqual(h.feeder_run_last['phase'],'failed')
        self.assertEqual(h.feeder_run_last['reason'],'mcu_shutdown')
        self.assertIsNone(h.feeder_run_context)

    def test_new_service_actions_and_second_run_do_not_steal_an_active_job(self):
        h,_,_=self.host();h.cmd_feeder_run(Command(CHANNEL=0,DURATION=10))
        for handler,command in (
            (h.cmd_feeder_run,Command(CHANNEL=1,DURATION=5)),
            (h.cmd_feeder_test,Command(CHANNEL=1)),
            (h.cmd_move,Command(AXIS='X',STEPS=40)),
            (h.cmd_control,Command(MODE='NATIVE')),
            (h.cmd_cassette_diag,Command(ACTION='STATUS'))):
            with self.assertRaises(RuntimeError):handler(command)
        self.assertIsNotNone(h.feeder_run_context)
        self.assertEqual(len(self.starts(h)),1)

    def test_new_run_after_stop_has_a_different_timer_and_old_callback_cannot_restart(self):
        h,_,_=self.host();h.cmd_feeder_run(Command(CHANNEL=0,DURATION=10))
        old=h.feeder_run_context;h.cmd_feeder_run(Command(ACTION='STOP'))
        h.cmd_feeder_run(Command(CHANNEL=1,DURATION=1))
        self.assertIsNot(old['timer'],h.feeder_run_context['timer'])
        self.assertEqual(h._feeder_run_tick(old,h.reactor.monotonic()),h.reactor.NEVER)
        h.reactor.pause(102.);self.assertEqual(h.feeder_run_last['phase'],'done')
        self.assertEqual(self.starts(h),[(0,1,1500),(1,1,1000)])

    def test_status_and_duration_validation_do_not_energize_anything(self):
        h,_,_=self.host();h.cmd_feeder_run(Command(ACTION='STATUS'))
        for duration in ('nan','inf',0,-1,50.0001,50.001,51,150,601):
            with self.assertRaises(RuntimeError):h.cmd_feeder_run(Command(CHANNEL=0,DURATION=duration))
        self.assertEqual(self.starts(h),[])

    def test_original_short_sequence_waits_for_idle_in_real_time_without_g4(self):
        h,_,_=self.host();h.cmd_cassette_diag(Command(ACTION='BASELINE'))
        h.cmd_feeder_test(Command(CHANNEL=0,DIRECTION=1,DURATION_MS=1500))
        cmd=Command(ACTION='COMPARE');h.cmd_cassette_diag(cmd)
        self.assertGreaterEqual(h.reactor.monotonic(),101.5)
        self.assertEqual(h.device_until[0],101.5)
        self.assertIsNone(h.feeder_test_pending_until)
        self.assertEqual(json.loads(cmd.lines[0].split(' ',1)[1])['action'],'COMPARE')

    def test_exact_reported_script_with_real_g4_reproduces_old_error_and_passes_after_fix(self):
        env={}
        exec(compile((ROOT/'tests/fixtures/pinned_toolhead_dwell.py').read_text(),
                     'pinned_toolhead_dwell','exec'),env)
        tree=ast.parse((ROOT/'tests/fixtures/inventure_mcu_control_r5_sensors.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='InventureMCU')
        old=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_cassette_gpio_snapshot')
        old_env=dict(vars(mod));exec(compile(ast.Module(body=[old],type_ignores=[]),'old_sensor_snapshot','exec'),old_env)
        script=('INVENTURE_CASSETTE_DIAG ACTION=BASELINE\n'
                'INVENTURE_FEEDER_TEST CHANNEL=0 DIRECTION=1 DURATION_MS=1500\n'
                'G4 P1800\nINVENTURE_CASSETTE_DIAG ACTION=COMPARE')
        for original in (True,False):
            h,_,_=self.host()
            if original:h._cassette_gpio_snapshot=types.MethodType(old_env['_cassette_gpio_snapshot'],h)
            head=env['PinnedToolHeadDwell']();head.toolhead=head;head.print_time=0.
            head._flush_lookahead=lambda:None
            head.get_last_move_time=lambda:head.print_time
            head._advance_move_time=lambda value:setattr(head,'print_time',value)
            # Below the high buffer threshold, _check_pause need not sleep.
            head._check_pause=lambda:None
            dispatch=control.gcode.GCodeDispatch(h.printer);dispatch.register_output_handler(lambda line:None)
            dispatch.register_command('G4',head.cmd_G4,True)
            dispatch.register_command('INVENTURE_CASSETTE_DIAG',h.cmd_cassette_diag,True)
            dispatch.register_command('INVENTURE_FEEDER_TEST',h.cmd_feeder_test,True)
            dispatch._handle_ready()
            if original:
                with self.assertRaisesRegex(control.gcode.CommandError,'requires idle SERVICE mode'):
                    dispatch.run_script_from_command(script)
                self.assertEqual(h.reactor.monotonic(),100.)
            else:
                dispatch.run_script_from_command(script)
                self.assertGreaterEqual(h.reactor.monotonic(),101.5)
            self.assertEqual(head.print_time,1.8)

    def test_long_run_is_rejected_by_the_real_legacy_owner_dispatcher(self):
        h,ops,_=self.host();dispatch=control.gcode.GCodeDispatch(h.printer)
        dispatch.register_output_handler(lambda line:None)
        fn=service.legacy_dispatch()
        def handler(original):
            parsed=dispatch._get_extended_params(original)
            return fn(ops,'INVENTURE_FEEDER_RUN',h.cmd_feeder_run,original,parsed)
        dispatch.register_command('INVENTURE_FEEDER_RUN',handler,True);dispatch._handle_ready()
        ops.state.owner=types.SimpleNamespace(kind='print')
        with self.assertRaisesRegex(control.gcode.CommandError,'idle operations'):
            dispatch.run_script_from_command('INVENTURE_FEEDER_RUN CHANNEL=0 DURATION=50')
        self.assertEqual(self.starts(h),[])

if __name__=='__main__':unittest.main()
