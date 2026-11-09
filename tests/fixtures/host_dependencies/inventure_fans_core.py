# SPDX-License-Identifier: GPL-3.0-or-later
"""r0323 normal fan arithmetic. Normalized tach is NOT RPM."""
import math
from .inventure_thermal_core import f32

MINIMUM = (96,)*6
SCALE = (135,180,90,90,255,255)
TACH_ENABLED = (True,True,True,True,False,False)
NAMES = ('extruder_front','extruder_bottom','chamber_left','chamber_right','rear','transfer_upper')
# material record +0x1A, verified independently from the original application.
HIGH_CHAMBER_MATERIALS = frozenset((5,10,14,15))
FATAL_MASK = 0x0d
ERROR_NUMBERS = (140,141,142,143,None,None)

def command(value, mode=0, channel=0):
    if mode not in (0,1) or not 0 <= channel < 6 or not isinstance(value,int) or not 0 <= value <= 255:
        raise ValueError('Invalid fan byte/mode/channel')
    effective = max(value,MINIMUM[channel]) if value and mode == 0 else value
    return effective, effective*SCALE[channel]//255

def hotend_curve(t0,t1):
    if not all(math.isfinite(t) for t in (t0,t1)): raise ValueError('Nonfinite nozzle temperature')
    t = math.trunc(max(f32(t0),f32(t1)))
    return 0 if t < 40 else 255 if t > 60 else (t-40)*255//20

def chamber_command(material):
    return 255 if material in HIGH_CHAMBER_MATERIALS else 64

def normalized_tach(edges,duty):
    if not isinstance(edges,int) or edges < 0 or not 0 <= duty <= 255: raise ValueError('Invalid tach sample')
    return edges*255//duty//4 if duty else 0

def detector(command_byte,measured,count,peak,fault,channel,pid_desired=None):
    # PID branch is modeled for audit only. Normal firmware leaves all PID
    # pointers NULL; the live port never silently enables a speed PID.
    if pid_desired is not None: bad=100*measured < 75*pid_desired
    elif TACH_ENABLED[channel]: bad=command_byte >= MINIMUM[channel] and measured == 0
    else: return count,peak,fault
    count=(count+1)&0xffffffff if bad else 0
    peak=max(peak,count)
    if count > 99: fault |= 1 << channel
    return count,peak,fault

def fan_test_result(samples,channel,minimum=False):
    if len(samples)!=20 or not 0<=channel<4 or any(not math.isfinite(v) for v in samples):
        raise ValueError('Fan Test needs exactly twenty finite samples')
    # Population sigma, inclusive clipping, truncation toward zero (not median).
    mean=sum(samples)/20.; sigma=math.sqrt(sum((v-mean)**2 for v in samples)/20.)
    included=[v for v in samples if mean-sigma <= v <= mean+sigma]
    value=math.trunc(sum(included)/len(included))
    low=(135,145,80,80)[channel]; high=(260,205,105,105)[channel]
    return {'value':value,'pass':0<value<low if minimum else low<=value<=high,
            'included':len(included),'mean':mean,'population_sigma':sigma}


def validate_report(sample):
    """Validate a complete FANS_ABI1 record before it can refresh readiness."""
    limits={'clock':0xffffffff,'window_clock':0xffffffff,'sequence':0xffffffff,
            'channel':5,'duty':255,'window_duty':255,'edges':0xffffffff,
            'normalized':0xffffffff,'bad':0xffffffff,'peak':0xffffffff,
            'fault_mask':15,'mixed':63,'required_mask':15}
    for key,limit in limits.items():
        value=sample.get(key)
        if type(value) is not int or not 0<=value<=limit:
            raise ValueError('Invalid fan field: '+key)
    i=sample['channel']
    if sample['peak']<sample['bad']:raise ValueError('Invalid fan peak')
    required=sample['required_mask']
    if required & ~FATAL_MASK or required & ~sample['fault_mask']:
        raise ValueError('Invalid required-cooling mask')
    if (sample['clock']-sample['window_clock'])&0xffffffff>=0x80000000:
        raise ValueError('Fan window is in the future')
    if i<4:
        if sample['normalized']!=normalized_tach(sample['edges'],sample['window_duty']):
            raise ValueError('Invalid fan normalizer')
    elif any(sample[k] for k in ('window_duty','edges','normalized','bad','peak')):
        raise ValueError('Tach data on channel without tach hardware')
    return sample
