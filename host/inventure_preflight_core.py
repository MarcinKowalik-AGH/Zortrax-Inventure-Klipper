# SPDX-License-Identifier: GPL-3.0-or-later
"""P15 preflight core. Pure checks; no MCU, movement, heat, or RFID writes.

Native G-code deliberately uses a bounded, explicitly supported subset. A
manifest is job metadata, not an authenticity assertion or permission to run.
Unknown/missing evidence is a refusal, never a guessed successful measurement.
"""
from dataclasses import dataclass, asdict
import hashlib
import json
import math
from pathlib import Path
import re
import stat
import time
import uuid
from .inventure_materials_core import Selection, compatibility, selected_material
from .inventure_toolchange_core import PRELOAD, purge_segments

SCHEMA = 1
VERSION = 'P15-RC1'
MAX_BYTES = 16 * 1024 * 1024
MAX_LINE = 4096
LIMITS = {'x': (0., 135.), 'y': (0., 135.), 'z': (0., 130.)}
NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
PARAM = re.compile(r'([A-Za-z])\s*(' + NUMBER + r')')
COMMAND = re.compile(r'^([GMT])(\d+)(.*)$', re.I)

class PreflightError(ValueError):
    def __init__(self, code, detail):
        self.code, self.detail = code, str(detail)
        super().__init__(code + ': ' + self.detail)

def finite(value, label, lower=0., upper=1.e9):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not lower <= value <= upper):
        raise PreflightError('INVALID_NUMBER', label + ' must be finite and in range')
    return float(value)

def integer(value, label, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise PreflightError('INVALID_INTEGER', label + ' must be an integer in range')
    return value

def _object(pairs):
    out = {}
    for key, val in pairs:
        if key in out:
            raise PreflightError('DUPLICATE_KEY', key)
        out[key] = val
    return out

def parse_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              PreflightError('NONFINITE_JSON', value)))
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError('INVALID_JSON', exc)

@dataclass(frozen=True)
class ToolNeed:
    tool: int
    material_id: int
    required_mm: float
    max_temperature: float

@dataclass(frozen=True)
class Job:
    name: str
    sha256: str
    size: int
    tools: tuple
    initial_tool: int
    bounds: dict

    @classmethod
    def parse(cls, data):
        if type(data) is not dict:
            raise PreflightError('MANIFEST', 'object required')
        required = {'schema', 'printer', 'format', 'file', 'sha256', 'size',
                    'tools', 'initial_tool', 'bounds'}
        if set(data) != required:
            raise PreflightError('MANIFEST_FIELDS', 'unexpected or missing fields')
        if data['schema'] != SCHEMA or type(data['schema']) is not int:
            raise PreflightError('SCHEMA', 'schema 1 required')
        if data['printer'] != 'Zortrax Inventure V9':
            raise PreflightError('WRONG_PRINTER', 'Zortrax Inventure V9 required')
        if data['format'] != 'native_gcode_p15_subset':
            raise PreflightError('UNSUPPORTED_FORMAT', 'classic Z-code execution belongs to the separate parser stage')
        name = relative_name(data['file'])
        if Path(name).suffix.lower() not in ('.gcode', '.gco', '.g'):
            raise PreflightError('UNSUPPORTED_FORMAT', 'native text G-code required')
        sha = data['sha256']
        if type(sha) is not str or not re.fullmatch('[0-9a-f]{64}', sha):
            raise PreflightError('HASH', 'lowercase SHA-256 required')
        size = integer(data['size'], 'file size', 1, MAX_BYTES)
        raw_tools = data['tools']
        if type(raw_tools) is not list or not 1 <= len(raw_tools) <= 2:
            raise PreflightError('TOOLS', 'one or two tools required')
        tools = []
        for t in raw_tools:
            if type(t) is not dict or set(t) != {'tool', 'material_id', 'required_mm', 'max_temperature'}:
                raise PreflightError('TOOLS', 'invalid tool record')
            tool = integer(t['tool'], 'tool', 0, 1)
            mid = integer(t['material_id'], 'material ID', 0, 255)
            try:
                selected_material(mid, tool, allow_hidden=True)
            except ValueError as exc:
                raise PreflightError('MATERIAL_CLASS', exc)
            tools.append(ToolNeed(tool, mid,
                        finite(t['required_mm'], 'required filament mm', 0., 1.e7),
                        finite(t['max_temperature'], 'maximum nozzle temperature', 0., 300.)))
        if len({t.tool for t in tools}) != len(tools):
            raise PreflightError('TOOLS', 'duplicate tool')
        initial = integer(data['initial_tool'], 'initial tool', 0, 1)
        if initial not in {t.tool for t in tools}:
            raise PreflightError('TOOLS', 'initial tool is not declared')
        b = data['bounds']
        if type(b) is not dict or set(b) != set(LIMITS):
            raise PreflightError('BOUNDS', 'x/y/z intervals required')
        bounds = {}
        for axis, limit in LIMITS.items():
            interval = b[axis]
            if type(interval) is not list or len(interval) != 2:
                raise PreflightError('BOUNDS', axis)
            lo, hi = [finite(v, axis, *limit) for v in interval]
            if lo > hi:
                raise PreflightError('BOUNDS', 'reversed interval')
            bounds[axis] = (lo, hi)
        return cls(name, sha, size, tuple(sorted(tools, key=lambda t: t.tool)), initial, bounds)

    def as_dict(self):
        return asdict(self)

