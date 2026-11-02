#!/usr/bin/env python3
"""Audit correction: validate markers against the compiled MCU dictionary.

The old RC7.1 builder checked a shortened validation report and could reject
valid firmware even though validate_build.check_dictionary checked its values.
Only the exact audited source is accepted; never disables or omits checks.
"""
from __future__ import annotations
import hashlib,sys
from pathlib import Path

OLD='c2fbfb15ee51a90f75b425f99149ed3c297c78e13de73f9e36133e4625fee818'
NEW='1b334834ee7b02b1b783ff06e151ad021296c57652273052f8c6e8662d3262f6'
BEFORE=b"if marker not in json.dumps(report['dictionary']):raise ValueError(f'Firmware lacks RC7 marker: {marker}')"
AFTER=b"if marker not in (root/'out/klipper.dict').read_text():raise ValueError(f'Firmware lacks RC7 marker: {marker}')"

def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()

def apply(kit:Path):
    path=kit/'scripts/build_firmware_on_mac.py'
    original=path.read_bytes()
    if sha(original)!=OLD or original.count(BEFORE)!=1:raise ValueError('Unexpected builder; refusal')
    fixed=original.replace(BEFORE,AFTER)
    if sha(fixed)!=NEW:raise ValueError('Patched builder hash mismatch')
    path.write_bytes(fixed)
    if sha(path.read_bytes())!=NEW:raise ValueError('Builder readback mismatch')
    print('PASS_RC7_2_DICTIONARY_FIX',NEW)

if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise ValueError('usage: apply_rc7_dict_fix.py KIT_DIRECTORY')
        apply(Path(sys.argv[1]).resolve())
    except (OSError,ValueError) as e:
        raise SystemExit('REFUSED: '+str(e))
