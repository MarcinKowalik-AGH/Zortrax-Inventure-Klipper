#!/usr/bin/env python3
"""RC7.2 source-only GNU ARM fix for DECL_CTR __LINE__ collision.

Pinned original is verified before modification. The original RC7.1 source
archive stays immutable, and any unrecognized source fails closed.
"""
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path

OLD='bfad28e3bbdcc946032b4ced3c3eb9f584ee3628f3392c71b8e168655b50342b'
NEW='ab84cc81e82410cb3cc43b1408e1beda83e349576a5b90c74c832a8543a9c5b9'
INSERT=b'#line 1000 "inventure_oled_local.inc"\n'
KEY='src/stm32/inventure_oled_local.inc'

def checksum(b: bytes)->str:
    return hashlib.sha256(b).hexdigest()

def main(directory: Path)->None:
    root=directory.resolve()
    path=root/'mcu/inventure_oled_local.inc'
    hashes=root/'baseline/expected_hashes.json'
    current=path.read_bytes()
    source_sha=checksum(current)
    records=json.loads(hashes.read_text())
    expected=records[KEY]['new_sha256']
    if source_sha != OLD or expected != OLD:
        raise ValueError(f'Source mismatch: source={source_sha}, manifest={expected}, expected={OLD}')
    replacement=INSERT+current
    if checksum(replacement)!=NEW:
        raise ValueError('Patched source SHA-256 mismatch')
    records[KEY]['new_sha256']=NEW
    path.write_bytes(replacement)
    hashes.write_text(json.dumps(records,indent=2,ensure_ascii=False)+'\n')
    if checksum(path.read_bytes())!=NEW:
        raise ValueError('Readback failed')
    print('PASS_GNU_CTR_LINE_FIX',NEW)

if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise ValueError('usage: apply_gnu_ctr_fix.py KIT_PATH')
        main(Path(sys.argv[1]))
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as e:
        raise SystemExit('REFUSED: '+str(e))
