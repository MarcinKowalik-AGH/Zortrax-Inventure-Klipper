#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline, fail-closed Klipper MCU build for the user's Inventure V9.

This builder NEVER flashes anything. A new .IVB appears ONLY after GNU ARM
compiler, dictionary, vectors, image, slot and all overlay hashes validate.
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, os, platform, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
from validate_build import check_config, validate

KIT=Path(__file__).resolve().parents[1]
BASE=KIT/'baseline/SOURCE_FULL_KLIPPER_PINNED_P17_RC2.zip'
HIST=KIT/'baseline/expected_hashes.json'
MAP={
    'src/generic/serial_irq.c': KIT/'mcu/serial_irq.c',
    'src/stm32/inventure_fans.inc': KIT/'mcu/inventure_fans.inc',
    'src/stm32/inventure_board.c': KIT/'mcu/inventure_board.c',
    'src/stm32/inventure_panel.c': KIT/'mcu/inventure_panel.c',
    'src/stm32/serial.c': KIT/'mcu/serial.c',
    'src/stm32/Makefile': KIT/'mcu/stm32_Makefile',
    'src/stm32/inventure_p4_baud.c': KIT/'mcu/inventure_p4_baud.c',
    'src/stm32/inventure_p4_baud.h': KIT/'mcu/inventure_p4_baud.h',
    'src/stm32/inventure_oled_local.inc': KIT/'mcu/inventure_oled_local.inc',
    'src/stm32/inventure_oled_stock_glyphs.inc': KIT/'mcu/inventure_oled_stock_glyphs.inc',
}
PIN='ee2c6fb6b00b2b5d097ef70d0868df7d74943b51'


def sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()

def doctor() -> dict:
    if sys.version_info < (3,11): raise ValueError('Python >= 3.11 is required')
    tools={}
    for name in ['arm-none-eabi-gcc','arm-none-eabi-cpp','arm-none-eabi-as',
                 'arm-none-eabi-objcopy','arm-none-eabi-ld','arm-none-eabi-objdump']:
        p=shutil.which(name)
        if not p: raise ValueError(f'Missing {name}. On macOS: brew install --cask gcc-arm-embedded')
        tools[name]=p
    make=shutil.which('gmake') or shutil.which('make')
    if not make:raise ValueError('GNU make is missing (brew install make)')
    v=subprocess.check_output([make,'--version'],text=True,stderr=subprocess.STDOUT)
    if 'GNU Make' not in v:raise ValueError('GNU make is required')
    tools['make']=make
    nano=subprocess.check_output([tools['arm-none-eabi-gcc'],'--print-file-name=libc_nano.a'],text=True).strip()
    if nano=='libc_nano.a' or not Path(nano).is_file():raise ValueError('ARM libc_nano.a missing: install full gcc-arm-embedded')
    with tempfile.TemporaryDirectory(prefix='inventure-v9-cc-check-') as temp:
        p=Path(temp);(p/'check.c').write_text('int check(void){return 42;}\n')
        subprocess.run([tools['arm-none-eabi-gcc'],'-mcpu=cortex-m3','-mthumb','-c',str(p/'check.c'),'-o',str(p/'check.o')],check=True)
    return {'platform':platform.platform(),'python':sys.version.split()[0],
            'gcc':subprocess.check_output([tools['arm-none-eabi-gcc'],'--version'],text=True).splitlines()[0],
            'tools':tools}