def relative_name(name):
    if (type(name) is not str or not name or len(name) > 240
            or any(ord(c) < 32 for c in name) or '\\' in name):
        raise PreflightError('PATH', 'invalid relative path')
    p = Path(name)
    if p.is_absolute() or any(v in ('..', '.') for v in name.split('/')) or '' in name.split('/'):
        raise PreflightError('PATH', 'absolute paths and traversal are forbidden')
    return p.as_posix()

def read_regular(root, name, maximum=MAX_BYTES):
    root = Path(root).expanduser().resolve(strict=True)
    relative_name(name)
    path = root / name
    current = root
    for part in Path(name).parts:
        current = current / part
        if current.is_symlink():
            raise PreflightError('PATH', 'symlinks are not accepted')
    path = path.resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError:
        raise PreflightError('PATH', 'outside job root')
    # O_NOFOLLOW covers the final component. Identity is checked after opening;
    # execution uses these copied bytes, not a later pathname re-open.
    import os
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum:
            raise PreflightError('FILE_SIZE', 'regular bounded non-empty file required')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise PreflightError('FILE_CHANGED', 'file changed while reading')
    if len(raw) != before.st_size or len(raw) > maximum:
        raise PreflightError('FILE_SIZE', 'short or oversized read')
    return raw

def _parameters(tail, lineno):
    result = {}; end = 0
    for match in PARAM.finditer(tail):
        if tail[end:match.start()].strip():
            raise PreflightError('GCODE_ARGUMENT', 'line %d' % lineno)
        key = match[1].upper()
        if key in result:
            raise PreflightError('GCODE_ARGUMENT', 'duplicate %s at line %d' % (key, lineno))
        val = float(match[2])
        finite(val, 'G-code argument', -1.e7, 1.e7)
        result[key] = val; end = match.end()
    if tail[end:].strip():
        raise PreflightError('GCODE_ARGUMENT', 'unparsed data at line %d' % lineno)
    return result

