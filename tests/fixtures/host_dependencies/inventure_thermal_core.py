# SPDX-License-Identifier: GPL-3.0-or-later
"""r0323 thermal detector semantics; no GPIO, timers, or recovery motion.

Reference: THERM final 2026-10-05, sections 4,6,7,11,15; original ARM
0x08020c90/0x08020cf0/0x08020d68/0x08020db0/0x08018b78.
The host uses monotonic seconds, deliberately avoiding the OEM uint32 wrap bug.
"""
from dataclasses import dataclass, asdict
import math
from .inventure_calibration_core import f32

NAMES = ('T0', 'T1', 'CHAMBER_L', 'CHAMBER_R', 'CHAMBER_MIDDLE',
         'PSU', 'MAINBOARD', 'HEAD_PCB')
OVERTEMP = (310.,310.,150.,150.,150.,100.,100.,190.)
FAULTS = {1:'SENSOR_UNRELIABLE', 2:'NO_HEATING_PROGRESS',
          4:'OVER_TEMPERATURE', 8:'TEMPERATURE_DROP'}
OEM_MESSAGES = ((110,115,120,124),(111,116,121,125),(113,117,122,126),
                (114,118,123,127),(112,None,None,None),(190,None,191,None),
                (192,None,193,None),(194,None,195,None))
PCB_FALLBACK = (32767,32767,32767,32767)
PCB_PROFILES = {7:(80,63,55,50),13:(100,90,80,70),
                17:(100,90,80,70),23:(100,90,80,70)}

def pcb_profile(material_a, material_b):
    """OEM B=0 means support7; greater second threshold wins; B on ties."""
    if material_b == 0: material_b = 7
    a,b = PCB_PROFILES.get(material_a,PCB_FALLBACK),PCB_PROFILES.get(material_b,PCB_FALLBACK)
    return a if a[1] > b[1] else b

def pcb_class(temperature, profile):
    if not math.isfinite(temperature): raise ValueError('Invalid PCB measurement')
    for level,threshold in zip((4,3,2,1),profile):
        if temperature >= threshold: return level
    return 0

def pcb_preflight(temperature):
    if not math.isfinite(temperature): raise ValueError('Invalid PCB measurement')
    return 197 if temperature >= 63. else 196 if temperature >= 55. else None

def irq_cause(cable_failed, thermal_masks, fan_fault_mask):
    """Separate cause domain: cable1, thermal2 (except index4), fans4(mask0D)."""
    if cable_failed: return 1
    if any(mask for i,mask in enumerate(thermal_masks) if i != 4): return 2
    if fan_fault_mask & 0x0d: return 4
    return 0

@dataclass
class Channel:
    temperature: float = 0.
    previous: float = 0.
    slope: float = 0.
    peak: float = 0.
    target: float = 0.
    progress_time: float = 0.
    ready: bool = False
    fault: int = 0
    reported: int = 0
    bad_sensor: int = 0
    bad_overtemp: int = 0
    bad_drop: int = 0
    samples: int = 0

class ThermalModel:
    def __init__(self):
        self.channels = [Channel() for _ in range(8)]

    def set_target(self, index, value, now):
        if index not in range(4) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid heater target')
        c=self.channels[index]
        # Repeating the same setter resets READY/progress, never faults/counters.
        c.target=value; c.ready=False; c.peak=c.temperature; c.progress_time=now

    def sample(self,index,raw_temperature,now):
        if index not in range(8) or not math.isfinite(raw_temperature) or not math.isfinite(now):
            raise ValueError('Non-finite thermal measurement/time')
        c=self.channels[index]; old_fault=c.fault; c.previous=c.temperature
        raw=f32(raw_temperature)
        c.temperature=(f32(f32(raw+f32(4.*c.previous))/5.) if index in (2,3) else raw)
        c.slope=f32(f32(f32(f32(c.temperature-c.previous)/f32(.1))+f32(19.*c.slope))/20.)
        c.samples+=1
        if c.temperature > c.peak:
            c.peak=c.temperature; c.progress_time=now
        if index < 4 and not c.fault and c.temperature >= c.target-1.:
            c.ready=True
        self.evaluate(index,now)
        return c.fault & ~old_fault

    def evaluate(self,index,now):
        c=self.channels[index]
        if index == 7: return 0  # OEM exclusion; port adds separate hard limits.
        if not c.fault:
            c.bad_sensor=(c.bad_sensor+1)&255 if c.temperature <= -30. or c.temperature >= 400. else 0
            if c.bad_sensor > 8: c.fault |= 1
        if not c.fault:
            c.bad_overtemp=(c.bad_overtemp+1)&255 if c.temperature >= OVERTEMP[index] else 0
            if c.bad_overtemp > 10: c.fault |= 4
        if index < 4:
            if not c.fault and not c.ready:
                far=c.temperature < c.target-10.
                limit=(20. if index < 2 else 40.) if far else (60. if index < 2 else 120.)
                slope_min=.5 if index < 2 else f32(.2)
                if c.progress_time+limit < now and (not far or c.slope < slope_min): c.fault |= 2
            if c.ready:
                margin=35. if index < 2 else 15.
                c.bad_drop=(c.bad_drop+1)&255 if c.temperature < c.target-margin else 0
                if c.bad_drop > 10: c.fault |= 8
            else: c.bad_drop=0

    def acknowledge(self):
        for c in self.channels: c.reported |= c.fault

    def status(self):
        return [dict(asdict(c),name=NAMES[i],fault_names=[name for bit,name in FAULTS.items() if c.fault&bit],
                     oem_messages=[OEM_MESSAGES[i][j] for j,bit in enumerate((1,2,4,8)) if c.fault&bit])
                for i,c in enumerate(self.channels)]
