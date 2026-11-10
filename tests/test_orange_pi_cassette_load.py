"""One owned warm-up/feed operation for the physically checked left cassette."""
import json
import unittest
import test_orange_pi_load_test as support
import test_orange_pi_load_temperature as temperature_support
from test_orange_pi_door_policy import Command


class JointDevice(support.Device):
    def active(self):
        return super().active() or any(self.now()<t for t in self.h.device_until)
    def control(self,args):
        if args[0]==2 or args[0]==1 and args[1]==0:self.h.device_until=[0.,0.]
        return super().control(args)


class CassetteLoadTests(unittest.TestCase):
    def host(self,plant=False):
        h,ops,p13=support.LoadTests().host();h.device=JointDevice(h)
        h.commands.update(control=support.Wire(h.device.control),output=support.Wire(h.device.out),
            motion=support.Wire(h.device.move),motion_driver=support.Wire(h.device.driver))
        original=h.commands['feeder'].send;h.feeder_trace=[]
        def feeder(args):
            h.feeder_trace.append((h.reactor.now,tuple(args),h.temperatures[0],h.device.active_motion()))
            return original(args)
        h.commands['feeder'].send=feeder
        model=temperature_support.ThermalPlant(h) if plant else None
        return h,ops,p13,model

    def ready(self,h):
        h.temperatures[h.load_test_context['tool']]=230.
        h.load_test_context['pid'].update(time=h.reactor.now,temperature=230.,derivative=0.,integral=0.)
        h.reactor.pause(h.reactor.now+2.5)

    def finish(self,h,limit=850.):
        until=h.reactor.now+limit
        while h.load_test_context is not None and not h.load_test_context['cassette_load']['feed_completed']:
            if h.reactor.now>=until:self.fail('Cassette feed exceeded its bound')
            h.reactor.pause(h.reactor.now+.1)
        self.assertIsNotNone(h.load_test_context,h.load_test_last)

    def starts(self,h):return support.LoadTests().starts(h,'motion')
    def feed_starts(self,h):return [r for r in h.feeder_trace if r[1][2]]

    def test_one_command_heats_then_feeds_tube_and_five_recorded_head_lengths(self):
        h,_,_,plant=self.host(plant=True);before=h.reactor.now
        h.cmd_load_test(Command(ACTION='LOAD'))
        self.assertEqual(h.reactor.now,before);self.assertEqual(self.starts(h),[])
        h.reactor.pause(130.);self.assertEqual(self.starts(h),[]);self.assertEqual(self.feed_starts(h),[])
        self.finish(h)
        plan=h.load_test_last['cassette_load'];motion=self.starts(h);feed=self.feed_starts(h)
        self.assertEqual((plan['tube_steps'],plan['head_steps'],plan['total_steps']),(76800,95000,171800))
        self.assertEqual(plan['sent'],171800)
        self.assertEqual(sum(p[6] for p in motion),171800)
        self.assertTrue(all(p[2]==0 and p[3:6]==(0,0,0) and 0<p[6]<=768 and p[7]==1536 and p[9]<=750 for p in motion))
        self.assertEqual(len(motion),len(feed));self.assertTrue(all(p[1][:2]==(0,1) and 0<p[1][2]<=500 for p in feed))
        self.assertTrue(all(p[2]>=215. and p[3] for p in feed))
        self.assertEqual(sum(p[1][2] for p in feed[:100]),50000)
        self.assertEqual(plan['feeder_ms_requested'],sum(p[1][2] for p in feed))
        self.assertEqual(h.device_until,[0.,0.]);self.assertTrue(plan['feed_completed'])
        self.assertEqual(h.load_test_last['phase'],'awaiting_flow_confirmation')
        self.assertFalse(h.load_test_last['load_completed']);self.assertFalse(h.load_test_last['flow_confirmed_by_operator'])
        self.assertEqual(h.load_test_context['deadline']-h.load_test_context['hold_started'],900.)
        self.assertGreater(max(t for t,_,power in plant.rows if power>0),feed[-1][0])
        h.cmd_load_test(Command(ACTION='CONFIRM',FLOW=1))
        self.assertTrue(h.load_test_last['flow_confirmed_by_operator']);self.assertTrue(h.load_test_last['cleaning_pending'])
        self.assertFalse(h.load_test_last['load_completed'])

    def test_explicit_head_and_purge_budgets_split_at_exact_boundaries(self):
        h,_,_,_=self.host();h.cmd_load_test(Command(ACTION='LOAD',TUBE_SECONDS=0,HEAD_STEPS=1000,PURGE_STEPS=537))
        self.ready(h);self.finish(h)
        self.assertEqual([p[6] for p in self.starts(h)],[768,232,537])
        self.assertEqual(h.load_test_last['cassette_load']['sent'],1537)
        self.assertEqual(h.load_test_last['measurement']['dir0_steps'],1537)
        self.assertEqual(h.load_test_last['cassette_load']['purge_steps'],537)

    def test_slow_E_uses_shorter_cassette_pulses_and_preserves_the_same_distance_budget(self):
        h,_,_,_=self.host();h.cmd_load_test(Command(ACTION='LOAD',TUBE_SECONDS=0,HEAD_STEPS=960,RATE=960))
        self.ready(h);self.finish(h)
        self.assertEqual([p[6] for p in self.starts(h)],[480,480])
        self.assertEqual([p[1][2] for p in self.feed_starts(h)],[313,313])
        self.assertEqual(h.load_test_last['cassette_load']['sent'],960)

    def test_invalid_or_unqualified_plans_never_energize_any_actuator(self):
        for values in ({'TOOL':'T2'},{'TUBE_SECONDS':'nan'},{'TUBE_SECONDS':50.1},
                       {'TUBE_SECONDS':-1},{'HEAD_STEPS':0},{'HEAD_STEPS':192001},
                       {'PURGE_STEPS':-1},{'RATE':4000},{'DURATION':10}):
            h,_,_,_=self.host()
            with self.assertRaises(RuntimeError):h.cmd_load_test(Command(ACTION='LOAD',**values))
            self.assertIsNone(h.load_test_context);self.assertEqual(h.device.packets,[])
            self.assertEqual(self.feed_starts(h),[])

    def test_pending_automatic_load_owns_manual_actions_before_temperature_is_ready(self):
        h,_,_,_=self.host();h.cmd_load_test(Command(ACTION='LOAD'))
        for action in ('ZERO','JOG','BASELINE','COMPARE','CONFIRM'):
            with self.assertRaisesRegex(RuntimeError,'Cassette load is heating or feeding'):
                h.cmd_load_test(Command(ACTION=action,DIR=0,FLOW=1))
        self.assertEqual(self.starts(h),[]);self.assertEqual(self.feed_starts(h),[])
        h.cmd_load_test(Command(ACTION='STATUS'))
        self.assertEqual(h.load_test_last['cassette_load']['stage'],'heating')

    def test_all_stop_paths_cancel_both_motors_and_all_future_heater_renewals(self):
        for mode in ('global','off','load','feeder_test','feeder_run','legacy','shutdown'):
            for hot in (False,True):
                h,_,_,_=self.host();h.cmd_load_test(Command(ACTION='LOAD'))
                if hot:self.ready(h)
                if mode=='global':h.cmd_control_stop(Command())
                elif mode=='off':h.cmd_control(Command(MODE='OFF'))
                elif mode=='load':h.cmd_load_test(Command(ACTION='STOP'))
                elif mode=='feeder_test':h.cmd_feeder_test(Command(CHANNEL=0,ACTION='STOP'))
                elif mode=='feeder_run':h.cmd_feeder_run(Command(ACTION='STOP'))
                elif mode=='legacy':h.cmd_feeder(Command(CHANNEL=0,DURATION_MS=0))
                else:h._shutdown()
                self.assertIsNone(h.load_test_context)
                if mode!='shutdown':self.assertFalse(h.device.active())
                count=(len(h.device.packets),len(h.feeder_trace))
                h.reactor.pause(h.reactor.now+50.)
                self.assertEqual(count,(len(h.device.packets),len(h.feeder_trace)))
                self.assertFalse(h.device.active())
                self.assertFalse(h.load_test_last['cassette_load']['feed_completed'])

    def test_cooling_pauses_both_motors_then_resumes_only_the_unfinished_budget(self):
        h,_,_,_=self.host();h.cmd_load_test(Command(ACTION='LOAD',TUBE_SECONDS=0,HEAD_STEPS=4000))
        self.ready(h);h.temperatures[0]=214.;h.reactor.pause(h.reactor.now+.9)
        self.assertIsNotNone(h.load_test_context);self.assertEqual(h.load_test_context['phase'],'reheating')
        self.assertFalse(any(h.reactor.now<t for t in h.device_until))
        count=len(self.feed_starts(h));h.reactor.pause(h.reactor.now+1.)
        self.assertEqual(len(self.feed_starts(h)),count)
        self.assertGreater(h.device.output(6)['duty'],0)
        self.ready(h);self.finish(h)
        self.assertEqual(sum(p[6] for p in self.starts(h)),4000)
        self.assertEqual(h.load_test_last['cassette_load']['sent'],4000)

    def test_guard_cable_and_owner_failures_do_not_renew_a_cassette_window(self):
        for cause in ('cable','stale','overheat','output_guard','lease','owner','p13'):
            h,ops,p13,_=self.host();h.cmd_load_test(Command(ACTION='LOAD'));self.ready(h)
            count=len(self.feed_starts(h))
            if cause=='cable':h.status['cable']=1
            elif cause=='stale':h.stale_board=True;h.status_received=h.reactor.now-1.
            elif cause=='overheat':h.temperatures[0]=241.
            elif cause=='output_guard':h.device.outputs[6].update(duty=0,reason=4)
            elif cause=='lease':
                h.load_test_context['duty']=10;h.device.outputs[6].update(duty=10,until=h.reactor.now-.1)
            elif cause=='owner':ops.state.owner='different_owner'
            else:p13.running=True
            h.reactor.pause(h.reactor.now+3.)
            self.assertIsNone(h.load_test_context,h.load_test_last)
            self.assertEqual(len(self.feed_starts(h)),count);self.assertFalse(h.device.active())

    def test_STOP_while_E_START_is_being_acknowledged_never_starts_the_cassette(self):
        h,_,_,_=self.host();original=h.device.move
        def stopped(args):
            result=original(args)
            if args[0]==1:h.cmd_control_stop(Command())
            return result
        h.commands['motion']=support.Wire(stopped)
        h.cmd_load_test(Command(ACTION='LOAD'));self.ready(h);h.reactor.pause(h.reactor.now+10.)
        self.assertIsNone(h.load_test_context);self.assertEqual(self.feed_starts(h),[])
        self.assertEqual(len(self.starts(h)),1);self.assertFalse(h.device.active())

    def test_feeder_write_failure_stops_the_already_acknowledged_E_window(self):
        h,_,_,_=self.host()
        def failed(args):raise RuntimeError('feeder serial write failed')
        h.commands['feeder'].send=failed
        h.cmd_load_test(Command(ACTION='LOAD'));self.ready(h);h.reactor.pause(h.reactor.now+10.)
        self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
        self.assertEqual(len(self.starts(h)),1)
        self.assertIn('feeder serial write failed',h.load_test_last['reason'])

    def test_STOP_during_feeder_write_prevents_every_subsequent_window(self):
        h,_,_,_=self.host();original=h.commands['feeder'].send
        def stopped(args):
            result=original(args)
            if args[2]:h.cmd_control_stop(Command())
            return result
        h.commands['feeder'].send=stopped
        h.cmd_load_test(Command(ACTION='LOAD'));self.ready(h);h.reactor.pause(h.reactor.now+10.)
        self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
        self.assertEqual(len(self.starts(h)),1);self.assertEqual(len(self.feed_starts(h)),1)
        reports=[json.loads(s.split(' ',1)[1]) for s in h.gcode.lines if s.startswith('V9_LOAD_TEST ')]
        self.assertEqual(reports[-1]['phase'],'stopped')


if __name__=='__main__':unittest.main(verbosity=2)
