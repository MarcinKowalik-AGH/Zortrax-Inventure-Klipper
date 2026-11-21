# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent operator settings and manual cassette transport for the R7 planner."""
import json
import math
from pathlib import Path

from .inventure_factory_core import E_STEPS_PER_MM, FactoryError, parameters

FIELDS = ('load_temp', 'load_speed', 'unload_temp', 'unload_speed')
DIRECTIONS = {'AUTO': 'load', 'UNLOAD': 'unload'}
WAIT_SECONDS = 120.


def number(raw, name, low, high):
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        value = math.nan
    if isinstance(raw, bool) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('%s must be %g..%g' % (name, low, high))
    return value


class CommandOptions:
    def __init__(self, command, **options):
        self.command = command
        self.options = options

    def get(self, key, default=None):
        return self.options[key] if key in self.options else self.command.get(key, default)

    def __getattr__(self, name):
        return getattr(self.command, name)


def resolve(gcmd, configured, operation, saved, max_target):
    direction = DIRECTIONS.get(operation)
    if operation == 'MANUAL':
        # MANUAL_DIR may also come from printer.cfg.
        tool = str(gcmd.get('TOOL', 'T0')).upper()
        defaults = {}
        if configured and tool in ('0', '1', 'T0', 'T1'):
            defaults.update(configured[0])
            defaults.update(configured[int(tool[-1])+1])
        direction = str(gcmd.get('MANUAL_DIR', defaults.get('manual_dir', 'LOAD'))).lower()
    if direction is None:
        return parameters(gcmd, configured, operation, max_target=max_target)
    options = {}
    target = saved.get(direction+'_temp')
    if target is not None and gcmd.get('TARGET', None) is None:
        options['TARGET'] = target
    if operation == 'AUTO' and 'unload_speed' in saved:
        # The original L4 fallback invokes the same U1/U2/U3 retraction.
        for key in ('UNLOAD_SPEED1', 'UNLOAD_SPEED2'):
            if gcmd.get(key, None) is None:
                options[key] = saved['unload_speed']
    speed = gcmd.get('SPEED', saved.get(direction+'_speed'))
    if speed is not None:
        speed = number(speed, 'SPEED', .1, 10.)
        keys = ('UNLOAD_SPEED1', 'UNLOAD_SPEED2') if operation == 'UNLOAD' else ('E_SPEED',)
        options.update((k, speed) for k in keys if gcmd.get(k, None) is None)
    p = parameters(CommandOptions(gcmd, **options), configured, operation, max_target=max_target)
    if operation == 'AUTO' and speed is not None:
        # L3 is straight E. In L1 and L4 the OEM X stroke meters a fixed
        # number of E steps; reduce X speed to get the requested filament rate.
        # Keep the qualified coordinated speed and geometry as upper bounds.
        p['purge_speed'] = min(p['purge_speed'], speed * E_STEPS_PER_MM
                               * p['purge_span'] / p['purge_e_steps'])
    return p


