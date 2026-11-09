"""Real adapter checks for a bounded release and fresh slow endstop approach."""
import types
import unittest
from test_orange_pi_door_policy import adapter,Wire,Command
from test_orange_pi_xy_seek import response as seek_response

def response(**updates):
    p=dict(run=1,token=11,axis=0,phase=1,stage=0,reason=0,reject=0,
           first_steps=0,backoff_steps=0,slow_steps=0,elapsed_ms=0,
           enabled=1,endstop_mask=0,hit_mask=0,released=0,repeat=1)
    p.update(updates);return p

class XYRepeatTests(unittest.TestCase):
    def host(self,**updates):
        h=adapter.InventureMCU.__new__(adapter.InventureMCU)
        h.connected=True;h.xy_seek_token=10;h.xy_repeat_last={};h.xy_repeat_timer=object()
        h.commands={'xy_repeat':Wire(response(**updates))}
        h.reactor=types.SimpleNamespace(NEVER=float('inf'),monotonic=lambda:100.,update_timer=lambda *a:None)
        h.gcode=Command();return h,h.commands['xy_repeat']

    def test_each_axis_starts_once_and_polls_without_restarting(self):
        for axis in (0,1):
            h,w=self.host(axis=axis);h.cmd_xy_repeat(Command(AXIS=('X','Y')[axis]))
            self.assertEqual(w.calls,[(1,axis,11)])
            self.assertEqual(h.xy_repeat_last['backoff_nominal_mm'],2)
            self.assertEqual(h.xy_repeat_last['slow_hz'],640)
            h._xy_repeat_tick(100.);self.assertEqual(w.calls[-1],(0,0,0))

    def test_a_slow_hit_requires_a_confirmed_release_and_the_complete_backoff(self):
        h,w=self.host(phase=2,stage=2,reason=1,backoff_steps=1280,slow_steps=1270,
                     enabled=0,hit_mask=1,released=1)
        self.assertEqual(h._xy_repeat_tick(100.),h.reactor.NEVER)
        self.assertTrue(h.xy_repeat_last['switch_release_confirmed'])
        self.assertFalse(h.xy_repeat_last['establishes_position'])
        w.reply['released']=0
        with self.assertRaises(RuntimeError):h.cmd_xy_repeat(Command())
        self.assertEqual(w.calls[-1],(2,0,0))

    def test_busy_old_mcu_and_unconditional_stop_are_explicit(self):
        h,w=self.host(reject=1,token=9)
        with self.assertRaisesRegex(RuntimeError,'another_test'):h.cmd_xy_repeat(Command())
        self.assertEqual(w.calls,[(1,0,11)])
        h.commands={}
        with self.assertRaisesRegex(RuntimeError,'RC8-MOTOR-R4'):h.cmd_xy_repeat(Command())
        h,w=self.host(phase=3,reason=3,enabled=0)
        h.cmd_xy_repeat_stop(Command());self.assertEqual(w.calls,[(2,0,0)])

    def test_repeat_and_single_seek_use_distinct_tokens(self):
        h,w=self.host();seek=Wire(seek_response(token=12));h.commands['xy_seek']=seek
        h.cmd_xy_repeat(Command());h._xy_seek_query(1,0)
        self.assertEqual(w.calls[0],(1,0,11));self.assertEqual(seek.calls[0],(1,0,12))

if __name__=='__main__':unittest.main(verbosity=2)
