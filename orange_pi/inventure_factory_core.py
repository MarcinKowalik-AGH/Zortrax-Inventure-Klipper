# SPDX-License-Identifier: GPL-3.0-or-later
"""Parameterized r0323 cartridge sequence, independent of serial/printer IO.

The executor owns fresh snapshots, completed motion and all hardware guards.
Counters below are auxiliary motor starts and accepted filament edges, not E
STEP counts. Coordinates are nominal service coordinates, before calibration.
"""
import math
import struct
import json


E_STEPS_PER_MM = 960
XY_STEPS_PER_MM = 640
UINT32 = 0xffffffff
PROCEDURES = {
    'AUTO': ('home', 'heat', 'select_tool', 'load_l1_l4'),
    'HOME': ('home',),
    'SELECT': ('home', 'select_tool'),
    'PURGE': ('home', 'heat', 'select_tool', 'position_bin', 'purge', 'optional_clean'),
    'CLEAN': ('home', 'heat', 'select_tool', 'position_bin', 'clean'),
    'MANUAL': ('home', 'heat', 'select_tool', 'position_bin', 'manual_feed'),
    'UNLOAD': ('home', 'heat', 'select_tool', 'position_bin', 'unload'),
}


class FactoryError(ValueError):
    def __init__(self, code, **details):
        super().__init__(code)
        self.code = code
        self.details = details


def _tool(value):
    value = str(value).upper()
    if value not in ('T0', 'T1', '0', '1'):
        raise FactoryError('TOOL must be T0 or T1')
    return int(value[-1])


def defaults(tool=0):
    tool = _tool(tool)
    return dict(tool=tool, l1_portion=20., l1_budget=500., l1_starts=6,
        l1_quiet=3., a_timeout=5., long_mm=250., pulse_count=6,
        pulse_quiet=2., final_mm=100., final_starts=3, e_speed=1.6,
        purge_speed=20., travel_speed=20., switch_speed=4., feeder_ms=1000,
        feeder_k=1.75, monitor=0, fallback=1, unload_l1=30., unload_l2=70.,
        unload_speed1=1., unload_speed2=1., unload_quiet=4.,
        unload_a_timeout=6., home_xy_max=160., home_z_max=250.,
        home_speed=5., z_speed=2., center_x=67.5, center_y=150.,
        approach_x=135. if tool == 0 else 10.5,
        switch_x=150. if tool == 0 else -4.5, switch_y=152.,
        service_x=135. if tool == 0 else 0., service_y=0.,
        min_x=-5., max_x=155., min_y=0., max_y=155.,
        purge_span=36., purge_e_steps=2073, purge_mm=20., clean_after=1,
        wipe_y=152., wipe_span=100., wipe_exit=23., manual_mm=20.,
        manual_dir='LOAD', feeder=0, target=230., max_power=40., duration=900.,
        procedure_timeout=1800.)


_LIMITS = dict(l1_portion=(.1, 100.), l1_budget=(.1, 1000.),
    l1_starts=(1, 100), l1_quiet=(.021, 30.), a_timeout=(.05, 30.),
    long_mm=(.1, 500.), pulse_count=(1, 1000), pulse_quiet=(.021, 30.),
    final_mm=(.1, 250.), final_starts=(1, 100), e_speed=(.1, 10.),
    purge_speed=(.1, 22.), travel_speed=(.1, 22.), switch_speed=(.1, 22.),
    feeder_ms=(1, 1500), feeder_k=(.01, 3.), monitor=(0, 1), fallback=(0, 1),
    unload_l1=(0., 250.), unload_l2=(0., 250.), unload_speed1=(.1, 10.),
    unload_speed2=(.1, 10.), unload_quiet=(.021, 30.),
    unload_a_timeout=(.05, 30.), home_xy_max=(1., 250.),
    home_z_max=(1., 300.), home_speed=(.1, 20.), z_speed=(.1, 5.),
    center_x=(-50., 250.), center_y=(-50., 250.),
    approach_x=(-50., 250.), switch_x=(-50., 250.), switch_y=(-50., 250.),
    service_x=(-50., 250.), service_y=(-50., 250.),
    min_x=(-50., 250.), max_x=(-50., 250.), min_y=(-50., 250.), max_y=(-50., 250.),
    purge_span=(1., 100.), purge_e_steps=(2, 96000), purge_mm=(.1, 250.),
    clean_after=(0, 1), wipe_y=(-50., 250.), wipe_span=(1., 200.),
    wipe_exit=(0., 100.), manual_mm=(.1, 250.), feeder=(0, 1),
    target=(180., 240.), max_power=(1., 50.), duration=(10., 900.),
    procedure_timeout=(60., 3600.))
