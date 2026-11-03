#!/usr/bin/env python3
"""READ ONLY: independent check of Mac-generated RC7.1 ELF/BIN/dictionary/IVB/ZIP.

No firmware writing or serial commands. Uses the same checked image envelope,
but independently rechecks the bin/elf vectors and packed IVB bytes.
"""
from __future__ import annotations
import argparse,hashlib,json,struct,sys,zipfile
from pathlib import Path
from ibtool_reference import pack,inspect
from validate_build import check_elf,check_config,check_dictionary

def check(folder:Path)->dict:
    folder=folder.resolve()
    needed=('klipper.bin','klipper.elf','klipper.dict','INVENTUR.IVB',
            'build.config','BUILD_REPORT.json','SHA256SUMS','INVENTURE_RC8_0_SD_BOOT16.zip')
    missing=[n for n in needed if not (folder/n).is_file()]
    if missing:raise ValueError('Missing artifact: '+','.join(missing))
    raw=(folder/'klipper.bin').read_bytes()
    ivb=(folder/'INVENTUR.IVB').read_bytes()
    if pack(raw)!=ivb:raise ValueError('Packed BIN differs from IVB')
    img=inspect(ivb)
    elf=check_elf((folder/'klipper.elf').read_bytes(),raw)
    dict_report=check_dictionary(folder/'klipper.dict')
    cfg=check_config(folder/'build.config')
    wanted=int(cfg['CONFIG_SERIAL_BAUD'])
    forced=0 if wanted==250000 else wanted
    if int(dict_report['RC7_forced_baud'])!=forced:
        raise ValueError('Forced P4 baud does not match compiled config')
    dig=hashlib.sha256(ivb).hexdigest()
    lines=[l.split() for l in (folder/'SHA256SUMS').read_text().splitlines() if l.strip()]
    if [dig,'INVENTUR.IVB'] not in lines:raise ValueError('SHA256SUMS does not match IVB')
    with zipfile.ZipFile(folder/'INVENTURE_RC8_0_SD_BOOT16.zip') as z:
        if z.testzip() or z.namelist().count('INVENTUR.IVB')!=1 or z.read('INVENTUR.IVB')!=ivb:
            raise ValueError('SD package CRC/content mismatch')
    report=json.loads((folder/'BUILD_REPORT.json').read_text())
    if report.get('status')!='BUILD_VALIDATED_NOT_HARDWARE_TESTED':raise ValueError('Unexpected build status')
    return {'status':'PASS_BUILT_IMAGE_OFFLINE_NOT_HARDWARE_TESTED','folder':str(folder),
        'ivb_bytes':len(ivb),'ivb_sha256':dig,'build_cfg_baud':wanted,
        'MCU_release':'P01-P17-RC8.0-LOCAL',
        'reset_vector':elf['reset_vector'], 'application_base':hex(0x08010000),
        'dict_checks':len(dict_report),'no_hardware_test':True,'not_flashed':True}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('build_folder',type=Path)
    a=p.parse_args()
    try:print(json.dumps(check(a.build_folder),indent=2))
    except (OSError,ValueError,KeyError,struct.error,zipfile.BadZipFile) as e:
        print('RC8.0 BUILT IMAGE REJECTED:',e,file=sys.stderr);sys.exit(1)
