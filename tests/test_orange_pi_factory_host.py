"""Factory procedure integration with independent sensor, motion and lease models."""
import math
import unittest
import test_orange_pi_load_test as support
from test_orange_pi_door_policy import Command


class FactoryDevice(support.Device):
    """Model the wire contract; real MCU C tests own electrical/timer behavior."""
    def __init__(self,h,scenario='short'):
        super().__init__(h)
        self.scenario=scenario;self.session=None;self.factory_packets=[]
        self.allow_output=False;self.last_update=self.now();self.last_edge=None
        self.pulses=0;self.starts=0;self.auto_entries=0;self.mode_history=[]
        self.running=False;self.run_end=0.;self.next_auto=0.
        self.a=0 if scenario=='wait_a' else 1;self.b=1;self.home_fail=None
        self.release_fail=None

    def active_motion(self):
        return not self.allow_output and super().active_motion()

    def active(self):
        return super().active() or bool(self.session and self.session['active'])

    def out(self,args):
        self.allow_output=bool(self.session and self.session['active']
                               and args[0]==1 and args[1]==6+self.session['tool'])
        try:return super().out(args)
        finally:self.allow_output=False

    def fraction(self):
        if self.motion is None:return 0.
        row=self.motion
        if 'fraction' in row:return row['fraction']
        return min(1.,max(0.,(self.now()-row['started'])/row['duration']))

    def stop_motion(self):
        if self.motion is None:return
        self.motion.update(fraction=self.fraction(),until=self.now(),stopped=True)

    def terminate(self,reason,heaters=True):
        if self.session:self.session.update(active=0,reason=reason)
        self.running=False;self.stop_motion()
        if heaters:
            for i,row in list(self.outputs.items()):self.outputs[i]=dict(row,duty=0,reason=2)

    def control(self,args):
        if args[0]==2 or args[0]==1 and args[1]==0:self.terminate(2)
        return super().control(args)

    def move(self,args):
        action,directions,a,b,z,e,rate,pulse,ms,mask,value,hold,token=args
        if action==1:
            self.packets.append(('motion',*args));self.run+=1
            counts=(a,b,z,e);duration=max(counts)/rate+.02
            home=bool(mask)
            failed=self.home_fail if value else self.release_fail
            if home and failed!=mask:duration=.05
            self.motion=dict(token=token,counts=counts,directions=directions,
                until=self.now()+duration,started=self.now(),duration=duration,
                stopped=False,home=home,hit=mask if home and failed!=mask else 0)
        elif action==2:self.stop_motion()
        if self.motion is None:
            return dict(run=0,token=0,phase=0,reason=0,reject=0,active=0,events=0,elapsed_ms=0,hit=0)
        row=self.motion;active=self.active_motion();sent=round(max(row['counts'])*self.fraction())
        stopped=row['stopped'];hit=0 if active else row['hit']
        return dict(run=self.run,token=row['token'],phase=1 if active else 4 if stopped else 3,
            reason=0 if active else 3 if stopped else 2 if hit else 1,
            reject=0,active=int(active),events=sent,
            elapsed_ms=round((self.now()-row['started'])*1000),hit=hit)

    def driver(self,args):
        i=args[0];row=self.motion
        fraction=self.fraction()
        if row['home'] and self.now()>=row['until'] and row['hit']:fraction=.01
        return dict(run=self.run,channel=i,planned=row['counts'][i],
                    sent=math.floor(row['counts'][i]*fraction),direction=(row['directions']>>i)&1)

    def update(self):
        now=self.now();s=self.session
        if not s or not s['active']:self.last_update=now;return
        if now>=s['deadline']:
            self.terminate(3);self.last_update=now;return
        if self.h.status.get('cable') or self.h.status.get('fault'):
            self.terminate(6);self.last_update=now;return
        t=self.h.temperatures[s['tool']]
        moving_e=bool(self.motion and self.motion['counts'][3] and super().active_motion())
        if t>=s['high'] or (s['mode'] or moving_e) and t<s['low']:
            self.terminate(4);self.last_update=now;return
        mode=s['mode']
        # A/B requests are physical model events, independent of renew packets.
        if mode==1:
            can_run=not (self.scenario=='fallback' and self.auto_entries>=2)
            self.b=int(can_run and now>=self.next_auto)
            if self.running and now>=self.run_end:
                self.running=False;self.next_auto=now+.15
            if not self.running and self.a and self.b and can_run and now>=self.next_auto:
                self.running=True;self.starts+=1;self.run_end=now+s['duration']/1000.
        elif mode in (2,3,4):self.running=True
        else:self.running=False
        observed=(self.scenario=='short' or mode==2 and self.scenario!='no_edges'
                  or mode==1 and self.auto_entries>=2)
        if moving_e and observed:
            previous=max(self.last_update,self.motion['started'])
            old_count=math.floor(previous/.2);new_count=math.floor(now/.2)
            delta=max(0,new_count-old_count)
            if delta:self.pulses+=delta;self.last_edge=now
        if mode==3:self.a=0  # operator-side model of completed physical rewind
        self.last_update=now

    def query_factory(self,args):
        action,tool,mode,monitor,duration,budget,lease,low,high,token,sequence=args
        self.factory_packets.append(tuple(args));self.update();reject=0
        if action==1:
            if self.session is None or self.session['token']!=token:
                self.session=dict(token=token,tool=tool,mode=mode,monitor=monitor,
                    duration=duration,budget=budget,low=low,high=high,sequence=sequence,
                    deadline=self.now()+lease/1000.,active=1,reason=0)
                self.mode_history.append(mode)
                if mode==1:self.auto_entries+=1
        elif action==2:
            if not self.session or not self.session['active'] or token!=self.session['token']:reject=4
            elif sequence>self.session['sequence']:
                if mode!=self.session['mode']:
                    self.running=False;self.mode_history.append(mode)
                    if mode==1:self.auto_entries+=1;self.next_auto=self.now()
                self.session.update(mode=mode,monitor=monitor,sequence=sequence,
                                    deadline=self.now()+lease/1000.)
        elif action==3:self.terminate(2,heaters=False)
        self.update()
        s=self.session or dict(token=token,tool=tool,mode=0,active=0,reason=0,sequence=0,deadline=0.)
        age=0xffffffff if self.last_edge is None else round((self.now()-self.last_edge)*1000)
        return dict(token=s['token'],active=s['active'],reason=s['reason'],reject=reject,
            tool=s['tool'],mode=s['mode'],running=int(self.running),a=self.a,b=self.b,
            pulses=self.pulses,pulse_age_ms=age,starts=self.starts,fault=0,
            sequence=s['sequence'],remaining_ms=max(0,round((s['deadline']-self.now())*1000)) if s['active'] else 0)


