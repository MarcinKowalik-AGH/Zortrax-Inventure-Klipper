#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe RC7.1 host overlay installer for a pinned Inventure Klipper on Orange Pi.

Dry run unless --apply --confirm-service-stopped. Never edits printer.cfg,
peripheral settings, MCU, or services. Exact SHA-256 allowlist is mandatory.
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

KIT=Path(__file__).resolve().parents[1]
CONFIG=KIT/'baseline/expected_hashes.json'
PIN='ee2c6fb6b00b2b5d097ef70d0868df7d74943b51'

def sha(data:bytes)->str: return hashlib.sha256(data).hexdigest()

def assert_service_stopped():
    if not shutil.which('systemctl'):return
    for svc in ('klipper.service',):
        proc=subprocess.run(['systemctl','is-active','--quiet',svc],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if proc.returncode==0:
            raise ValueError(f'{svc} is still running. Stop it before host update.')

def safe_target(klipper:Path):
    root=klipper.expanduser().resolve()
    extras=root/'klippy/extras'
    check=root/'klippy/mcu.py'
    if not extras.is_dir() or not check.is_file() or check.is_symlink():raise ValueError('Not an identified Klipper tree: '+str(root))
    history=json.loads(CONFIG.read_text())
    if sha(check.read_bytes())!=history['klippy/mcu.py']['sha256']:
        raise ValueError('Host Klippy API does not match the pinned baseline')
    if (root/'.git').exists() and shutil.which('git'):
        q=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        if q.returncode==0 and q.stdout.strip()!=PIN:
            # More recent local integration commits are allowed only when the API hash is known.
            print('NOTE: git HEAD differs; pinned klippy/mcu.py verified by SHA-256.')
    return root,extras,history

def plan(root:Path,extras:Path,history:dict):
    result=[]
    sources=[('klippy/extras/'+source.name,source) for source in sorted((KIT/'host').glob('*.py'))]
    sources.append(('klippy/extras/inventure_oem_assets.json',KIT/'host/inventure_oem_assets.json'))
    sources.append(('klippy/serialhdl.py',KIT/'host_core/serialhdl.py'))
    for rel,source in sources:
        name=source.name
        entry=history.get(rel)
        if not entry:raise ValueError('Missing release hash: '+rel)
        intended=source.read_bytes()
        if sha(intended)!=entry['new_sha256']:raise ValueError('Kit changed after release: '+rel)
        dst=root/rel
        if dst.is_symlink():raise ValueError('Refusing symlink: '+str(dst))
        if dst.exists() and not dst.is_file():raise ValueError('Not a regular file: '+str(dst))
        previous=sha(dst.read_bytes()) if dst.exists() else None
        allowed=set(entry.get('allowed_existing',[]))|{entry['new_sha256']}
        if entry.get('sha256'):allowed.add(entry['sha256'])
        if previous is not None and previous not in allowed:
            raise ValueError(f'REFUSED unknown modification to {name}: {previous}. Manually reconcile; no files changed.')
        result.append(dict(file=name,path=rel,was_present=previous is not None,
                           old_sha256=previous,new_sha256=entry['new_sha256'],
                           action='unchanged' if previous==entry['new_sha256'] else ('replace' if previous else 'add')))
    if len(result)!=16:raise ValueError('Incomplete RC7 payload, expected 14 extras + OEM assets + serialhdl.py')
    return result

def atomic_write(data:bytes,dst:Path):
    fd,name=tempfile.mkstemp(prefix='.'+dst.name+'.rc7_1-',dir=dst.parent)
    tmp=Path(name)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o644)
        os.replace(tmp,dst)
    finally:
        if tmp.exists():tmp.unlink()

