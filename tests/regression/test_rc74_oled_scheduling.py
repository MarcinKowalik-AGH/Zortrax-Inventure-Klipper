"""Exercise the host scheduler, transport fences and real renderer on doubles."""
import tempfile
import unittest

from test_host_adapters import Printer, Cmd
from v9_test_extras.inventure_oled_transport import OledStream


class OledScheduling(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=Printer(self.tmp.name)
        self.p.settings['inventure_oled'].update(oem_menu=True)
        self.o=self.p.load_object(None,'inventure_oled')
        self.p.board_ready();self.p.configure();self.p.send_event('klippy:ready')

    def idle(self):
        self.o.stream.operations=None;self.o.stream.pending=None
        self.o.stream.previous=self.o.stream.latest
        self.o.next_query=1.25;self.o.render_at=1.2

    def test_seconds_defaults_match_drive_and_rc73(self):
        self.assertEqual(self.o.interval,.002)
        self.assertEqual(self.o.stream.refresh_interval,.2)
        self.assertEqual(OledStream().refresh_interval,.2)

    def test_active_transfer_keeps_two_millisecond_pacing(self):
        self.assertAlmostEqual(self.o._next_tick(1.),1.002)
        self.idle();self.o.stream.pending=(1,2.,0.,1.)
        self.assertAlmostEqual(self.o._next_tick(1.),1.002)
        self.idle();self.o.stream.latest=bytes(8192)
        self.assertAlmostEqual(self.o._next_tick(1.),1.002)

    def test_static_screen_polls_twenty_times_per_second(self):
        self.idle()
        self.assertAlmostEqual(self.o._next_tick(1.),1.05)

    def test_idle_never_defers_service_or_render_deadline(self):
        for field in ('next_query','render_at'):
            with self.subTest(field=field):
                self.idle();setattr(self.o,field,1.01)
                self.assertAlmostEqual(self.o._next_tick(1.),1.01)

    def test_idle_honors_recovery_deadline(self):
        self.idle();self.o.recovery_state='backoff';self.o.recover_at=1.012
        self.assertAlmostEqual(self.o._next_tick(1.),1.012)

    def test_held_buttons_keep_repeat_and_power_hold_responsive(self):
        self.idle();self.o.pressed_at[3]=.9
        self.assertAlmostEqual(self.o._next_tick(1.),1.002)

    def test_panel_event_wakes_idle_timer_without_gcode(self):
        self.idle();self.o.timer.when=1.05
        self.o._buttons(1.,4,1)
        self.assertEqual(self.o.timer.when,self.p.reactor.NOW)
        self.assertEqual(self.o.oem_display_mode,'menu')
        self.assertFalse(self.p.objects['gcode'].scripts)

    def test_text_wakes_timer_but_cannot_reactivate_shutdown(self):
        self.idle();self.o.timer.when=1.05
        self.o.cmd_text(Cmd({'LINE1':'Diagnostic'}))
        self.assertEqual(self.o.timer.when,self.p.reactor.NOW)
        self.p.send_event('klippy:shutdown')
        self.o.cmd_text(Cmd({'LINE1':'After shutdown'}))
        self.assertEqual(self.o.timer.when,self.p.reactor.NEVER)
        self.assertFalse(self.o.stream.enabled)

    def test_render_deadline_follows_configured_refresh(self):
        self.o._tick(0.)
        self.assertAlmostEqual(self.o.render_at,.2)

    def test_static_cache_and_message_return_invalidate_correctly(self):
        original=self.o.stream.latest;count=self.o._rendered_frames
        self.o._render(.1)
        self.assertIs(self.o.stream.latest,original)
        self.assertEqual(self.o._rendered_frames,count)
        self.assertGreater(self.o._render_cache_hits,0)
        self.o.cmd_text(Cmd({'LINE1':'Diagnostic'}));self.o._render(.2)
        self.assertNotEqual(self.o.stream.latest,original)
        self.o._select(.3);self.o._render(.3)
        self.assertEqual(self.o.stream.latest,original)
        self.assertEqual(self.o._rendered_frames,count+2)

    def test_missing_ack_still_times_out_and_invalidates_readiness(self):
        s=OledStream(timeout=.1);s.start(0.)
        self.assertIsNotNone(s.step(0.))
        self.assertIsNone(s.step(.002))
        with self.assertRaises(TimeoutError):s.step(.1)
        self.assertFalse(s.ready);self.assertFalse(s.enabled)

    def test_old_generation_ack_cannot_unlock_next_operation(self):
        s=OledStream();s.start(0.);first=s.step(0.)
        self.assertFalse(s.ack(first[1],.001,generation=s.generation-1))
        self.assertIsNone(s.step(.002))
        self.assertEqual(s.sent,1)
        self.assertIsNotNone(s.pending)


if __name__=='__main__':unittest.main()
