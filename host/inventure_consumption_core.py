# SPDX-License-Identifier: GPL-3.0-or-later
"""P14 replacement source: durable issued-STEP accounting, not filament-motion metrology.

SAFETY: 1) never consume G-code's planned E 2) do not credit retracts,
3) fail closed on counter gap, reset or lost accounting persistence.
"""
from dataclasses import dataclass, asdict
import json, math, os, tempfile, hashlib
from pathlib import Path

STEPS_PER_MM=960
MAX32=1<<32
SCHEMA=1
class AccountingError(ValueError):pass

def u32diff(now, old):
    if type(now) is not int or type(old) is not int or not 0<=now<MAX32 or not 0<=old<MAX32:
        raise AccountingError('BAD_COUNTER')
    return (now-old) % MAX32

@dataclass
class Channel:
    source: str
    material_id: int
    identity: str
    target_mm: int | None
    durable_anchor_mm: int | None = None
    fraction_steps: int = 0
    forward_steps: int = 0
    reverse_steps: int = 0
    clock: int | None = None
    raw_forward: int | None = None
    raw_reverse: int | None = None
    last_timestamp: float | None = None
    uncertain: bool = False
    intent: dict | None = None

    def __post_init__(self):
        if self.source not in ('cartridge','spool') or type(self.material_id) is not int or not 0<=self.material_id<=255:
            raise AccountingError('INVALID_SOURCE')
        if not isinstance(self.identity,str) or not self.identity:
            raise AccountingError('INVALID_IDENTITY')
        if self.source=='cartridge' and (type(self.target_mm) is not int or not -2**31<=self.target_mm<2**31):
            raise AccountingError('INVALID_TAG_BALANCE')
        if self.source=='spool' and self.target_mm is not None:
            raise AccountingError('SPOOL_HAS_NO_TAG_BALANCE')
        if self.source=='cartridge':
            if self.durable_anchor_mm is None:self.durable_anchor_mm=self.target_mm
            if type(self.durable_anchor_mm) is not int:raise AccountingError('INVALID_ANCHOR')
        elif self.durable_anchor_mm is not None:raise AccountingError('SPOOL_ANCHOR_FORBIDDEN')
        if type(self.fraction_steps) is not int or not 0<=self.fraction_steps<960:
            raise AccountingError('BAD_FRACTION')

    def observe(self, p, received, *, clock_hz, max_gap=.5, max_step_rate=120000):
        """Return newly *issued* forward and reverse STEP edges. Atomic only after WAL write.

        Clock and both counters must advance monotonically within half an unsigned cycle.
        First sample is only a baseline, never an inferred amount of consumption.
        """
        if type(received) not in (int,float) or not math.isfinite(received):raise AccountingError('TIME_INVALID')
        if type(clock_hz) not in (int,float) or not 0<clock_hz<1e9:raise AccountingError('CLOCK_INVALID')
        fields=('clock','forward','reverse')
        if not isinstance(p,dict) or any(type(p.get(k)) is not int or not 0<=p[k]<MAX32 for k in fields):
            raise AccountingError('BAD_REPORT')
        if self.raw_forward is None:
            self.clock,self.raw_forward,self.raw_reverse=p['clock'],p['forward'],p['reverse']
            self.last_timestamp=received
            return 0,0
        dc=u32diff(p['clock'],self.clock)
        if dc==0 and p['forward']==self.raw_forward and p['reverse']==self.raw_reverse:return 0,0
        if not 0<dc<MAX32//2:raise AccountingError('CLOCK_RESTART_OR_REORDER')
        seconds=dc/clock_hz
        if seconds>max_gap or received < self.last_timestamp or received-self.last_timestamp>max_gap:
            raise AccountingError('TRANSPORT_GAP')
        f,r=u32diff(p['forward'],self.raw_forward),u32diff(p['reverse'],self.raw_reverse)
        limit=int(max_step_rate*seconds)+8
        if f>limit or r>limit:raise AccountingError('COUNTER_JUMP')
        self.clock,self.raw_forward,self.raw_reverse=p['clock'],p['forward'],p['reverse']
        self.last_timestamp=received
        self.forward_steps+=f;self.reverse_steps+=r
        # No reverse credit: filament may not have returned to usable supply.
        # This is intentionally conservative for the remaining supply estimate.
        whole,self.fraction_steps=divmod(self.fraction_steps+f,STEPS_PER_MM)
        if self.source=='cartridge':
            nxt=self.target_mm-whole
            if not -2**31<=nxt<2**31:raise AccountingError('BALANCE_OVERFLOW')
            self.target_mm=nxt
        return f,r

    def as_dict(self):return asdict(self)

