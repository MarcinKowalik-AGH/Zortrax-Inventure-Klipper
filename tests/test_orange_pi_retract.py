"""Heat, reverse both owned drives, then automatically stop heating."""
import unittest
import test_orange_pi_load_stream as stream_support
import test_orange_pi_load_test as support
import test_orange_pi_cassette_load as cassette_support
from test_orange_pi_door_policy import Command


class RetractTests(unittest.TestCase):
    def host(self,plant=False):return stream_support.LoadStreamTests().host(plant)
    def start(self,h,**params):h.cmd_load_test(Command(ACTION='RETRACT',**params))
    def finish(self,h):
        deadline=h.reactor.now+500.
        while h.load_test_context is not None:
            self.assertLess(h.reactor.now,deadline)
            h.reactor.pause(h.reactor.now+.25)

    def test_full_75_percent_heats_then_reverses_both_motors_once_and_turns_heat_off(self):
        h,_,_,plant=self.host(plant=True);self.start(h)
        self.assertEqual(h.device.stream_packets,[])
        h.reactor.pause(130.);self.assertEqual(h.device.stream_packets,[])
        self.finish(h)
        starts=[p for p in h.device.stream_packets if p[0]==1]
        self.assertEqual(len(starts),1)
        self.assertEqual(starts[0][1],128850)
        self.assertTrue(all(p[9:]==(0,0,1,0) for p in h.device.stream_packets))
        moves=support.LoadTests().starts(h,'motion')
        self.assertEqual(len(moves),1);self.assertEqual(moves[0][2:8],(8,0,0,0,128850,1536))
        last=h.load_test_last
        self.assertEqual((last['phase'],last['reason']),('done','retraction_finished'))
        self.assertEqual(last['cassette_load']['percent_of_reference'],75.)
        self.assertEqual(last['cassette_load']['e_direction'],1)
        self.assertEqual(last['cassette_load']['cassette_direction'],0)
        self.assertEqual(last['measurement']['dir0_steps'],0)
        self.assertEqual(last['measurement']['dir1_steps'],128850)
        self.assertEqual(last['measurement']['net_dir0_steps'],-128850)
        self.assertFalse(last['flow_confirmed_by_operator'])
        self.assertFalse(h.device.active());self.assertEqual(h.device.output(6)['duty'],0)
        count=len(h.device.packets);h.reactor.pause(h.reactor.now+30.)
        self.assertEqual(len(h.device.packets),count)

    def test_explicit_shorter_retraction_keeps_actual_reverse_step_accounting(self):
        h,_,_,_=self.host();self.start(h,STEPS=14250)
        stream_support.LoadStreamTests().ready(h);self.finish(h)
        self.assertEqual(h.load_test_last['measurement']['dir1_steps'],14250)
        self.assertEqual(h.load_test_last['last_jog']['direction'],1)
        self.assertEqual(h.load_test_last['last_jog']['sent'],14250)
        self.assertFalse(h.device.active())

    def test_missing_MCU_and_invalid_reverse_parameters_never_start_heating(self):
        h,_,_,_=cassette_support.CassetteLoadTests().host()
        with self.assertRaisesRegex(RuntimeError,'R6-SMOOTH'):self.start(h)
        self.assertEqual(h.device.packets,[])
        for params in ({'SMOOTH':0},{'STEPS':0},{'STEPS':192001},{'TOOL':'T2'},{'RATE':960},
                       {'HEAD_STEPS':95000},{'TUBE_SECONDS':50},{'PURGE_STEPS':1000}):
            h,_,_,_=self.host()
            with self.assertRaises(RuntimeError):self.start(h,**params)
            self.assertIsNone(h.load_test_context);self.assertEqual(h.device.packets,[])

    def test_operator_stop_during_reverse_never_restarts_either_drive(self):
        h,_,_,_=self.host();self.start(h);stream_support.LoadStreamTests().ready(h)
        h.reactor.pause(h.reactor.now+2.);h.cmd_control_stop(Command())
        self.assertIsNone(h.load_test_context);self.assertFalse(h.device.active())
        count=len(h.device.stream_packets);h.reactor.pause(h.reactor.now+30.)
        self.assertEqual(len(h.device.stream_packets),count)
        self.assertFalse(h.load_test_last['cassette_load']['feed_completed'])
        self.assertLess(h.load_test_last['measurement']['dir1_steps'],128850)


if __name__=='__main__':unittest.main(verbosity=2)