def extract_pinned(target: Path, expected: dict):
    if sha(BASE.read_bytes()) != expected['SOURCE_FULL_KLIPPER_PINNED.zip']['sha256']:
        raise ValueError('RC2 source ZIP SHA-256 mismatch; refusing to build')
    # Only unmodified original files, as protected by the P13/RC2 source manifest.
    with zipfile.ZipFile(BASE) as z:
        bad=z.testzip()
        if bad:raise ValueError(f'Corrupt base ZIP member: {bad}')
        names=z.namelist()
        for name in names:
            parts=Path(name).parts
            if not parts or parts[0]!='klipper-inventure' or '..' in parts or name.startswith('/') or '\\' in name:
                raise ValueError(f'Unsafe source archive path: {name}')
        z.extractall(target)
        # Python zipfile.extractall() does not restore Unix execute bits.
        # The pinned archive is SHA-256 verified and its paths are checked above.
        # Restore only execute bits recorded in that authenticated archive;
        # never run chmod on any existing Klipper installation.
        for member in z.infolist():
            if member.is_dir():
                continue
            if (member.external_attr >> 16) & 0o111:
                extracted = target / member.filename
                extracted.chmod(extracted.stat().st_mode | 0o111)
    root=target/'klipper-inventure'
    if not (root/'Makefile').is_file():raise ValueError('Source missing Makefile')
    original=json.loads((root/'.inventure-v9-patch.json').read_text())
    if original.get('upstream')!=PIN:raise ValueError('Unexpected Klipper pinned upstream')
    for path, source in MAP.items():
        before=root/path
        baseline_sha=expected[path]['sha256']
        if baseline_sha is None:
            if before.exists():raise ValueError(f'New RC7 module unexpectedly exists in pinned baseline: {path}')
        elif sha(before.read_bytes())!=baseline_sha:
            raise ValueError(f'Unknown base MCU file: {path}')
    if sha((root/'klippy/serialhdl.py').read_bytes())!=expected['klippy/serialhdl.py']['sha256']:
        raise ValueError('Unknown pinned Klipper serialhdl.py')
    if sha((root/'klippy/mcu.py').read_bytes())!=expected['klippy/mcu.py']['sha256']:
        raise ValueError('Unexpected Klippy API version')
    for path in expected:
        if not path.startswith('klippy/extras/') or path not in original.get('patched_sha256',{}):continue
        if sha((root/path).read_bytes())!=expected[path]['sha256']:
            raise ValueError(f'Unknown base host module: {path}')
    return root,original


def apply_rc7(root: Path,original:dict,expected:dict):
    changed={}
    for path, source in MAP.items():
        data=source.read_bytes()
        if sha(data)!=expected[path]['new_sha256']:raise ValueError(f'Release MCU payload tampered: {path}')
        dest=root/path;dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(data);changed[path]=sha(data)
    serialhdl=KIT/'host_core/serialhdl.py'
    path='klippy/serialhdl.py'
    if sha(serialhdl.read_bytes())!=expected[path]['new_sha256']:
        raise ValueError('RC7 serial scheduler source tampered')
    (root/path).write_bytes(serialhdl.read_bytes());changed[path]=sha(serialhdl.read_bytes())
    for source in sorted((KIT/'host').glob('*.py')):
        path='klippy/extras/'+source.name
        expected_sha=expected.get(path,{}).get('new_sha256')
        data=source.read_bytes()
        if not expected_sha or sha(data)!=expected_sha:raise ValueError(f'Release host payload tampered: {path}')
        dst=root/path;dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_bytes(data);changed[path]=sha(data)
    # OEM font/icons are DATA, not executable code. Hash-match before copying.
    source=KIT/'host/inventure_oem_assets.json'
    path='klippy/extras/inventure_oem_assets.json'
    data=source.read_bytes()
    if sha(data)!=expected[path]['new_sha256']:
        raise ValueError('OEM r0323 asset hash mismatch')
    (root/path).write_bytes(data);changed[path]=sha(data)
    config=(KIT/'config/inventure_v9.defconfig').read_bytes()
    (root/'.config').write_bytes(config)
    shutil.copy2(KIT/'baseline/p0_module_contracts_RC7.json',root/'config/p0_module_contracts.json')
    original['version']='P01_P17_RC7_1_OEM_UI_ZERO3'
    original['release_kind']='LOCAL_OFFLINE_PATCH_UNFLASHED'
    original['patched_sha256'].update(changed)
    original['release_kit_sha256']=sha(HIST.read_bytes())
    (root/'.inventure-v9-patch.json').write_text(json.dumps(original,indent=2,sort_keys=True)+'\n')
    # Pins/bootloader regions unaffected; no installer touches printer.cfg.
    return changed