class Ledger:
    def __init__(self, channels=None, session=None):
        self.channels=channels or [None,None]
        if len(self.channels)!=2:raise AccountingError('TWO_CHANNELS_REQUIRED')
        self.session=session or os.urandom(16).hex()
        self.error=None;self.jam=None
        self.sequence=0

    def bind(self,i,source,material_id,identity,target_mm):
        if i not in (0,1):raise AccountingError('TOOL_INVALID')
        old=self.channels[i]
        if old is not None and (old.intent or old.uncertain or old.fraction_steps) and (old.identity,old.source,old.material_id)!=(identity,source,material_id):
            raise AccountingError('UNRESOLVED_PRIOR_BALANCE')
        self.channels[i]=Channel(source,material_id,identity,target_mm)
        self.sequence+=1

    def account(self,i,report,received,*,clock_hz,**kwargs):
        c=self.channels[i]
        if c is None:raise AccountingError('NOT_BOUND')
        if c.uncertain:raise AccountingError('UNCERTAIN')
        try:
            result=c.observe(report,received,clock_hz=clock_hz,**kwargs)
        except AccountingError:
            c.uncertain=True
            raise
        if result!=(0,0):self.sequence+=1
        return result

    def serializable(self):
        return dict(schema=SCHEMA,session=self.session,sequence=self.sequence,error=self.error,jam=self.jam,
                    channels=[None if c is None else c.as_dict() for c in self.channels])

    @classmethod
    def from_json(cls,raw):
        try:
            v=json.loads(raw)
            if type(v) is not dict or v['schema']!=SCHEMA or type(v['channels']) is not list or len(v['channels'])!=2:raise ValueError()
            inst=cls([None if x is None else Channel(**x) for x in v['channels']],v['session'])
            inst.sequence=v['sequence'];inst.error=v['error'];inst.jam=v['jam']
            # Counters from a previous host session are NEVER presumed continuous.
            for c in inst.channels:
                if c is not None:
                    c.uncertain=True;c.clock=None;c.raw_forward=None;c.raw_reverse=None;c.last_timestamp=None
            return inst
        except Exception as exc:raise AccountingError('CORRUPT_JOURNAL') from exc

class AtomicJournal:
    def __init__(self,path):self.path=Path(path)
    def save(self,ledger):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        raw=json.dumps(ledger.serializable(),sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        digest=hashlib.sha256(raw).hexdigest()
        packet=json.dumps(dict(sha256=digest,data=raw.decode()),sort_keys=True).encode()
        fd,tmp=tempfile.mkstemp(prefix='.p14-',dir=str(self.path.parent))
        try:
            with os.fdopen(fd,'wb') as f:f.write(packet);f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.path)
            dfd=os.open(str(self.path.parent),os.O_RDONLY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    def load(self):
        try:
            packet=json.loads(self.path.read_bytes())
            raw=packet['data'].encode()
            if hashlib.sha256(raw).hexdigest()!=packet['sha256']:raise AccountingError('JOURNAL_HASH_MISMATCH')
            return Ledger.from_json(raw)
        except FileNotFoundError:return Ledger()
        except Exception as exc:raise AccountingError('JOURNAL_UNVERIFIED') from exc

@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    explanation: str
    allow_resume: bool


def classify_recovery(*,jam,paused,material_present,pulses,starts,physical_safe,attempts):
    if not jam:return RecoveryDecision('NONE','No jam detected',False)
    if not paused or not physical_safe:return RecoveryDecision('STOP','Recovery requires paused, physically qualified machine',False)
    if attempts>=2:return RecoveryDecision('STOP','Recovery attempt limit reached',False)
    if not material_present:return RecoveryDecision('RELOAD','Reinsert and verify material before further action',False)
    if pulses>=10 and starts>=3:return RecoveryDecision('REVIEW','Sensor evidence improved; operator verification required',False)
    if pulses<10:return RecoveryDecision('R1','Recovery phase R1 proposed; no movement executed',False)
    return RecoveryDecision('R2','Recovery phase R2 proposed; no movement executed',False)