def inspect_gcode(raw, job):
    if not isinstance(raw, bytes) or len(raw) != job.size or hashlib.sha256(raw).hexdigest() != job.sha256:
        raise PreflightError('FILE_IDENTITY', 'size/SHA-256 mismatch')
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise PreflightError('ENCODING', 'UTF-8 text required')
    if '\x00' in text or '\ufeff' in text:
        raise PreflightError('ENCODING', 'NUL or byte-order-mark byte sequence')
    declared = {t.tool: t for t in job.tools}
    abs_xyz = abs_e = None; tool = None; have_feed = False
    pos = dict.fromkeys('XYZ', None); epos = None; flow = 1.
    positive = [0., 0.]; service = [0., 0.]; highest = [0., 0.]; used = set(); count = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if len(line.encode()) > MAX_LINE:
            raise PreflightError('LINE_LENGTH', str(lineno))
        code = line.split(';', 1)[0].strip()
        if not code:
            continue
        if any(ord(c) < 32 for c in code) or '(' in code or ')' in code or '*' in code:
            raise PreflightError('GCODE_SYNTAX', 'line %d' % lineno)
        if code.upper() == 'TURN_OFF_HEATERS':
            count += 1; continue
        m = COMMAND.fullmatch(code)
        if not m:
            raise PreflightError('UNSUPPORTED_COMMAND', 'line %d: %s' % (lineno, code[:80]))
        if len(m[2]) > 1 and m[2][0] == '0':
            raise PreflightError('GCODE_SYNTAX', 'noncanonical command at line %d' % lineno)
        op = m[1].upper() + m[2]; p = _parameters(m[3], lineno)
        allowed = {'G0': set('XYZEF'), 'G1': set('XYZEF'), 'G4': {'P'},
                   'G21': set(), 'G90': set(), 'G91': set(), 'G92': {'E'},
                   'M82': set(), 'M83': set(), 'M104': set('TS'), 'M109': set('TS'),
                   'M106': {'S'}, 'M107': set(), 'M400': set(), 'M221': {'S'},
                   'T0': set(), 'T1': set()}
        if op not in allowed or not set(p) <= allowed[op]:
            raise PreflightError('UNSUPPORTED_COMMAND', 'line %d: %s' % (lineno, op))
        count += 1
        if op in ('T0', 'T1'):
            new_tool = int(op[1])
            if new_tool not in declared:
                raise PreflightError('UNDECLARED_TOOL', str(new_tool))
            if tool is None and new_tool != job.initial_tool:
                raise PreflightError('INITIAL_TOOL', 'first tool differs from manifest')
            if tool is not None and tool != new_tool:
                service[new_tool] += PRELOAD[new_tool] + sum(e for dx,e in purge_segments(new_tool,50))
            tool = new_tool
        elif op in ('G90', 'G91'):
            abs_xyz = op == 'G90'
        elif op in ('M82', 'M83'):
            abs_e = op == 'M82'
        elif op == 'G92':
            if set(p) != {'E'}:
                raise PreflightError('ORIGIN_CHANGE', 'only explicit E-only G92 is supported')
            epos = p['E']
        elif op == 'M221':
            flow = finite(p.get('S'), 'flow percent', 1., 200.) / 100.
        elif op in ('M104', 'M109'):
            t = p.get('T', tool)
            if t is None or int(t) != t or int(t) not in declared:
                raise PreflightError('UNDECLARED_TOOL', 'heater selection')
            temp = finite(p.get('S'), 'nozzle target', 0., declared[int(t)].max_temperature)
            highest[int(t)] = max(highest[int(t)], temp)
        elif op == 'M106':
            finite(p.get('S', 255.), 'fan command', 0., 255.)
        elif op == 'G4':
            if len(p) != 1:
                raise PreflightError('DWELL', 'exactly P (milliseconds) required')
            seconds = p['P'] / 1000.
            finite(seconds, 'dwell seconds', 0., 60.)
        elif op in ('G0', 'G1'):
            if abs_xyz is None:
                raise PreflightError('MODAL_STATE', 'explicit G90/G91 required before motion')
            if 'F' in p:
                finite(p['F'], 'feedrate', 0.001, 12000.)
                have_feed = True
            if not have_feed:
                raise PreflightError('MODAL_STATE', 'explicit feedrate required before motion')
            for a in 'XYZ':
                if a not in p:
                    continue
                if not abs_xyz and pos[a] is None:
                    raise PreflightError('MODAL_STATE', 'relative coordinate has no known start')
                pos[a] = p[a] if abs_xyz else pos[a] + p[a]
                finite(pos[a], a + ' print envelope', *job.bounds[a.lower()])
            if 'E' in p:
                absolute_extrusion = abs_xyz and abs_e
                if tool is None or abs_e is None or (absolute_extrusion and epos is None):
                    raise PreflightError('MODAL_STATE', 'explicit tool and E mode/reference required')
                delta = p['E'] - epos if absolute_extrusion else p['E']
                epos = p['E'] if absolute_extrusion else (epos or 0.) + p['E']
                if delta:
                    used.add(tool)
                    positive[tool] += max(0., delta * flow)
    if not used or used != set(declared):
        raise PreflightError('TOOLS', 'declared tools must match actual nonzero E usage')
    for t in job.tools:
        if positive[t.tool] + service[t.tool] > t.required_mm + 1.e-6:
            raise PreflightError('FILAMENT_ESTIMATE', 'declared mm below conservative positive E plus P08 service for T%d' % t.tool)
    return dict(commands=count, tools=sorted(used), positive_E_mm=positive,
                service_positive_E_mm=service, conservative_total_mm=[p+s for p,s in zip(positive,service)],
                max_temperature=highest, format='native_gcode_p15_subset',
                printer_geometry_proven=False, material_motion_measured=False)