def install(root:Path,extras:Path,items:list,confirmed:bool):
    if not confirmed:raise ValueError('Use --confirm-service-stopped AFTER stopping Klipper')
    assert_service_stopped()
    # Recheck all hashes immediately before creating the backup or any write.
    for e in items:
        dst=root/e['path'];cur=sha(dst.read_bytes()) if dst.exists() else None
        if cur!=e['old_sha256']:raise ValueError('Host changed since preflight: '+e['file'])
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    backup=root/'.inventure-rc7_1-backups'/stamp
    backup.mkdir(parents=True,exist_ok=False)
    for e in items:
        if e['was_present']:
            old=(root/e['path']).read_bytes()
            atomic_write(old,backup/e['file'])
    (backup/'manifest.json').write_text(json.dumps({'kit':'P01_P17_RC7_1_OEM_UI_ZERO3','klipper':str(root),'files':items,'printer_cfg_changed':False,'mcu_flashed':False},indent=2)+'\n')
    completed=[]
    try:
        for e in items:
            if e['action']=='unchanged':continue
            # A previously trusted file cannot be changed by another process unnoticed.
            dst=root/e['path']
            cur=sha(dst.read_bytes()) if dst.exists() else None
            if cur!=e['old_sha256']:raise ValueError('Concurrent modification: '+e['file'])
            source=(KIT/'host_core'/e['file'] if e['path']=='klippy/serialhdl.py' else KIT/'host'/e['file'])
            atomic_write(source.read_bytes(),dst)
            completed.append(e)
            if sha(dst.read_bytes())!=e['new_sha256']:raise ValueError('Failed host readback: '+e['file'])
    except BaseException:
        for e in reversed(completed):
            dst=root/e['path']
            if e['was_present']:atomic_write((backup/e['file']).read_bytes(),dst)
            else:dst.unlink(missing_ok=True)
        raise
    print('HOST RC7.1 INSTALLED, READBACK PASS. Backup:',backup)
    print('No printer.cfg edits, firmware writes, network commands or service restart.')

def rollback(root:Path,backup:Path,confirmed:bool):
    if not confirmed:raise ValueError('Use --confirm-service-stopped after stopping Klipper')
    assert_service_stopped()
    backup=backup.expanduser().resolve()
    if backup.parent!=root/'.inventure-rc7_1-backups':raise ValueError('Backup not in this Klipper tree')
    manifest=json.loads((backup/'manifest.json').read_text())
    if manifest['kit']!='P01_P17_RC7_1_OEM_UI_ZERO3' or manifest['klipper']!=str(root):
        raise ValueError('Backup identity mismatch')
    items=manifest['files']
    for e in items:
        dest=root/e['path']
        if dest.is_symlink() or not dest.exists() or sha(dest.read_bytes())!=e['new_sha256']:
            raise ValueError('Refusing rollback over modified installed file: '+e['file'])
        if e['was_present'] and sha((backup/e['file']).read_bytes())!=e['old_sha256']:
            raise ValueError('Backup integrity mismatch: '+e['file'])
    for e in reversed(items):
        dest=root/e['path']
        if e['was_present']:atomic_write((backup/e['file']).read_bytes(),dest)
        else:dest.unlink()
    print('RC7.1 HOST ROLLBACK PASS. Printer.cfg and MCU unchanged.')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('klipper',type=Path,help='Root of the existing klipper-inventure checkout')
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--confirm-service-stopped',action='store_true')
    ap.add_argument('--rollback',type=Path,help='Backup directory printed by --apply')
    a=ap.parse_args()
    root,extras,history=safe_target(a.klipper)
    if a.rollback:
        if a.apply:raise ValueError('--rollback and --apply are mutually exclusive')
        rollback(root,a.rollback,a.confirm_service_stopped)
        return
    items=plan(root,extras,history)
    print(json.dumps({'release':'P01_P17_RC7_1_OEM_UI_ZERO3','mode':'APPLY' if a.apply else 'DRY_RUN',
                      'klipper':str(root),'files':items},indent=2))
    if a.apply:install(root,extras,items,a.confirm_service_stopped)
    else: print('DRY RUN only. No files changed. Use --apply --confirm-service-stopped after stopping Klipper.')
if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        print('RC7.1 HOST INSTALL STOPPED:',exc,file=sys.stderr);sys.exit(1)
