# SPDX-License-Identifier: GPL-3.0-or-later
"""r0323 platform wizard on the released-switch SERVICE motion transport."""
import math

from .inventure_factory_core import FactoryError
from .inventure_z_home import NOZZLE_CONTACT, Z_BOTTOM, Z_STEPS_PER_MM, ZHomeProgram

OEM_CENTER = (67.5, 65.5)
STEPS = Z_STEPS_PER_MM
CENTER_STABLE_CYCLES = 101


def raw_point(oem_xy, center_xy):
    return tuple(raw + coord - origin for raw, coord, origin in zip(center_xy, oem_xy, OEM_CENTER))


def platform_reference(record, parameters):
    """Saved FULL measurements are in the released-switch frame, including R23 files."""
    from .inventure_platform_level import POINTS
    if not isinstance(record, dict):
        raise ValueError('Invalid inventure_nozzle_pad.json')
    measurements = record.get('measurements')
    if (type(record.get('schema')) is not int or record['schema'] != 1 or record.get('mode', 'FULL') != 'FULL'
            or not isinstance(record.get('candidate'), dict) or not record['candidate']
            or not isinstance(measurements, dict) or set(measurements) != {'T0', 'T1', 'T0_RETURN'}):
        raise ValueError('Leveling requires a saved FULL pad calibration for both nozzles')
    for sample in measurements.values():
        if not isinstance(sample, dict):
            raise ValueError('Incomplete measurement in inventure_nozzle_pad.json')
        coords, height = sample.get('center_xy_mm'), sample.get('z_release_mm_from_pe12')
        if (not isinstance(coords, (tuple, list)) or len(coords) != 2
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in (*coords, height))
                or not 20. < height <= 150.):
            raise ValueError('Invalid measurement in inventure_nozzle_pad.json')
    from .inventure_nozzle_xyz import pad_xyz_candidate
    settings = record.get('measurement_settings', {})
    if not isinstance(settings, dict):
        raise ValueError('Invalid settings in inventure_nozzle_pad.json')
    limits = {}
    for key, default, low in (('xy_repeat_mm', .08, .01), ('z_repeat_mm', .05, .005)):
        value = settings.get(key, default)
        if type(value) not in (int, float) or not low <= value <= .25:
            raise ValueError('Invalid tolerance in inventure_nozzle_pad.json')
        limits[key] = value
    candidate = pad_xyz_candidate(measurements, limits)
    if candidate != record['candidate']:
        raise ValueError('The saved T0/T1 result does not match the measurements in inventure_nozzle_pad.json')
    center = measurements['T0']['center_xy_mm']
    z = measurements['T0']['z_release_mm_from_pe12']
    for point in POINTS:
        for axis, coord in zip('xy', raw_point(point, center)):
            if not parameters['min_' + axis] <= coord <= parameters['max_' + axis]:
                raise ValueError('Leveling point is outside the travel envelope for axis ' + axis.upper())
    return tuple(center), round(z * STEPS)