class MaterialService:
    def __init__(self, board):
        self.board = board
        self.path = Path(board.printer.get_start_args()['config_file']).absolute().with_name(
            'inventure_filament_settings.json')
        self.profiles = self._read()

    def _read(self):
        try:
            record = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {'T0': {}, 'T1': {}}
        if not isinstance(record, dict) or record.get('schema') != 1:
            raise ValueError('Unsupported Inventure filament settings file')
        profiles = record.get('tools')
        if not isinstance(profiles, dict) or set(profiles) != {'T0', 'T1'}:
            raise ValueError('Invalid T0/T1 filament settings')
        for values in profiles.values():
            if not isinstance(values, dict) or set(values) - set(FIELDS):
                raise ValueError('Unknown saved filament setting')
            for key, value in values.items():
                limits = (.1, 10.) if key.endswith('speed') else (180., 310.)
                values[key] = number(value, key.upper(), *limits)
        return profiles

    def parameters(self, gcmd, operation, profiles=None):
        tool = str(gcmd.get('TOOL', 'T0')).upper()
        if tool in ('0', '1'):
            tool = 'T'+tool
        return resolve(gcmd, getattr(self.board, 'procedure_profiles', None), operation,
                       (self.profiles if profiles is None else profiles).get(tool, {}),
                       getattr(self.board, 'nozzle_service_target_max', 240.))

    def _save(self, gcmd, tool):
        changes = {k: gcmd.get(k.upper()) for k in FIELDS if gcmd.get(k.upper(), None) is not None}
        if not changes:
            raise ValueError('Set LOAD_TEMP, LOAD_SPEED, UNLOAD_TEMP or UNLOAD_SPEED (number or AUTO)')
        updated = {name: dict(values) for name, values in self.profiles.items()}
        for key, raw in changes.items():
            if str(raw).strip().upper() == 'AUTO':
                updated[tool].pop(key, None)
            elif key.endswith('temp'):
                value = number(raw, key.upper(), 180., self.board.nozzle_service_target_max)
                self.board._nozzle_service_cutoff(int(tool[-1]), value)
                updated[tool][key] = value
            else:
                updated[tool][key] = number(raw, key.upper(), .1, 10.)
        for op in DIRECTIONS:
            self.parameters({'TOOL': tool, 'SPEED_PROFILE': 'OEM'}, op, updated)
        self.board._write_calibration_file(self.path, dict(schema=1, tools=updated))
        self.profiles = updated

    def status(self):
        ctx = getattr(self.board, 'load_test_context', None)
        f = (ctx or {}).get('factory') or {}
        prompt = f.get('material_prompt')
        return dict(settings={t: dict(v) for t, v in self.profiles.items()},
                    tool='T%d' % ctx['tool'] if f.get('interactive_material') else None,
                    session=f.get('token') if f.get('interactive_material') else None,
                    prompt=prompt, operation=f.get('operation') if f.get('interactive_material') else None)

    def command(self, gcmd):
        action = str(gcmd.get('ACTION', 'STATUS')).upper()
        keys = set(gcmd.get_command_parameters()) - {'ACTION', 'TOOL'}
        try:
            if action in ('MORE', 'FINISH'):
                if keys - ({'SESSION', 'PROMPT', 'DIR', 'MM', 'SPEED'} if action == 'MORE'
                           else {'SESSION', 'PROMPT'}):
                    raise ValueError('Unsupported manual transport parameter')
                self._answer(gcmd, action)
                return
            if action == 'STATUS' and not keys:
                state = self.status()
                effective = {}
                for tool in self.profiles:
                    options = {'TOOL': tool, 'SPEED_PROFILE': 'OEM'}
                    load = self.parameters(options, 'AUTO')
                    unload = self.parameters(options, 'UNLOAD')
                    effective[tool] = dict(load_temp=None if load['target_requires_override'] else load['target'],
                        load_speed=load['e_speed'], unload_temp=None if unload['target_requires_override'] else unload['target'],
                        unload_speed1=unload['unload_speed1'], unload_speed2=unload['unload_speed2'],
                        oscillating_feed_mm_s=round(load['purge_speed']*load['purge_e_steps']
                                                   / (load['purge_span']*E_STEPS_PER_MM), 4))
                state['effective'] = effective
                gcmd.respond_info('INVENTURE_MATERIAL '+json.dumps(state, sort_keys=True))
                return
            tool = str(gcmd.get('TOOL', '')).upper()
            if not tool and action in ('LOAD', 'UNLOAD', 'JOG'):
                options = gcmd.get_command_parameters()
                self.board.zortrax.choose_cassette(action, {k: options[k] for k in keys})
                return
            if tool not in self.profiles:
                raise ValueError('Choose TOOL=T0 or T1')
            if action == 'SAVE' and not keys - {k.upper() for k in FIELDS}:
                self._save(gcmd, tool)
                gcmd.respond_info('Saved %s filament settings %s' % (tool, json.dumps(self.profiles[tool], sort_keys=True)))
                return
            options = {'SPEED_PROFILE': 'OEM'}
            if action == 'JOG' and not keys - {'TEMP', 'SPEED', 'MM', 'DIR', 'MATERIAL'}:
                operation = 'MANUAL'
                direction = str(gcmd.get('DIR', 'LOAD')).upper()
                if direction not in ('LOAD', 'UNLOAD'):
                    raise ValueError('DIR must be LOAD or UNLOAD')
                options.update(MANUAL_DIR=direction, MANUAL_MM=number(gcmd.get('MM', 5.), 'MM', .1, 20.), FEEDER=1)
            elif action in ('LOAD', 'UNLOAD') and not keys - {'TEMP', 'SPEED', 'MATERIAL', 'INTERACTIVE'}:
                operation = 'AUTO' if action == 'LOAD' else 'UNLOAD'
                options['INTERACTIVE'] = gcmd.get('INTERACTIVE', 1)
            else:
                raise ValueError('Use STATUS, SAVE, LOAD, UNLOAD, MORE, FINISH or JOG with the matching parameters')
            if gcmd.get('TEMP', None) is not None:
                options['TARGET'] = gcmd.get('TEMP')
            self.board._factory_start(CommandOptions(gcmd, **options), operation=operation,
                                      enter_service=True, cassette_jog=operation == 'MANUAL')
        except (ValueError, OSError) as exc:
            raise gcmd.error(str(exc)) from exc

    def _answer(self, gcmd, action):
        state = self.status()
        if state['prompt'] is None:
            raise ValueError('No cassette procedure is waiting for a manual command')
        if (str(gcmd.get('TOOL', state['tool'])).upper() != state['tool']
                or str(gcmd.get('SESSION', '')) != str(state['session'])
                or str(gcmd.get('PROMPT', '')) != str(state['prompt'])):
            raise ValueError('The cassette tool, session or prompt has changed')
        f = self.board.load_test_context['factory']
        if self.board.reactor.monotonic() >= f['material_until']:
            raise ValueError('The manual transport prompt expired')
        reply = {'kind': action}
        if action == 'MORE':
            context = self.board.load_test_context
            temp, state_code = self.board.panel_temperature(context['tool'], self.board.reactor.monotonic())
            if state_code != 'valid' or not max(180., context['target']-15.) <= temp <= context['target']+8.:
                raise ValueError('Wait for the selected nozzle to reach its filament temperature')
            direction = str(gcmd.get('DIR', 'LOAD')).upper()
            if direction not in ('LOAD', 'UNLOAD'):
                raise ValueError('DIR must be LOAD or UNLOAD')
            mm = number(gcmd.get('MM', 5.), 'MM', .1, 20.)
            speed = number(gcmd.get('SPEED', f['manual_speeds'][direction]), 'SPEED', .1, 10.)
            reply.update(mm=mm if direction == 'LOAD' else -mm, speed=speed)
        # Consume this invitation synchronously before the next timer tick.
        f.update(material_prompt=None, material_reply=reply)


def wait_for_operator(board, context, action):
    f = context['factory']
    now = board.reactor.monotonic()
    if 'until' not in action:
        if not f['reference_valid'] or not f['selection_commanded'] or f['mode'] != 0:
            raise FactoryError('Manual transport needs a parked, selected tool and stopped feeder')
        p = f['parameters']
        if tuple(f['position'][:2]) != (p['center_x'], p['center_y']):
            raise FactoryError('Manual transport requires the waste bin position')
        action['until'] = min(now + WAIT_SECONDS, f['total_deadline'])
        f['material_until'] = action['until']
        f['material_prompt'] = action['prompt']
        board.gcode.respond_info('T%d at waste bin. INVENTURE_FEED_MORE, INVENTURE_RETRACT_MORE or INVENTURE_FILAMENT_FINISH (%.0f s).' %
                                 (context['tool'], action['until'] - now))
    if f.get('material_reply') is not None:
        return f.pop('material_reply')
    if now >= action['until']:
        f['material_prompt'] = None
        return {'kind': 'FINISH'}
    return None
