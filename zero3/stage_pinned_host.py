#!/usr/bin/env python3
# Offline staging only: unpacks verified pinned Klipper baseline and overlays.
# Does NOT change systemd, printer.cfg, Moonraker, bootloader or live source.
from __future__ import annotations
import argparse,json,shutil,sys,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE/'scripts'))
from build_firmware_on_mac import extract_pinned,apply_rc7,sha,HIST

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--apply',action='store_true',help='Create a NEW separate Klipper source tree')
    a=p.parse_args()
    dest=a.destination.expanduser().resolve()
    if dest==Path('/') or str(dest).startswith('/usr/') or str(dest).startswith('/etc/'):
        raise ValueError('Cannot stage into system directories')
    if dest.exists():raise ValueError('Destination already exists. NEVER overwrite live Klipper: '+str(dest))
    expected=json.loads(HIST.read_text())
    print('RC8.0 staging preflight: pinned source SHA-256 and 29 overlays will be checked.')
    if not a.apply:
        print('DRY_RUN. Destination does not exist:',dest)
        return
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.inventure-rc8_0-stage-',dir=dest.parent) as tmp:
        tmpdir=Path(tmp)
        src,orig=extract_pinned(tmpdir,expected)
        overlays=apply_rc7(src,orig,expected)
        if len(overlays)!=29:raise ValueError('Expected 29 overlay modules, got '+str(len(overlays)))
        (src/'RC8_0_STAGED_NOT_CONFIGURED.json').write_text(json.dumps({
            'status':'STAGED_NOT_CONFIGURED','target':'Orange Pi Zero 3 / Linux ARM64',
            'pin':'STM32F207 V9; NOT M300','patches':overlays,'count':len(overlays),
            'no_service_changes':True,'no_printer_cfg':True,
            'next':'review host environment/serial/dedicated klippy-env and systemd service manually'},indent=2)+'\n')
        src.rename(dest)
    print('STAGED, NOT ACTIVE:',dest)
    print('No service changed. Do not start until printer.cfg and host dependencies are confirmed.')
if __name__=='__main__':
    try:main()
    except (ValueError,OSError,RuntimeError) as exc:
        print('STAGE REFUSED:',exc,file=sys.stderr);sys.exit(1)
