"""15-minute hot measurement session with bounded, heated E windows."""
import importlib.util
import json
import threading
from pathlib import Path
import unittest
import test_orange_pi_load_test as support
import test_orange_pi_load_temperature as temperature_support
from test_orange_pi_door_policy import Command


class Load15Tests(unittest.TestCase):
    def host(self,old=False,plant=True):
        h,_,_=support.LoadTests().host()
        if old:
            path=Path(__file__).with_name('fixtures')/'inventure_mcu_control_r5_load230_r3.py'
            spec=importlib.util.spec_from_file_location('door_test_host.previous_load230_r3',path)
            previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
            h.__class__=previous.InventureMCU
        model=temperature_support.ThermalPlant(h) if plant else None
        h.cmd_load_test(Command(ACTION='HEAT'))
        if plant:h.reactor.pause(300.)
        else:
            h.reactor.pause(100.25);support.LoadTests().ready(h)
        self.assertTrue(h.load_test_last['ready_for_jog'])
        return h,model

    def finish_jog(self,h):
        until=h.reactor.now+60.
        while h.load_test_context is not None and h.load_test_context['jog'] is not None:
            if h.reactor.now>=until:self.fail('Jog did not finish within its bound')
            h.reactor.pause(h.reactor.now+.1)
        self.assertIsNotNone(h.load_test_context,h.load_test_last)

    def test_hold_starts_at_ready_and_all_commands_keep_its_original_deadline(self):
        h,_,_=support.LoadTests().host();h.cmd_load_test(Command(ACTION='HEAT'))
        h.reactor.pause(130.);context=h.load_test_context
        self.assertIsNone(context['hold_started'])
        support.LoadTests().ready(h)
        deadline=context['deadline']
        self.assertEqual(deadline-context['hold_started'],900.)
        h.cmd_load_test(Command(ACTION='ZERO'));h.cmd_load_test(Command(ACTION='STATUS'))
        h.cmd_load_test(Command(ACTION='COMPARE'))
        h.cmd_load_test(Command(ACTION='JOG',DIR=0,STEPS=960,RATE=4000));self.finish_jog(h)
        h.temperatures[0]=214.;h.reactor.pause(h.reactor.now+.25)
        support.LoadTests().ready(h)
        h.cmd_load_test(Command(ACTION='CONFIRM',FLOW=1))
        self.assertEqual(context['deadline'],deadline)
        h.reactor.pause(deadline-.1);self.assertIs(h.load_test_context,context)
        h.reactor.pause(deadline+.3)
        self.assertIsNone(h.load_test_context)
        self.assertEqual(h.load_test_last['reason'],'hold_duration_finished')
        self.assertEqual(h.load_test_last['phase'],'done')
        self.assertFalse(h.device.active())

    def test_three_long_jogs_then_fourth_work_in_both_directions_with_heat_on(self):
        h,plant=self.host()
        for direction in (1,0):
            for _ in range(4):
                self.assertTrue(h._load_test_jog_ready(h.load_test_context))
                h.cmd_load_test(Command(ACTION='JOG',DIR=direction,STEPS=19000,RATE=4000))
                self.finish_jog(h)
                last=h.load_test_context['last_jog']
                self.assertEqual((last['sent'],last['windows']),(19000,10))
                self.assertTrue(h._load_test_jog_ready(h.load_test_context))
        m=h.load_test_last['measurement']
        self.assertEqual((m['dir0_steps'],m['dir1_steps'],m['net_dir0_steps']),(76000,76000,0))
        self.assertEqual(m['completed_jogs'],8)
        starts=support.LoadTests().starts(h,'motion')
        self.assertEqual(sum(p[6] for p in starts),152000)
        self.assertTrue(all(p[3:6]==(0,0,0) and p[6]<=2000 and p[9]<=750 for p in starts))
        self.assertTrue(any(power>0 and now>=300. for now,_,power in plant.rows))
        temps=[temp for now,temp,_ in plant.rows if now>=300.]
        self.assertGreater(min(temps),215.);self.assertLess(max(temps),238.)

    def test_exact_previous_R3_loses_readiness_under_repeated_long_heat_OFF_jogs(self):
        h,_=self.host(old=True)
        refused=False
        for _ in range(4):
            try:h.cmd_load_test(Command(ACTION='JOG',DIR=1,STEPS=19000,RATE=4000))
            except RuntimeError as exc:
                self.assertIn('ready_for_jog',str(exc));refused=True;break
            self.finish_jog(h)
        self.assertTrue(refused)

    def test_ZERO_cumulative_actual_steps_and_flow_mark_are_independent_of_heating(self):
        h,_=self.host();deadline=h.load_test_context['deadline']
        h.cmd_load_test(Command(ACTION='JOG',DIR=1,STEPS=19000,RATE=4000));self.finish_jog(h)
        h.cmd_load_test(Command(ACTION='ZERO'))
        for steps in (19000,19000,960):
            h.cmd_load_test(Command(ACTION='JOG',DIR=0,STEPS=steps,RATE=4000));self.finish_jog(h)
        h.cmd_load_test(Command(ACTION='JOG',DIR=1,STEPS=960,RATE=4000));self.finish_jog(h)
        h.cmd_load_test(Command(ACTION='CONFIRM',FLOW=1))
        m=h.load_test_last['measurement']
        self.assertTrue(m['zeroed_by_operator']);self.assertEqual(m['dir0_steps'],38960)
        self.assertEqual(m['dir1_steps'],960);self.assertEqual(m['net_dir0_steps'],38000)
        self.assertAlmostEqual(m['net_dir0_mm'],39.583,3)
        self.assertEqual(m['flow_mark']['net_dir0_steps'],38000)
        h.cmd_load_test(Command(ACTION='JOG',DIR=0,STEPS=960,RATE=4000));self.finish_jog(h)
        self.assertEqual(h.load_test_last['measurement']['flow_mark']['net_dir0_steps'],38000)
        h.cmd_load_test(Command(ACTION='ZERO'))
        self.assertFalse(h.load_test_last['flow_confirmed_by_operator'])
        self.assertEqual(h.load_test_last['measurement']['net_dir0_steps'],0)
        self.assertEqual(h.load_test_context['deadline'],deadline)

    def test_STOP_OFF_and_shutdown_cancel_all_future_windows_and_heater_renewals(self):
        for mode in ('global','off','dedicated','shutdown'):
            h,_=self.host();h.cmd_load_test(Command(ACTION='JOG',DIR=0,STEPS=19000,RATE=4000))
            h.reactor.pause(h.reactor.now+.8)
            if mode=='global':h.cmd_control_stop(Command())
            elif mode=='off':h.cmd_control(Command(MODE='OFF'))
            elif mode=='dedicated':h.cmd_load_test(Command(ACTION='STOP'))
            else:h._shutdown()
            count=len(h.device.packets);h.reactor.pause(h.reactor.now+20.)
            self.assertEqual(len(h.device.packets),count)
            self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
            self.assertLess(h.load_test_last['measurement']['dir0_steps'],19000)

    def test_current_guard_or_expired_lease_during_E_never_restarts_a_window(self):
        for kind in ('temperature','guard','lease'):
            h,_=self.host(plant=False);support.LoadTests().ready(h)
            h.temperatures[0]=227.;h.refresh()
            h.cmd_load_test(Command(ACTION='JOG',DIR=0,STEPS=19000,RATE=4000))
            if kind=='temperature':h.temperatures[0]=241.
            elif kind=='guard':h.device.outputs[6].update(duty=0,reason=4)
            else:h.device.outputs[6]['until']=h.reactor.now-.1
            h.reactor.pause(h.reactor.now+2.)
            self.assertIsNone(h.load_test_context)
            self.assertEqual(len(support.LoadTests().starts(h,'motion')),1)
            self.assertFalse(h.device.active())

    def test_unfinished_jog_waits_for_reheating_then_finishes_exact_remaining_steps(self):
        h,_=self.host(plant=False);support.LoadTests().ready(h)
        h.cmd_load_test(Command(ACTION='JOG',DIR=0,STEPS=19000,RATE=4000))
        h.temperatures[0]=214.;h.reactor.pause(h.reactor.now+.9)
        self.assertIsNotNone(h.load_test_context)
        self.assertEqual(h.load_test_context['phase'],'reheating')
        self.assertEqual(len(support.LoadTests().starts(h,'motion')),1)
        self.assertGreater(h.device.output(6)['duty'],0)
        h.temperatures[0]=230.;h.reactor.pause(h.reactor.now+12.)
        self.assertIsNone(h.load_test_context['jog'])
        self.assertEqual(h.load_test_context['last_jog']['sent'],19000)
        self.assertEqual(h.load_test_last['measurement']['dir0_steps'],19000)

    def test_repeated_identical_partial_driver_readbacks_do_not_double_count(self):
        h,_=self.host(plant=False);support.LoadTests().ready(h)
        original=h.device.driver
        def partial(args):
            row=original(args)
            if args[0]==3 and h.device.active_motion():row['sent']=min(200,row['planned'])
            return row
        h.commands['motion_driver']=support.Wire(partial)
        h.cmd_load_test(Command(ACTION='ZERO'))
        h.cmd_load_test(Command(ACTION='JOG',DIR=0,STEPS=19000,RATE=4000))
        h.reactor.pause(h.reactor.now+.4)
        self.assertEqual(h.load_test_context['measurement']['dir0_steps'],200)
        self.finish_jog(h)
        self.assertEqual(h.load_test_last['measurement']['dir0_steps'],19000)

    def test_STOP_during_window_START_response_never_schedules_a_later_motor_window(self):
        h,_=self.host();original=h.device.move
        def stop_during_reply(args):
            row=original(args)
            if args[0]==1:h.cmd_control_stop(Command())
            return row
        h.commands['motion']=support.Wire(stop_during_reply)
        h.cmd_load_test(Command(ACTION='JOG',DIR=0,STEPS=19000,RATE=4000))
        h.reactor.pause(h.reactor.now+10.)
        self.assertIsNone(h.load_test_context)
        self.assertEqual(len(support.LoadTests().starts(h,'motion')),1)
        self.assertFalse(h.device.active())
        reports=[json.loads(s.split(' ',1)[1]) for s in h.gcode.lines if s.startswith('V9_LOAD_TEST ')]
        self.assertEqual(reports[-1]['phase'],'stopped')

    def test_driver_poll_is_atomic_against_the_next_window_START(self):
        h,_=self.host(plant=False);context=h.load_test_context
        h.motion_mutex=threading.Lock()
        h.device.move((1,0,0,0,0,960,960,10,1250,0,0,0,1))
        h.device.motion['until']=h.reactor.now-.1
        first_driver=threading.Event();release=threading.Event();attempt=threading.Event();started=threading.Event()
        original_driver=h.device.driver;original_move=h.device.move;results={}
        def driver(args):
            if threading.current_thread().name=='old_reader' and args[0]==0:
                first_driver.set()
                if not release.wait(2.):raise RuntimeError('Test reader release timed out')
            return original_driver(args)
        def move(args):
            if args[0]==1:started.set()
            return original_move(args)
        h.commands['motion_driver']=support.Wire(driver);h.commands['motion']=support.Wire(move)
        def reader():
            try:results['old']=h._motion_query()
            except Exception as exc:results['error']=exc
        def writer():
            attempt.set()
            try:results['new']=h._motion_query(1,(0,0,0,-480),960,owner=context)
            except Exception as exc:results['error']=exc
        a=threading.Thread(target=reader,name='old_reader');b=threading.Thread(target=writer)
        a.start();self.assertTrue(first_driver.wait(2.));b.start();self.assertTrue(attempt.wait(2.))
        try:self.assertFalse(started.wait(.03))
        finally:release.set();a.join(2.);b.join(2.)
        self.assertNotIn('error',results)
        self.assertEqual(results['old']['run'],1);self.assertEqual(results['new']['run'],2)


if __name__=='__main__':unittest.main(verbosity=2)
