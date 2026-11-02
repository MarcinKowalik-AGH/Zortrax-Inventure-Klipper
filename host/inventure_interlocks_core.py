# SPDX-License-Identifier: GPL-3.0-or-later
"""P16 pure safety-input evaluator: observations != actuator feedback."""
from dataclasses import dataclass

EXPECTED = {'front_door':1, 'lid':1, 'tray':0, 'bin':0}

@dataclass(frozen=True)
class Evaluation:
    valid: bool
    permitted: bool
    reasons: tuple
    stable_seconds: float
    confirmed_lock_command: bool

def evaluate(raw, age, connected, armed, fault, status_age, stable_seconds,
             lock_mask, *, max_age=.5, min_stable=.5, require_lock=True):
    reasons=[]
    if not connected or not armed or fault or not isinstance(status_age,(float,int)) or not 0<=status_age<=max_age:
        reasons.append('MCU_NOT_READY')
    if not isinstance(age,(float,int)) or not 0<=age<=max_age: reasons.append('INPUTS_STALE')
    if not isinstance(raw,dict): raw={}
    for name,value in EXPECTED.items():
        if type(raw.get(name)) is not int or raw[name] not in (0,1):
            reasons.append('UNKNOWN_'+name.upper())
        elif raw[name]!=value:
            reasons.append('OPEN_'+name.upper() if value else 'MISSING_'+name.upper())
    locked = type(lock_mask) is int and (lock_mask & 3)==3
    if require_lock and not locked: reasons.append('LOCK_COMMAND_NOT_CONFIRMED')
    if not isinstance(stable_seconds,(float,int)) or stable_seconds<min_stable:
        reasons.append('NOT_STABLE')
    return Evaluation(not any(x.startswith(('UNKNOWN_','INPUTS_STALE','MCU_NOT_READY')) for x in reasons),
                      not reasons,tuple(reasons),max(0.,float(stable_seconds)),locked)
