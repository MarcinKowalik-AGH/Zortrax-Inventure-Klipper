#!/usr/bin/env python3
"""Integrate reviewed host-only RC7.5 into the immutable RC7.2 build kit.

Validate every source and contract before writing anything. Only the four OLED
host modules, their contracts/tests and OLED example options may change.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[1]

def sha(data):return hashlib.sha256(data).hexdigest()

def revised_verifier(data):
    text=data.decode()
    changes={
        'Ran 567 tests':'Ran 588 tests',
        'P01_P17_RC7_unit_mock_566_PASS_1_':'P01_P17_RC7_unit_mock_587_PASS_1_',
        "'unit_tests':567":"'unit_tests':588",
        "'public_source_passed':566":"'public_source_passed':587",
        "'total_offline_python_tests':595":"'total_offline_python_tests':616",
        "'status':'PASS_OFFLINE_ONLY',":"'status':'PASS_OFFLINE_ONLY','host_release':'RC7.5-M300-ACK',",
    }
    for before,after in changes.items():
        if text.count(before)!=1:raise ValueError('Unexpected verifier contract: '+before)
        text=text.replace(before,after)
    return text.encode()

def apply(kit,repo=REPO):
    kit=Path(kit).resolve();repo=Path(repo).resolve()
    manifest=json.loads((repo/'ci/rc75_manifest.json').read_text())
    for name,expected in manifest['kit_inputs'].items():
        if sha((kit/name).read_bytes())!=expected:
            raise ValueError('Unknown kit input: '+name)
    # The readable MCU and serial overlays must remain exactly the same code
    # built from the snapshot plus the existing two audited GNU ARM fixes.
    for folder in ('mcu','host_core'):
        rc8_path=repo/'ci/rc8_manifest.json'
        rc8=json.loads(rc8_path.read_text())['mcu'] if rc8_path.exists() else {}
        for source in (repo/folder).iterdir():
            if source.is_file() and source.suffix in ('.c','.h','.inc','.py'):
                record=rc8.get('src/stm32/'+source.name) if folder=='mcu' else None
                if record:
                    if sha(source.read_bytes())!=record['new_sha256']:
                        raise ValueError('Unknown RC8 readable MCU: '+source.name)
                    previous=kit/folder/source.name
                    if record['kit_mcu_present'] and sha(previous.read_bytes())!=record['old_sha256']:
                        raise ValueError('Unknown RC8 baseline MCU: '+source.name)
                    if not record['kit_mcu_present'] and previous.exists():
                        raise ValueError('Unexpected new overlay already exists: '+source.name)
                    continue
                if source.read_bytes()!=(kit/folder/source.name).read_bytes():
                    raise ValueError('Readable/build overlay mismatch: '+str(source))
    writes={};hashes=json.loads((kit/'baseline/expected_hashes.json').read_text())
    for key,record in manifest['host'].items():
        name=Path(key).name;source=repo/'host'/name
        data=source.read_bytes()
        if sha(data)!=record['new_sha256']:raise ValueError('Unknown RC7.5 host: '+name)
        if hashes[key]['new_sha256']!=record['old_sha256']:
            raise ValueError('Unknown manifest host: '+name)
        for dest in (kit/'host'/name,kit/'regression_tests/overlay'/key):
            if sha(dest.read_bytes())!=record['old_sha256']:
                raise ValueError('Unknown baseline host: '+str(dest))
            writes[dest]=data
        hashes[key]['new_sha256']=record['new_sha256']
    writes[kit/'baseline/expected_hashes.json']=(json.dumps(hashes,indent=2,ensure_ascii=False)+'\n').encode()
    for name in ('baseline/p0_module_contracts_RC7.json',
                 'regression_tests/config/p0_module_contracts.json'):
        contract=json.loads((kit/name).read_text())
        for key,record in manifest['host'].items():
            path='overlay/'+key
            if path in contract['protected_files']:
                if contract['protected_files'][path]!=record['old_sha256']:
                    raise ValueError('Unknown protected OLED: '+path)
                contract['protected_files'][path]=record['new_sha256']
        contract['baseline_release']='RC7.5 M300 launcher and ACK pump; MCU RC7.1 unchanged'
        writes[kit/name]=(json.dumps(contract,indent=2,ensure_ascii=False)+'\n').encode()
    for name in ('test_rc7_oem_menu.py','test_rc73_m300_oled.py','test_rc74_oled_scheduling.py'):
        dest=kit/'regression_tests/tests'/name
        if name!='test_rc7_oem_menu.py' and dest.exists():
            raise ValueError('New test already exists: '+name)
        writes[dest]=(repo/'tests/regression'/name).read_bytes()
    writes[kit/'scripts/verify_release.py']=revised_verifier((kit/'scripts/verify_release.py').read_bytes())
    fragment=(repo/'config/OLED_FAST_RC7_5.cfg').read_bytes()
    writes[kit/'config/OLED_OEM_MENU_RC7_1_FRAGMENT_PL.txt']=fragment
    example=(kit/'config/printer_DIAGNOSTIC_RC2_example.cfg').read_text()
    for old,new in (('chunk_interval: 0.004','chunk_interval: 0.002'),
                    ('refresh_interval: 0.5','refresh_interval: 0.2'),
                    ('[inventure_oled]','[inventure_oled]\noem_menu: True\noem_start_screen: klipper\nmenu_profile: m300')):
        if example.count(old)!=1:raise ValueError('Unknown OLED example: '+old)
        example=example.replace(old,new)
    writes[kit/'config/printer_DIAGNOSTIC_RC2_example.cfg']=example.encode()
    # All validation above is complete. No actuator/printer config is rewritten.
    for path,data in writes.items():
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (kit/'HOST_RELEASE_RC7_5.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('PASS_RC7_5_OLED_HOST_ONLY',len(writes),'files; MCU and ABI unchanged')

if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise ValueError('usage: apply_rc75_oled.py KIT_DIRECTORY')
        apply(Path(sys.argv[1]))
    except (OSError,ValueError,KeyError) as exc:
        raise SystemExit('REFUSED: '+str(exc))
