"""Right cassette, tool-specific E polarity, selected PID and every stop path."""
import unittest
import test_orange_pi_cassette_load as cassette_support
import test_orange_pi_load_stream as stream_support
import test_orange_pi_load_temperature as temperature_support
import test_orange_pi_load_test as load_support
from test_orange_pi_door_policy import Command


class T1LoadTests(unittest.TestCase):
    def host(self,plant=False):
        h,ops,p13,_=stream_support.LoadStreamTests().host()
        model=temperature_support.ThermalPlant(h,tool=1) if plant else None
        return h,ops,p13,model

    def start(self,h,action='LOAD',**params):
        h.cmd_load_test(Command(ACTION=action,TOOL='T1',SMOOTH=1,**params))

    def ready(self,h):cassette_support.CassetteLoadTests().ready(h)

    def finish(self,h):cassette_support.CassetteLoadTests().finish(h)

    def test_full_right_load_heats_T1_then_runs_E_DIR1_and_cassette1_DIR1_continuously(self):
        h,_,_,plant=self.host(plant=True);h.temperatures[0]=230.
        self.start(h);h.reactor.pause(130.)
        self.assertEqual(h.device.stream_packets,[])  # A hot T0 cannot qualify a cold T1.
        self.assertGreater(h.device.output(7)['duty'],0)
        self.assertEqual(h.device.output(6)['duty'],0)
        self.finish(h)
        packets=h.device.stream_packets
        self.assertEqual(sum(p[0]==1 for p in packets),1)
        self.assertTrue(all(p[9:]==(1,1,1,1) for p in packets))
        moves=load_support.LoadTests().starts(h,'motion')
        self.assertEqual(len(moves),1)
        self.assertEqual(moves[0][2:8],(8,0,0,0,171800,1536))
        self.assertEqual(h.commands['feeder'].calls,[])
        self.assertEqual(h.device_until,[0.,0.])
        self.assertTrue(all(p[2]==7 for p in load_support.LoadTests().starts(h)))
        h.cmd_load_test(Command(ACTION='STATUS'))
        last=h.load_test_last;plan=last['cassette_load'];m=last['measurement']
        self.assertEqual((last['tool'],plan['channel'],plan['e_direction'],plan['cassette_direction']),('T1',1,1,1))
        self.assertEqual((m['dir0_steps'],m['dir1_steps'],m['net_feed_steps']),(0,171800,171800))
        self.assertEqual(m['feed_direction'],1)
        self.assertEqual(last['phase'],'awaiting_flow_confirmation')
        self.assertFalse(plan['automatic_tool_switch'])
        self.assertFalse(plan['mechanical_tool_selection_verified'])
        self.assertFalse(last['load_completed'])
        samples=[t for now,t,power in plant.rows if now>=h.device.stream['start']]
        self.assertGreater(min(samples),215.);self.assertLess(max(samples),238.)
        h.cmd_load_test(Command(ACTION='CONFIRM',FLOW=1))
        self.assertTrue(h.load_test_last['flow_confirmed_by_operator'])

    def test_ready_gate_uses_T1_and_the_selected_ADC_instead_of_T0(self):
        h,_,_,_=self.host();self.start(h);h.temperatures[0]=230.
        h.reactor.pause(h.reactor.now+3.)
        self.assertEqual(h.device.stream_packets,[])
        self.ready(h)
        self.assertEqual(h.device.stream_packets[0][9:],(1,1,1,1))

    def test_T1_80_percent_retract_reverses_both_drives_and_turns_heating_off(self):
        h,_,_,_=self.host();self.start(h,action='RETRACT',STEPS=137440)
        self.ready(h)
        until=h.reactor.now+120.
        while h.load_test_context is not None:
            self.assertLess(h.reactor.now,until)
            h.reactor.pause(h.reactor.now+.25)
        packets=h.device.stream_packets
        self.assertEqual(sum(p[0]==1 for p in packets),1)
        self.assertTrue(all(p[9:]==(1,1,0,0) for p in packets))
        last=h.load_test_last
        self.assertEqual((last['phase'],last['reason']),('done','retraction_finished'))
        self.assertEqual(last['cassette_load']['percent_of_reference'],80.)
        self.assertEqual(last['measurement']['dir0_steps'],137440)
        self.assertEqual(last['measurement']['net_feed_steps'],-137440)
        self.assertEqual(h.device.output(7)['duty'],0)
        self.assertFalse(h.device.active())
        count=len(packets);h.reactor.pause(h.reactor.now+30.)
        self.assertEqual(len(h.device.stream_packets),count)

    def test_all_stop_entrypoints_cancel_right_transport_without_late_restart(self):
        for kind in ('global','off','load','feeder_test','feeder_run','legacy','shutdown'):
            for hot in (False,True):
                with self.subTest(kind=kind,hot=hot):
                    h,_,_,_=self.host();self.start(h)
                    if hot:self.ready(h)
                    if kind=='global':h.cmd_control_stop(Command())
                    elif kind=='off':h.cmd_control(Command(MODE='OFF'))
                    elif kind=='load':h.cmd_load_test(Command(ACTION='STOP'))
                    elif kind=='feeder_test':h.cmd_feeder_test(Command(CHANNEL=1,ACTION='STOP'))
                    elif kind=='feeder_run':h.cmd_feeder_run(Command(ACTION='STOP'))
                    elif kind=='legacy':h.cmd_feeder(Command(CHANNEL=1,DURATION_MS=0))
                    else:h._shutdown()
                    count=len(h.device.packets);h.reactor.pause(h.reactor.now+10.)
                    self.assertEqual(len(h.device.packets),count)
                    self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())

    def test_left_feeder_STOP_does_not_masquerade_as_right_owner_STOP(self):
        h,_,_,_=self.host();self.start(h);self.ready(h)
        h.cmd_feeder_test(Command(CHANNEL=0,ACTION='STOP'))
        self.assertIsNotNone(h.load_test_context)
        h.cmd_feeder_test(Command(CHANNEL=1,ACTION='STOP'))
        self.assertIsNone(h.load_test_context)
        self.assertFalse(h.device.active())

    def test_T1_guards_cut_both_drives_and_heating_without_restart(self):
        for cause in ('temperature','cold','adc','cable','heater','owner'):
            with self.subTest(cause=cause):
                h,ops,_,_=self.host();self.start(h);self.ready(h)
                if cause=='temperature':h.temperatures[1]=241.
                elif cause=='cold':h.temperatures[1]=204.
                elif cause=='adc':h.stale_adc={1};h.adc_received[1]=h.reactor.now-2.
                elif cause=='cable':h.status['cable']=1
                elif cause=='heater':h.device.outputs[7].update(duty=0,reason=4)
                else:ops.state.owner='other'
                h.reactor.pause(h.reactor.now+2.)
                self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
                self.assertEqual(sum(p[0]==1 for p in h.device.stream_packets),1)

    def test_right_stream_lease_is_independent_of_background_heartbeat(self):
        h,_,_,_=self.host();self.start(h);self.ready(h)
        context=h.load_test_context;context['busy']=True
        h.reactor.pause(h.reactor.now+2.)
        self.assertEqual(h.device.stream['reason'],3)
        self.assertFalse(h.device.active());self.assertEqual(h.device_until,[0.,0.])
        context['busy']=False;h.reactor.pause(h.reactor.now+.5)
        self.assertIsNone(h.load_test_context)
        self.assertEqual(sum(p[0]==1 for p in h.device.stream_packets),1)

    def test_STOP_during_right_START_reply_cannot_be_followed_by_renewal_or_PID(self):
        h,_,_,_=self.host();original=h.device.query_stream
        def stop(args):
            row=original(args)
            if args[0]==1:h.cmd_control_stop(Command())
            return row
        h.commands['load_stream']=load_support.Wire(stop)
        self.start(h);self.ready(h);count=len(h.device.packets)
        h.reactor.pause(h.reactor.now+5.)
        self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
        self.assertEqual(len(h.device.packets),count)

    def test_legacy_windowed_right_transport_uses_the_same_polarities(self):
        h,_,_,_=cassette_support.CassetteLoadTests().host()
        h.cmd_load_test(Command(ACTION='LOAD',TOOL='T1',SMOOTH=0,TUBE_SECONDS=0,HEAD_STEPS=1537))
        self.ready(h);self.finish(h)
        moves=load_support.LoadTests().starts(h,'motion')
        self.assertEqual(sum(p[6] for p in moves),1537)
        self.assertTrue(all(p[2]==8 and p[3:6]==(0,0,0) for p in moves))
        feed=[p for p in h.feeder_trace if p[1][2]]
        self.assertTrue(feed);self.assertTrue(all(p[1][:2]==(1,1) for p in feed))
        self.assertEqual(h.load_test_last['measurement']['net_feed_steps'],1537)

    def test_explicit_smooth_with_old_MCU_refuses_T1_before_heating(self):
        h,_,_,_=cassette_support.CassetteLoadTests().host()
        with self.assertRaisesRegex(RuntimeError,'R6-SMOOTH'):self.start(h)
        self.assertIsNone(h.load_test_context);self.assertEqual(h.device.packets,[])


if __name__=='__main__':unittest.main(verbosity=2)
