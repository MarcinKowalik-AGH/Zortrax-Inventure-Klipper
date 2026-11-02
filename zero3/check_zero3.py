#!/usr/bin/env python3
# Read-only Orange Pi Zero 3 / Armbian UART and Klipper preflight.
# No network activity, no serial writes, no service modifications.
from __future__ import annotations
import argparse,glob,grp,json,os,platform,re,subprocess,sys
from pathlib import Path

def text(path):
    try:return Path(path).read_text(errors='replace')
    except OSError:return ''

def kv(content):
    return dict((k,v.strip('"')) for k,v in re.findall(r'^([A-Za-z_][A-Za-z_0-9]*)=(.+)$',content,re.M))

def probe(cfg):
    arm=kv(text('/etc/armbian-release'))
    env=kv(text('/boot/armbianEnv.txt'))
    model=text('/proc/device-tree/model').replace('\x00',' ').strip()
    board=(arm.get('BOARD') or arm.get('BOARD_NAME') or '').lower()
    is_zero3=('zero3' in board or 'zero 3' in model.lower())
    nodes=sorted(set(glob.glob('/dev/ttyS*')+glob.glob('/dev/ttyAS*')+glob.glob('/dev/serial/by-id/*')+glob.glob('/dev/serial/by-path/*')))
    details={}
    for path in nodes:
        try: details[path]={'realpath':os.path.realpath(path),'readable':os.access(path,os.R_OK),'writable':os.access(path,os.W_OK)}
        except OSError: pass
    cfg_txt=text(cfg) if cfg else ''
    section=None; serial=None; baud=None; step={}
    for raw in cfg_txt.splitlines():
        ln=raw.split('#',1)[0].strip()
        if ln.startswith('[') and ln.endswith(']'):section=ln[1:-1].strip()
        elif ':' in ln:
            k,v=[x.strip() for x in ln.split(':',1)]
            if section=='mcu' and k in ('serial','baud'):
                if k=='serial':serial=v
                else:baud=v
            if section and section in ('stepper_x','stepper_y','stepper_z','extruder') and k=='step_pulse_duration':step[section]=v
    groups=[g.gr_name for g in grp.getgrall() if os.getuid() in g.gr_mem]
    try: groups.extend(g.gr_name for g in grp.getgrall() if g.gr_gid in os.getgroups())
    except OSError:pass
    notices=[]
    if not is_zero3:notices.append('BOARD UNCONFIRMED: require Orange Pi Zero 3 H618, do not copy UART pin map from H2+/H3 Zero.')
    if not nodes:notices.append('No serial node detected. Check Armbian DT overlay and UART pin mapping.')
    if serial and serial not in nodes and not Path(serial).exists():notices.append('Configured [mcu] serial missing: '+serial)
    if baud and baud not in ('115200','250000','500000','1000000','2000000'):
        notices.append('Configured baud differs from InventureBoot 1.6 menu; check legacy P4 journal.')
    if cfg and not cfg_txt:notices.append('Cannot read requested printer.cfg; no changes made.')
    if cfg_txt:
        for axis in ('stepper_x','stepper_y','stepper_z','extruder'):
            val=step.get(axis)
            if val is None:
                notices.append('No step_pulse_duration in ['+axis+']; 2us is NOT qualified by this check.')
            else:
                try:
                    if abs(float(val)-0.000002)>0.000000001:notices.append('Not 2us in ['+axis+']: '+val)
                except ValueError:notices.append('Invalid pulse duration in ['+axis+']: '+val)
    if 'dialout' not in set(groups) and os.getuid()!=0:notices.append('Current account may lack dialout group access; verify actual serial permissions.')
    return {'host':platform.node(),'machine':platform.machine(),'kernel':platform.release(),
            'model':model,'armbian':{k:arm.get(k) for k in ('BOARD','BOARD_NAME','BOARDFAMILY','ARCH')},
            'zero3_detected':is_zero3, 'armbian_overlays':env.get('overlays',None),
            'serial_devices':details,'printer_cfg':str(cfg) if cfg else None,
            'configured_mcu_serial':serial,'configured_mcu_baud':baud,'step_pulse_duration':step,
            'notices':notices,'changed_files':False,'accessed_uart':False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--printer-cfg',type=Path)
    a=p.parse_args()
    print(json.dumps(probe(a.printer_cfg),indent=2,ensure_ascii=False))
if __name__=='__main__':main()
