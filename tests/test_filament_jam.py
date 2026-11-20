"""Host recovery sequencing against independent fake print and transport objects."""
from collections import defaultdict
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orange_pi import inventure_filament_jam as jam


class Reactor:
    NOW, NEVER = 0., 1.e30

    def __init__(self):
        self.now = 0.
        self.callbacks = []

    def register_timer(self, fn):
        return fn

    def register_callback(self, fn):
        self.callbacks.append(fn)

    def update_timer(self, *args):
        pass

    def monotonic(self):
        return self.now

    def flush(self):
        while self.callbacks:
            self.callbacks.pop(0)(self.now)


class SD:
    def __init__(self):
        self.current_file = object()
        self.running = True

    def is_active(self):
        return self.running

    def do_resume(self):
        self.running = True


class Gcode:
    error = ValueError

    def __init__(self):
        self.commands = {}
        self.base_gcode_handlers = {}

    def register_command(self, name, fn, when_not_ready=False):
        if fn is None:
            return self.commands.pop(name, None)
        self.commands[name] = fn

    def get_status(self, now):
        return {'commands': self.commands.copy()}

    def get_mutex(self):
        return nullcontext()

    def respond_info(self, msg):
        pass


class Command(dict):
    error = ValueError

    def get_int(self, key, default):
        return int(self.get(key, default))

    def respond_info(self, msg):
        pass


class Monitor:
    def __init__(self, board):
        self.board = board
        self.source_names = {'T0': 'CASSETTE', 'T1': 'EXTERNAL'}
        self.present = 1
        self.tool = 'T0'
        self.reason = None
        self.mode = self.pulses = self.starts = 0
        self.r1, self.r2 = True, True
        self.sample_hook = None
        self.sources_touched = []

    def poll(self, now):
        if self.reason:
            reason, self.reason = self.reason, None
            return dict(tool=self.tool, source=self.source(self.tool), reason=reason)

    def begin_recovery(self, tool):
        self.sources_touched.append(tool)

    def active_tool(self):
        return self.tool

    def source(self, tool):
        return self.source_names[tool]

    def set_source(self, tool, source):
        self.source_names[tool] = source

    def _sample(self):
        return dict(a=self.present, pulses=self.pulses, starts=self.starts, pulse_age_ms=0)

    def sample(self, tool):
        if self.sample_hook:
            return self.sample_hook()
        if self.mode == 2 and self.r1:
            self.pulses = (self.pulses + 1) & 0xffffffff
        if self.mode == 1 and self.r2:
            self.starts += 1
        return self._sample()

    def set_mode(self, tool, mode):
        self.mode = mode
        return self._sample()

    def stop_recovery(self):
        self.mode = 0

    def prepare_resume(self, tool):
        self.mode = int(self.source(tool) == 'CASSETTE')
        if not self.present:
            raise ValueError('Filament missing before resume')

    def complete_resume(self, tool):
        pass

    def reset(self):
        self.stop_recovery()

    def status(self):
        return {}