@dataclass(frozen=True)
class Check:
    id: str
    passed: bool
    reason: str
    module: str


def evaluate(job, snap, spool_supply_ack=False):
    """All inputs come from runtime providers. Tests use marked fixtures."""
    checks = []
    def add(key, passed, reason, module):
        checks.append(Check(key, bool(passed), 'OK' if passed else reason, module))
    def fresh(value, limit):
        return (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= limit)
    b = snap.get('board') or {}; io = snap.get('io') or {}; op = snap.get('operations') or {}
    add('MCU', b.get('connected') and b.get('armed') and not b.get('fault') and fresh(b.get('status_age'), .5), 'MCU absent, stale, disarmed or faulted', 'P01')
    add('IO_QUALIFIED', io.get('motion_qualified') is True and io.get('heating_qualified') is True, 'Motion/thermal outputs not qualified', 'P01')
    add('EXCLUSIVE_IDLE', op.get('mode') == 'idle' and op.get('operation') is None and op.get('fatal') is None, 'Busy or fatal; another procedure owns the printer', 'P02')
    add('REFERENCE', op.get('xyz_print_reference_valid') is True, 'Full current XYZ reference required', 'P03')
    frame = op.get('toolframe') or {}; selector = op.get('selector') or {}
    add('TOOLFRAME', frame.get('valid') is True and frame.get('tool') == selector.get('tool') == op.get('logical_extruder_tool') == job.initial_tool,
        'Confirmed selector, logical tool and frame must match initial tool', 'P04')
    t = snap.get('thermal') or {}; ages = t.get('sensor_ages')
    add('THERMAL', t.get('fatal') is None and type(ages) is list and len(ages) == 8 and all(fresh(v, 1.) for v in ages)
        and t.get('pcb_pause') is None and not snap.get('thermal_error'), 'All eight temperatures must be fresh and PCB/thermal gates satisfied', 'P05')
    f = snap.get('fans') or {}
    add('FANS', f.get('process_qualified') is True and f.get('stopped') is False and not f.get('fatal') and not f.get('fault_mask')
        and not f.get('process_fault') and not snap.get('fan_error'), 'Fan qualification/freshness/fault gate failed', 'P06')
    inp = b.get('inputs', {}).get('raw', {})
    add('DOORS', fresh(b.get('inputs_age'), .5) and inp.get('front_door') == 1 and inp.get('lid') == 1
        and fresh(snap.get('doors_closed_for'), 86400.) and snap['doors_closed_for'] >= .5,
        'Closed doors must be continuously observed for at least 0.5 seconds', 'P07/P16')
    add('TRAY_BIN', fresh(b.get('inputs_age'), .5) and inp.get('tray') == 0 and inp.get('bin') == 0,
        'Fresh platform and waste-bin presence required; operator OK is not a sensor', 'P07/P16')
    if snap.get('p16_required') is True:
        locks = snap.get('interlocks') or {}
        add('P16_GLOBAL_INTERLOCKS', locks.get('enabled') is True and locks.get('hardware_qualified') is True
            and locks.get('ready') is True and locks.get('latch') is None,
            'P16 continuous interlock status not qualified/ready', 'P16')
    cal = snap.get('calibration') or {}
    add('CALIBRATION', cal.get('candidate') is None and cal.get('storage_busy') is False
        and cal.get('committed', {}).get('established') is True
        and frame.get('calibration_revision') == cal.get('revision'),
        'Uncommitted/stale calibration or pending storage', 'P09/P10')
    add('TOOLCHANGE', len(job.tools) == 1 or (snap.get('toolchange_ready') is True and snap.get('toolchange_routed') is True), 'Dual jobs need qualified P08 with route_t_commands enabled', 'P08')
    mat = snap.get('materials') or {}; pair = mat.get('selection')
    add('MATERIAL_OWNER', mat.get('enabled') is True and mat.get('ready') is True and mat.get('storage_busy') is False
        and type(pair) is list and len(pair) == 2, 'P11 material selection/identity not ready', 'P11')
    fil = snap.get('filament') or {}; channels = fil.get('channels', [])
    add('TRANSPORT', fil.get('enabled') is True and fil.get('ready') is True and not fil.get('running') and fil.get('stopped') is False,
        'P13 transport disabled, busy, stale or faulted', 'P13')
    add('LOCK_COMMANDS', len(channels) == 2 and all(type(c) is dict and c.get('locks') == 3
        and c.get('report_is_current') is True and fresh(c.get('age'), .5) for c in channels),
        'Fresh MCU confirmation of both lock commands required; not an independent lock-position sensor', 'P13/P16')
    rf = snap.get('rfid') or {}; rfch = rf.get('channels', [])
    ledger = snap.get('consumption')
    add('P14_PRESENT', type(ledger) is dict and ledger.get('enabled') is True and ledger.get('stopped') is False
        and ledger.get('automatic_step_accounting') is True,
        'P14 source/runtime absent or disabled; old test reports do not satisfy this gate', 'P14')
    add('P14_INTEGRATED', snap.get('p14_integration_qualified') is True,
        'P14/P15 integration has not been qualified from the actual source build', 'P14')
    if type(ledger) is dict:
        add('P14_CLEAN', ledger.get('error') is None and ledger.get('storage_busy') is False and ledger.get('jam') is None,
            'Unresolved accounting, storage or jam', 'P14')
    for need in job.tools:
        tool = need.tool; label = 'T%d_' % tool
        s = pair[tool] if type(pair) is list and len(pair) == 2 and type(pair[tool]) is dict else {}
        source = s.get('source')
        add(label+'SOURCE', source in ('cartridge','spool'), 'Select cartridge or spool explicitly', 'P11')
        r = rfch[tool] if len(rfch) == 2 and type(rfch[tool]) is dict else {}
        tag = r.get('tag') or {}
        try:
            match = compatibility(tool, Selection(s.get('material_id'), source), need.material_id,
                                  tag.get('material_id'), tag.get('tag_class'))
            ok = match['match'] is True
        except (ValueError, TypeError):
            ok = False
        add(label+'MATERIAL', ok, 'Job material/class does not match selected source', 'P11/P12')
        channel = channels[tool] if len(channels) == 2 and type(channels[tool]) is dict else {}
        add(label+'TRANSPORT_REPORT', channel.get('report_is_current') is True
            and fresh(channel.get('age'), .5) and channel.get('fault') == 0
            and channel.get('live') == 1, 'Current live fault-free transport report required', 'P13')
        if source == 'cartridge':
            add(label+'RFID', rf.get('enabled') is True and r.get('ready') is True and r.get('uncertain') is False and r.get('busy') is None and not r.get('error')
                and fresh(r.get('identity_age'), 5.), 'Fresh verified P12 identity required', 'P12')
            add(label+'A_PRESENT', channel.get('a') == 1, 'Cassette material presence A is missing', 'P13')
        elif source == 'spool':
            add(label+'LOADED', mat.get('operator_confirmed') is True, 'Manual loading needs current operator confirmation', 'P11/P13')
            add(label+'SUPPLY', spool_supply_ack is True, 'Confirm sufficient external spool material for this job', 'P15')
            add(label+'FEEDER_OFF', channel.get('mode') == 0 and channel.get('running') == 0 and channel.get('assist') == 0,
                'External spool must not run automatic/helper feeder', 'P13')
        lc = (ledger or {}).get('channels', [])
        l = lc[tool] if type(lc) is list and len(lc) == 2 and type(lc[tool]) is dict else None
        add(label+'LEDGER', l is not None and l.get('uncertain') is False and l.get('intent') is None and l.get('source') == source
            and l.get('material_id') == s.get('material_id'), 'Missing, uncertain or mismatched P14 ledger', 'P14')
        if source == 'cartridge':
            add(label+'LEDGER_IDENTITY', l is not None and bool(tag.get('identity_sha256'))
                and l.get('identity') == tag.get('identity_sha256'),
                'P14 ledger must belong to the currently verified cartridge identity', 'P12/P14')
            remaining = (l or {}).get('target_mm'); fraction = (l or {}).get('fraction_steps')
            exact = (type(remaining) in (int,float) and math.isfinite(remaining)
                     and type(fraction) is int and 0 <= fraction < 960)
            available = remaining - fraction / 960. if exact else -1.
            add(label+'QUANTITY', exact and available >= need.required_mm,
                'Effective remaining balance insufficient or unknown', 'P12/P14')
    return checks


