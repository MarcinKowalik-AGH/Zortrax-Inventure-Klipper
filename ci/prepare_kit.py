#!/usr/bin/env python3
"""Reconstruct and validate a new offline build kit; never edits a live host."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

REPO=Path(__file__).resolve().parents[1]

def prepare(destination,repo=REPO):
    destination=Path(destination).resolve();repo=Path(repo).resolve()
    if destination.exists():raise ValueError('Destination already exists: '+str(destination))
    parts=sorted((repo/'source/parts').glob('part*.bin'))
    expected,_=(repo/'source/SHA256SUMS').read_text().strip().split()
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.inventure-kit-',dir=destination.parent) as tmp:
        root=Path(tmp);archive=root/'source.zip'
        with archive.open('wb') as out:
            for part in parts:out.write(part.read_bytes())
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
            raise ValueError('Source archive checksum mismatch')
        kit=root/'kit';kit.mkdir()
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('Source archive CRC failure')
            for info in z.infolist():
                path=Path(info.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in info.filename:
                    raise ValueError('Unsafe archive path')
            z.extractall(kit)
        for script in ('apply_gnu_ctr_fix.py','apply_rc7_dict_fix.py','apply_rc75_oled.py','apply_rc8_mcu.py'):
            subprocess.run([sys.executable,str(repo/'ci'/script),str(kit)],check=True)
        kit.rename(destination)
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    try:print('PREPARED:',prepare(args.destination))
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        raise SystemExit('REFUSED: '+str(exc))
