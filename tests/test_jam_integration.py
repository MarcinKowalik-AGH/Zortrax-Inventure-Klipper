"""Exercise the native print recovery controller across the R13 wire boundary."""
import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from test_filament_jam import Command
from test_jam_motion import make_motion
from orange_pi.inventure_filament_jam import FilamentJam


class TransportWire:
    """Minimal R13 wire model. Sensor counters advance only on planner E moves."""
    def __init__(self, tool):
        self.tool = tool
        self.calls = []
        self.tick = 0
        self.mode = self.pulses = self.starts = self.forward = self.reverse = 0
        self.budget = 100
        self.reader = None
        self.have_transport = True
        self.r2_starts = True

    def get_constants(self):
        return {'INVENTURE_TRANSPORT_ABI': 1}

    def get_constant_float(self, key):
        return 1000000.

    def check_valid_response(self, fmt):
        return fmt.startswith('inventure_transport_status ')

    def register_serial_response(self, fn, fmt):
        self.reader = fn

    def lookup_command(self, fmt):
        name = fmt.split()[0].removeprefix('inventure_transport_')
        send = lambda args: self.send(name, list(args))
        return SimpleNamespace(send=send, send_wait_ack=send)

    def send(self, name, args):
        self.calls.append((name, args))
        if name == 'profile':
            self.budget = args[-1]
        elif name == 'mode':
            assert args[0] == self.tool
            self.mode = args[1]
        elif name == 'select':
            assert args == [self.tool]
        elif name == 'query':
            self.tick += 1000
            self.reader(dict(channel=self.tool, clock=self.tick, live=1, a=1, b=0,
                             mode=self.mode, running=int(self.mode == 2),
                             pulses=self.pulses, starts=self.starts,
                             last_pulse_clock=self.tick if self.pulses else 0,
                             forward=self.forward, reverse=self.reverse,
                             fault=0, budget=self.budget))

    def move_e(self, distance):
        if distance < 0:
            self.reverse += round(-960 * distance)
            return
        self.forward += round(960 * distance)
        self.budget -= round(960 * distance)
        if distance and self.have_transport:
            if self.mode == 2:
                self.pulses += 1
            elif self.mode == 1 and (self.r2_starts or distance != 1.):
                self.starts += 1


class PrintRun:
    def __init__(self, tool):
        motion, _, _ = make_motion(tool)
        self.board = motion.board
        self.sd = motion._need('virtual_sdcard')
        self.head = motion._need('toolhead')
        self.gm = motion._need('gcode_move')
        self.ops = motion._need('inventure_operations')
        self.pause = motion._need('pause_resume')
        self.tools = motion._need('inventure_tools')
        self.gcode = motion._need('gcode')
        self.name = 'T%d' % tool
        self.saved = tuple(self.head.get_position())
        self.saved_e = self.saved[3] - self.gm.base_position[3]
        self.pending = []
        self.wire = TransportWire(tool)
        self.board.mcu = self.wire
        self.board.connected = self.board.allow_actuators = True
        self.board.status = {'armed': 1, 'fault': 0}
        self.board.status_received = self.board.inputs_received = self.board.reactor.monotonic()
        self.board.inputs = {'raw': {self.name.lower() + '_a': 1, self.name.lower() + '_b': 0}}
        self.board.gcode = self.gcode
        self.gcode.base_gcode_handlers = {}
        self.handlers = {'RESUME': self.pause.cmd_RESUME}
        self.gcode.error = ValueError
        self.gcode.get_status = lambda now: {'commands': self.handlers}
        self.gcode.get_mutex = nullcontext
        self.gcode.respond_info = lambda msg: None
        self.gcode.register_command = self.register
        self.board.printer.register_event_handler = lambda *args: None
        self.board.printer.command_error = ValueError
        self.board.reactor.register_timer = lambda cb: cb
        self.board.reactor.update_timer = lambda *args: None
        self.board.reactor.register_async_callback = lambda cb: cb(self.board.reactor.monotonic())
        self.board.reactor.register_callback = self.pending.append
        self.board.reactor.NEVER = 1.e16
        self.board.reactor.NOW = 0.
        self.tools.running = False
        get = self.board.printer.lookup_object
        stats = SimpleNamespace(get_status=lambda now: {'state': 'printing' if self.sd.is_active() else 'paused'})
        self.board.printer.lookup_object = lambda name, default=None: stats if name == 'print_stats' else get(name, default)
        move = self.ops.paused_print_move
        def step(lease, target, speed):
            before = self.head.get_position()[3]
            move(lease, target, speed)
            # The native fixture advances time by the actual motion duration.
            # Model the R13 periodic board/input telemetry still arriving.
            self.board.status_received = self.board.inputs_received = self.board.reactor.monotonic()
            self.wire.move_e(target[3] - before)
        self.ops.paused_print_move = step

        self.jam = FilamentJam(self.board)
        self.board.filament_jam = self.jam
        self.jam._ready()

    def register(self, name, handler, **kwargs):
        if handler is None:
            return self.handlers.pop(name, None)
        self.handlers[name] = handler

    def issue(self, action, **args):
        self.jam.cmd(Command(ACTION=action, **args))

    def drain(self):
        count = 0
        while self.pending:
            self.pending.pop(0)(self.board.reactor.monotonic())
            count += 1
            if count > 20:
                raise AssertionError('Unbounded recovery work queue')

    def poll(self):
        self.jam._tick(self.board.reactor.monotonic())
        self.drain()

    def select(self, source):
        self.jam.monitor.set_source(self.name, source)


