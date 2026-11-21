"""Pinned native geometry, paused print ownership, physical guards and E restoration."""
from contextlib import nullcontext
import functools
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ci.check_native_nozzle_startup import prepare_runtime
from orange_pi import inventure_jam_motion as motion
from orange_pi import patch_jam_operations as patch


@functools.lru_cache(None)
def pinned():
    global TEMP
    TEMP = tempfile.TemporaryDirectory()
    extras = prepare_runtime(Path(TEMP.name)) / 'klippy/extras'
    # Keep the pinned extras available even when other test modules alter sys.path.
    package = sys.modules['orange_pi']
    package.__path__ = [*package.__path__, str(extras)]
    ops_path = extras / 'inventure_operations.py'
    code = patch.render(ops_path.read_bytes())
    assert hashlib.sha256(code).hexdigest() == patch.PATCHED_SHA256
    path = Path(TEMP.name) / 'owned_pause.py'
    path.write_bytes(code)
    spec = importlib.util.spec_from_file_location('orange_pi.pinned_jam_ops', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Command:
    def __init__(self, params=None):
        self.params = params or {}

    def get(self, key, default=None):
        return self.params.get(key, default)

    def get_int(self, key, default=None):
        return int(self.get(key, default))

    def get_float(self, key, default=None, **bounds):
        return float(self.get(key, default))

    def error(self, message):
        return ValueError(message)


class Clock:
    def __init__(self):
        self.now = 1000.

    def monotonic(self):
        return self.now

    def pause(self, until):
        self.now = until


def make_motion(tool=0):
    """Construct a paused-capable native printer with real pinned state handlers."""
    P02 = pinned().InventureOperations
    from orange_pi.gcode_move import GCodeMove
    from orange_pi.pause_resume import PauseResume
    from orange_pi.inventure_operations_core import OperationState

    objects = {}
    printer = NS(lookup_object=objects.get, command_error=ValueError, is_shutdown=lambda: False)
    clock = Clock()
    board = NS(printer=printer, reactor=clock, door_policy_global=True,
               _control_query=lambda: dict(mode='NATIVE', armed=1, fault=0, latched=0, reject=None),
               _fresh_inputs=lambda: {'raw': {'tray': 0, 'bin': 0}},
               _door_policy_query=lambda: dict(enabled=False, reject=None),
               panel_temperature=lambda t, now: (239., 'valid'))
    hot = NS(target_temp=240., min_extrude_temp=170., max_temp=310., can_extrude=True)
    extruder = NS(get_heater=lambda: hot, max_e_dist=50., get_name=lambda: ('extruder', 'extruder1')[tool])
    kin = NS(limits=[(-5., 138.), (0., 138.), (0., 150.)])
    native = NS(pos=[40., 54., 2., 917.], wait_moves=lambda: None,
                get_kinematics=lambda: kin, get_extruder=lambda: extruder)
    native.get_position = lambda: list(native.pos)
    gm = object.__new__(GCodeMove)
    gm.last_position = list(native.pos)
    gm.homing_position = [1., 0., 0., 0.]
    gm.base_position = [1., 0., 0., 111.]
    gm.speed, gm.speed_factor, gm.extrude_factor = 16., 1./55., 1.2
    gm.absolute_coord, gm.allow_absolute_extrude = False, True
    gm.move_transform = None
    gm.saved_states = {}
    gm.reset_last_position = lambda: setattr(gm, 'last_position', list(native.pos))
    gm.move_with_transform = lambda pos, speed: native.move(pos, speed)
    lease_state = OperationState()
    lease = lease_state.begin('print')
    for axis in 'xyz':
        lease_state.homed(axis)
    sd = NS(active=True, current_file=object(), file_position=241)
    sd.is_active = lambda: sd.active
    def pause_sd():
        sd.active = False
        lease_state.pause(lease, 'source')
    def resume_sd():
        assert ops.state.mode == 'running'
        sd.active = True
    sd.do_pause, sd.do_resume = pause_sd, resume_sd
    def script(text):
        key, *args = text.split()
        cmd = Command(dict(arg.split('=') for arg in args))
        if key == 'SAVE_GCODE_STATE':
            return gm.cmd_SAVE_GCODE_STATE(cmd)
        if key == 'RESTORE_GCODE_STATE':
            return gm.cmd_RESTORE_GCODE_STATE(cmd)
        raise AssertionError(text)
    gcode = NS(create_gcode_command=lambda key, text, params: Command(params),
               run_script_from_command=script, get_mutex=nullcontext)
    pause = object.__new__(PauseResume)
    pause.gcode, pause.v_sd, pause.recover_velocity = gcode, sd, 50.
    pause.is_paused = pause.sd_paused = pause.pause_command_sent = False
    ops = object.__new__(P02)
    ops.gcode, ops.printer, ops.state = gcode, printer, lease_state
    ops.vsd, ops.th, ops.pause, ops.installed, ops.contexts = sd, native, pause, True, {}
    ops.require_motion = ops._require_print_reference = lambda: None
    calls = []
    def move(position, speed):
        if tuple(position) == tuple(native.pos):
            return
        assert ops.current_lease() == lease and ops.state.mode == 'paused'
        assert motion.JamMotion._in_box(position[:3], kin.limits)
        norm = max(abs(a-b) for a, b in zip(position, native.pos))
        clock.now += norm / speed
        native.pos = list(position)
        calls.append((list(position), speed))
    native.move = move
    tools = NS(enabled=True, require_binding=lambda: None, bound_tool=tool,
               frame=NS(cal=NS(store=NS(revision=3, value=object()))),
               box=((-5., 155.), (0., 155.)), z_min=1., z_max=140., travel_speed=50.)
    objects.update(toolhead=native, gcode_move=gm, gcode=gcode, pause_resume=pause,
                   virtual_sdcard=sd, inventure_tools=tools, inventure_operations=ops)
    return motion.JamMotion(board), tools, calls


class TestJamMotion(unittest.TestCase):
    def test_both_tools_preserve_modes_and_resume_above_saved_xy(self):
        for tool in (0, 1):
            with self.subTest(tool=tool):
                m, tools, moves = make_motion(tool)
                m.pause()
                ctx = m.park('T%d' % tool)
                self.assertEqual([p for p, _ in moves][:2], [[40., 54., 2., 916.], [40., 54., 3., 916.]])
                self.assertEqual(moves[-2][0][:2], [(31.5, 103.5)[tool]+1., 150.])
                self.assertEqual(moves[-1][0][:2], [68.5, 150.])
                m.feed(ctx, 10.)
                m.clean(ctx)
                self.assertEqual(moves[-1][0][:2], [68.5, 150.])
                self.assertEqual(tuple(m.head.get_kinematics().limits), ctx.limits)
                m.resume(ctx)
                raw = m.head.get_position()
                self.assertTrue(m._get('virtual_sdcard').active)
                self.assertEqual(raw[:3], [40., 54., 2.])
                self.assertEqual([p[:3] for p, _ in moves[-3:]],
                                 [[40.,54.,3.], [40.,54.,2.], [40.,54.,2.]])
                self.assertEqual(m.gm._get_gcode_position()[3], (917.-111.) / 1.2)
                self.assertEqual(m._modes(m.gm), ctx.modes)
                self.assertFalse(m.authorized_resume())

    def test_stops_before_first_move_if_hotend_bin_or_profile_invalid(self):
        for cause in ('cold', 'small_box', 'missing_motor_binding', 'bin', 'z_limit'):
            with self.subTest(cause=cause):
                m, t, moves = make_motion()
                if cause == 'cold':
                    m.board.panel_temperature = lambda i, t: (43., 'valid')
                elif cause == 'small_box':
                    t.box = ((0, 138), (0, 138))
                elif cause == 'missing_motor_binding':
                    t.bound_tool = None
                elif cause == 'bin':
                    m.board._fresh_inputs = lambda: {'raw': {'tray': 0, 'bin': 1}}
                else:
                    m._need('toolhead').get_kinematics().limits[2] = (0., 2.5)
                m.pause()
                with self.assertRaises(ValueError):
                    m.park(0)
                self.assertEqual(moves, [])
                self.assertTrue(m._need('pause_resume').is_paused)

    def test_changed_file_position_or_foreign_move_blocks_resume(self):
        for cause in ('file', 'seek', 'move', 'epoch', 'temperature', 'cancel'):
            with self.subTest(cause=cause):
                m, t, moves = make_motion()
                m.pause()
                ctx = m.park(0)
                if cause == 'file':
                    m._need('virtual_sdcard').current_file = object()
                elif cause == 'seek':
                    m._need('virtual_sdcard').file_position += 1
                elif cause == 'move':
                    m.head.pos[1] += .5
                elif cause == 'epoch':
                    m.ops.state.invalidate('xyz', 'motor_off')
                elif cause == 'temperature':
                    m.head.get_extruder().get_heater().target_temp = 0.
                else:
                    m.abort(ctx)
                count = len(moves)
                with self.assertRaises(ValueError):
                    m.resume(ctx)
                self.assertEqual(len(moves), count)
                self.assertFalse(m._need('virtual_sdcard').active)

    def test_cassette_load_reuses_short_path_without_rehoming_or_reselecting(self):
        m, t, moves = make_motion(1)
        m.pause()
        ctx = m.park(1)
        starts = 0
        mode = 0
        def sample(tool):
            nonlocal starts
            self.assertEqual(tool, 'T1')
            if mode == 1:
                starts += 1
            return dict(a=1, b=0, starts=starts, pulses=0, pulse_age_ms=0, fault=0)
        def set_mode(tool, value, monitor):
            nonlocal mode
            mode = value
            return sample(tool)
        phases = []
        self.assertEqual(m.reload(ctx, NS(sample=sample, set_mode=set_mode), lambda: None, phases.append), 'L1')
        self.assertEqual(phases, ['L1'])
        self.assertEqual(mode, 0)
        self.assertEqual(moves[-1][0][:2], [68.5, 150.])

    def test_p17_interlocks_checked_while_over_waste_bin(self):
        from host.inventure_cycle import InventureCycle
        from host.inventure_cycle_core import Cycle, Phase
        m, _, moves = make_motion()
        m.pause()
        ctx = m.park(0)
        records = dict(material_generation=3, calibration_revision=4,
                       position_epoch=m.ops.state.position_epoch, job_id=id(ctx.file))
        mat = NS(generation=3, pair=[NS(source='spool', material_id=1)] * 2)
        thermal = NS(require_ready=lambda: None, get_status=lambda now: {},
                     before_resume=lambda: None)
        filament = fans = NS(require_ready=lambda: None, require_print_ready=lambda: None)
        objects = dict(inventure_materials=mat, inventure_thermal=thermal, inventure_fans=fans,
                       inventure_filament=filament, inventure_calibration=NS(get_status=lambda now: dict(revision=4)))
        bridge = object.__new__(InventureCycle)
        bridge.reactor, bridge.ops, bridge.vsd, bridge.enabled = m.reactor, m.ops, m._get('virtual_sdcard'), True
        bridge.printer = NS(lookup_object=objects.get, command_error=ValueError)
        bridge.cycle = Cycle(phase=Phase.PAUSED, epoch=ctx.lease.epoch, snapshot=records)
        bridge._sync = lambda: None
        bridge.p16 = fans
        bridge.p14 = NS(get_status=lambda now: dict(automatic_step_accounting=True, stopped=False, error=None,
                        jam=False, storage_busy=False, channels=[dict(uncertain=False, intent=None, report_current=True)] * 2))
        bridge._resume_checks(commit=False)
        self.assertEqual(bridge.cycle.phase, Phase.PAUSED)
        self.assertEqual(bridge.cycle.history, [])
        mat.pair[0] = NS(source='cartridge', material_id=2)
        old_lookup = m.printer.lookup_object
        m.printer.lookup_object = lambda key, default=None: (bridge if key == 'inventure_cycle'
                                                             else old_lookup(key, default))
        old_count = len(moves)
        with self.assertRaisesRegex(ValueError, 'P12_RFID'):
            m.resume(ctx)
        self.assertEqual(len(moves), old_count)
        self.assertEqual(ctx.expected[:2], (68.5, 150.))
        self.assertEqual(bridge.cycle.phase, Phase.PAUSED)

    def test_cancel_during_park_and_soft_range_restore(self):
        m, t, moves = make_motion()
        m.pause()
        def guard():
            if len(moves) == 2:
                raise ValueError('cancelled')
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            m.park('T0', guard)
        self.assertEqual(len(moves), 2)
        self.assertEqual(m._need('toolhead').get_kinematics().limits[1], (0., 138.))
        self.assertEqual(m.ops.state.mode, 'paused')
        self.assertFalse(m._need('virtual_sdcard').active)


if __name__ == '__main__':
    unittest.main()