class PlatformLevelTransport(ZHomeProgram):
    def __init__(self, p, settings, center_xy, saved_release_steps, run, initial_bin):
        super().__init__(p, settings)
        # r0323 counts consecutive releases since the last lower minimum.
        # Platform probes keep their Z window and the factory run deadline.
        self.settings.update(z_stable_cycles=CENTER_STABLE_CYCLES, z_max_cycles=None)
        self.center = center_xy
        self.saved_release_steps = saved_release_steps
        self.run = run
        self.initial_bin = initial_bin
        self.max_z = round(settings['z_bottom_max_mm'] * STEPS)
        self.resume_required = False
        self.datum_shift = 0

    def _bottom(self):
        result = yield dict(kind='home', axis='Z', max_mm=self.p['home_z_max'], speed=self.p['z_speed'])
        if not result or result.get('ok') is not True:
            raise FactoryError('The PE12 bottom endstop was not confirmed')
        self.z_steps = 0

    def _z_position(self, target, stage=None):
        if not 0 < target <= self.max_z:
            raise FactoryError('Z is outside the platform travel range')
        delta = target - self.z_steps
        if delta:
            up = delta > 0
            mask = NOZZLE_CONTACT if up else Z_BOTTOM
            _, hit, _ = yield from self._z_move(delta, self.settings['z_approach_speed'] if up else
                                                self.settings['z_backoff_speed'], mask, 0 if up else mask,
                                                1 if up else None, 0, stage=stage)
            if hit or self.z_steps != target:
                raise FactoryError('Unexpected contact during Z travel', hit=hit)
        yield dict(kind='z_inputs', expected={'z_bottom': 0, 'nozzle_contact': 1})

    def _travel(self, point):
        yield dict(kind='z_inputs', expected={'nozzle_contact': 1})
        yield from self._move(*point)

    def prepare(self):
        yield from self._bottom()
        yield from self._z_leave_bottom(stage='level_setup')
        self.datum = self.z_steps
        shift = 0.
        if self.run['initial_tool'] == 'T1':
            shift = self.run['t1_lower_by_mm']
        elif self.run['initial_tool'] == 'UNKNOWN':
            shift = max(0., self.run['t1_lower_by_mm'])
        yield from self._z_position(self.z_steps + round((135 - 40 - shift) * STEPS), stage='level_setup')
        yield from self._home_steps(y_first=True, include_z=False)
        yield from self._travel(self.initial_bin)

    def await_operator(self, phase, instructions):
        resumed = yield dict(kind='level_wait', phase=phase)
        if resumed.get('released_drivers'):
            self.resume_required = True

    def heat_and_select_t0(self):
        if self.resume_required:
            yield from self.prepare()
            self.resume_required = False
        yield dict(kind='level_heat')
        yield from self.select_tool()
        yield dict(kind='level_selected')

    def _z_edge(self, operation, steps):
        # MCU messages allow 20 mm per leg. A 15 mm +/- search can require
        # more than 20 mm when reversing. Keep one bound and count each STEP.
        remaining = steps
        while remaining > 16040:
            found, sent = yield from super()._z_edge(operation, 16040)
            remaining -= sent
            if found and operation != 2:
                return True, steps - remaining
        found, sent = yield from super()._z_edge(operation, remaining)
        return found, steps - remaining + sent

    def _reference(self, span_mm, repeats):
        self.settings['z_stable_cycles'] = repeats
        result = yield from self._z_oem_reference(span_mm * STEPS, upper_limit=self.max_z)
        if result['completion_basis'] != 'stable_repetitions':
            raise FactoryError('Could not confirm a repeatable PC14 release')
        return result['release_steps_from_bottom']

    def center_reference(self):
        if self.run['initial_tool'] != 'T0':
            # R13 removes holding torque during the dual/single handover.
            yield from self._bottom()
        yield from self._home_steps(y_first=True, include_z=False)
        if self.run['initial_tool'] != 'T0':
            yield from self._z_leave_bottom()
            self.datum = self.z_steps
        yield from self._travel(self.center)
        datum = self.datum
        approach = datum + (135 - 2) * STEPS
        if not approach < self.saved_release_steps <= min(self.max_z, approach + 10 * STEPS):
            raise FactoryError('The saved T0 center height is outside the OEM reference window')
        yield from self._z_position(approach)
        center = yield from self._reference(10, CENTER_STABLE_CYCLES)
        self.reference_datum = datum
        return center, datum

    def prepare_sampling(self, depth_steps):
        yield from self._z_position(depth_steps - 3 * STEPS)

    def _resume_adjustment(self, original_center, first_point):
        yield from self._bottom()
        yield from self._home_steps(y_first=True, include_z=False)
        yield from self._z_leave_bottom(stage='level_setup')
        self.datum = self.z_steps
        self.datum_shift = self.datum - self.reference_datum
        self.run.setdefault('resume_datums', []).append(dict(
            original_datum_steps=self.reference_datum, current_datum_steps=self.datum,
            difference_steps=self.datum_shift, current_center_steps=original_center + self.datum_shift))
        # The smallest OEM screw acceptance bound is 79 steps. A datum that
        # cannot be reproduced to that precision cannot compare the two groups.
        if abs(self.datum_shift) > 79:
            raise FactoryError('The PE12 bottom reference did not repeat after adjustment',
                               difference_steps=self.datum_shift)
        # Physical tool is T0 after the original verified selector press. The
        # operator handles the knobs only. Heat at its bin and reseat T0 there.
        yield from self.park_for_operator()
        yield dict(kind='level_heat')
        yield from self.select_tool()
        yield dict(kind='level_selected')
        # Leave the service bay with the table low, then approach Z3 directly
        # at the next conductive site after re-establishing the reference.
        yield from self._travel(raw_point(first_point, self.center))
        yield from self._z_position(original_center + self.datum_shift - 3 * STEPS)
        self.resume_required = False

    def probe(self, point_id, machine_xy, depth_steps):
        from .inventure_platform_level import POINTS
        if not 0 <= point_id < len(POINTS) or machine_xy != POINTS[point_id]:
            raise FactoryError('Invalid leveling point')
        if self.resume_required:
            yield from self._resume_adjustment(depth_steps, machine_xy)
        depth_steps += self.datum_shift
        if self.z_steps != depth_steps - 3 * STEPS:
            raise FactoryError('The 3 mm Z clearance was not established before probing')
        yield from self._travel(raw_point(machine_xy, self.center))
        sample = yield from self._reference(15, 151)
        yield from self._z_position(depth_steps - 3 * STEPS)
        return depth_steps - sample

    def park_for_operator(self):
        yield from self._travel((self.p['center_x'], self.p['center_y']))

    def finish(self):
        yield dict(kind='level_heaters_off')
        yield from self._bottom()
        yield from self.park_for_operator()

    def execute(self):
        from .inventure_platform_level import run_leveling
        yield from run_leveling(self, self.run)
        yield dict(kind='done', success=True, terminal='PLATFORM_LEVEL')