class NativeRecoveryIntegration(unittest.TestCase):
    def test_cassette_exhausted_native_step_budget_parks_checks_wipes_and_resumes(self):
        for tool in (0, 1):
            with self.subTest(tool=tool):
                run = PrintRun(tool)
                run.select('CASSETTE')
                run.poll()
                self.assertFalse(run.jam.active)
                run.wire.budget = -1
                run.wire.forward += 1
                run.poll()
                self.assertEqual(run.jam.state['phase'], 'completed', run.jam.state)
                self.assertEqual(run.jam.state['reason'], 'EXTRUDER_JAM')
                self.assertEqual(run.wire.pulses, 10)
                self.assertEqual(run.wire.starts, 3)
                self.assertEqual(tuple(run.head.get_position()[:3]), run.saved[:3])
                self.assertAlmostEqual(run.head.get_position()[3] - run.gm.base_position[3], run.saved_e)
                self.assertTrue(run.sd.is_active())
                self.assertFalse(run.pause.is_paused)
                self.assertEqual(run.wire.mode, 1)
                self.assertEqual(run.jam.monitor.print_tool, run.name)
                run.sd.active = False
                run.poll()
                self.assertEqual(run.wire.mode, 0)

    def test_external_runout_needs_reload_and_current_visible_flow_confirmation(self):
        run = PrintRun(1)
        run.select('EXTERNAL')
        inputs = run.board.inputs['raw']
        inputs['t1_a'] = 0
        run.poll()
        self.assertEqual(run.jam.state['reason'], 'RUNOUT')
        self.assertEqual(run.jam.state['phase'], 'waiting', run.jam.state)
        self.assertFalse(run.sd.is_active())
        with self.assertRaisesRegex(ValueError, 'recovery|Recovery'):
            run.sd.do_resume()
        with self.assertRaisesRegex(ValueError, 'recovery|Recovery'):
            run.handlers['RESUME'](Command())
        inputs['t1_a'] = 1
        run.issue('RETRY')
        run.drain()
        self.assertEqual(run.jam.state['phase'], 'verifying', run.jam.state)
        self.assertFalse(run.sd.is_active())
        with self.assertRaisesRegex(ValueError, 'prompt'):
            run.issue('CONFIRM', RUN_ID=run.jam.state['run_id'], PROMPT_ID=0)
        run.issue('CONFIRM', RUN_ID=run.jam.state['run_id'], PROMPT_ID=run.jam.state['prompt_id'])
        run.drain()
        self.assertTrue(run.sd.is_active(), run.jam.state)
        self.assertEqual(run.jam.state['phase'], 'completed')
        self.assertEqual(run.wire.calls, [])

    def test_r2_without_starts_calls_the_real_cassette_loader_and_resumes(self):
        run = PrintRun(1)
        run.select('CASSETTE')
        run.wire.r2_starts = False
        run.issue('REPORT')
        run.drain()
        self.assertEqual(run.jam.state['phase'], 'completed', run.jam.state)
        self.assertGreaterEqual(run.wire.starts, 6)
        self.assertGreater(run.wire.forward, 60 * 960)
        self.assertEqual(tuple(run.head.get_position()[:3]), run.saved[:3])

    def test_changing_the_file_while_r1_is_in_progress_stops_further_steps(self):
        run = PrintRun(0)
        run.select('CASSETTE')
        move = run.ops.paused_print_move

        def change_file(lease, target, speed):
            move(lease, target, speed)
            if run.wire.mode == 2:
                run.sd.current_file = object()
                run.jam._file_reset()

        run.ops.paused_print_move = change_file
        run.issue('REPORT')
        run.drain()
        self.assertEqual(run.jam.state['phase'], 'cancelled', run.jam.state)
        self.assertEqual(run.wire.forward, 960)
        self.assertEqual(run.wire.mode, 0)
        self.assertFalse(run.sd.is_active())

    def test_without_sensor_edges_cassette_stops_at_r1_and_cancel_blocks_sd_resume(self):
        run = PrintRun(0)
        run.select('CASSETTE')
        run.wire.have_transport = False
        run.issue('REPORT')
        run.drain()
        self.assertEqual(run.jam.state['stage'], 'R1', run.jam.state)
        self.assertEqual(run.wire.forward, 50 * 960)
        self.assertFalse(run.sd.is_active())
        self.assertEqual(run.wire.mode, 0)
        run.issue('CANCEL')
        with self.assertRaisesRegex(ValueError, 'Cancel|cancel'):
            run.sd.do_resume()
        self.assertFalse(run.sd.is_active())
