# SPDX-License-Identifier: GPL-3.0-or-later
"""Filament repair moves in the paused native print's existing coordinate frame."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import math

from greenlet import getcurrent

from .inventure_factory_core import FactoryProgram, delta32, parameters


@dataclass
class PausedJob:
    tool: int
    file: object
    file_position: int
    lease: object
    epoch: int
    revision: int
    position: tuple
    expected: tuple
    base_xy: tuple
    modes: tuple
    target: float
    params: dict
    limits: tuple
    saved_state: dict
    guard: object = None
    entered: bool = False
    valid: bool = True


class JamMotion:
    def __init__(self, owner):
        self.board = owner
        self.printer = owner.printer
        self.reactor = owner.reactor
        self.pause_job = None
        self.context = None
        self.resume_caller = None

    def _get(self, name):
        return self.printer.lookup_object(name, None)

    def _need(self, name):
        obj = self._get(name)
        if obj is None:
            raise ValueError('Filament recovery needs [' + name + ']')
        return obj

    def pause(self):
        sd = self._need('virtual_sdcard')
        if not sd.is_active() or sd.current_file is None:
            raise ValueError('No running virtual SD print to pause')
        self.pause_job = sd.current_file
        pause = self._get('pause_resume')
        if pause is None:
            sd.do_pause()
            raise ValueError('Print paused. Install [pause_resume] before the next print')
        if pause.is_paused:
            raise ValueError('Another pause owns the print')
        gcode = self._need('gcode')
        pause.cmd_PAUSE(gcode.create_gcode_command('PAUSE', 'PAUSE', {}))
        self._need('toolhead').wait_moves()
        if not pause.is_paused or not pause.sd_paused or sd.is_active() or sd.current_file is not self.pause_job:
            raise ValueError('Could not confirm that the same print has paused')

    def authorized_resume(self):
        return self.resume_caller is getcurrent()

    @staticmethod
    def _modes(gm):
        return (gm.absolute_coord, gm.allow_absolute_extrude, gm.speed, gm.speed_factor,
                gm.extrude_factor, tuple(gm.homing_position), tuple(gm.base_position[:3]))

    def _heat(self, ctx):
        h = self.head.get_extruder().get_heater()
        now = self.reactor.monotonic()
        actual, quality = self.board.panel_temperature(ctx.tool, now)
        if (quality != 'valid' or actual is None or not math.isfinite(actual)
                or not h.min_extrude_temp <= actual <= h.max_temp or not h.can_extrude
                or h.target_temp != ctx.target or not h.min_extrude_temp <= ctx.target <= h.max_temp):
            raise ValueError('The selected nozzle must retain an extrusion-safe print temperature')

    def validate(self, ctx):
        if ctx.guard is not None:
            ctx.guard()
        sd = self._need('virtual_sdcard')
        pause = self._need('pause_resume')
        if (self.context is not ctx or not ctx.valid or self.printer.is_shutdown()
                or not pause.is_paused or not pause.sd_paused or sd.is_active()
                or sd.current_file is not ctx.file or sd.file_position != ctx.file_position
                or self.gm.saved_states.get('PAUSE_STATE') != ctx.saved_state):
            raise ValueError('The original print is no longer paused at its saved file position')
        state = self.ops.state
        if (state.owner != ctx.lease or state.mode != 'paused' or state.fatal
                or state.position_epoch != ctx.epoch or state.pause_reason not in ('material', 'source')):
            raise ValueError('Print ownership, pause reason or position reference changed')
        self.ops._require_print_reference()
        self.ops.require_motion()
        self.tools.require_binding()
        if (self.tools.bound_tool != ctx.tool or self.tools.frame.cal.store.revision != ctx.revision
                or self.head.get_extruder().get_name() != ('extruder', 'extruder1')[ctx.tool]
                or self.gm.move_transform is not None or self._modes(self.gm) != ctx.modes):
            raise ValueError('Print tool, coordinate frame, speed or extrusion mode changed')
        if tuple(self.head.get_position()) != ctx.expected:
            raise ValueError('The print head moved outside the filament recovery procedure')
        control = self.board._control_query()
        if (control['mode'] != 'NATIVE' or not control['armed'] or control['fault']
                or control['latched'] or control['reject']):
            raise ValueError('Native motion control is unavailable')
        raw = self.board._fresh_inputs()['raw']
        if raw.get('tray') != 0 or raw.get('bin') != 0:
            raise ValueError('Seat the tray and waste bin before continuing')
        if self.board.door_policy_global:
            doors = self.board._door_policy_query()
            if doors['reject'] or doors['enabled'] and not (doors['front_door_closed'] and doors['lid_closed']):
                raise ValueError('Global door lock requires the doors to be closed')
        self._heat(ctx)
        # All of the checks above may wait for an MCU reply. Check ownership and
        # position again before admitting the next native planner operation.
        if (not ctx.valid or state.owner != ctx.lease or state.mode != 'paused'
                or state.position_epoch != ctx.epoch or sd.current_file is not ctx.file
                or sd.is_active() or tuple(self.head.get_position()) != ctx.expected):
            raise ValueError('Recovery ownership changed during motion preflight')
        return True

    def _profile(self, tool):
        # The factory program uses released-switch coordinates. Request its
        # canonical OEM geometry here, then translate at the native boundary.
        class Options:
            def get(self, key, default=None):
                return {'TOOL': 'T%d' % tool, 'SPEED_PROFILE': 'OEM'}.get(key, default)
        return parameters(Options(), None, max_target=310.)

    def _raw_xy(self, ctx, x, y):
        # ID0 is fixed at logical OEM (67.5, 150), including in P04/P08.
        # Translate the factory planner's released-switch frame back to OEM,
        # then to native physical coordinates with the confirmed P04 origin.
        return (ctx.base_xy[0] + 67.5 + (x - ctx.params['center_x']),
                ctx.base_xy[1] + 150. + (y - ctx.params['center_y']))

    @staticmethod
    def _in_box(point, box):
        return all(math.isfinite(v) and lo <= v <= hi for v, (lo, hi) in zip(point, box))

    def _point(self, ctx, identifier):
        from .inventure_toolchange_core import service_point
        return tuple(a + b for a, b in zip(
            service_point(identifier, ctx.tool, self.tools.frame.cal.store.value), ctx.base_xy))

    def _path_check(self, ctx):
        p = ctx.params
        candidates = [self._point(ctx, i) for i in (0, 7 + ctx.tool, 9 + ctx.tool)]
        candidates += [self._raw_xy(ctx, x, y) for x, y in (
            (p['center_x'] - p['wipe_span']/2., p['wipe_y']),
            (p['center_x'] + p['wipe_span']/2., p['wipe_y']),
            (p['center_x'] - p['park_span']/2., p['center_y']),
            (p['center_x'] + p['park_span']/2., p['center_y']),
            (p['service_x'], p['service_y']))]
        if not all(self._in_box(point, self.tools.box) for point in candidates):
            raise ValueError('Jam recovery path exceeds the qualified Inventure service envelope')
        z = ctx.position[2] + 1.
        if (not self._in_box(ctx.position[:3], ctx.limits[:3])
                or not ctx.limits[2][0] <= z <= ctx.limits[2][1]
                or not self.tools.z_min <= z <= self.tools.z_max):
            raise ValueError('The 1 mm print clearance exceeds the qualified Z travel')
        if self.head.get_extruder().max_e_dist < 1.:
            raise ValueError('Native print profile does not permit a 1 mm retraction')

    def park(self, tool, guard=None):
        if guard is not None:
            guard()
        if tool in ('T0', 'T1'):
            tool = int(tool[-1])
        if tool not in (0, 1):
            raise ValueError('A physically bound T0 or T1 is required')
        sd = self._need('virtual_sdcard')
        self.head = self._need('toolhead')
        self.gm = self._need('gcode_move')
        self.tools = self._need('inventure_tools')
        self.ops = self._need('inventure_operations')
        if (not self.tools.enabled or not self.ops.installed or not hasattr(self.ops, 'paused_print_move')
                or self.ops.state.owner is None or self.ops.state.owner.kind != 'print'):
            raise ValueError('A qualified P04 tool and a paused native print owner are required')
        if sd.current_file is None or sd.current_file is not self.pause_job:
            raise ValueError('The paused file changed')
        self.tools.require_binding()
        if self.tools.bound_tool != tool:
            raise ValueError('The selected nozzle differs from the paused print tool')
        self.head.wait_moves()
        self.gm.reset_last_position()
        pos = tuple(self.head.get_position())
        pause_state = self.gm.saved_states.get('PAUSE_STATE')
        if (pause_state is None or tuple(pause_state['last_position']) != pos
                or self.gm.move_transform is not None):
            raise ValueError('Paused Klipper coordinates differ from the verified native print frame')
        # P04 frame is tied to the homing origin. A user G92 XY work origin
        # must never move fixed service positions or put a hot tool off the bin.
        ctx = PausedJob(tool, sd.current_file, sd.file_position, self.ops.state.owner,
                        self.ops.state.position_epoch, self.tools.frame.cal.store.revision,
                        pos, pos, tuple(self.gm.homing_position[:2]), self._modes(self.gm),
                        self.head.get_extruder().get_heater().target_temp, self._profile(tool),
                        tuple(self.head.get_kinematics().limits), deepcopy(pause_state), guard)
        self.context = ctx
        self.validate(ctx)
        self._path_check(ctx)
        self._move(ctx, e=-1., speed=25.)
        self._move(ctx, z=pos[2] + 1., speed=8.)
        self._move(ctx, xy=self._point(ctx, 7 + tool))
        self._move(ctx, xy=self._point(ctx, 0))
        ctx.entered = True
        return ctx

    @contextmanager
    def _service_limits(self, ctx):
        # Allow the same qualified physical XY box as the P04 selector for a
        # single owned move. Never re-establish limits lost during that move.
        kin = self.head.get_kinematics()
        if tuple(kin.limits) != ctx.limits:
            raise ValueError('Native travel limits changed since the print was paused')
        for axis in (0, 1):
            low, high = ctx.limits[axis]
            box = self.tools.box[axis]
            kin.limits[axis] = min(low, box[0]), max(high, box[1])
        try:
            yield
        finally:
            for axis in (0, 1):
                if kin.limits[axis][0] <= kin.limits[axis][1] and self.ops.state.axes['xy'[axis]]['valid']:
                    kin.limits[axis] = ctx.limits[axis]

    def _move(self, ctx, **leg):
        with self._need('gcode').get_mutex():
            self._locked_move(ctx, **leg)

    def _locked_move(self, ctx, xy=None, z=None, e=0., speed=None, returning=False):
        self.validate(ctx)
        p = list(ctx.expected)
        if xy is not None:
            if not self._in_box(xy, ctx.limits[:2] if returning else self.tools.box):
                raise ValueError('Filament recovery XY motion outside its qualified envelope')
            p[:2] = xy
        if z is not None:
            if not ctx.limits[2][0] <= z <= ctx.limits[2][1]:
                raise ValueError('Filament recovery Z move is out of range')
            p[2] = z
        if not math.isfinite(e) or abs(e) > self.head.get_extruder().max_e_dist:
            raise ValueError('Filament recovery exceeds the native extrusion limit')
        p[3] += e
        if speed is None:
            speed = self.tools.travel_speed
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError('Invalid recovery speed')
        if tuple(p) != ctx.expected:
            with self._service_limits(ctx):
                self.ops.paused_print_move(ctx.lease, p, speed)
            self.gm.reset_last_position()
            ctx.expected = tuple(p)
        self.validate(ctx)

    def feed(self, ctx, signed_mm, speed=1.6):
        if not ctx.entered or not math.isfinite(signed_mm) or abs(signed_mm) > 50.:
            raise ValueError('Filament feed requires a parked nozzle and a bounded distance')
        if tuple(ctx.expected[:2]) != self._point(ctx, 0):
            raise ValueError('Filament feed is permitted over the waste bin')
        self._move(ctx, e=signed_mm, speed=speed)

    def clean(self, ctx):
        if not ctx.entered:
            raise ValueError('The selected nozzle has not reached the waste bin')
        from .inventure_toolchange_core import FINAL_X
        for x in FINAL_X[ctx.tool]:
            self._move(ctx, xy=(ctx.base_xy[0] + x, ctx.base_xy[1] + 152.))
        self._move(ctx, xy=(ctx.expected[0], ctx.base_xy[1] + 150.))
        self._move(ctx, xy=self._point(ctx, 0))

    def reload(self, ctx, transport, guard, progress, *, unload=False):
        """Run the verified cassette load modules on the existing native lease."""
        guard()
        self.validate(ctx)
        if not ctx.entered or tuple(ctx.expected[:2]) != self._point(ctx, 0):
            raise ValueError('Cassette reload must start above the waste bin')
        program = FactoryProgram(ctx.params)
        program.xy = (ctx.params['center_x'], ctx.params['center_y'])
        steps = program.unload() if unload else program.load(transport.sample('T%d' % ctx.tool))
        response = None
        quiet = None
        deadline = self.reactor.monotonic() + ctx.params['procedure_timeout']

        def check():
            guard()
            self.validate(ctx)
            if self.reactor.monotonic() >= deadline:
                raise ValueError('Cassette recovery load timed out')

        try:
            while True:
                check()
                try:
                    action = steps.send(response)
                except StopIteration as done:
                    if unload:
                        return done.value['sensor_empty_proven']
                    if done.value not in ('L1', 'L4'):
                        raise ValueError('Cassette reload finished without transport confirmation')
                    if tuple(ctx.expected[:2]) != self._point(ctx, 0):
                        self._move(ctx, xy=self._point(ctx, 0))
                    return 'CLEANED' if done.value == 'L4' else 'L1'
                kind = action['kind']
                if kind == 'module':
                    response = None
                elif kind == 'phase':
                    progress(action['name'])
                    response = None
                elif kind == 'mode':
                    response = transport.set_mode('T%d' % ctx.tool, action['mode'], action['monitor'])
                elif kind == 'move':
                    self._move(ctx, xy=self._raw_xy(ctx, action['x'], action['y']),
                               e=action['e'], speed=action['speed'])
                    response = transport.sample('T%d' % ctx.tool)
                elif kind in ('wait_a', 'wait_unload'):
                    until = self.reactor.monotonic() + action['timeout_s']
                    while True:
                        check()
                        response = dict(transport.sample('T%d' % ctx.tool))
                        ok = bool(response['a']) if kind == 'wait_a' else not response['a'] or bool(response['b'])
                        expired = self.reactor.monotonic() >= until
                        if ok or expired:
                            response['ok'] = bool(ok and not expired)
                            break
                        self.reactor.pause(self.reactor.monotonic() + .05)
                elif kind in ('feed_until_edges', 'retract_until_quiet', 'retract_until_a'):
                    response, quiet = self._feedback_move(ctx, transport, action, check, quiet)
                else:
                    raise ValueError('Unsupported native reload action ' + kind)
        finally:
            steps.close()
            # The session owner releases the lease on success, error, or STOP.
            # Never re-arm or restart a feeder in this cleanup path.

    def _feedback_move(self, ctx, transport, action, check, quiet):
        kind = action['kind']
        speed = action['speed']
        now = self.reactor.monotonic()
        end = now + action['timeout_s'] if kind == 'retract_until_a' else None
        remaining = action.get('mm', action.get('timeout_s', 0.) * speed)
        snap = transport.sample('T%d' % ctx.tool)
        last_pulse = snap['pulses']
        baseline = action.get('pulse_base', last_pulse)
        last_edge = None
        if kind == 'retract_until_quiet' and (action.get('reset_quiet') or quiet is None):
            quiet = (last_pulse, now)
        while True:
            check()
            now = self.reactor.monotonic()
            pulses, age = snap['pulses'], snap['pulse_age_ms'] / 1000.
            if kind == 'feed_until_edges':
                if age > action['quiet_s']:
                    baseline, last_edge = pulses, None
                elif pulses != last_pulse:
                    edge = now - age
                    if last_edge is not None and edge - last_edge > action['quiet_s']:
                        baseline = (pulses - 1) & 0xffffffff
                    last_edge = edge
                ok = delta32(pulses, baseline) >= action['count']
                last_pulse = pulses
            elif kind == 'retract_until_quiet':
                if pulses != quiet[0]:
                    quiet = (pulses, now - age)
                ok = now - quiet[1] > action['quiet_s']
                if action.get('min_steps') and remaining == action['mm']:
                    ok = False
            else:
                ok = not snap['a']
                remaining = min(remaining, max(0., end - now) * speed)
            if ok or remaining < .5 / 960.:
                return dict(snap, ok=ok), quiet
            # Sub-second segments allow both fresh pulse-window evidence and
            # cancellation between segments, without a stop/rebase of the axis.
            part = min(remaining, .4 * speed)
            self._move(ctx, e=part if kind == 'feed_until_edges' else -part, speed=speed)
            remaining -= part
            snap = transport.sample('T%d' % ctx.tool)

    def restore_e(self, ctx):
        gcode = self._need('gcode')
        with gcode.get_mutex():
            self.validate(ctx)
            cmd = gcode.create_gcode_command('RESTORE_GCODE_STATE', '', {'NAME': 'PAUSE_STATE', 'MOVE': '0'})
            self.gm.cmd_RESTORE_GCODE_STATE(cmd)

    def resume(self, ctx, guard=None, before_resume=None):
        if not ctx.entered or tuple(ctx.expected[:2]) != self._point(ctx, 0):
            raise ValueError('Verified recovery must finish over the waste bin')
        check = guard if guard is not None else lambda: None
        check()
        # Verify the same P02/P17 resume prerequisites over the bin before
        # bringing the nozzle back over the model. Final dispatch rechecks.
        with self._need('gcode').get_mutex():
            self.validate(ctx)
            thermal = self._get('inventure_thermal')
            if thermal is not None:
                thermal.before_resume()
            cycle = self._get('inventure_cycle')
            if cycle is not None and cycle.enabled:
                cycle._resume_checks(commit=False)
        check()
        self._move(ctx, xy=self._point(ctx, 9 + ctx.tool))
        check()
        self._move(ctx, xy=self._point(ctx, 0))
        check()
        self._move(ctx, e=-1., speed=25.)
        check()
        self._move(ctx, xy=ctx.position[:2], returning=True)
        check()
        self._move(ctx, z=ctx.position[2], speed=8.)
        check()
        self._move(ctx, e=1., speed=25.)
        self.restore_e(ctx)
        check()
        self.validate(ctx)
        with self._need('gcode').get_mutex():
            check()
            self.validate(ctx)
            if before_resume is not None:
                before_resume()
                check()
            self.resume_caller = getcurrent()
            try:
                self.ops.resume_filament_recovery(ctx.lease)
            finally:
                self.resume_caller = None
        if self._need('pause_resume').is_paused or not self._need('virtual_sdcard').is_active():
            raise ValueError('Klipper did not confirm print resume')
        self.abort(ctx)

    def abort(self, ctx):
        if ctx is not None:
            ctx.valid = False
        if ctx is self.context:
            self.context = None
        self.pause_job = None
