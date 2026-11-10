"""Continuous MCU-owned loading, independent renewals, and PID during E."""
import json
from pathlib import Path
import unittest
import test_orange_pi_cassette_load as cassette_support
import test_orange_pi_load_test as support
import test_orange_pi_load_temperature as temperature_support
from test_orange_pi_door_policy import adapter as mod,Command


class StreamDevice(cassette_support.JointDevice):
    def __init__(self,h):
        super().__init__(h);self.stream=None;self.stream_packets=[];self.allow_output=False
    def active_motion(self):return not self.allow_output and super().active_motion()
    def out(self,args):
        self.allow_output=bool(args[0]==1 and args[1]==6 and self.stream and self.stream['active'])
        try:return super().out(args)
        finally:self.allow_output=False
    def sent(self):
        if self.stream is None:return 0
        return min(self.stream['planned'],max(0,int((self.now()-self.stream['start']-.02)*self.stream['rate'])))
    def stop(self,reason):
        self.stream.update(sent=self.sent(),active=0,reason=reason)
        self.h.device_until=[0.,0.]
        if reason!=1:
            if self.motion:self.motion.update(until=self.now(),stopped=True)
            for i,row in list(self.outputs.items()):self.outputs[i]=dict(row,duty=0,reason=2)
    def guard(self):
        if not self.stream or not self.stream['active']:return
        if self.sent()==self.stream['planned']:self.stop(1)
        elif self.now()>=self.stream['deadline']:self.stop(3)
        elif self.h.status.get('cable') or self.h.status.get('fault'):self.stop(6)
        elif self.h.temperatures[0]<205. or self.h.temperatures[0]>=240. or self.output(6)['reason']>=3:self.stop(4)
    def control(self,args):
        if self.stream and self.stream['active'] and (args[0]==2 or args[0]==1 and args[1]==0):self.stop(2)
        return super().control(args)
    def query_stream(self,args):
        action,steps,rate,maximum,lease,low,high,token,sequence=args[:9]
        assert args[9:11]==[0,0] and args[11:] in ([0,1],[1,0])
        self.stream_packets.append(tuple(args));self.guard();reject=0
        if action==1:
            if self.stream is None or token!=self.stream['token']:
                self.move((1,8 if args[11] else 0,0,0,0,steps,rate,10,maximum,0,0,0,token))
                self.stream=dict(token=token,active=1,reason=0,planned=steps,sent=0,
                    elapsed_ms=0,remaining_ms=lease,sequence=0,feeder=1,
                    start=self.now(),rate=rate,deadline=self.now()+lease/1000.)
                self.h.device_until=[self.stream['deadline'],0.]
        elif action==2:
            if not self.stream['active'] or self.stream['token']!=token:reject=4
            elif sequence>self.stream['sequence']:
                self.stream.update(sequence=sequence,deadline=self.now()+lease/1000.)
                self.h.device_until[0]=self.stream['deadline']
        row=self.stream
        return dict(token=row['token'],active=row['active'],reason=row['reason'],reject=reject,
            planned=row['planned'],sent=self.sent() if row['active'] else row['sent'],
            elapsed_ms=round((self.now()-row['start'])*1000),
            remaining_ms=max(0,round((row['deadline']-self.now())*1000)) if row['active'] else 0,
            sequence=row['sequence'],feeder=int(self.now()<self.h.device_until[0]))