def available(board):
    return bool(board.connected and board.allow_actuators and board._z_home_mcu_available()
                and board._factory_motion_available(0) and 'factory_session' in board.commands)


def _initial_tool(board):
    tools = board.printer.lookup_object('inventure_tools', None)
    ops = board.printer.lookup_object('inventure_operations', None)
    if tools is not None and getattr(tools, 'enabled', False) and ops is not None:
        try:
            tools.require_binding()
        except board.printer.command_error:
            return 'UNKNOWN'
        known = getattr(ops.state, 'selector_tool', None)
        if type(known) is int and known in (0, 1):
            return 'T%d' % known
    return 'UNKNOWN'


def _initial_bin(initial_tool, t0, t1):
    if initial_tool == 'T0':
        return (t0['center_x'], t0['center_y'])
    if initial_tool == 'T1':
        x, y = t1['center_x'], t1['center_y']
    else:
        # Each profile defines a horizontal purge stroke over the waste bin.
        # Park inside their overlap while the lowered nozzle is unknown.
        left = max(p['center_x'] - p['purge_span'] / 2 for p in (t0, t1))
        right = min(p['center_x'] + p['purge_span'] / 2 for p in (t0, t1))
        if left >= right or t0['center_y'] != t1['center_y']:
            raise ValueError('The T0 and T1 waste-bin purge paths have no common parking position')
        x, y = (left + right) / 2, t0['center_y']
    if not t0['min_x'] <= x <= t0['max_x'] or not t0['min_y'] <= y <= t0['max_y']:
        raise ValueError('The initial waste-bin parking position is outside the service envelope')
    return (x, y)


