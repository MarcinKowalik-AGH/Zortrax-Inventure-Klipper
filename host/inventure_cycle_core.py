# SPDX-License-Identifier: GPL-3.0-or-later
"""P17 pure print-cycle FSM. Never equate G-code position to physical position."""
from dataclasses import dataclass, field
from enum import Enum

class CycleError(ValueError):pass
class Phase(str,Enum):
    IDLE='idle';RUNNING='running';PAUSED='paused';RESUMING='resuming';CANCELLING='cancelling';
    COMPLETED='completed';FAILED='failed';FAULT='fault'
class Reason(str,Enum):
    OPERATOR='operator';SOURCE='source';MATERIAL='material';DOOR='door';POWER='power';
    PCB_COOLING='pcb_cooling';FILAMENT='filament';JAM='jam';FAULT='fault';UNKNOWN='unknown'
@dataclass
class Cycle:
    phase: str=Phase.IDLE
    reason: str|None=None
    epoch: int|None=None
    snapshot: dict|None=None
    result: str|None=None
    history: list=field(default_factory=list)
    
    def sync(self,kind,mode,epoch,fatal=None,operation_result=None):
        if fatal:
            self.phase=Phase.FAULT;self.reason=Reason.FAULT;self.result=str(fatal)
            return self.phase
        if kind!='print':
            if self.phase in (Phase.RUNNING,Phase.RESUMING,Phase.PAUSED,Phase.CANCELLING):
                if operation_result=='completed' and self.phase==Phase.RUNNING:
                    self.phase=Phase.COMPLETED;self.result='completed'
                elif operation_result=='cancelled' or self.phase==Phase.CANCELLING:
                    self.phase=Phase.IDLE;self.result='cancelled'
                else:
                    self.phase=Phase.FAILED;self.result='print ownership ended without safe completion'
                self.snapshot=None;self.epoch=None
            return self.phase
        if self.epoch is not None and epoch!=self.epoch:
            self.phase=Phase.FAULT;self.result='print lease changed unexpectedly';return self.phase
        if self.epoch is None:self.epoch=epoch
        if mode=='paused' and self.phase==Phase.RUNNING:self.phase=Phase.PAUSED;self.reason=Reason.SOURCE
        if mode=='running' and self.phase==Phase.PAUSED:
            self.phase=Phase.FAULT;self.result='resume bypassed current P17 authorization'
        if mode=='running' and self.phase==Phase.IDLE:self.phase=Phase.RUNNING
        return self.phase
    def pause(self,epoch,reason,snapshot):
        if self.phase not in (Phase.RUNNING,):raise CycleError('PAUSE_REQUIRES_RUNNING_PRINT')
        if self.epoch is not None and epoch!=self.epoch:raise CycleError('STALE_PRINT_LEASE')
        if reason not in tuple(x.value for x in Reason):raise CycleError('INVALID_REASON')
        self.phase=Phase.PAUSED;self.reason=reason;self.epoch=epoch;self.snapshot=dict(snapshot)
        self.history.append(('pause',str(reason),epoch))
    def resume(self,epoch,job_id,checks):
        if self.phase!=Phase.PAUSED or self.snapshot is None:raise CycleError('RESUME_REQUIRES_PAUSED_SNAPSHOT')
        if epoch!=self.epoch or job_id!=self.snapshot.get('job_id'):raise CycleError('JOB_CHANGED')
        if not checks or any(not passed for passed in checks.values()):
            raise CycleError('RESUME_INTERLOCK:'+','.join(k for k,v in checks.items() if not v))
        self.phase=Phase.RESUMING;self.history.append(('resume_requested',str(self.reason),epoch))
    def resumed(self):
        if self.phase!=Phase.RESUMING:raise CycleError('INVALID_RESUME_ACK')
        self.phase=Phase.RUNNING;self.reason=None;self.snapshot=None
    def resume_failed(self):
        if self.phase==Phase.RESUMING:self.phase=Phase.PAUSED
    def cancel(self,epoch):
        if self.phase not in (Phase.RUNNING,Phase.PAUSED,Phase.RESUMING):raise CycleError('NOTHING_TO_CANCEL')
        if self.epoch!=epoch:raise CycleError('STALE_PRINT_LEASE')
        self.phase=Phase.CANCELLING;self.history.append(('cancel',None,epoch))
    def cancelled(self):
        if self.phase!=Phase.CANCELLING:raise CycleError('INVALID_CANCEL_ACK')
        self.phase=Phase.IDLE;self.reason=None;self.snapshot=None;self.epoch=None;self.result='cancelled'
    def to_status(self):return dict(phase=self.phase,reason=self.reason,epoch=self.epoch,
                                   resume_snapshot_present=self.snapshot is not None,result=self.result,
                                   history=list(self.history)[-10:])
