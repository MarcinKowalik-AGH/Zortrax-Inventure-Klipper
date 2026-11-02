#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe, hash-pinned RC7.2/RC7.3 -> RC7.4 OLED host-only patch and rollback.

Dry-run by default; refuses active Klipper, mismatched sources, symlinks,
unknown configuration, duplicate parameters, and concurrent modification.
Does not touch MCU, bootloader, UART settings, motion, fans, PWM or interlocks.
"""
from __future__ import annotations
import argparse,datetime,difflib,hashlib,json,os,re,shutil,stat,subprocess,sys,tempfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
PAYLOAD=(HERE/'host') if (HERE/'host').is_dir() else HERE.parent/'host'
FILES={'inventure_oem_menu.py': ('e7a16d116d6d7a9c98952a8f29def4024fd8d373f27e938bffe832a165247949',
                           '53b9a88e1fce3749c70d7a7a4a2a96aeee6467ea992226fc78583cef9e1539e0'),
 'inventure_oled.py': ('90e5f0210fe7d53fdec8944d2dd0833bd862b0ddfe7a0ee0b2d8fc92dc82c58d',
                       'cca9728a8b107e176fddef38191080931529784a58b28bb28c892f2868bf6008'),
 'inventure_oled_render.py': ('34006181ee40229c740c88f97a7f0a278548af5eca6904d8325a2a89eed4faab',
                              '327b9f5a9d495420586f734d17fed9641995135a5814adf5f596d5785b698767'),
 'inventure_oled_transport.py': ('2112bc6e438d90437637633301c3eacd52648a53dd033849828824dfbe63feaf',
                                 'c5e9fd6abd697c79add5716683c20548db58f9a0c38d4e193283cf2509bc27d6')}
INTERMEDIATE={'inventure_oem_menu.py': '53b9a88e1fce3749c70d7a7a4a2a96aeee6467ea992226fc78583cef9e1539e0',
 'inventure_oled.py': 'e86a81f6a632353a47ae339ba92021e00a7e308a209abad16452172d29583cd5',
 'inventure_oled_render.py': '327b9f5a9d495420586f734d17fed9641995135a5814adf5f596d5785b698767'}
RC74_ORIGINAL={'inventure_oled.py': '207a6a4627431c2ea4fa4c6198ff6442ce35513aaab89b0f66ccbe27c542ff5f'}
SHA=lambda b:hashlib.sha256(b).hexdigest()
HEADER=re.compile(r'^\s*\[([^]]+)\]\s*(?:[#;].*)?$')
ENTRY=re.compile(r'^(\s*)([A-Za-z0-9_]+)(\s*:\s*)([^#;\r\n]*)(.*)$')
CFG={'chunk_interval':'0.002','refresh_interval':'0.2',
     'oem_start_screen':'klipper'}

def service_stopped():
    if shutil.which('systemctl'):
        p=subprocess.run(['systemctl','is-active','--quiet','klipper.service'],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if p.returncode==0:raise ValueError('STOP KLIPPER SERVICE FIRST: sudo systemctl stop klipper')

def unique_path(p:Path):
    if p.is_symlink() or not p.is_file():raise ValueError('Refusing symlink/non-regular file: '+str(p))
    return p

def plan_config(content:str):
    if '\r' in content:raise ValueError('CRLF config not automatically modified')
    lines=content.splitlines(keepends=True); active=None; start=None; end=None; found=0
    params={}
    for i,raw in enumerate(lines):
        mt=HEADER.match(raw.strip())
        if mt:
            if active=='inventure_oled':end=i
            active=mt.group(1).strip().lower()
            if active=='inventure_oled':
                found+=1
                if found>1:raise ValueError('Exactly one local [inventure_oled] required')
                start=i;end=len(lines)
            continue
        if active=='inventure_oled':
            m=ENTRY.match(raw.rstrip('\n'))
            if m:
                key=m.group(2).lower()
                if key in params:raise ValueError('Duplicate [inventure_oled] option: '+key)
                params[key]=(i,m)
    if found!=1 or start is None:raise ValueError('Exactly one local [inventure_oled] required')
    if 'oem_menu' not in params or params['oem_menu'][1].group(4).strip().lower() not in ('true','1','yes','on'):
        raise ValueError('Expected existing oem_menu: True; do not change legacy mode')
    # Preserve all unrelated options and comments; narrowly update OLED pacing.
    for key in ('chunk_interval','refresh_interval'):
        if key not in params:raise ValueError('Expected existing '+key+' in [inventure_oled]')
    if float(params['chunk_interval'][1].group(4).strip()) not in (0.02,0.004,0.002):
        raise ValueError('Unexpected chunk_interval; manual review required')
    if float(params['refresh_interval'][1].group(4).strip()) not in (0.5,0.2):
        raise ValueError('Unexpected refresh_interval; manual review required')
    if 'oem_start_screen' in params and params['oem_start_screen'][1].group(4).strip() not in ('klipper','menu'):
        raise ValueError('Unknown oem_start_screen; manually review')
    for key,value in CFG.items():
        if key=='oem_start_screen' and key in params and params[key][1].group(4).strip()=='menu':
            # Respect a deliberate user opt-in for immediate menu.
            continue
        if key not in params:continue
        i,m=params[key]
        if m.group(4).strip()!=value:
            # Keep original indentation and trailing commentary.
            tail=m.group(5)
            lines[i]=m.group(1)+m.group(2)+m.group(3)+value+(' ' if tail and not tail.startswith(' ') else '')+tail+('\n' if lines[i].endswith('\n') else '')
    if 'oem_start_screen' not in params:
        # End index is next section; add before that section.
        lines.insert(end,'oem_start_screen: klipper\n')
    return ''.join(lines)

def make_plan(klipper:Path,cfg:Path):
    extras=klipper/'klippy/extras'
    if not (klipper/'klippy/klippy.py').is_file():raise ValueError('Not a Klipper tree: '+str(klipper))
    result=[]
    for name,(old,new) in FILES.items():
        src=unique_path(PAYLOAD/name)
        if SHA(src.read_bytes())!=new:raise ValueError('Unexpected patch SHA: '+name)
        target=unique_path(extras/name)
        current=SHA(target.read_bytes())
        if current not in (old,new,INTERMEDIATE.get(name),RC74_ORIGINAL.get(name)):
            raise ValueError('REFUSED unknown existing SHA '+name+': '+current)
        result.append(dict(name=name,path=str(target),current=current,new=new,action='same' if current==new else 'update'))
    unique_path(cfg)
    original=cfg.read_text()
    patched=plan_config(original)
    return result,original,patched

def atomic_write(dst:Path,data:bytes):
    orig_mode=stat.S_IMODE(dst.stat().st_mode)
    fd,name=tempfile.mkstemp(prefix='.'+dst.name+'.rc74-',dir=dst.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(name,orig_mode)
        os.replace(name,dst)
        if SHA(dst.read_bytes())!=SHA(data):raise ValueError('Readback verification failed: '+str(dst))
    finally:
        if os.path.exists(name):os.unlink(name)

def apply(klipper,cfg,items,orig,patched):
    service_stopped()
    if all(i['action']=='same' for i in items) and patched==orig:
        print('ALREADY_INSTALLED: no writes');return
    for item in items:
        if SHA(Path(item['path']).read_bytes())!=item['current']:
            raise ValueError('Concurrent modification: '+item['name'])
    if cfg.read_text()!=orig:raise ValueError('Configuration changed during preflight')
    t=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    backup=klipper/'.inventure-rc74-backups'/t
    backup.mkdir(parents=True,exist_ok=False)
    (backup/'HOST').mkdir()
    for item in items:(backup/'HOST'/item['name']).write_bytes(Path(item['path']).read_bytes())
    (backup/'printer.cfg').write_bytes(orig.encode('utf-8'))
    manifest={'kind':'RC7.2_to_RC7.4_OLED_HOST_ONLY',
              'target':str(klipper),'config':str(cfg),'files':items,
              'old_config_sha256':SHA(orig.encode('utf-8')),
              'new_config_sha256':SHA(patched.encode('utf-8')),
              'MCU_or_BOOT_changed':False,'time_utc':t}
    (backup/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    completed=[]
    try:
        for item in items:
            if item['action']=='same':continue
            target=Path(item['path'])
            atomic_write(target,(PAYLOAD/item['name']).read_bytes())
            completed.append((target,(backup/'HOST'/item['name']).read_bytes()))
        if patched!=orig:
            atomic_write(cfg,patched.encode('utf-8'))
            completed.append((cfg,orig.encode('utf-8')))
    except BaseException:
        for target,data in reversed(completed):atomic_write(target,data)
        raise
    print('RC7.4 HOST OLED PATCH INSTALLED / READBACK PASS')
    print('BACKUP:',backup)
    print('NO MCU/BOOT/FLUIDD/MOTION/INTERLOCK WRITES')
    print('Service remains STOPPED; start with sudo systemctl start klipper')

def rollback(klipper,cfg,backup):
    service_stopped()
    backup=backup.expanduser().resolve()
    if backup.parent!=klipper/'.inventure-rc74-backups':raise ValueError('Unexpected backup location')
    m=json.loads((backup/'manifest.json').read_text())
    if m['kind']!='RC7.2_to_RC7.4_OLED_HOST_ONLY' or m['target']!=str(klipper) or m['config']!=str(cfg):
        raise ValueError('Backup identity mismatch')
    for item in m['files']:
        target=unique_path(Path(item['path']))
        if SHA(target.read_bytes())!=item['new']:
            raise ValueError('Refusing rollback over modified host file: '+item['name'])
    if SHA(cfg.read_bytes())!=m['new_config_sha256']:
        raise ValueError('Refusing rollback over independently modified printer.cfg')
    for item in m['files']:
        atomic_write(Path(item['path']),(backup/'HOST'/item['name']).read_bytes())
    atomic_write(cfg,(backup/'printer.cfg').read_bytes())
    print('ROLLBACK PASS; service remains stopped')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--klipper',type=Path,default=Path('~/klipper'))
    ap.add_argument('--printer-cfg',type=Path,default=Path('~/printer_data/config/printer.cfg'))
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--confirm-service-stopped',action='store_true')
    ap.add_argument('--rollback',type=Path)
    a=ap.parse_args()
    klipper=a.klipper.expanduser().resolve()
    cfg=a.printer_cfg.expanduser().resolve()
    if a.rollback:
        if a.apply:raise ValueError('Cannot rollback and install at once')
        if not a.confirm_service_stopped:raise ValueError('Rollback requires --confirm-service-stopped')
        rollback(klipper,cfg,a.rollback)
        return
    items,old,new=make_plan(klipper,cfg)
    print('TARGET:',klipper,'CONFIG:',cfg)
    for item in items:print(item['action'],item['name'],item['current'][:16]+'...')
    print('CONFIG DIFF:')
    print(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='before printer.cfg',tofile='RC7.4 printer.cfg')) or '(no changes)')
    if a.apply:
        if not a.confirm_service_stopped:raise ValueError('Use --confirm-service-stopped after stopping Klipper')
        apply(klipper,cfg,items,old,new)
    else:print('DRY_RUN_ONLY: nothing changed')
if __name__=='__main__':
    try:main()
    except (ValueError,OSError) as e:print('RC7.4 REFUSED:',e,file=sys.stderr);sys.exit(1)