class LoadStreamTests(unittest.TestCase):
    def host(self,plant=False):
        h,ops,p13,_=cassette_support.CassetteLoadTests().host();h.device=StreamDevice(h)
        h.commands.update(control=support.Wire(h.device.control),output=support.Wire(h.device.out),
            motion=support.Wire(h.device.move),motion_driver=support.Wire(h.device.driver),
            load_stream=support.Wire(h.device.query_stream))
        refresh=h.refresh
        def guarded():refresh();h.device.guard()
        h.refresh=guarded
        model=temperature_support.ThermalPlant(h) if plant else None
        return h,ops,p13,model
    def start(self,h,**params):h.cmd_load_test(Command(ACTION='LOAD',SMOOTH=1,**params))
    def ready(self,h):cassette_support.CassetteLoadTests().ready(h)
    def finish(self,h):cassette_support.CassetteLoadTests().finish(h)

    def test_full_observed_distance_is_one_E_run_with_PID_and_continuous_feeder(self):
        h,_,_,plant=self.host(plant=True);self.start(h);self.finish(h)
        moves=support.LoadTests().starts(h,'motion');packets=h.device.stream_packets
        self.assertEqual(len(moves),1);self.assertEqual(moves[0][2:8],(0,0,0,0,171800,1536))
        self.assertEqual(sum(p[0]==1 for p in packets),1);self.assertGreater(sum(p[0]==2 for p in packets),100)
        self.assertEqual(h.commands['feeder'].calls,[])
        self.assertTrue(h.load_test_last['cassette_load']['continuous_motion'])
        self.assertEqual(h.load_test_last['measurement']['dir0_steps'],171800)
        self.assertEqual(h.load_test_last['last_jog']['windows'],1)
        self.assertLess(h.load_test_last['last_jog']['elapsed_s'],113.)
        self.assertTrue(h.load_test_last['cassette_load']['feed_completed'])
        self.assertEqual(h.device_until,[0.,0.]);self.assertFalse(h.load_test_last['load_completed'])
        self.assertGreater(sum(p[0]=='output' and p[1]==1 for p in h.device.packets),100)
        samples=[t for now,t,power in plant.rows if h.device.stream['start']<=now<=h.device.stream['start']+112.]
        self.assertGreater(min(samples),215.);self.assertLess(max(samples),238.)

    def test_missing_stream_MCU_is_refused_before_heating_and_legacy_remains_available(self):
        h,_,_,_=cassette_support.CassetteLoadTests().host()
        with self.assertRaisesRegex(RuntimeError,'R6-SMOOTH'):self.start(h)
        self.assertIsNone(h.load_test_context);self.assertEqual(h.device.packets,[])
        h.cmd_load_test(Command(ACTION='LOAD',SMOOTH=0))
        self.assertFalse(h.load_test_context['cassette_load']['smooth'])

    def test_smooth_loading_requires_the_matched_cassette_speed(self):
        h,_,_,_=self.host()
        with self.assertRaisesRegex(RuntimeError,'RATE=1536'):self.start(h,RATE=960)
        self.assertIsNone(h.load_test_context);self.assertEqual(h.device.packets,[])

    def test_no_renewals_after_any_common_stop_or_selected_feeder_stop(self):
        for kind in ('global','off','load','feeder','shutdown'):
            h,_,_,_=self.host();self.start(h);self.ready(h)
            if kind=='global':h.cmd_control_stop(Command())
            elif kind=='off':h.cmd_control(Command(MODE='OFF'))
            elif kind=='load':h.cmd_load_test(Command(ACTION='STOP'))
            elif kind=='feeder':h.cmd_feeder_test(Command(CHANNEL=0,ACTION='STOP'))
            else:h._shutdown()
            count=len(h.device.stream_packets);h.reactor.pause(h.reactor.now+5.)
            self.assertEqual(len(h.device.stream_packets),count)
            self.assertFalse(h.device.active());self.assertIsNone(h.load_test_context)
            self.assertFalse(h.load_test_last['cassette_load']['feed_completed'])

    def test_independent_lease_stops_motion_when_load_timer_cannot_renew(self):
        h,_,_,_=self.host();self.start(h);self.ready(h)
        context=h.load_test_context;context['busy']=True
        count=len(h.device.stream_packets);h.reactor.pause(h.reactor.now+2.)
        self.assertFalse(h.device.active());self.assertEqual(h.device.stream['reason'],3)
        context['busy']=False;h.reactor.pause(h.reactor.now+.5)
        self.assertIsNone(h.load_test_context)
        self.assertEqual(sum(p[0]==1 for p in h.device.stream_packets),1)
        self.assertLessEqual(len(h.device.stream_packets),count+1)

    def test_current_guard_stale_samples_and_owner_loss_abort_without_a_restart(self):
        for cause in ('temperature','cable','adc','owner','lease'):
            h,ops,_,_=self.host();self.start(h);self.ready(h)
            if cause=='temperature':h.temperatures[0]=241.
            elif cause=='cable':h.status['cable']=1
            elif cause=='adc':h.stale_adc={0};h.adc_received[0]=h.reactor.now-2.
            elif cause=='owner':ops.state.owner='other'
            else:h.device.outputs[6].update(duty=32,until=h.reactor.now-1.,reason=3)
            h.reactor.pause(h.reactor.now+2.)
            self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
            self.assertEqual(sum(p[0]==1 for p in h.device.stream_packets),1)

    def test_bad_token_counter_or_renewal_acknowledgement_stops_the_existing_run(self):
        for field in ('token','sent','sequence'):
            h,_,_,_=self.host();original=h.device.query_stream
            def malformed(args):
                row=original(args)
                if args[0]==2:
                    row[field]=row[field]+1 if field=='token' else 999999 if field=='sent' else 0
                return row
            h.commands['load_stream']=support.Wire(malformed)
            self.start(h);self.ready(h);h.reactor.pause(h.reactor.now+2.)
            self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
            self.assertEqual(sum(p[0]==1 for p in h.device.stream_packets),1)

    def test_STOP_during_START_response_cannot_be_followed_by_PID_or_renewal(self):
        h,_,_,_=self.host();original=h.device.query_stream
        def stop(args):
            row=original(args)
            if args[0]==1:h.cmd_control_stop(Command())
            return row
        h.commands['load_stream']=support.Wire(stop);self.start(h);self.ready(h)
        count=len(h.device.packets);h.reactor.pause(h.reactor.now+5.)
        self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
        self.assertEqual(len(h.device.packets),count);self.assertEqual(len(h.device.stream_packets),1)

    def test_actual_compiled_dictionary_matches_the_host_stream_protocol(self):
        path=Path(__file__).with_name('fixtures')/'control_r6.dict'
        d=json.loads(path.read_bytes())
        self.assertEqual(d['config']['INVENTURE_LOAD_STREAM_ABI'],1)
        self.assertIn(mod.LOAD_STREAM_COMMAND,d['commands']);self.assertIn(mod.LOAD_STREAM_RESPONSE,d['responses'])
        self.assertEqual(d['config']['INVENTURE_MCU_RELEASE'],'RC8-CONTROL-R6-SMOOTH')


if __name__=='__main__':unittest.main(verbosity=2)
