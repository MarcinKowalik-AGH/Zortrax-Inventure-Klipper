# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventure r0323 platform leveling, based on the archived P09 arithmetic.

The motion adapter implements the PC14 contact engine, OEM-to-carriage
transform and heater ownership. All heights here are signed OEM steps.
"""
import copy
import json
import math
from pathlib import Path
import struct
import uuid

POINTS = ((127.5, 13.), (7.5, 13.), (67.5, 65.5), (127.5, 118.), (7.5, 118.))
CORNERS = (0, 1, 4, 3)
STEPS_PER_MM = 802
FINISHED = ('failed', 'cancelled', 'saved')
PROMPTS = ('await_knobs', 'adjust')


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def trunc_div(numerator, denominator):
    return (abs(numerator) // denominator) * (-1 if numerator < 0 else 1)


def turns(delta):
    factor = f32(0.0019953702576463223)
    return math.ceil(f32(f32(abs(delta) * factor) * 4.)) * 0.25


def center_depth_um(release_steps):
    if type(release_steps) is not int or release_steps <= 0:
        raise ValueError('Invalid center reference from PE12')
    # 0x0801DD34 starts sensing at OEM Z2 with a nominal 135 mm depth.
    relative = release_steps - 133 * STEPS_PER_MM
    millimeters = f32(f32(f32(relative / STEPS_PER_MM) + 135.) - 2.)
    return math.trunc(f32(millimeters * 1000.))


def corrections(heights, reference):
    rear = [(knob, heights[i] - reference)
            for i, knob in ((4, 'rear_left'), (3, 'rear_right'))
            if not -160 <= heights[i] - reference <= 80]
    if rear:
        return 'rear', rear
    front = trunc_div(heights[0] + heights[1] - 2 * reference, 2)
    if not -159 <= front <= 79:
        return 'front', [('front', front)]
    return None, []


def final_result(heights, reference, old_depth_um):
    mean = trunc_div(sum(heights.values()), 5)
    depth = math.trunc(f32(f32(old_depth_um) - f32(f32(mean * 1000) / STEPS_PER_MM)))
    # The final check averages differences from Href, not unsigned raw heights.
    corner_delta = trunc_div(sum(heights[i] - reference for i in CORNERS), 4)
    center_error = heights[2] - reference - corner_delta
    if depth <= 0:
        raise ValueError('Invalid platform depth')
    return dict(candidate_depth_um=depth, center_error_steps=center_error,
                deformed=abs(center_error) > 401)


def _ask(transport, run, phase, instructions):
    run.update(phase=phase, instructions=instructions, operator_confirmed=False, _prompt_ready=False,
               prompt_id=run.get('prompt_id', 0) + 1, next_point=None)
    yield from transport.await_operator(phase, instructions)
    if not run['operator_confirmed']:
        raise ValueError('Platform leveling continued without operator confirmation')
    run.update(operator_confirmed=False, _prompt_ready=False, instructions=[])


def _measure(transport, run, points, depth_steps):
    for point in points:
        run.update(phase='measuring', next_point=point, contact_progress=None)
        height = yield from transport.probe(point, POINTS[point], depth_steps)
        if type(height) is not int or abs(height) > 15 * STEPS_PER_MM:
            raise ValueError('Invalid PC14 measurement at point %d' % point)
        run['heights_steps'][point] = height
        run['measurement_count'] += 1


def run_leveling(transport, run):
    yield from transport.prepare()
    yield from _ask(transport, run, 'await_knobs', [])
    run['phase'] = 'heating'
    yield from transport.heat_and_select_t0()
    run['phase'] = 'reference'
    depth_steps, datum_steps = yield from transport.center_reference()
    if type(depth_steps) is not int or type(datum_steps) is not int or datum_steps < 0:
        raise ValueError('Invalid physical PE12 reference')
    run.update(center_release_steps=depth_steps, reference_datum_steps=datum_steps,
               center_depth_um=center_depth_um(depth_steps - datum_steps))
    yield from transport.prepare_sampling(depth_steps)

    heights = run['heights_steps']
    yield from _measure(transport, run, CORNERS, depth_steps)
    reference = run['reference_steps'] = max(heights.values())
    last = CORNERS[-1]
    while True:
        group, items = corrections(heights, reference)
        if not items:
            break
        instructions = [
            dict(knob=knob, delta_steps=delta, turns=turns(delta),
                 action='tighten' if delta > 0 else 'loosen',
                 direction='counterclockwise' if delta > 0 else 'clockwise')
            for knob, delta in items]
        yield from transport.park_for_operator()
        # Each OEM instruction is displayed and counted separately (0x0801E28E).
        for instruction in instructions:
            run['instruction_count'] += 1
            yield from _ask(transport, run, 'adjust', [instruction])
        if run['instruction_count'] > 39:
            raise ValueError('OEM screw instruction limit reached')
        if group == 'rear':
            pair = (4, 3) if last in (1, 4) else (3, 4)
        else:
            pair = (1, 0) if last in (1, 4) else (0, 1)
        yield from _measure(transport, run, pair, depth_steps)
        last = pair[-1]

    yield from _measure(transport, run, (2,), depth_steps)
    result = final_result(heights, reference, run['center_depth_um'])
    run.update(phase='finishing', next_point=None)
    yield from transport.finish()
    run['_pending_result'] = result


def publish_candidate(run):
    # Called by the host only after terminal motion, heater cutoff and stop
    # have all been acknowledged. Do not expose an unverified depth to SAVE.
    if run['phase'] != 'finishing' or '_pending_result' not in run:
        raise ValueError('Platform leveling lacks a verified end-of-run result')
    run.update(run.pop('_pending_result'), phase='candidate')


def status(board):
    run = getattr(board, 'platform_level_run', None)
    if run is None:
        return dict(phase='idle', run_id=None, awaiting_operator=False)
    # The reactor may update the next measurement between Moonraker queries.
    public = {key: value for key, value in run.items()
              if key != 'operator_confirmed' and not key.startswith('_')}
    public['awaiting_operator'] = (run['phase'] in PROMPTS and run.get('_prompt_ready', False)
                                   and not run['operator_confirmed'])
    return copy.deepcopy(public)


def adjustment_text(instruction):
    knob = {'rear_left': 'rear-left', 'rear_right': 'rear-right',
            'front': 'front'}[instruction['knob']]
    action = instruction['action'].capitalize()
    direction = instruction['direction']
    turns = instruction['turns']
    unit = 'turn' if turns == 1 else 'turns'
    return '%s the %s knob %s by %.2f %s.' % (action, knob, direction, turns, unit)


def notice(board, run):
    phase = run['phase']
    if phase == 'await_knobs':
        message = 'Tighten the three knobs under the platform.'
    elif phase == 'adjust':
        message = adjustment_text(run['instructions'][0])
    elif phase == 'candidate':
        message = ('Measurement complete. Platform depth %.3f mm, center deviation %+.3f mm. '
                   % (run['candidate_depth_um'] / 1000., run['center_error_steps'] / STEPS_PER_MM))
        if run['deformed']:
            message += 'The center of the tray is out of tolerance. Replace the tray and repeat leveling.'
        else:
            message += 'Save with ZORTRAX_PLATFORM_LEVEL ACTION=SAVE RUN_ID=' + run['run_id']
    elif phase in ('failed', 'cancelled'):
        message = ('Leveling stopped. ' + str(run.get('error') or 'Cancelled by the operator.'))
    else:
        return
    if phase in PROMPTS:
        if run.get('door_pause'):
            message += (' Heater power and motors are off. Leave the nozzle selector in place. '
                        'Close the door and lid after adjusting the knobs.')
        message += ' Select Next (ZORTRAX_PLATFORM_LEVEL ACTION=NEXT).'
    board.gcode.respond_info('Platform leveling T0 | ' + message)


def _new_run():
    return dict(phase='preparing', run_id=uuid.uuid4().hex, instructions=[],
                operator_confirmed=False, prompt_id=0, next_point=None, contact_progress=None,
                instruction_count=0, measurement_count=0, heights_steps={},
                reference_steps=None, candidate_depth_um=None, deformed=None,
                error=None, saved_file=None, applied_to_print=False)


def _save(board, run, gcmd):
    if (not run or run['phase'] != 'candidate'
            or gcmd.get('RUN_ID', '') != run['run_id']):
        raise gcmd.error('SAVE requires the current candidate RUN_ID')
    if run['deformed'] and gcmd.get('DEFORMATION_ACK', '') != 'ACCEPT_DEFORMED_TRAY':
        raise gcmd.error('Deformed center: replace the tray or confirm DEFORMATION_ACK=ACCEPT_DEFORMED_TRAY')
    path = Path(board.printer.get_start_args()['config_file']).absolute().with_name(
        'inventure_platform_level.json')
    record = dict(schema=1, method='r0323_platform_level', source='T0_PC14',
                  depth_um=run['candidate_depth_um'], run_id=run['run_id'], applied_to_print=False)
    for key in ('heights_steps', 'reference_steps', 'center_depth_um',
                'center_error_steps', 'deformed', 'measurement_count', 'instruction_count',
                'reference_datum_steps', 'center_release_steps'):
        record[key] = run[key]
    record.update({key: run[key] for key in ('initial_tool', 't0_center_xy_mm', 'global_door_enabled',
                                             'resume_datums') if key in run})
    try:
        board._write_calibration_file(path, record)
    except OSError as exc:
        raise gcmd.error(str(exc))
    run.update(phase='saved', saved_file=str(path))


def command(board, gcmd):
    action = str(gcmd.get('ACTION', 'STATUS')).upper()
    run = getattr(board, 'platform_level_run', None)
    if action == 'START':
        if run and run['phase'] not in FINISHED:
            raise gcmd.error('Platform leveling is active or has an unsaved candidate')
        previous = run
        run = board.platform_level_run = _new_run()
        try:
            board._start_platform_level(gcmd, run)
        except Exception:
            board.platform_level_run = previous
            raise
    elif action == 'NEXT':
        if (not run or run['phase'] not in PROMPTS or not run.get('_prompt_ready')
                or run['operator_confirmed']):
            raise gcmd.error('No pending platform leveling instruction')
        if gcmd.get('RUN_ID', run['run_id']) != run['run_id']:
            raise gcmd.error('Wrong platform leveling RUN_ID')
        prompt_id = run['prompt_id']
        if str(gcmd.get('PROMPT_ID', prompt_id)) != str(prompt_id):
            raise gcmd.error('This platform leveling instruction is stale')
        if run.get('door_pause'):
            board._platform_level_confirm_doors(gcmd, run)
            if (board.platform_level_run is not run or prompt_id != run['prompt_id']
                    or not run.get('_prompt_ready') or run['phase'] not in PROMPTS
                    or run['operator_confirmed']):
                raise gcmd.error('This platform leveling instruction is stale')
        run['operator_confirmed'] = True
    elif action == 'CANCEL':
        if run and run['phase'] not in FINISHED:
            if gcmd.get('RUN_ID', run['run_id']) != run['run_id']:
                raise gcmd.error('Wrong platform leveling RUN_ID')
            if run['phase'] != 'candidate':
                board._stop_platform_level(gcmd, run)
            run.update(phase='cancelled', instructions=[], next_point=None,
                       candidate_depth_um=None, operator_confirmed=False)
    elif action == 'SAVE':
        _save(board, run, gcmd)
    elif action != 'STATUS':
        raise gcmd.error('Use ACTION=START, NEXT, STATUS, CANCEL or SAVE')
    gcmd.respond_info('V9_PLATFORM_LEVEL ' + json.dumps(status(board), sort_keys=True))