class Motion:
    def __init__(self, board):
        self.board = board
        self.log = []
        self.context = None
        self.granted = False
        self.park_failure = False
        self.feed_hook = None
        self.load_result = True

    def pause(self):
        self.board.sd.running = False
        self.board.stats.state = 'paused'
        self.log.append('pause')

    def park(self, tool, guard):
        if self.park_failure:
            raise ValueError('Service envelope unavailable')
        guard()
        self.log.append(('park', tool))
        self.context = object()
        return self.context

    def feed(self, ctx, mm):
        self.log.append(('feed', mm))
        self.board.reactor.now += .8
        if self.feed_hook:
            self.feed_hook()

    def clean(self, ctx):
        self.log.append('clean')

    def reload(self, ctx, transport, guard, progress):
        guard()
        self.log.append('reload')
        return self.load_result

    def restore_e(self, ctx):
        self.log.append('restore_e')

    def resume(self, ctx, guard, before_resume=None):
        guard()
        if before_resume:
            before_resume()
        self.granted = True
        try:
            self.board.gcode.commands['RESUME'](Command())
        finally:
            self.granted = False
        self.log.append('resume')

    def authorized_resume(self):
        return self.granted

    def abort(self, ctx):
        self.context = None


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.board = board = SimpleNamespace(connected=True, reactor=Reactor(), gcode=Gcode(),
                                             stats=SimpleNamespace(state='printing'), sd=SD())
        events = defaultdict(list)
        objs = {'virtual_sdcard': board.sd, 'print_stats': board.stats}
        board.printer = SimpleNamespace(lookup_object=objs.get, register_event_handler=lambda name, fn:events[name].append(fn))
        board.gcode.register_command('RESUME', lambda gcmd:board.sd.do_resume())
        with patch.object(jam, 'JamMotion', Motion), patch.object(jam, 'JamMonitor', Monitor):
            self.run = jam.FilamentJam(board)
        self.run._ready()
        self.monitor = self.run.monitor
        self.motion = self.run.motion

    def action(self, action, **args):
        self.run.cmd(Command(ACTION=action, **args))

    def trigger(self, reason='EXTRUDER_JAM'):
        self.monitor.reason = reason
        self.run._tick(0.)
        self.board.reactor.flush()

    def test_native_cassette_success_and_rollover(self):
        self.monitor.pulses = 0xfffffffb
        self.trigger()
        self.assertEqual(self.run.state['phase'], 'completed')
        self.assertEqual(self.motion.log[:2], ['pause', ('park', 0)])
        self.assertEqual(self.motion.log.count(('feed', 1.)), 13)
        self.assertEqual(self.motion.log[-2:], ['clean', 'resume'])

    def test_t1_overheating_warning_needs_current_valid_pcb_measurement(self):
        report = dict(channels=[{}]*7 + [dict(fault=0, bad_sensor=0, samples=10)],
                      sensor_ages=[0.]*8, fatal=None, pcb_class=2)
        thermal = SimpleNamespace(stale=.75, get_status=lambda now: report)
        lookup = self.board.printer.lookup_object
        self.board.printer.lookup_object = lambda key, default=None: thermal if key == 'inventure_thermal' else lookup(key, default)
        self.assertEqual(self.run._pcb_class(), 2)
        report['sensor_ages'][7] = 1.
        self.assertIsNone(self.run._pcb_class())
        report['sensor_ages'][7] = .1
        report['channels'][7]['bad_sensor'] = 1
        self.assertIsNone(self.run._pcb_class())

    def test_missing_pulses_never_loads_or_resumes(self):
        self.monitor.r1 = False
        self.trigger()
        self.assertEqual(self.motion.log.count(('feed', 1.)), 50)
        self.assertEqual(self.motion.log[-1], 'restore_e')
        self.assertEqual(self.run.state['allowed_actions'], ['RETRY', 'UNLOAD', 'BROKEN', 'CANCEL'])
        self.assertFalse(self.board.sd.is_active())
        for method in (lambda:self.board.sd.do_resume(), lambda:self.board.gcode.commands['RESUME'](Command())):
            with self.assertRaises(ValueError):
                method()

    def test_long_gap_cannot_credit_stale_batch_of_ten_pulses(self):
        def batch():
            if self.monitor.pulses == 0:
                self.monitor.pulses = 9
            elif self.monitor.pulses == 9:
                self.board.reactor.now += 2.1
                self.monitor.pulses = 19
            return self.monitor._sample()
        self.monitor.sample_hook = batch
        self.trigger()
        self.assertEqual(self.run.state['stage'], 'R1')
        self.assertNotIn('reload', self.motion.log)
        self.assertNotIn('resume', self.motion.log)

    def test_r2_reloads_only_after_first_phase_passes(self):
        self.monitor.r2 = False
        self.motion.load_result = 'CLEANED'
        self.trigger()
        self.assertEqual(self.motion.log.count(('feed', 1.)), 60)
        self.assertEqual(self.motion.log[-2:], ['reload', 'resume'])
        self.assertNotIn('clean', self.motion.log)

    def test_runout_requires_operator_and_external_visual_flow(self):
        self.monitor.tool = 'T1'
        self.trigger('RUNOUT')
        self.assertEqual(self.motion.log, ['pause', ('park', 1)])
        old = str(self.run.state['prompt_id'])
        self.action('RETRY')
        self.board.reactor.flush()
        self.assertEqual(self.motion.log.count(('feed', 5.)), 4)
        self.assertEqual(self.run.state['phase'], 'verifying')
        self.assertEqual(self.monitor.sources_touched, [])
        with self.assertRaisesRegex(ValueError, 'prompt has changed'):
            self.action('CONFIRM', PROMPT_ID=old)
        self.action('CONFIRM')
        self.board.reactor.flush()
        self.assertTrue(self.board.sd.is_active())

    def test_external_removed_before_confirmation_prompts_again(self):
        self.monitor.tool = 'T1'
        self.trigger()
        self.monitor.present = 0
        self.action('CONFIRM')
        self.board.reactor.flush()
        self.assertEqual(self.run.state['reason'], 'RUNOUT')
        self.assertFalse(self.board.sd.is_active())
        self.assertNotIn('resume', self.motion.log)

    def test_unknown_source_and_cartridge_fault_do_not_auto_feed(self):
        self.monitor.source_names['T0'] = 'UNKNOWN'
        self.action('REPORT')
        self.board.reactor.flush()
        self.assertIn('SOURCE', self.run.state['allowed_actions'])
        self.action('SOURCE', TOOL='T0', SOURCE='CASSETTE')
        self.assertEqual(self.run.state['phase'], 'waiting')
        self.assertEqual(self.motion.log, ['pause', ('park', 0)])
        self.action('CANCEL')
        with self.assertRaisesRegex(ValueError, 'Cancel the interrupted print'):
            self.board.sd.do_resume()

    def test_missing_cassette_material_prevents_a_retry_from_feeding(self):
        self.monitor.present = 0
        self.trigger('MATERIAL_MISSING')
        self.action('RETRY')
        self.board.reactor.flush()
        self.assertEqual(self.run.state['phase'], 'waiting')
        self.assertEqual(self.run.state['reason'], 'MATERIAL_MISSING')
        self.assertEqual(self.monitor.mode, 0)
        self.assertFalse(any(isinstance(entry, tuple) and entry[0] == 'feed' for entry in self.motion.log))

    def test_different_job_at_callback_prevents_motion(self):
        self.action('REPORT')
        self.board.sd.current_file = object()
        self.board.reactor.flush()
        self.assertEqual(self.run.state['phase'], 'cancelled')
        self.assertEqual(self.motion.log, [])

    def test_cancel_mid_feed_has_no_followup_or_resume(self):
        self.motion.feed_hook = lambda:self.action('CANCEL')
        self.trigger()
        self.assertEqual(self.motion.log.count(('feed', 1.)), 1)
        self.assertEqual(self.run.state['phase'], 'cancelled')
        self.assertEqual(self.monitor.mode, 0)
        self.assertNotIn('resume', self.motion.log)

    def test_unqualified_motion_leaves_job_paused(self):
        self.motion.park_failure = True
        self.trigger()
        self.assertEqual(self.run.state['stage'], 'fault')
        self.assertEqual(self.run.state['allowed_actions'], ['CANCEL'])
        self.assertFalse(self.board.sd.is_active())
        self.assertEqual(self.motion.log, ['pause'])

    def test_status_identifies_missing_native_equipment_and_previous_service_result(self):
        self.board.load_test_last = dict(tool='T0', phase='done', factory=dict(
            operation='JAM_CASSETTE', phase='DONE',
            result={'terminal': 'CASSETTE_R2', 'success': True, 'print_resumed': False}))
        status = self.run.get_status()
        self.assertFalse(status['print_profile']['configured'])
        self.assertIn('extruder_stepper shared_e', status['print_profile']['missing'])
        self.assertEqual(status['service_test']['result']['terminal'], 'CASSETTE_R2')
        self.assertFalse(status['service_test']['result']['print_resumed'])
        self.board.load_test_context = dict(tool=1, phase='heating', factory=dict(
            operation='JAM_EXTERNAL', phase='HOME'))
        snap = self.run.get_status()['service_test']
        self.assertEqual((snap['tool'], snap['source'], snap['stage']), ('T1', 'EXTERNAL', 'HOME'))

    def test_source_selection_does_not_require_a_print(self):
        self.board.sd.running = False
        self.board.sd.current_file = None
        self.assertEqual(self.run.get_status()['allowed_actions'], ['SOURCE'])
        self.action('SOURCE', TOOL='T0', SOURCE='EXTERNAL')
        self.assertEqual(self.run.get_status()['sources']['T0'], 'EXTERNAL')
        with self.assertRaisesRegex(ValueError, 'running virtual SD print'):
            self.action('REPORT')