def start(board, gcmd, run):
    import json
    from pathlib import Path
    from .inventure_mcu import CONTROL_MODES
    if not available(board):
        raise ValueError('Leveling requires an MCU connection, allow_actuators and the R13 Z probe ABI')
    if getattr(board, 'nozzle_z_context', None) is not None:
        raise ValueError('Nozzle calibration is active')
    control = board._feeder_service_preflight(allow_off=True)
    active_tool = _initial_tool(board)
    heaters = board.printer.lookup_object('heaters', None)
    if heaters is not None and any(h.target_temp or h.last_pwm_value for h in heaters.heaters.values()):
        raise ValueError('Set Klipper heater targets to zero before starting SERVICE heating')
    from .inventure_factory_core import parameters
    from .inventure_z_home import z_home_parameters
    class StockOptions:
        def __init__(self, tool):
            self.tool = tool

        def get(self, name, default=None):
            return {'TOOL': self.tool, 'SPEED_PROFILE': 'OEM', 'TARGET': 200.,
                    'PROCEDURE_TIMEOUT': 3600.}.get(name, default)
    options = StockOptions('T0')
    profiles = getattr(board, 'procedure_profiles', None)
    p = parameters(options, profiles)
    p['heater_cutoff'] = board._nozzle_service_cutoff(0, 200.)
    dual_heaters = []
    t1 = None
    if active_tool != 'T0':
        if getattr(board, 'nozzle_z_dual_abi', 0) != 1:
            raise ValueError('Preheating both nozzles requires the MCU dual-nozzle heating ABI')
        t1 = parameters(StockOptions('T1'), profiles)
        t1['heater_cutoff'] = board._nozzle_service_cutoff(1, 200.)
        for tool, plan in enumerate((p, t1)):
            dual_heaters.append(dict(tool=tool, target=200., power=plan['max_power'],
                                     limit=plan['heater_cutoff'], duty=0, output_token=None))
    initial_bin = _initial_bin(active_tool, p, t1)
    settings = z_home_parameters(options, p, nozzle_test=True)
    cfg = Path(board.printer.get_start_args()['config_file']).absolute()
    with cfg.with_name('inventure_nozzle_pad.json').open(encoding='utf-8') as source:
        record = json.load(source)
    center, release = platform_reference(record, p)
    shift = record['candidate'].get('t1_lower_by_mm')
    if type(shift) not in (int, float) or not math.isfinite(shift) or abs(shift) > 10.:
        raise ValueError('Invalid T1 Z offset in the saved nozzle calibration')
    run.update(initial_tool=active_tool, t0_center_xy_mm=list(center), t1_lower_by_mm=shift)
    board._z_home_inputs({})
    door_policy = board._check_pad_door_policy()
    run['door_pause'] = False
    run['global_door_enabled'] = bool(door_policy and door_policy['enabled'])
    toolhead = board.printer.lookup_object('toolhead', None)
    if toolhead is not None:
        toolhead.wait_moves()
        board._feeder_service_preflight(allow_off=True)
        native_ops = board.printer.lookup_object('inventure_operations', None)
        if native_ops is not None:
            native_ops.state.selector_unknown('platform_level_service_selection')
            native_ops.invalidate('xyz', 'platform_level_service_motion')
        kin = toolhead.get_kinematics()
        if hasattr(kin, 'clear_homing_state'):
            kin.clear_homing_state('xyz')
        elif kin.get_status(board.reactor.monotonic()).get('homed_axes'):
            raise ValueError('Cannot clear the Klipper homing state before SERVICE motion')
    if control['mode'] == 'OFF':
        state = board._control_query(1, CONTROL_MODES.index('SERVICE'))
        if state['reject'] or state['mode'] != 'SERVICE':
            raise ValueError('The MCU did not enter SERVICE mode')
    program = PlatformLevelTransport(p, settings, center, release, run, initial_bin)
    program.dual_heaters = dual_heaters
    board._factory_start(gcmd, operation='PLATFORM_LEVEL', nozzle_parameters=(p, settings),
                         platform=program)