def run(cmd,cwd):
    print('+', ' '.join(map(str,cmd)),flush=True)
    subprocess.run([str(x) for x in cmd],cwd=cwd,check=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--doctor',action='store_true',help='Only test local compiler')
    ap.add_argument('--verify-source',action='store_true',help='Verify bundle and payload hashes without compiler')
    ap.add_argument('--jobs',type=int,default=4)
    ap.add_argument('--baud',type=int,choices=(250000,1000000,2000000),default=250000,help='250000 reads InventureBoot journal; 1M/2M explicit nonpersistent MCU test variant')
    a=ap.parse_args()
    if not 1<=a.jobs<=16:raise ValueError('Parallel jobs must be 1..16')
    expected=json.loads(HIST.read_text())
    if a.doctor:
        print(json.dumps(doctor(),indent=2));return
    # TEMP source never touches any existing installation, Klipper or flash.
    with tempfile.TemporaryDirectory(prefix='inventure-rc7-build-') as t:
        root,original=extract_pinned(Path(t),expected)
        changed=apply_rc7(root,original,expected)
        if a.baud!=250000:
            cfg=root/'.config'
            raw=cfg.read_text()
            assert 'CONFIG_SERIAL_BAUD=250000' in raw
            cfg.write_text(raw.replace('CONFIG_SERIAL_BAUD=250000',f'CONFIG_SERIAL_BAUD={a.baud}'))
        print('Pinned source RC2 + all RC7 overlays verified:',len(changed),'files')
        if a.verify_source:return
        print('Running full release verification before ARM compilation...',flush=True)
        subprocess.run([sys.executable,str(KIT/'scripts/verify_release.py')],check=True)
        env=doctor(); opts=[f'CPP={env["tools"]["arm-none-eabi-cpp"]}',
                           f'PYTHON={sys.executable}','CROSS_PREFIX=arm-none-eabi-']
        make=env['tools']['make']
        run([make,'olddefconfig',*opts],root)
        check_config(root/'.config')
        # Prevent any obsolete command dictionary from surviving protocol changes.
        if (root/'out').exists():shutil.rmtree(root/'out')
        run([make,'clean',*opts],root)
        run([make,f'-j{a.jobs}',*opts],root)
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        output=KIT/'dist'/f'RC7_1_{stamp}_{os.getpid()}_BAUD{a.baud}'
        report=validate(root,output)
        for marker in ('INVENTURE_TX_RING_ABI','INVENTURE_OLED_REPLAY_ABI','INVENTURE_FAN_BATCH_ABI','INVENTURE_OLED_SPI_BURST_ABI', 'INVENTURE_P4_BOOT_JOURNAL_ABI', 'INVENTURE_LOCAL_OLED_ABI','INVENTURE_MCU_RELEASE','INVENTURE_RC7_ADC_SNAPSHOT_ABI','INVENTURE_P4_FORCED_BAUD','inventure_local_oled_query'):
            if marker not in (root/'out/klipper.dict').read_text():raise ValueError(f'Firmware lacks RC7 marker: {marker}')
        # Check after pack, and only then leave image in dist.
        (output/'TOOLCHAIN_RC7.json').write_text(json.dumps(env,indent=2)+'\n')
        (output/'RELEASE_RC7.json').write_text(json.dumps({'status':'COMPILED_NOT_HARDWARE_QUALIFIED','source_hashes':changed,'reference_zip_sha256':sha(BASE.read_bytes()),'physical_tests':False,'baud_requested':a.baud},indent=2)+'\n')
        # SD-ready ZIP is emitted only after a successful GNU ARM build and
        # all vector, ABI, dictionary and image integrity checks. It contains
        # the new IVB, NEVER the old RC2 binary or vendor InventureUpdate.bin.
        ivb=output/'INVENTUR.IVB'
        note=('INVENTURE RC7.1 application update for custom InventureBoot 1.4/1.5/1.6-RC2.\n'
              'STILL NOT HARDWARE QUALIFIED. Use only after complete Flash/NVM backup.\n'
              'Extract INVENTUR.IVB to FAT32 SD root. Do not rename to InventureUpdate.bin.\n')
        (output/'README_SD_FLASH.txt').write_text(note)
        digest=sha(ivb.read_bytes())
        (output/'SHA256SUMS').write_text(digest+'  INVENTUR.IVB\n')
        sdzip=output/'INVENTURE_RC7_1_SD_BOOT16.zip'
        with zipfile.ZipFile(sdzip,'w',zipfile.ZIP_DEFLATED) as z:
            for file in ('INVENTUR.IVB','README_SD_FLASH.txt','SHA256SUMS'):
                z.write(output/file,arcname=file)
        with zipfile.ZipFile(sdzip) as z:
            if z.testzip() or z.read('INVENTUR.IVB')!=ivb.read_bytes():
                raise ValueError('RC7 SD flash archive integrity failure')
        print('SD card update archive:',sdzip)
        print('BUILD COMPLETE - NOT FLASHED:',output)
        print('Validated image:',output/'INVENTUR.IVB')
        print('No stock InventureUpdate.bin produced. No flash or activation performed.')

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,subprocess.CalledProcessError,zipfile.BadZipFile) as e:
        print('RC7 BUILD STOPPED:',e,file=sys.stderr)
        sys.exit(1)