class FactoryHostTests(unittest.TestCase):
    def host(self,scenario='short'):
        h,ops,p13=support.LoadTests().host();h.device=FactoryDevice(h,scenario)
        h.factory_abi=1
        h.factory_feed_clock=1
        h.commands.update(control=support.Wire(h.device.control),output=support.Wire(h.device.out),
            motion=support.Wire(h.device.move),motion_driver=support.Wire(h.device.driver),
            factory_session=support.Wire(h.device.query_factory))
        original=h.refresh
        def refresh():
            original()
            # An ideal settled heater is sufficient here. Temperature dynamics
            # are verified separately against the retained PID plant tests.
            context=h.load_test_context
            if context is not None:
                i=context['tool']
                if h.device.output(6+i)['duty']:
                    h.temperatures[i]=230.;h.raw_adc[i]=round(230.*4095./660.)
            h.device.update()
        h.refresh=refresh
        return h,ops,p13

    def start(self,h,tool=0,**params):
        h.cmd_load_test(Command(ACTION='AUTO',TOOL='T%d'%tool,**params))

    def finish(self,h,limit=1500.):
        until=h.reactor.now+limit
        while h.load_test_context is not None:
            self.assertLess(h.reactor.now,until,h.load_test_last)
            h.reactor.pause(h.reactor.now+.1)
        return h.load_test_last

    def wait_session(self,h):
        until=h.reactor.now+300.
        while not h.device.session:
            self.assertIsNotNone(h.load_test_context,h.load_test_last)
            self.assertLess(h.reactor.now,until)
            h.reactor.pause(h.reactor.now+.1)

    def wait_heat(self,h):
        until=h.reactor.now+300.
        while not any(p[:2]==('output',1) for p in h.device.packets):
            self.assertIsNotNone(h.load_test_context,h.load_test_last)
            self.assertLess(h.reactor.now,until)
            h.reactor.pause(h.reactor.now+.1)

    def wait_phase(self,h,phase):
        until=h.reactor.now+1000.
        while h.load_test_context is not None and h.load_test_context['factory']['phase']!=phase:
            self.assertLess(h.reactor.now,until)
            h.reactor.pause(h.reactor.now+.1)
        self.assertIsNotNone(h.load_test_context,h.load_test_last)
        self.assertEqual(h.load_test_context['factory']['phase'],phase)

    def test_PARAMETERS_is_read_only_and_old_MCU_refuses_before_actuation(self):
        h,_,_=self.host();cmd=Command(ACTION='PARAMS',TOOL='T1');h.cmd_load_test(cmd)
        self.assertIsNone(h.load_test_context);self.assertEqual(h.device.packets,[])
        self.assertTrue(any('l1_portion' in line for line in cmd.lines+h.gcode.lines))
        h.commands.pop('factory_session')
        with self.assertRaises(RuntimeError):self.start(h,1)
        self.assertEqual(h.device.packets,[])

    def test_legacy_factory_MCU_refuses_cassette_operations_before_heating(self):
        h,_,_=self.host()
        h.factory_feed_clock=0
        for action, extra in (('AUTO',{}),('UNLOAD',{}),('MANUAL',{'FEEDER':1}),('PURGE',{'FEEDER':1})):
            with self.subTest(action=action):
                with self.assertRaisesRegex(RuntimeError, 'RC8-CONTROL-R7-FACTORY-FEED'):
                    h.cmd_procedure(Command(ACTION=action, TOOL='T0', **extra))
        self.assertEqual(h.device.packets,[])
        h.cmd_procedure(Command(ACTION='SELECT', TOOL='T0'))
        self.assertEqual(self.finish(h)['factory']['result']['terminal'], 'SELECT')

    def test_empty_A_uses_stock_L1_probe_only_for_T0(self):
        for tool in (0,1):
            with self.subTest(tool=tool):
                h,_,_=self.host('wait_a')
                self.start(h,tool)
                self.wait_phase(h,'L2')
                self.assertTrue(h.device.running)
                self.assertEqual(h.device.mode_history,[0,1,2] if tool==0 else [0,2])
                e=sum(p[6] for p in h.device.packets if p[0]=='motion' and p[1]==1)
                self.assertEqual(e,19693 if tool==0 else 0)
                h.device.a=1
                last=self.finish(h)
                self.assertEqual(last['factory']['result']['terminal'],'L4')
                self.assertEqual('L1' in last['factory']['history'],tool==0)
                self.assertEqual(h.device.auto_entries,2 if tool==0 else 1)
                self.assertEqual(last['factory']['e_steps_by_phase']['L4'],96394)

    def test_missing_forward_run_confirmation_aborts_before_E(self):
        h,_,_=self.host('wait_a')
        def query_without_forward_run(args):
            state=h.device.query_factory(args)
            if state['mode']==2:
                state['running']=0
            return state
        h.commands['factory_session']=support.Wire(query_without_forward_run)
        self.start(h)
        last=self.finish(h)
        self.assertIn('MCU did not start forward cassette feed',last['reason'])
        self.assertEqual(last['factory']['e_steps_by_phase'],{'L1':19693})
        self.assertNotIn('L3',last['factory']['history'])
        self.assertFalse(h.device.active())
        self.assertEqual(h.device.output(6)['duty'],0)

    def test_invalid_tuning_cannot_move_or_start_heating(self):
        for params in ({'LONG_MM':'nan'},{'L1_BUDGET':-1},{'FEEDER_MS':1501},
                       {'SWITCH_X':999},{'TRAVEL_SPEED':100},{'HOME_Z_MAX':5000}):
            with self.subTest(params=params):
                h,_,_=self.host()
                with self.assertRaises(RuntimeError):self.start(h,**params)
                self.assertIsNone(h.load_test_context);self.assertEqual(h.device.packets,[])

    def test_both_tools_home_cold_and_choose_their_own_heater_and_session(self):
        for tool in (0,1):
            with self.subTest(tool=tool):
                h,_,_=self.host();self.start(h,tool);self.wait_heat(h)
                packets=h.device.packets
                heat=next(i for i,p in enumerate(packets) if p[:2]==('output',1))
                homes=[p for p in packets[:heat] if p[0]=='motion' and p[1]==1 and p[10]]
                hits=[p[10] for p in homes if p[10]==p[11]]
                self.assertEqual(hits,[4,2,1] if tool==0 else [4,1,2])
                releases=[(p[10],p[11],p[2],p[3:7]) for p in homes if not p[11]]
                self.assertCountEqual(releases,[(1,0,3,(5000,5000,0,0)),
                                                (2,0,1,(5000,5000,0,0))] if tool==0 else [])
                self.assertTrue(all(p[2]==6+tool for p in packets if p[:2]==('output',1)))
                self.assertEqual(h.device.session['tool'],tool)
                self.assertEqual(h.temperatures[1-tool],25.)
                h.cmd_control_stop(Command())

    def test_cold_T0_selector_dispatches_full_positive_X_press(self):
        h,_,_=self.host()
        h.cmd_procedure(Command(ACTION='SELECT', TOOL='T0'))
        last=self.finish(h)
        self.assertEqual(last['phase'],'done',last)
        moves=[p for p in h.device.packets if p[:2]==('motion',1) and not p[10]]
        approach,press,return_move=moves[-3:]
        self.assertEqual(approach[2:7],(3,37568,35008,0,0))
        self.assertEqual(press[2:8],(3,9600,9600,0,0,2667))
        self.assertEqual(return_move[2:7],(0,47168,44608,0,0))
        self.assertFalse(any(p[:2]==('output',1) for p in h.device.packets))
        factory=last['factory']
        self.assertTrue(factory['selection_commanded'])
        self.assertEqual(factory['position'][:2],[82.8,152.5])
        legs=factory['motion_legs']
        self.assertEqual(legs['selector_approach']['target'],[139.5,154.5])
        self.assertEqual(legs['selector_return']['target'],[82.8,152.5])
        stroke=legs['selector_press']
        self.assertEqual(stroke['start'],[139.5,154.5])
        self.assertEqual(stroke['target'],[154.5,154.5])
        self.assertAlmostEqual(stroke['requested_speed'],250/60,places=3)
        self.assertAlmostEqual(stroke['effective_speed'],2667/640,places=3)
        self.assertEqual(stroke['rate'],2667)
        for driver in ('A','B'):
            self.assertEqual(stroke['drivers'][driver],dict(planned=9600,sent=9600,direction=1))

    def test_cold_T0_diagonal_uses_the_MCU_rate_cap(self):
        h,_,_=self.host()
        h.cmd_procedure(Command(ACTION='SELECT', TOOL='T0', XY_INPUT_FRAME='RAW',
                                CENTER_X=60, CENTER_Y=60))
        last=self.finish(h)
        self.assertEqual(last['phase'],'done',last)
        moves=[p for p in h.device.packets if p[:2]==('motion',1) and not p[10]]
        diagonal=moves[0]
        self.assertEqual(diagonal[3:8],(76800,0,0,0,20000))
        self.assertEqual(diagonal[9],8840)

    def test_cold_T0_sweep_uses_load_oscillation_without_E_heat_or_feeder(self):
        h,_,_=self.host()
        h.cmd_procedure(Command(ACTION='SWEEP', TOOL='T0'))
        last=self.finish(h)
        self.assertEqual(last['phase'],'done',last)
        self.assertEqual(last['factory']['result']['terminal'],'SWEEP')
        moves=[p for p in h.device.packets if p[:2]==('motion',1) and not p[10]]
        sweeps=[p for p in moves if p[3]==p[4] and p[3] in (11520,23040)]
        self.assertEqual([p[2:7] for p in sweeps[:3]],[
            (0,11520,11520,0,0),(3,23040,23040,0,0),(0,23040,23040,0,0)])
        self.assertTrue(all(not p[6] for p in moves))
        self.assertFalse(any(p[:2]==('output',1) for p in h.device.packets))
        self.assertEqual(h.device.mode_history,[0])

    def test_T0_home_requires_both_limits_to_release_before_heat(self):
        for mask in (1,2):
            with self.subTest(mask=mask):
                h,_,_=self.host();h.device.release_fail=mask
                self.start(h)
                last=self.finish(h)
                self.assertEqual(last['phase'],'failed',last)
                self.assertIn('release',last['reason'].lower())
                self.assertFalse(last['factory']['reference_valid'])
                self.assertFalse(any(p[:2]==('output',1) for p in h.device.packets))
                self.assertFalse(h.device.active())

    def test_incomplete_T0_press_cannot_claim_selection_or_start_loading(self):
        h,_,_=self.host()
        original=h.device.driver
        def omit_press_step(args):
            row=original(args)
            move=h.device.motion
            if move['directions']==3 and move['counts']==(9600,9600,0,0):
                if args[0]==0 and h.device.now()>=move['until']:
                    row['sent']-=1
            return row
        h.commands['motion_driver']=support.Wire(omit_press_step)
        self.start(h)
        last=self.finish(h)
        self.assertEqual(last['phase'],'failed',last)
        self.assertIn('missing steps',last['reason'])
        self.assertFalse(last['factory']['selection_commanded'])
        self.assertNotIn('L1',last['factory']['history'])
        self.assertFalse(any(p[:2]==('motion',1) and p[6] for p in h.device.packets))

    def test_short_path_uses_real_AUTO_start_counter_not_number_of_renewals(self):
        h,_,_=self.host();self.start(h);last=self.finish(h)
        self.assertEqual(last['phase'],'done',last)
        self.assertGreaterEqual(h.device.starts,6)
        self.assertGreater(sum(p[0]==2 for p in h.device.factory_packets),h.device.starts)
        self.assertEqual(h.device.auto_entries,1)
        self.assertNotIn(2,h.device.mode_history)
        feed=[p for p in h.device.packets if p[:2]==('motion',1) and p[6]]
        self.assertEqual([p[2:8] for p in feed[:3]],[
            (0,11520,11520,0,1036,19200),
            (3,23040,23040,0,2073,19200),
            (0,23040,23040,0,2073,19200)])
        self.assertEqual(last['factory']['motion_legs']['selector_press']['drivers']['A']['sent'],9600)
        self.assertEqual(h.device.output(6)['duty'],0);self.assertFalse(h.device.active())

    def test_factory_owner_refuses_manual_actions_and_hot_E_has_tool_specific_direction(self):
        for tool in (0,1):
            with self.subTest(tool=tool):
                h,_,_=self.host();self.start(h,tool);self.wait_phase(h,'L1')
                # Let the first coupled XY/E segment start, without completing
                # the full procedure or permitting a second owner.
                while not any(p[0]=='motion' and p[1]==1 and p[6] for p in h.device.packets):
                    h.reactor.pause(h.reactor.now+.1)
                h.cmd_load_test(Command(ACTION='STATUS'))
                self.assertFalse(h.load_test_last['ready_for_jog'])
                starts=len([p for p in h.device.packets if p[0]=='motion' and p[1]==1])
                for action in ('JOG','ZERO','BASELINE','COMPARE','CONFIRM'):
                    with self.assertRaises(RuntimeError):
                        h.cmd_load_test(Command(ACTION=action,DIR=tool,FLOW=1))
                self.assertEqual(len([p for p in h.device.packets if p[0]=='motion' and p[1]==1]),starts)
                feed=[p for p in h.device.packets if p[0]=='motion' and p[1]==1 and p[6]]
                self.assertTrue(all(bool(p[2]&8)==bool(tool) for p in feed))
                h.cmd_control_stop(Command())

    def test_long_path_stops_at_sensor_edges_before_full_budget_and_cleans(self):
        h,_,_=self.host('long');self.start(h,1);last=self.finish(h)
        self.assertEqual(last['phase'],'done',last)
        self.assertIn(2,h.device.mode_history);self.assertEqual(h.device.auto_entries,2)
        self.assertGreaterEqual(h.device.pulses,6)
        moves=[p for p in h.device.packets if p[0]=='motion' and p[1]==1 and p[6]]
        self.assertTrue(moves)
        self.assertTrue(any(p[6]>=round(250*960) for p in moves))
        self.assertEqual(h.device.output(7)['duty'],0);self.assertFalse(h.device.active())

    def test_no_long_transport_edges_fails_and_stops_every_actuator(self):
        h,_,_=self.host('no_edges');self.start(h);last=self.finish(h)
        self.assertEqual(last['phase'],'failed',last)
        self.assertIn(2,h.device.mode_history)
        self.assertFalse(h.device.active());self.assertEqual(h.device.output(6)['duty'],0)
        count=len(h.device.packets);h.reactor.pause(h.reactor.now+10.)
        self.assertEqual(len(h.device.packets),count)

    def test_final_missing_starts_runs_one_owned_reverse_fallback_then_fails(self):
        h,_,_=self.host('fallback');self.start(h);last=self.finish(h)
        self.assertEqual(last['phase'],'failed',last)
        self.assertIn(3,h.device.mode_history);self.assertIn(4,h.device.mode_history)
        self.assertEqual(h.device.auto_entries,2)
        self.assertFalse(h.device.active());self.assertEqual(h.device.output(6)['duty'],0)

    def test_common_STOP_and_mode_OFF_remove_owner_without_future_START(self):
        for kind in ('global','off','load','shutdown'):
            with self.subTest(kind=kind):
                h,_,_=self.host('long');self.start(h,1);self.wait_session(h)
                if kind=='global':h.cmd_control_stop(Command())
                elif kind=='off':h.cmd_control(Command(MODE='OFF'))
                elif kind=='load':h.cmd_load_test(Command(ACTION='STOP'))
                else:h._shutdown()
                count=len(h.device.packets);renewals=len(h.device.factory_packets)
                h.reactor.pause(h.reactor.now+10.)
                self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
                self.assertEqual(len(h.device.packets),count)
                self.assertEqual(len(h.device.factory_packets),renewals)

    def test_independent_session_lease_cuts_motion_even_with_background_heartbeat(self):
        h,_,_=self.host();self.start(h);self.wait_session(h)
        context=h.load_test_context;context['busy']=True
        h.reactor.pause(h.reactor.now+2.)
        self.assertFalse(h.device.active());self.assertEqual(h.device.session['reason'],3)
        context['busy']=False;h.reactor.pause(h.reactor.now+.5)
        self.assertIsNone(h.load_test_context)

    def test_STOP_in_hot_purge_recovery_final_clean_and_fallback_never_resumes(self):
        for phase,scenario in (('HEAT','short'),('L1','short'),('L2','wait_a'),
                               ('L3','long'),('L4','long'),('U1','fallback')):
            for kind in ('global','feeder','off'):
                with self.subTest(phase=phase,kind=kind):
                    h,_,_=self.host(scenario);self.start(h,1);self.wait_phase(h,phase)
                    if kind=='global':h.cmd_control_stop(Command())
                    elif kind=='feeder':h.cmd_feeder_test(Command(CHANNEL=1,ACTION='STOP'))
                    else:h.cmd_control(Command(MODE='OFF'))
                    count=len(h.device.packets);renewals=len(h.device.factory_packets)
                    h.reactor.pause(h.reactor.now+10.)
                    self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
                    self.assertEqual(len(h.device.packets),count)
                    self.assertEqual(len(h.device.factory_packets),renewals)

    def test_missing_any_homing_limit_fails_before_heating(self):
        for mask in (1,2,4):
            with self.subTest(mask=mask):
                h,_,_=self.host();h.device.home_fail=mask;self.start(h)
                last=self.finish(h)
                self.assertEqual(last['phase'],'failed',last)
                self.assertFalse(any(p[:2]==('output',1) for p in h.device.packets))
                self.assertFalse(h.device.active())
                self.assertFalse(last['factory']['reference_valid'])

    def test_bad_session_acknowledgement_stops_without_attempting_another_START(self):
        for field in ('token','tool','mode','sequence','remaining_ms'):
            with self.subTest(field=field):
                h,_,_=self.host();original=h.device.query_factory
                def malformed(args):
                    row=original(args)
                    if args[0]==2:
                        row[field]=(row[field]+1 if field=='token' else 1-row[field]
                                    if field=='tool' else 4 if field=='mode' else 0)
                    return row
                h.commands['factory_session']=support.Wire(malformed)
                self.start(h);last=self.finish(h)
                self.assertEqual(last['phase'],'failed',last)
                self.assertFalse(h.device.active())
                self.assertEqual(sum(p[0]==1 for p in h.device.factory_packets),1)

    def test_STOP_inside_factory_START_response_cannot_be_followed_by_motion_or_heat(self):
        h,_,_=self.host();original=h.device.query_factory
        def stop(args):
            row=original(args)
            if args[0]==1:h.cmd_control_stop(Command())
            return row
        h.commands['factory_session']=support.Wire(stop)
        self.start(h);self.finish(h)
        self.assertFalse(h.device.active());self.assertEqual(h.device.motion,None)
        self.assertFalse(any(p[:2]==('output',1) for p in h.device.packets))
        count=len(h.device.packets);h.reactor.pause(h.reactor.now+10.)
        self.assertEqual(len(h.device.packets),count)

    def test_STOP_inside_selected_PID_response_prevents_future_requests(self):
        h,_,_=self.host();original=h.device.out
        def stop(args):
            row=original(args)
            if args[0]==1:h.cmd_control_stop(Command())
            return row
        h.commands['output']=support.Wire(stop)
        self.start(h);self.finish(h)
        self.assertFalse(h.device.active())
        count=len(h.device.packets);renewals=len(h.device.factory_packets)
        h.reactor.pause(h.reactor.now+10.)
        self.assertEqual(len(h.device.packets),count)
        self.assertEqual(len(h.device.factory_packets),renewals)


if __name__=='__main__':unittest.main(verbosity=2)