def factory_action(board, context, action):
    from .inventure_platform_level import notice
    f = context['factory']
    run = f['platform_level_run']
    now = board.reactor.monotonic()
    kind = action['kind']
    if kind == 'level_wait':
        if not action.get('announced'):
            door = board._check_pad_door_policy()
            if board.load_test_context is not context:
                return None
            if door and door['enabled']:
                if f['move'] is not None or f.get('z_edge_run') is not None:
                    raise FactoryError('Motion is still active before knob adjustment')
                state = board._control_query(2)
                if board.load_test_context is not context:
                    return None
                if state['reject'] or state['active'] or state['armed'] or state['fault'] or state['latched']:
                    raise FactoryError('The MCU did not confirm that motion and heating stopped before adjustment')
                after = board._door_policy_query()
                if board.load_test_context is not context:
                    return None
                if after['reject'] or after['lock_command_mask'] or after['motor_busy']:
                    raise FactoryError('The MCU did not confirm that the door locks were released before adjustment')
                f.update(level_paused=True, session=False, heat_started=False, reference_valid=False,
                         selection_commanded=False, homed=[], snapshot={}, position=[0., 0., 0.])
                f['motion_legs'].pop('selector_press', None)
                context.update(duty=0, output_token=None, ready_latched=False, ready_since=None,
                               reached_target=False, factory_preparing=True, hold_started=None,
                               deadline=f['total_deadline'])
                for heater in f.get('dual_heaters', ()):
                    heater.update(duty=0, output_token=None)
                run['door_pause'] = True
            run['_prompt_ready'] = True
            action['announced'] = True
            notice(board, run)
        return dict(f['snapshot']) if run['operator_confirmed'] else None
    if kind == 'level_heat':
        if 'dual_heaters' in f:
            if not f['heat_started']:
                board._dual_nozzle_heat_start(context, now)
            ready = board._dual_nozzle_heat_ready(context, now)
        else:
            ready = board._factory_action(context, dict(kind='heat')) is not None
        if not ready:
            return None
        tools = (0, 1) if 'dual_heaters' in f else (0,)
        for tool in tools:
            temp, state = board.panel_temperature(tool, board.reactor.monotonic())
            if state != 'valid' or temp < 199.:
                return None
        context['deadline'] = f['total_deadline']
    elif kind == 'level_selected':
        if 'dual_heaters' in f:
            # R13's dual session releases the drivers and turns both outputs
            # off at STOP. Never reuse that session's positional reference.
            row = board._control_send('factory_session', [3, 0, 0, 0, 0, 0, 0, 0, 0, f['token'], 0],
                                      mutate=True, owner=context)
            if (board.load_test_context is not context or row['reject'] or row['active']
                    or row['token'] != f['token'] or row['reason'] != 2):
                raise FactoryError('The MCU did not confirm the end of the dual-nozzle heating session')
            f.pop('dual_heaters')
            f.update(token=board._control_next_token(), session=False, sequence=-1, snapshot={},
                     reference_valid=False, homed=[], position=[0., 0., 0.])
            context.update(output_token=None, duty=0)
            temp, state = board.panel_temperature(0, board.reactor.monotonic())
            if state != 'valid':
                raise FactoryError('No fresh T0 temperature reading after the session change')
            context['pid'].update(time=now, temperature=temp, derivative=0., integral=0.)
            board._factory_session(context)
    elif kind == 'level_heaters_off':
        f['heat_started'] = False
        board._load_test_output(context, 0)
        context['ready_latched'] = False
    else:
        raise FactoryError('Unknown leveling stage ' + kind)
    return dict(f['snapshot'])


def confirm_doors(board, run):
    door = board._door_policy_query()
    if door['reject'] or door['motor_busy'] or door['lock_command_mask']:
        raise ValueError('The door locks or motors are still engaged')
    if not (door['front_door_closed'] and door['lid_closed']):
        raise ValueError('Close the front door and top cover, then select Next')
    board._z_home_inputs({})
    context = getattr(board, 'load_test_context', None)
    if not context or context['factory'].get('platform_level_run') is not run or not run['door_pause']:
        raise ValueError('The operator pause has already ended')


def paused_tick(board, context):
    # No factory lease, heater lease or physical movement may be renewed while
    # waiting. MCU heartbeat and automatic cooling remain available independently.
    f = context['factory']
    run = f['platform_level_run']
    board._load_test_safety(context)
    if not run['operator_confirmed']:
        return
    try:
        confirm_doors(board, run)
    except ValueError:
        if board.load_test_context is context:
            run['operator_confirmed'] = False
        return
    if board.load_test_context is not context:
        return
    # Opening the cover again between this read and MCU START is also rejected
    # locally by R13. Generate a fresh lease identifier, never replay START.
    f.update(token=board._control_next_token(), sequence=-1, level_paused=False,
             action=None, resume={'released_drivers': True})
    run['door_pause'] = False