def context_fingerprint(snap):
    """Exclude fluctuating sensor values; re-evaluate those at every start."""
    op = snap.get('operations') or {}; b = snap.get('board') or {}
    mat = snap.get('materials') or {}; cal = snap.get('calibration') or {}
    rf = snap.get('rfid') or {}; ledger = snap.get('consumption') or {}
    stable = dict(host_session=snap.get('host_session'), material_generation=snap.get('material_generation'),
                  selection=mat.get('selection'), operator_confirmed=mat.get('operator_confirmed'),
                  material_revision=mat.get('revision'), calibration_revision=cal.get('revision'),
                  committed=cal.get('committed'), toolframe=op.get('toolframe'), selector=op.get('selector'),
                  position_epoch=op.get('position_epoch'), targets=snap.get('heater_targets'),
                  tags=[c.get('tag') for c in rf.get('channels', [])],
                  ledger=[{k:c.get(k) for k in ('identity','material_id','source','target_mm','uncertain','intent')} if c else None
                          for c in ledger.get('channels', [])], qualifications=snap.get('qualifications'))
    return hashlib.sha256(json.dumps(stable,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

class Ticket:
    """One-use proof of a checked immutable job, not persistent readiness."""
    def __init__(self, job, snapshot, now, ttl=30., spool_ack=False):
        self.ttl = finite(ttl, 'token TTL', 1., 300.)
        finite(now, 'time', 0., 1.e12)
        checks = evaluate(job, snapshot, spool_ack)
        bad = [c.id for c in checks if not c.passed]
        if bad:
            raise PreflightError('NOT_READY', ', '.join(bad))
        self.token = uuid.uuid4().hex
        self.job = job; self.context = context_fingerprint(snapshot)
        self.deadline = now + ttl; self.used = False; self.spool_ack = spool_ack

    def consume(self, token, snapshot, now):
        if self.used or not isinstance(token,str) or token != self.token:
            raise PreflightError('TOKEN', 'unknown, stale or consumed token')
        finite(now, 'time', 0., 1.e12)
        if now >= self.deadline:
            self.used = True
            raise PreflightError('EXPIRED', 'repeat preflight')
        # Consume BEFORE handing control to the source/arbiter. No retry of a
        # partially-started job using an old authorization.
        self.used = True
        if context_fingerprint(snapshot) != self.context:
            raise PreflightError('CONTEXT_CHANGED', 'material/calibration/tool/job context changed')
        bad = [c.id for c in evaluate(self.job,snapshot,self.spool_ack) if not c.passed]
        if bad:
            raise PreflightError('NOT_READY', ', '.join(bad))
        return self.job