_CHOICES = {'manual_dir': ('LOAD', 'UNLOAD')}
_INTEGERS = frozenset(('l1_starts', 'pulse_count', 'final_starts',
                      'feeder_ms', 'monitor', 'fallback', 'purge_e_steps',
                      'clean_after', 'feeder'))


def feeder_budget(p):
    """Exact r0323 float32 estimate of E steps per auxiliary run."""
    def f32(value):return struct.unpack('<f', struct.pack('<f', value))[0]
    return int(f32(f32(p['feeder_ms'] * f32(11.52)) * f32(p['feeder_k'])))


def _validated(values):
    p = dict(values)
    p['tool'] = _tool(p['tool'])
    for key, (low, high) in _LIMITS.items():
        raw = p[key]
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError):
            raise FactoryError(key.upper() + ' must be finite')
        if isinstance(raw, bool) or not math.isfinite(number) or not low <= number <= high:
            raise FactoryError('%s must be %g..%g' % (key.upper(), low, high))
        if key in _INTEGERS:
            if not number.is_integer():
                raise FactoryError(key.upper() + ' must be an integer')
            number = int(number)
        p[key] = number
    for key, choices in _CHOICES.items():
        value = str(p[key]).upper()
        if value not in choices:
            raise FactoryError(key.upper() + ' must be ' + ' or '.join(choices))
        p[key] = value
    if p['l1_budget'] < p['l1_portion']:
        raise FactoryError('L1_BUDGET must cover at least one L1_PORTION')
    if p['unload_l1'] + p['unload_l2'] <= 0:
        raise FactoryError('UNLOAD_L1 plus UNLOAD_L2 must be positive')
    if feeder_budget(p) <= 0:
        raise FactoryError('FEEDER_MS and FEEDER_K must produce a positive E budget')
    if p['min_x'] >= p['max_x'] or p['min_y'] >= p['max_y']:
        raise FactoryError('MIN_X/Y must be below MAX_X/Y')
    # Validate every possible point, including fallback/clean paths, before the
    # generator emits homing, heat or any actuator request. A/B DDA is affine,
    # so the line connecting two points in this box remains inside the box.
    cx, cy = p['center_x'], p['center_y']
    points = [(0., 0.), (cx, cy),
              (p['approach_x'], p['switch_y']),
              (p['switch_x'], p['switch_y']),
              (p['service_x'], p['service_y']),
              (cx - p['purge_span']/2., cy), (cx + p['purge_span']/2., cy)]
    points += [(cx + dx, p['wipe_y']) for dx in
               (-p['wipe_span']/2., p['wipe_span']/2., -p['wipe_exit'], p['wipe_exit'])]
    if any(not p['min_x'] <= x <= p['max_x']
           or not p['min_y'] <= y <= p['max_y'] for x, y in points):
        raise FactoryError('Factory service geometry exceeds MIN/MAX_X/Y')
    # The service MCU accepts at most 600000ms per trajectory. Reserve five
    # seconds for ramp/ack margins, including paths reached only after a fault.
    # Evaluate actual possible legs rather than rejecting the whole box's
    # diagonal, which need not occur in this program.
    def travel(x0, y0, x1, y1):
        return math.hypot(x1 - x0, y1 - y0) / p['travel_speed']
    seconds = dict(
        HOME_Z=p['home_z_max'] / p['z_speed'],
        HOME_XY=p['home_xy_max'] / p['home_speed'],
        CENTER=travel(0., 0., cx, cy),
        APPROACH=travel(cx, cy, p['approach_x'], p['switch_y']),
        SWITCH=abs(p['switch_x'] - p['approach_x']) / p['switch_speed'],
        SWITCH_RETURN=travel(p['switch_x'], p['switch_y'], cx, cy),
        SERVICE=travel(cx, cy, p['service_x'], p['service_y']),
        PURGE=p['purge_span'] / p['purge_speed'],
        PURGE_RETURN=p['purge_span']/2. / p['travel_speed'],
        CLEAN_Y=abs(p['wipe_y'] - cy) / p['travel_speed'],
        CLEAN_X=max(p['wipe_span'], p['wipe_span']/2.+p['purge_span']/2.,
                    p['wipe_span']/2.+p['wipe_exit']) / p['travel_speed'],
        LONG_MM=p['long_mm'] / p['e_speed'],
        MANUAL_MM=p['manual_mm'] / p['e_speed'],
        UNLOAD_L1=p['unload_l1'] / p['unload_speed1'],
        UNLOAD_L2=p['unload_l2'] / p['unload_speed2'])
    for label, duration in seconds.items():
        if duration > 595.:
            raise FactoryError(label + ' trajectory exceeds 595 seconds; increase speed or shorten travel')
    # Coordinated purge ties E to XY. A narrow span with excessive E must be
    # refused before even cold homing, not only when that later leg starts.
    for span, steps in ((p['purge_span'], p['purge_e_steps']),
                        (p['purge_span']/2., p['purge_e_steps']//2)):
        master = max(round(span * XY_STEPS_PER_MM), steps)
        if math.ceil(master * p['purge_speed'] / span) > 20000:
            raise FactoryError('PURGE trajectory exceeds 20000 master steps/s')
    return p


def profiles(common='{}', t0='{}', t1='{}'):
    """Persistent config layers; reject typos instead of silently ignoring them."""
    layers = []
    for name, raw in (('procedure_defaults', common), ('procedure_t0', t0), ('procedure_t1', t1)):
        try:
            values = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError) as exc:
            raise FactoryError(name + ' must be a JSON object') from exc
        if not isinstance(values, dict):
            raise FactoryError(name + ' must be a JSON object')
        layer = {}
        for key, value in values.items():
            normalized = str(key).lower()
            if normalized not in _LIMITS and normalized not in _CHOICES:
                raise FactoryError(name + ': unknown parameter ' + str(key))
            if normalized in layer:
                raise FactoryError(name + ': duplicate parameter ' + normalized)
            layer[normalized] = value
        layers.append(layer)
    for tool in (0, 1):
        _validated(dict(defaults(tool), **dict(layers[0], **layers[tool+1])))
    return tuple(layers)


def parameters(gcmd, configured=None):
    p = defaults(gcmd.get('TOOL', 'T0'))
    if configured is not None:
        p.update(configured[0]);p.update(configured[p['tool']+1])
    for key in (*_LIMITS, *_CHOICES):
        p[key] = gcmd.get(key.upper(), p[key])
    return _validated(p)


def delta32(current, before):
    if (type(current) is not int or type(before) is not int
            or not 0 <= current <= UINT32 or not 0 <= before <= UINT32):
        raise FactoryError('Invalid transport counter')
    delta = (current - before) & UINT32
    if delta >= 0x80000000:
        raise FactoryError('Backward or ambiguous transport counter')
    return delta


def _snapshot(result):
    if not isinstance(result, dict):
        raise FactoryError('Missing fresh transport snapshot')
    for key in ('a', 'b'):
        if type(result.get(key)) is not int or result[key] not in (0, 1):
            raise FactoryError('Invalid transport input ' + key)
    for key in ('pulses', 'starts'):
        delta32(result.get(key), result.get(key))
    age = result.get('pulse_age_ms')
    if (isinstance(age, bool) or not isinstance(age, (int, float))
            or not math.isfinite(age) or age < 0):
        raise FactoryError('Invalid accepted-edge age')
    if type(result.get('fault')) is not int or not 0 <= result['fault'] <= 255:
        raise FactoryError('Invalid transport fault')
    if result['fault']:
        raise FactoryError('MCU transport fault', fault=result['fault'])
    return result


class FactoryProgram:
    """Yield bounded actions; the host sends their fresh completion snapshots."""
    def __init__(self, p):
        self.p = _validated(p)
        self.xy = (0., 0.)
        self.history = []

    def _phase(self, name):
        self.history.append(name)
        yield dict(kind='phase', name=name)

    def _mode(self, mode, monitor=0):
        result = yield dict(kind='mode', mode=mode, monitor=monitor)
        return _snapshot(result)

    def _move(self, x, y, e=0., speed=None):
        result = yield dict(kind='move', x=x, y=y, e=e,
                            speed=self.p['travel_speed'] if speed is None else speed)
        snap = _snapshot(result)
        if result.get('ok') is False:
            raise FactoryError('Factory motion did not complete')
        self.xy = (x, y)
        return snap

    def _center(self):
        return (yield from self.position_bin())

    def _module(self, name, body):
        yield dict(kind='module', name=name, state='start')
        result = yield from body
        yield dict(kind='module', name=name, state='done')
        return result

    def position_bin(self):
        return (yield from self._module('position_bin',
            self._move(self.p['center_x'], self.p['center_y'])))

    def purge(self, mm, clean=False):
        return (yield from self._module('purge', self._purge_steps(mm, clean)))

    def _purge(self, mm, clean=False):
        # Compatibility for callers of the original R7 internal helper.
        return (yield from self.purge(mm, clean))

    def _purge_steps(self, mm, clean):
        p = self.p
        entry = self.xy
        # Exact r0323 integer arithmetic: 36*640=23040 raw X units;
        # trunc(float32(23040*.09))=2073 raw E units, first half=1036.
        dx = -p['purge_span'] if p['tool'] == 0 else p['purge_span']
        issued = 0
        target = round(mm * E_STEPS_PER_MM)
        first = True
        snap = None
        while issued < target:
            step_x = dx / 2. if first else dx
            step_e = p['purge_e_steps']//2 if first else p['purge_e_steps']
            snap = yield from self._move(self.xy[0] + step_x, self.xy[1],
                step_e / E_STEPS_PER_MM, p['purge_speed'])
            issued += step_e
            dx = -dx
            first = False
        if clean:
            snap = yield from self.clean(dx)
        else:
            snap = yield from self._move(*entry)
        return issued, snap

    def clean(self, next_direction=None):
        return (yield from self._module('clean', self._clean_steps(next_direction)))

    def _clean_steps(self, next_direction):
        p = self.p
        if next_direction is None:
            next_direction = -1 if p['tool'] == 0 else 1
        snap = yield from self._move(self.xy[0], p['wipe_y'])
        # Preserve the OEM parity of the remaining purge oscillation; a
        # standalone clean chooses its direction from the selected tool.
        half = p['wipe_span']/2.
        offsets = (-half, half, p['wipe_exit']) if next_direction < 0 else (half, -half, -p['wipe_exit'])
        for offset in offsets:
            snap = yield from self._move(p['center_x'] + offset, p['wipe_y'])
        return snap

    def unload(self):
        return (yield from self._module('unload', self._unload_steps()))

    def _unload_steps(self):
        p = self.p
        yield from self._phase('U0')
        yield from self._mode(3)
        _snapshot((yield dict(kind='wait_unload', timeout_s=10.)))
        yield from self._mode(4)
        quiet = False
        for phase, length, speed in (
                ('U1', p['unload_l1'], p['unload_speed1']),
                ('U2', p['unload_l2'], p['unload_speed2'])):
            if quiet:
                break
            yield from self._phase(phase)
            if length:
                result = yield dict(kind='retract_until_quiet', mm=length,
                    speed=speed, quiet_s=p['unload_quiet'], reset_quiet=phase == 'U1')
                _snapshot(result)
                quiet = bool(result.get('ok'))
        yield from self._phase('U3')
        yield from self._mode(3)
        result = yield dict(kind='retract_until_a',
            timeout_s=p['unload_a_timeout'], speed=p['unload_speed2'])
        snap = _snapshot(result)
        yield from self._mode(0)
        return dict(sensor_empty_proven=snap['a'] == 0)

    def _fallback(self):
        result = yield from self.unload()
        raise FactoryError('l4_starts_missing_fallback_completed',
                           **result)

    def home(self):
        return (yield from self._module('home', self._home_steps()))

    def _home_steps(self):
        p = self.p
        yield from self._phase('HOME')
        for axis, maximum, speed in (('Z', p['home_z_max'], p['z_speed']),
                ('X', p['home_xy_max'], p['home_speed']),
                ('Y', p['home_xy_max'], p['home_speed'])):
            result = yield dict(kind='home', axis=axis, max_mm=maximum, speed=speed)
            if isinstance(result, dict) and result.get('ok') is False:
                raise FactoryError('homing_' + axis.lower() + '_failed')
        self.xy = (0., 0.)

    def heat(self):
        yield from self._module('heat', self._heat_steps())

    def _heat_steps(self):
        yield from self._phase('HEAT')
        yield dict(kind='heat')

    def select_tool(self):
        return (yield from self._module('select_tool', self._select_steps()))

    def _select_steps(self):
        p = self.p
        yield from self._mode(0)
        yield from self._phase('SWITCH')
        yield from self._center()
        yield from self._move(p['approach_x'], p['switch_y'])
        yield from self._move(p['switch_x'], p['switch_y'], speed=p['switch_speed'])
        return (yield from self._center())

    def manual_feed(self):
        yield from self._module('manual_feed', self._manual_steps())

    def _manual_steps(self):
        p = self.p
        forward = p['manual_dir'] == 'LOAD'
        yield from self._mode((2 if forward else 3) if p['feeder'] else 0)
        yield from self._move(*self.xy, e=p['manual_mm']*(1 if forward else -1),
                              speed=p['e_speed'])
        yield from self._mode(0)

    def _load_l1(self):
        p = self.p
        yield from self._phase('L1')
        baseline = (yield from self._mode(1, monitor=p['monitor']))['starts']
        total = 0
        while True:
            issued, snap = yield from self._purge(p['l1_portion'])
            total += issued
            starts = delta32(snap['starts'], baseline)
            if not snap['a'] or snap['pulse_age_ms'] > p['l1_quiet'] * 1000.:
                break
            if starts >= p['l1_starts']:
                yield from self._mode(0)
                return True
            if total >= round(p['l1_budget'] * E_STEPS_PER_MM):
                raise FactoryError('l1_budget_exhausted')
        return False

    def _load_l2(self):
        p = self.p
        yield from self._phase('L2')
        yield from self._mode(2)
        result = yield dict(kind='wait_a', timeout_s=p['a_timeout'])
        _snapshot(result)
        yield from self._mode(0)
        if not result.get('ok') or not result['a']:
            raise FactoryError('l2_a_timeout')
        yield from self._move(p['service_x'], p['service_y'])

    def _load_l3(self):
        p = self.p
        yield from self._phase('L3')
        baseline = (yield from self._mode(2))['pulses']
        result = yield dict(kind='feed_until_edges', mm=p['long_mm'],
            speed=p['e_speed'], count=p['pulse_count'], quiet_s=p['pulse_quiet'])
        snap = _snapshot(result)
        if result.get('ok') and (delta32(snap['pulses'], baseline) < p['pulse_count']
                or snap['pulse_age_ms'] > p['pulse_quiet'] * 1000.):
            raise FactoryError('l3_unverified_transport_result')
        yield from self._mode(0)
        yield from self._center()
        if not result.get('ok'):
            raise FactoryError('l3_no_transport')

    def _load_l4(self):
        p = self.p
        yield from self._phase('L4')
        baseline = (yield from self._mode(1, monitor=p['monitor']))['starts']
        issued, snap = yield from self._purge(p['final_mm'], clean=True)
        if delta32(snap['starts'], baseline) < p['final_starts']:
            if p['fallback']:
                yield from self._fallback()
            raise FactoryError('l4_starts_missing')
        yield from self._mode(0)

    def load(self, selection):
        # The last selector move returns a fresh snapshot from this MCU owner.
        # A low skips the OEM's initial L1 probe with an empty inlet.
        if _snapshot(selection)['a']:
            short = yield from self._module('load_l1', self._load_l1())
            if short:
                return 'L1'
        yield from self._module('load_l2', self._load_l2())
        yield from self._module('load_l3', self._load_l3())
        yield from self._module('load_l4', self._load_l4())
        return 'L4'

    def run(self, operation='AUTO'):
        """Compose shared modules once under one lease and homing reference."""
        operation = str(operation).upper()
        if operation not in PROCEDURES:
            raise FactoryError('Unknown procedure ' + operation)
        p = self.p
        yield from self.home()
        terminal = operation
        cleaned = False
        if operation not in ('HOME', 'SELECT'):
            yield from self.heat()
        if operation != 'HOME':
            selection = yield from self.select_tool()
        if operation == 'AUTO':
            terminal = yield from self.load(selection)
            cleaned = terminal == 'L4'
        elif operation in ('PURGE', 'CLEAN', 'MANUAL', 'UNLOAD'):
            yield from self.position_bin()
            yield from self._phase(operation)
            if operation == 'PURGE':
                yield from self._mode(1 if p['feeder'] else 0, monitor=p['monitor'])
                yield from self.purge(p['purge_mm'], clean=bool(p['clean_after']))
                cleaned = bool(p['clean_after'])
            elif operation == 'CLEAN':
                yield from self.clean()
                cleaned = True
            elif operation == 'MANUAL':
                yield from self.manual_feed()
            else:
                result = yield from self.unload()
                if not result['sensor_empty_proven']:
                    raise FactoryError('unload_sensor_still_present', **result)
            yield from self._mode(0)
        yield from self._phase('DONE')
        yield dict(kind='done', success=True, terminal=terminal, cleaning_done=cleaned)
