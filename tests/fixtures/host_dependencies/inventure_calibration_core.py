# SPDX-License-Identifier: GPL-3.0-or-later
"""Audited Inventure calibration arithmetic and transactional host storage.

No GPIO, G-code or Flash operations. OX/OY/OZ are residual stock corrections,
not complete tool offsets. ID52 remains a separate depth in micrometres.
"""
from dataclasses import dataclass, asdict, replace
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import struct
import tempfile

SCHEMA = 1
REFERENCE_SHA256 = '9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268'
SENTINELS = (-2147483648, 2147483647, 0xDEAD)


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Calibration values must be finite, not NaN/Inf')
    return value


def f32(value):
    try:
        value = struct.unpack('<f', struct.pack('<f', finite(value)))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError('Calibration value is outside float32 range') from exc
    return finite(value)


def xy_correction(old, screen_line):
    if isinstance(screen_line, bool) or int(screen_line) != screen_line or not 1 <= screen_line <= 13:
        raise ValueError('Displayed line must be 1..13; line 7 is neutral')
    return f32(f32(old) - f32((int(screen_line)-7) * f32(0.05)))


def stock_z_correction(old, saved_z_steps, last_t1_steps):
    for v in (saved_z_steps, last_t1_steps):
        if isinstance(v, bool) or int(v) != v or v in SENTINELS or not -2**31 <= v < 2**31:
            raise ValueError('Invalid stock probe result or error sentinel')
    delta = int(saved_z_steps) - int(last_t1_steps)
    if not -2**31 <= delta < 2**31:
        raise ValueError('Stock arithmetic difference overflows int32')
    return f32(f32(old) + f32(f32(delta) / f32(802)))


def validate_samples(values, count, max_spread, *, stock_steps=False):
    values = list(values)
    if len(values) != count:
        raise ValueError('Expected exactly %d complete probe measurements' % count)
    if stock_steps and any(v in SENTINELS for v in values):
        raise ValueError('Probe error: refusing to reuse an earlier good sample')
    values = [finite(v) for v in values]
    if finite(max_spread) <= 0:
        raise ValueError('A positive repeatability tolerance is required')
    if max(values)-min(values) > max_spread:
        raise ValueError('Probe spread exceeds the configured tolerance')
    return values[-1]  # Audited choice: LAST, never silently replace by mean.


@dataclass(frozen=True)
class Calibration:
    ox: float = 0.0
    oy: float = 0.0
    oz: float = 0.0
    depth_um: int = None
    established: bool = False

    def __post_init__(self):
        for key in ('ox', 'oy', 'oz'):
            object.__setattr__(self, key, f32(getattr(self, key)))
        if self.depth_um is not None:
            d = self.depth_um
            if isinstance(d, bool) or not isinstance(d, int) or not 0 < d < 2**31:
                raise ValueError('ID52 depth_um must be a positive int32, not OZ in mm')
        if not isinstance(self.established, bool):
            raise ValueError('established must be a boolean')

    def totals(self):
        return {'x': 10.8 + self.ox, 'y': self.oy, 'z': 1.0 + self.oz}

    def with_xy(self, x_line=None, y_line=None):
        return replace(self, ox=self.ox if x_line is None else xy_correction(self.ox,x_line),
                       oy=self.oy if y_line is None else xy_correction(self.oy,y_line))

    def with_native_z(self, t0_release, t1_release, tolerance):
        a=validate_samples(t0_release,2,tolerance)
        b=validate_samples(t1_release,3,tolerance)
        # Native Klipper positions are mm, not unverified 802 physical steps/mm.
        # Both inputs must be in the already compensated OLD logical tool frame.
        return replace(self,oz=f32(self.oz + f32(b-a)))

    def tool_transition(self, current, target, position_steps=(0,0,0)):
        if current not in (0,1) or target not in (0,1):
            raise ValueError('A confirmed T0/T1 is required')
        sign=target-current
        if len(position_steps)!=3 or any(type(p) is not int or not -2**31<=p<2**31 for p in position_steps):
            raise ValueError('Three signed int32 planned OEM positions required')
        x,y,z=position_steps
        # Direct execution of 0x0801FCA0 confirms that conversion follows
        # addition/subtraction to the ABSOLUTE current position, not to the
        # offset alone. X uses double, Y/Z software float32.
        if sign:
            nx=math.trunc(float(x)+sign*((10.8+self.ox)*640.))
            ny=math.trunc(f32(f32(y)+sign*f32(self.oy*f32(640.))))
            dz=f32(f32(f32(1.)+self.oz)*f32(802.))
            nz=z if dz==0. else math.trunc(f32(f32(z)+sign*dz))
            steps=(nx-x,ny-y,nz-z)
        else:steps=(0,0,0)
        return {'physical_z_delta': steps[2]/802.,
                'logical_xy_rebase': (steps[0]/640.,steps[1]/640.),
                'step_deltas_xyz': steps,
                'planned_position_steps': tuple(position_steps),
                'logical_z_delta': 0.0,
                'move_required': bool(sign)}

    def bottom_z_reference_mm(self, tool):
        if self.depth_um is None or tool not in (0,1):
            raise ValueError('A separate platform reference and confirmed tool are required')
        base=self.depth_um*802//1000
        if tool:base=math.trunc(f32(f32(base)-f32(f32(f32(1.)+self.oz)*f32(802.))))
        return base/802.


