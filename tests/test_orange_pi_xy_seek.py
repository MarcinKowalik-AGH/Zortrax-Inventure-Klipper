"""Exercise actual XY seek adapter: fixed limits, no repeated START, stop on error."""
import types
import unittest
from test_orange_pi_door_policy import adapter,Wire,Command

def response(**updates):
    p=dict(run=1,token=11,axis=0,phase=1,reason=0,reject=0,steps=0,
           elapsed_ms=0,enabled=1,endstop_mask=0,hit_mask=0,door_lock_enabled=0)
    p.update(updates);return p

class XYSeekTests(unittest.TestCase):
    def host(self,**updates):
        h=adapter.InventureMCU.__new__(adapter.InventureMCU)
        h.connected=True;h.xy_seek_token=10;h.xy_seek_last={};h.xy_seek_timer=object()
        h.commands={'xy_seek':Wire(response(**updates))}
        h.reactor=types.SimpleNamespace(NEVER=float('inf'),monotonic=lambda:100.,update_timer=lambda *a:None)
        h.gcode=Command();return h,h.commands['xy_seek']

    def test_each_axis_starts_once_and_polls_are_read_only(self):
        for axis in (0,1):
            h,w=self.host(axis=axis);h.cmd_xy_seek(Command(AXIS=('X','Y')[axis]))
            self.assertEqual(w.calls,[(1,axis,11)])
            self.assertEqual(h.xy_seek_last['maximum_steps_each_motor'],102400)
            self.assertEqual(h.xy_seek_last['maximum_duration_s'],40)
            self.assertFalse(h.xy_seek_last['establishes_position'])
            h._xy_seek_tick(100.);self.assertEqual(w.calls[-1],(0,0,0))

    def test_target_hit_stops_polling_without_retract_or_homing_claim(self):
        h,w=self.host(phase=2,reason=1,enabled=0,steps=4000,hit_mask=1)
        self.assertEqual(h._xy_seek_tick(100.),h.reactor.NEVER)
        self.assertEqual(h.xy_seek_last['reason'],'endstop_hit')
        self.assertFalse(h.xy_seek_last['automatic_retract'])
        self.assertFalse(h.xy_seek_last['driver_enabled'])

    def test_bad_start_reply_stops_but_busy_reply_does_not_stop_an_existing_run(self):
        h,w=self.host(token=2)
        with self.assertRaises(RuntimeError):h.cmd_xy_seek(Command(AXIS='X'))
        self.assertEqual(w.calls[-1],(2,0,0))
        h,w=self.host(token=2,reject=1)
        with self.assertRaisesRegex(RuntimeError,'another_test'):h.cmd_xy_seek(Command(AXIS='Y'))
        self.assertEqual(w.calls,[(1,1,11)])

    def test_unconditional_stop_and_old_mcu_error_are_explicit(self):
        h,w=self.host(phase=3,reason=3,enabled=0)
        h.cmd_xy_seek_stop(Command());self.assertEqual(w.calls,[(2,0,0)])
        h,w=self.host();h.commands={}
        with self.assertRaisesRegex(RuntimeError,'RC8-MOTOR-R3'):h.cmd_xy_seek(Command())
        self.assertEqual(w.calls,[])

if __name__=='__main__':unittest.main(verbosity=2)
