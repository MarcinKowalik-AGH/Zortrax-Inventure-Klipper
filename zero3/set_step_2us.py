#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Controlled, strictly opt-in 2 us Klipper stepper pulse config edit for Inventure V9.

Only changes step_pulse_duration in existing [stepper_x/y/z]/[extruder]; never
writes pin/rotation/speed/accel/temperature/ARM fields. Fails closed on ambiguous
config, missing sections or a running klipper.service. Dry-run by default.
"""
from __future__ import annotations
import argparse,datetime,difflib,os,re,shutil,subprocess,sys,tempfile
from pathlib import Path

SECTIONS=('stepper_x','stepper_y','stepper_z','extruder')
TARGET='0.000002'
HEADER=re.compile(r'^\s*\[\s*([^\]]+?)\s*\]\s*(?:[#;].*)?$')
VALUE=re.compile(r'^(\s*step_pulse_duration\s*:\s*)([^\s#;]+)(.*)$',re.I)
STEP_PIN=re.compile(r'^\s*step_pin\s*:',re.I)

def plan(source:str)->tuple[str,list[dict]]:
    if '\r' in source: raise ValueError('CRLF/CR file: preserve manually; no automatic newline normalization')
    lines=source.splitlines(keepends=True)
    blocks={};active=None
    for i,line in enumerate(lines):
        m=HEADER.match(line.rstrip('\n'))
        if m:
            active=m.group(1).strip().lower()
            if active in blocks:raise ValueError('Duplicate section ['+active+']')
            blocks[active]=[i,i+1]
        elif active in blocks:
            blocks[active][1]=i+1
    if 'printer' not in blocks:raise ValueError('Missing local [printer] section')
    p0,p1=blocks['printer']
    kinematics=[m.group(1).strip().lower() for line in lines[p0+1:p1] if (m:=re.match(r'^\s*kinematics\s*:\s*([^\s#;]+)',line,re.I))]
    if kinematics!=['corexy']:raise ValueError('Inventure V9 requires unambiguous [printer] kinematics: corexy')
    if any(k not in blocks for k in SECTIONS):raise ValueError('Missing local stepper section(s): '+', '.join(k for k in SECTIONS if k not in blocks))
    edits=[]
    for name in SECTIONS:
        first,end=blocks[name]
        entries=[(i,VALUE.match(lines[i].rstrip('\n'))) for i in range(first+1,end) if VALUE.match(lines[i].rstrip('\n'))]
        if len(entries)>1:raise ValueError('Duplicate step_pulse_duration in ['+name+']')
        if entries:
            i,m=entries[0]
            try:old=float(m.group(2))
            except ValueError:raise ValueError('Invalid step pulse in ['+name+']')
            if not 0<old<=0.001:raise ValueError('Out of range step pulse in ['+name+']')
            current=m.group(2)
            if abs(old-0.000002)<1e-12:continue
            new_line=m.group(1)+TARGET+m.group(3)+ ('\n' if lines[i].endswith('\n') else '')
            edits.append({'section':name,'old':current,'new':TARGET,'position':i,'line':new_line,'action':'replace'})
        else:
            pin=[i for i in range(first+1,end) if STEP_PIN.match(lines[i])]
            if len(pin)!=1:raise ValueError('Missing or duplicate step_pin in ['+name+']')
            i=pin[0]+1
            edits.append({'section':name,'old':None,'new':TARGET,'position':i,'line':'step_pulse_duration: '+TARGET+'\n','action':'insert'})
    result=list(lines)
    for x in sorted(edits,key=lambda x:x['position'],reverse=True):
        if x['action']=='replace':result[x['position']]=x['line']
        else:result.insert(x['position'],x['line'])
    return ''.join(result),edits

def active_klipper()->bool:
    if not shutil.which('systemctl'):return False
    p=subprocess.run(['systemctl','is-active','--quiet','klipper.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return p.returncode==0

def apply(src:Path,original:str,patched:str):
    if active_klipper():raise ValueError('klipper.service is active; stop it before modifying printer.cfg')
    if src.is_symlink() or not src.is_file():raise ValueError('Refusing symlink/nonfile printer.cfg')
    if src.read_text()!=original:raise ValueError('printer.cfg changed since planning')
    if original==patched:print('ALREADY CONFIGURED — no write');return None
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    backup=src.with_name(src.name+'.before_RC7_1_STEP2US_'+stamp+'.bak')
    fd,tmp=tempfile.mkstemp(prefix='.inventure-step2us-',dir=src.parent)
    try:
        os.close(fd)
        shutil.copy2(src,backup)
        Path(tmp).write_text(patched)
        os.chmod(tmp,src.stat().st_mode & 0o777)
        if src.read_text()!=original or active_klipper():raise ValueError('Concurrent modification / service started')
        os.replace(tmp,src)
        if src.read_text()!=patched:
            shutil.copy2(backup,src)
            raise ValueError('Readback failed; restored from backup')
    finally:
        if Path(tmp).exists():Path(tmp).unlink()
    return backup

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('printer_cfg',type=Path)
    p.add_argument('--apply',action='store_true')
    p.add_argument('--confirm-inventure-v9',action='store_true')
    a=p.parse_args()
    source_path=a.printer_cfg.expanduser()
    if source_path.is_symlink():raise ValueError('Refusing symlink printer.cfg')
    cfg=source_path.resolve(strict=True)
    if not cfg.is_file():raise ValueError('Not a regular file')
    old=cfg.read_text();new,edits=plan(old)
    print('Inventure V9 2us patch — scope:',[x['section'] for x in edits]);print('changes:',len(edits))
    if edits:
        print(''.join(difflib.unified_diff(old.splitlines(keepends=True),new.splitlines(keepends=True),fromfile=str(cfg),tofile='proposed'))[:8000])
    if not a.apply:
        print('DRY RUN: no edits made. For install, stop Klipper, then --apply --confirm-inventure-v9.')
        return
    if not a.confirm_inventure_v9:raise ValueError('Explicit --confirm-inventure-v9 required')
    b=apply(cfg,old,new)
    print('STEP 2us:', 'WRITE AND READBACK PASS; BACKUP '+str(b) if b else 'ALREADY CONFIGURED')
    print('No other settings or services changed. Do not home/heat until physical qualification.')
if __name__=='__main__':
    try:main()
    except (OSError,ValueError) as e:print('STEP2US REFUSED:',e,file=sys.stderr);sys.exit(1)