def check_bounds(value, limits):
    """Project decisions supplied explicitly after mechanical qualification."""
    if not isinstance(value, Calibration):
        raise ValueError('Invalid calibration object')
    for axis in ('ox','oy','oz'):
        low,high=limits[axis]
        if not finite(low) < finite(high) or not low <= getattr(value,axis) <= high:
            raise ValueError('%s outside configured mechanical bounds' % axis.upper())
    return value


def pattern_segments(axis, tool, mode='stock_reference'):
    """Return geometrical references, not unconditionally executable G-code.

    differential_x adds the missing T1 X staircase as an EXPLICIT deviation.
    """
    axis=axis.upper()
    if axis not in ('X','Y') or tool not in (0,1):
        raise ValueError('Axis X/Y and tool 0/1 required')
    if mode not in ('stock_reference','differential_x'):
        raise ValueError('Explicit stock_reference or differential_x required')
    if axis=='Y':
        seg=[{'kind':'crossbar','start':[10.,95.5],'end':[10.,35.5]}]
    else:
        seg=[{'kind':'crossbar','start':[132.5,43.],'end':[72.5,43.]}]
    for i in range(-6,7):
        if axis=='Y':
            v=f32(65.5+5*i)
            if tool: v=f32(v+f32(i*f32(.05)))
            a,b=[10.,v],[55.,v]
        else:
            v=f32(102.5+5*i)
            if tool and mode=='differential_x': v=f32(v+f32(i*f32(.05)))
            a,b=[v,43.],[v,88.]
        seg.append({'kind':'line','index':i,'display_line':i+7,'start':a,'end':b})
    return seg


class CalibrationStore:
    """Own JSON only. Never reads/writes the two stock Flash NVM banks."""
    def __init__(self, filename):
        self.filename=Path(filename).expanduser()
        self.value=Calibration()
        self.revision=0
        if self.filename.exists():
            envelope=json.loads(self.filename.read_text(encoding='utf-8'))
            record=envelope['record']
            raw=json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            if hashlib.sha256(raw).hexdigest()!=envelope.get('sha256'):
                raise ValueError('Calibration file checksum mismatch')
            if record.get('schema')!=SCHEMA or record.get('board')!='Inventure-V9':
                raise ValueError('Wrong calibration schema/board')
            self.value=Calibration(**record['calibration'])
            self.revision=record['revision']
            if type(self.revision) is not int or self.revision<1:
                raise ValueError('Invalid calibration revision')

    def save(self, candidate, expected_revision, provenance, publish=True):
        self.filename.parent.mkdir(parents=True,exist_ok=True)
        lock_path=self.filename.with_name(self.filename.name+'.lock')
        with lock_path.open('a') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            return self._save_locked(candidate,expected_revision,provenance,publish)

    def _save_locked(self, candidate, expected_revision, provenance, publish=True):
        if expected_revision!=self.revision:
            raise ValueError('Calibration changed since this transaction began')
        # Detect changes by a second process before replacing its data.
        if self.filename.exists():
            disk=CalibrationStore(self.filename)
            if disk.revision!=self.revision or disk.value!=self.value:
                raise ValueError('Calibration file changed externally; reload first')
        elif self.revision:
            raise ValueError('Previously saved calibration file disappeared')
        record={'schema':SCHEMA,'board':'Inventure-V9','revision':self.revision+1,
                'reference_firmware_sha256':REFERENCE_SHA256,
                'calibration':asdict(candidate),'provenance':provenance}
        raw=json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        envelope={'record':record,'sha256':hashlib.sha256(raw).hexdigest()}
        payload=json.dumps(envelope,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        self.filename.parent.mkdir(parents=True,exist_ok=True)
        tmp=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=str(self.filename.parent),
                                             prefix='.inventure-cal-',delete=False) as stream:
                tmp=Path(stream.name)
                stream.write(payload);stream.flush();os.fsync(stream.fileno())
            # Verify temporary content before the atomic replacement.
            verified=CalibrationStore(tmp)
            if verified.value!=candidate:
                raise ValueError('Written calibration did not read back correctly')
            os.replace(str(tmp),str(self.filename));tmp=None
            # On a directory-fsync error report uncertain durability, NOT success.
            if hasattr(os,'O_DIRECTORY'):
                fd=os.open(str(self.filename.parent),os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            if publish:
                self.value=candidate;self.revision=record['revision']
        finally:
            if tmp is not None:
                try:tmp.unlink()
                except FileNotFoundError:pass
        return record['revision']


class CalibrationSession:
    """Candidate state never changes committed calibration implicitly."""
    def __init__(self, value, revision, tolerance):
        self.original=value;self.revision=revision;self.tolerance=finite(tolerance)
        if self.tolerance<=0:raise ValueError('Positive measurement tolerance required')
        self.samples=[[],[]];self.state='t0';self.candidate=None;self.reason=None
    def add_probe(self,tool,release_z):
        expected=0 if self.state=='t0' else 1 if self.state=='t1' else None
        if tool!=expected:raise ValueError('Unexpected tool or completed calibration session')
        value=finite(release_z)
        self.samples[tool].append(value)
        count=2 if tool==0 else 3
        if len(self.samples[tool])==count:
            validate_samples(self.samples[tool],count,self.tolerance)
            if tool==0:self.state='t1'
            else:
                self.candidate=self.original.with_native_z(*self.samples,self.tolerance)
                self.state='pending'
        return self.state
    def abort(self,reason='Operator cancelled'):
        self.candidate=None;self.state='aborted';self.reason=str(reason)
