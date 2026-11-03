#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage an Inventure V9 Orange Pi Zero 3 [mcu] baud switch, NEVER touch MCU.

The normal application follows the InventureBoot P4 setting. The separate
1.5 Mbaud lab image overrides it for the application only. This script
changes only the host printer.cfg after stopping klipper.service.

No serial port is opened. No MCU/GPIO writes, firmware resets or heat/motion.
"""
from __future__ import annotations
import argparse,datetime,hashlib,json,os,re,shutil,stat,subprocess,sys,tempfile
from pathlib import Path

BASE_RATES={250000,500000}
FORCED_LAB_BAUD=1500000
RATES=BASE_RATES|{1000000,FORCED_LAB_BAUD,2000000}
TRANSITIONS={(old,new) for old in RATES for new in BASE_RATES}
TRANSITIONS.update({(250000,1000000),(500000,1000000),(1000000,2000000),
                    (2000000,1000000),(250000,FORCED_LAB_BAUD),
                    (500000,FORCED_LAB_BAUD),(1000000,FORCED_LAB_BAUD)})
HEADER=re.compile(r'^\s*\[([^\]]+)\]\s*(?:[#;].*)?$')
ENTRY=re.compile(r'^(\s*)(baud|serial)(\s*:\s*)([^#;\s]+)(.*)$',re.I)
SHA=lambda data:hashlib.sha256(data).hexdigest()

def active_service():
    if not shutil.which('systemctl'):return False
    return subprocess.run(['systemctl','is-active','--quiet','klipper.service'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0

def read_cfg(path):
    if path.is_symlink() or not path.is_file():raise ValueError('Refusing symlink/unknown cfg')
    data=path.read_bytes()
    if b'\r' in data:raise ValueError('CRLF not changed automatically')
    return data.decode('utf-8')

def parse_and_plan(content,new_baud):
    if new_baud not in RATES:raise ValueError('Invalid/unsupported target baud')
    lines=content.splitlines(keepends=True);sections=0;sec=None;options={}
    for i,ln in enumerate(lines):
        m=HEADER.match(ln.strip())
        if m:
            sec=m.group(1).strip().lower()
            if sec=='mcu':sections+=1
            continue
        if sec!='mcu':continue
        m=ENTRY.match(ln.rstrip('\n'))
        if not m:continue
        k=m.group(2).lower()
        if k in options:raise ValueError('Duplicate [mcu] '+k)
        options[k]=(i,m)
    if sections!=1 or set(options)!={'serial','baud'}:raise ValueError('Expected exactly one [mcu] section with baud and serial')
    if options['serial'][1].group(4)!='/dev/ttyS1':
        raise ValueError('Configured UART is not verified Orange Pi Zero3 /dev/ttyS1')
    try:old=int(options['baud'][1].group(4))
    except ValueError:raise ValueError('Invalid existing baud')
    if old not in RATES:raise ValueError('Existing baud not among qualified candidate rates')
    if old==new_baud:return content,old
    if (old,new_baud) not in TRANSITIONS:
        raise ValueError('Select a base rate first, or qualify 1000000 before 2000000')
    idx,m=options['baud']
    # Preserve whitespace, comments, exact neighboring options and sections.
    tail=m.group(5)
    lines[idx]=m.group(1)+m.group(2)+m.group(3)+str(new_baud)+tail+('\n' if lines[idx].endswith('\n') else '')
    return ''.join(lines),old

def validate_clock(baud,uart_clock_hz):
    if uart_clock_hz is None:
        if baud not in BASE_RATES:
            raise ValueError('Supply --uart-clock-hz from the active UART5 clock before staging a high baud')
        return
    divisor,remainder=divmod(uart_clock_hz,16*baud)
    if not 1<=divisor<=65535 or remainder:
        raise ValueError(f'UART5 clock {uart_clock_hz} Hz does not give an exact integer divisor at {baud} baud')

def atomic_write(path,data):
    mode=stat.S_IMODE(path.stat().st_mode)
    fd,tmp=tempfile.mkstemp(prefix='.inventure-p4-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    if path.read_bytes()!=data:raise ValueError('Readback mismatch; stop')

def apply(path,original,modified,old,new):
    if active_service():raise ValueError('Stop Klipper first: sudo systemctl stop klipper')
    if read_cfg(path)!=original:raise ValueError('Configuration changed since preflight')
    if original==modified:print('ALREADY_SET: no writes');return
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    folder=path.parent/'.inventure-p4-backups'/stamp
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'printer.cfg').write_bytes(original.encode())
    manifest={'old_baud':old,'new_baud':new,'cfg':str(path),'old_sha256':SHA(original.encode()),
              'new_sha256':SHA(modified.encode()),'mcu_changed':False,
              'bootloader_changed':False,'host_baud_staged_only':True}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    atomic_write(path,modified.encode())
    print('HOST_BAUD_STAGED_NOT_CONNECTED',old,'->',new)
    print('BACKUP:',folder)
    if new==FORCED_LAB_BAUD:
        print('NEXT: use the separate LAB 1500000 MCU application. Keep the verified BOOT baud.')
    else:
        if old==FORCED_LAB_BAUD:
            print('NEXT: restore the journal-mode MCU application before starting Klipper.')
        print('NEXT: set bootloader Diagnostics -> UART -> P4 baud rate to '+str(new))
    print('NEXT: leave UART on bootwait OFF, cold power cycle MCU; only then start Klipper.')

def rollback(path,folder):
    if active_service():raise ValueError('Stop Klipper before rollback')
    folder=folder.expanduser().resolve()
    if folder.parent!=path.parent/'.inventure-p4-backups':raise ValueError('Invalid backup directory')
    data=json.loads((folder/'manifest.json').read_text());original=(folder/'printer.cfg').read_bytes()
    if data['cfg']!=str(path) or SHA(original)!=data['old_sha256']:
        raise ValueError('Backup path or SHA mismatch')
    current=read_cfg(path).encode()
    if SHA(current)!=data['new_sha256']:raise ValueError('Current config was modified; refusing rollback')
    atomic_write(path,original)
    print('HOST_BAUD_ROLLBACK_OK ->',data['old_baud'])
    print('BOOTLOADER IS UNCHANGED by this command; manually restore its matching baud.')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--printer-cfg',type=Path,required=True)
    mx=ap.add_mutually_exclusive_group(required=True)
    mx.add_argument('--baud',type=int,choices=sorted(RATES))
    mx.add_argument('--rollback',type=Path)
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--confirm-klipper-stopped',action='store_true')
    a=ap.parse_args();p=a.printer_cfg.expanduser().resolve()
    # Resolve check also rejects *original* symlinks, not just the target.
    if a.printer_cfg.expanduser().is_symlink():raise ValueError('Symlink printer.cfg refused')
    if a.rollback:
        if not a.apply or not a.confirm_klipper_stopped:raise ValueError('Rollback needs --apply --confirm-klipper-stopped')
        rollback(p,a.rollback);return
    validate_clock(a.baud,a.uart_clock_hz)
    old_text=read_cfg(p);new_text,old=parse_and_plan(old_text,a.baud)
    print('HOST_CONFIG',p,'PORT=/dev/ttyS1',old,'->',a.baud)
    print('MCU_AND_BOOTLOADER_UNCHANGED_BY_THIS_TOOL')
    if not a.apply:
        print('DRY_RUN_ONLY; no files changed');return
    if not a.confirm_klipper_stopped:raise ValueError('--confirm-klipper-stopped required')
    apply(p,old_text,new_text,old,a.baud)

if __name__=='__main__':
    try:main()
    except (ValueError,OSError) as exc:
        print('INVENTURE_P4_BAUD_REFUSED:',exc,file=sys.stderr);sys.exit(1)
