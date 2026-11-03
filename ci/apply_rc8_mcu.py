#!/usr/bin/env python3
"""Apply reviewed RC8 local services to the verified RC7.5 kit, fail closed."""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

REPO=Path(__file__).resolve().parents[1]
def sha(data):return hashlib.sha256(data).hexdigest()
def encoded(value):return (json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode()
def methods(data):
    source=data.decode();tree=ast.parse(source);out={}
    for item in tree.body:
        if isinstance(item,ast.ClassDef):
            for node in item.body:
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    out[item.name+'.'+node.name]=sha(ast.get_source_segment(source,node).encode())
    return out

def apply(kit,repo=REPO):
    kit=Path(kit).resolve();repo=Path(repo).resolve()
    meta=json.loads((repo/'ci/rc8_manifest.json').read_text());writes={}
    for name,expected in meta['contracts'].items():
        if sha((kit/name).read_bytes())!=expected:raise ValueError('Unknown RC8 contract input: '+name)
    hashes=json.loads((kit/'baseline/expected_hashes.json').read_text())
    base=kit/'baseline/SOURCE_FULL_KLIPPER_PINNED_P17_RC2.zip'
    if sha(base.read_bytes())!=hashes['SOURCE_FULL_KLIPPER_PINNED.zip']['sha256']:
        raise ValueError('Unrecognized original source archive')
    with zipfile.ZipFile(base) as z:
        for key,record in meta['mcu'].items():
            name=Path(key).name;source=repo/'mcu'/name;data=source.read_bytes()
            if sha(data)!=record['new_sha256']:raise ValueError('Changed RC8 MCU source: '+name)
            if record['kit_mcu_present']:
                before=(kit/'mcu'/name).read_bytes()
                if hashes[key]['new_sha256']!=record['old_sha256']:
                    raise ValueError('Unknown prior MCU manifest: '+name)
            else:
                archive_name='klipper-inventure/'+key
                before=z.read(archive_name) if archive_name in z.namelist() else None
            if (sha(before) if before is not None else None)!=record['old_sha256']:
                raise ValueError('Unknown prior MCU source: '+name)
            if key not in hashes:hashes[key]={'sha256':record['old_sha256']}
            hashes[key]['new_sha256']=record['new_sha256']
            writes[kit/'mcu'/name]=data;writes[kit/'regression_tests/overlay'/key]=data
    for key,record in meta['host'].items():
        name=Path(key).name;folder='host_core' if key=='klippy/serialhdl.py' else 'host'
        data=(repo/folder/name).read_bytes();dest=kit/folder/name
        if (sha(data)!=record['new_sha256'] or sha(dest.read_bytes())!=record['old_sha256']
                or hashes[key]['new_sha256']!=record['old_sha256']):
            raise ValueError('Unknown host transfer: '+name)
        hashes[key]['new_sha256']=record['new_sha256']
        writes[dest]=data
        if folder=='host':writes[kit/'regression_tests/overlay'/key]=data
    for key,allowed in meta['host_compat'].items():
        hashes[key]['allowed_existing']=sorted(set(hashes[key].get('allowed_existing',[]))|set(allowed))
    writes[kit/'baseline/expected_hashes.json']=encoded(hashes)
    for name in ('baseline/p0_module_contracts_RC7.json','regression_tests/config/p0_module_contracts.json'):
        c=json.loads((kit/name).read_text())
        for key in list(meta['mcu'])+list(meta['host']):
            rel='overlay/'+key;target=kit/'regression_tests'/rel
            if target not in writes:continue
            new=writes[target]
            if rel in c['protected_files']:
                if sha(target.read_bytes())!=c['protected_files'][rel]:raise ValueError('Unknown protected source: '+rel)
                c['protected_files'][rel]=sha(new)
            if rel in c['protected_methods']:
                previous=methods(target.read_bytes());after=methods(new)
                for method,value in c['protected_methods'][rel].items():
                    if previous.get(method)!=value:raise ValueError('Unknown protected method: '+method)
                    c['protected_methods'][rel][method]=after[method]
            if rel in c['masked_files']:
                rule=c['masked_files'][rel]
                old,count=re.subn(rule['pattern'],rule['replacement'],target.read_text())
                if count!=1 or sha(old.encode())!=rule['sha256']:raise ValueError('Unknown masked board input')
                changed,count=re.subn(rule['pattern'],rule['replacement'],new.decode())
                if count!=1:raise ValueError('Changed board branding contract')
                rule['sha256']=sha(changed.encode())
        for key in ('src/stm32/adc.c','src/stm32/inventure_v9.h','src/stm32/inventure_cooling.inc'):
            c['protected_files']['overlay/'+key]=meta['mcu'][key]['new_sha256']
        c['baseline_release']='RC8.0 local cooling and OLED recovery; 20 kHz sampler retained'
        writes[kit/name]=encoded(c)
    for name,record in meta['files'].items():
        data=(repo/name).read_bytes()
        if sha(data)!=record['new_sha256'] or sha((kit/name).read_bytes())!=record['old_sha256']:
            raise ValueError('Unknown reviewed build input: '+name)
        writes[kit/name]=data
    dest=kit/'regression_tests/tests/test_rc8_local_status.py'
    if dest.exists():raise ValueError('RC8 tests already installed')
    writes[dest]=(repo/'tests/regression/test_rc8_local_status.py').read_bytes()
    # Update assertions for the intentional new release and 128-byte row UI;
    # legacy session/fault/MCU-schema rejection tests remain in the suite.
    for name in ('tests/acceptance/test_rc7_dictionary_contract.py','tests/acceptance/test_zero3_fluidd.py'):
        s=(kit/name).read_text().replace('P01-P17-RC7.1-ZERO3','P01-P17-RC8.0-LOCAL')
        if name.endswith('dictionary_contract.py'):
            s=s.replace('test_all_three_baud_build_modes','test_all_five_baud_build_modes')
            s=s.replace('(0,1000000,2000000)','(0,1000000,1500000,2000000,3000000)')
            s=s.replace("('INVENTURE_P4_FORCED_BAUD',1500000)","('INVENTURE_P4_FORCED_BAUD',1750000)")
            s=s.replace("'INVENTURE_LOCAL_OLED_ABI':1,", "'INVENTURE_LOCAL_OLED_ABI':1,'INVENTURE_LOCAL_OLED_RECOVERY_ABI':1,'INVENTURE_LOCAL_OLED_BUFFER_BYTES':128,'INVENTURE_LOCAL_COOLING_ABI':1,'INVENTURE_LOCAL_COOLING_ON_C':50,'INVENTURE_LOCAL_COOLING_OFF_C':45,")
            s=s.replace("'inventure_local_oled_query'", "'inventure_local_oled_query','inventure_cooling_query'")
            s=s.replace("'inventure_local_oled_status state=%c boot_revision=%c baud=%u'", "'inventure_local_oled_status state=%c boot_revision=%c baud=%u','inventure_cooling_state clock=%u mode=%c reason=%c valid=%c mask=%c max_temp=%hi native_owner=%c adc_failures=%u gpio_updates=%u'")
        writes[kit/name]=s.encode()
    name='tests/test_rc6_local_oled_mock.c';s=(kit/name).read_text()
    start=s.index('    assert(local_frame[0]==0);');end=s.index('    for(unsigned i=0;i<400u',start)
    s=s[:start]+'    assert(sizeof(local_frame)==128);\n    local_render(12);\n    unsigned lit=0;for(unsigned i=0;i<128;i++)if(local_frame[i])lit++;\n    assert(lit>100);\n'+s[end:]
    writes[kit/name]=s.encode()
    for path,data in writes.items():path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (kit/'MCU_RELEASE_RC8.json').write_bytes(encoded(meta))
    print('PASS_RC8_LOCAL_SERVICES',len(writes),'reviewed files; APP only, no bootloader changes')

if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise ValueError('usage: apply_rc8_mcu.py KIT_DIRECTORY')
        apply(Path(sys.argv[1]))
    except (OSError,ValueError,KeyError) as exc:raise SystemExit('REFUSED: '+str(exc))
