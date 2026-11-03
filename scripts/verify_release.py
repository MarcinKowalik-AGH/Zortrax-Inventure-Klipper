#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent RC7.1 offline validation. NEVER programs STM32 nor connects UART.

Final ARM link, flash and physical validation are separate; cannot be inferred
from native models or object compilation with Clang.
"""
from __future__ import annotations
import hashlib,json,shutil,subprocess,sys,tempfile,zipfile
from pathlib import Path
from build_firmware_on_mac import extract_pinned,apply_rc7
KIT=Path(__file__).resolve().parents[1]
sha=lambda data:hashlib.sha256(data).hexdigest()

def run(cmd,cwd=None):
    p=subprocess.run([str(x) for x in cmd],cwd=cwd,stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE,text=True,timeout=120)
    if p.returncode:
        raise RuntimeError('FAILED '+repr(cmd)+'\n'+p.stdout[-3000:]+p.stderr[-3000:])
    return p.stdout+p.stderr

def main():
    hashes=json.loads((KIT/'baseline/expected_hashes.json').read_text())
    hosts=sorted((KIT/'host').glob('*.py'))
    if len(hosts)!=14:raise ValueError('Host extras count changed without audit')
    for src in hosts:
        k='klippy/extras/'+src.name
        if sha(src.read_bytes())!=hashes[k]['new_sha256']:
            raise ValueError('Host hash mismatch: '+k)
    others={
       'src/stm32/adc.c':KIT/'mcu/adc.c',
       'src/stm32/inventure_v9.h':KIT/'mcu/inventure_v9.h',
       'src/stm32/inventure_cooling.inc':KIT/'mcu/inventure_cooling.inc',
       'klippy/serialhdl.py':KIT/'host_core/serialhdl.py',
       'src/generic/serial_irq.c':KIT/'mcu/serial_irq.c',
       'src/stm32/inventure_fans.inc':KIT/'mcu/inventure_fans.inc',
       'src/stm32/inventure_board.c':KIT/'mcu/inventure_board.c',
       'src/stm32/inventure_panel.c':KIT/'mcu/inventure_panel.c',
       'src/stm32/serial.c':KIT/'mcu/serial.c',
       'src/stm32/Makefile':KIT/'mcu/stm32_Makefile',
       'src/stm32/inventure_p4_baud.c':KIT/'mcu/inventure_p4_baud.c',
       'src/stm32/inventure_p4_baud.h':KIT/'mcu/inventure_p4_baud.h',
       'src/stm32/inventure_oled_local.inc':KIT/'mcu/inventure_oled_local.inc',
       'src/stm32/inventure_oled_stock_glyphs.inc':KIT/'mcu/inventure_oled_stock_glyphs.inc',
    }
    for path,src in others.items():
        if sha(src.read_bytes())!=hashes[path]['new_sha256']:
            raise ValueError('Overlay tampered: '+path)
    a=KIT/'host/inventure_oem_assets.json'
    if sha(a.read_bytes())!=hashes['klippy/extras/inventure_oem_assets.json']['new_sha256']:
        raise ValueError('RC7 OEM font/assets integrity failure')
    items=[]
    with tempfile.TemporaryDirectory(prefix='inventure-rc7-verify-') as td:
        t=Path(td);src,prior=extract_pinned(t,hashes)
        applied=apply_rc7(src,prior,hashes)
        if len(applied)!=29:raise ValueError('Expected exactly 29 source overlays, got '+str(len(applied)))
        items.append('RC7_source_29_overlays_integrity_PASS')
        for p in [*hosts, *others.values(), *(KIT/'scripts').glob('*.py')]:
            if p.suffix=='.py':compile(p.read_bytes(),str(p),'exec')
        items.append('Python_compile_PASS')
        cc=shutil.which('cc') or shutil.which('gcc')
        if not cc:raise RuntimeError('Native C compiler required for RC7 acceptance')
        native=[cc,'-std=gnu11','-O2','-Wall','-Wextra','-Werror']
        run(native+['-I',KIT/'tests/rc3_mock','-I',KIT/'tests/mock',
                    '-I',src/'src/generic','-I',src/'src/stm32',
                    KIT/'tests/test_uart_tx_ring_rc3.c','-o',t/'uart_ring'])
        items.append(run([t/'uart_ring']).strip())
        for rate in (2000000,500000):
            run(native+['-Wno-pointer-to-int-cast',f'-DOLED_TEST_SPI_RATE={rate}',
                       '-I',KIT/'mcu','-I',KIT/'tests/mock','-I',src/'src/stm32',
                       KIT/'tests/test_panel_mock.c','-o',t/f'panel{rate}'])
            items.append(run([t/f'panel{rate}']).strip())

        for name in ('test_rc6_local_oled_mock.c','test_rc6_local_mid_fault.c'):
            out=t/name.replace('.c','')
            run(native+['-Wno-pointer-to-int-cast','-I',KIT/'mcu',
                '-I',KIT/'tests/mock','-I',src/'src/stm32',
                KIT/'tests'/name,'-o',out])
            items.append(run([out]).strip())
        run(native+['-I',KIT/'mcu',KIT/'tests/test_rc7_boot_v15.c',
                    KIT/'mcu/inventure_p4_baud.c','-o',t/'p4_v15'])
        items.append(run([t/'p4_v15']).strip())
        run(native+['-I',KIT/'mcu',KIT/'tests/test_rc7_boot_v16.c',
                    KIT/'mcu/inventure_p4_baud.c','-o',t/'p4_v16'])
        items.append(run([t/'p4_v16']).strip())
        run(native+['-Wno-pointer-to-int-cast','-I',KIT/'tests/p4_mock',
                    '-I',KIT/'mcu',KIT/'tests/test_rc7_autobaud_2m.c',
                    KIT/'mcu/serial.c','-o',t/'p4_auto2m'])
        items.append(run([t/'p4_auto2m']).strip())
        run(native+['-Wno-pointer-to-int-cast','-DCONFIG_SERIAL_BAUD=1500000',
                    '-I',KIT/'tests/p4_mock','-I',KIT/'mcu',KIT/'tests/test_rc7_forced_baud.c',
                    KIT/'mcu/serial.c','-o',t/'p4_forced1500k'])
        items.append(run([t/'p4_forced1500k']).strip())
        run(native+['-I',KIT/'mcu',KIT/'tests/test_p4_boot_journal_rc5.c',
                    KIT/'mcu/inventure_p4_baud.c','-o',t/'p4_journal'])
        items.append(run([t/'p4_journal']).strip())
        run(native+['-Wno-pointer-to-int-cast','-I',KIT/'tests/p4_mock','-I',KIT/'mcu',
                    KIT/'tests/test_p4_serial_init_mock.c',KIT/'mcu/serial.c',
                    '-o',t/'p4_serial'])
        items.append(run([t/'p4_serial']).strip())
        clang=shutil.which('clang')
        arm='NOT_RUN'
        if clang:
            run([clang,'--target=arm-none-eabi','-mcpu=cortex-m3','-mthumb',
                 '-Os','-ffreestanding','-Wall','-Wextra','-Werror','-I',KIT/'mcu',
                 '-c',KIT/'mcu/inventure_p4_baud.c','-o',t/'p4.arm.o'])
            # Verify the modified USART6 serial_init source with the same ARM
            # target, using a minimal model of the platform headers. Not a link.
            run([clang,'--target=arm-none-eabi','-mcpu=cortex-m3','-mthumb',
                 '-Os','-ffreestanding','-Wall','-Wextra','-Werror',
                 '-I',KIT/'tests/p4_mock','-I',KIT/'mcu','-c',
                 KIT/'mcu/serial.c','-o',t/'serial-p4.arm.o'])
            run([clang,'--target=arm-none-eabi','-mcpu=cortex-m3','-mthumb',
                 '-Os','-ffreestanding','-Wall','-Wextra','-Werror',
                 '-Wno-pointer-to-int-cast','-I',KIT/'tests/arm_mock',
                 '-I',KIT/'tests/mock','-I',KIT/'mcu',
                 '-I',src/'src/stm32','-c',
                 KIT/'mcu/inventure_panel.c','-o',t/'oled-rc7.arm.o'])
            arm='P4_USART6_AND_RC7_LOCAL_OLED_ARM_EABI_OBJECTS_PASS' 
        items.append(arm)
    p=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py'],
                     cwd=KIT/'regression_tests',capture_output=True,text=True,timeout=120)
    log=p.stdout+p.stderr
    (KIT/'LATEST_RC7_FULL_REGRESSION.log').write_text(log)
    if (p.returncode or 'Ran 592 tests' not in log or
            not log.rstrip().endswith('OK (skipped=1)') or
            'FAILED (' in log):
        raise RuntimeError('RC7 regression failure: '+log[-4000:])
    items.append('P01_P17_RC7_unit_mock_591_PASS_1_OEM_FLASH_COMPARISON_SKIPPED_PUBLIC_CI')
    # Independent test of 29 default Fluidd macros and 19 explicit opt-in
    # procedures, MCU version/epoch and Zero3 staging contracts.
    p=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(KIT/'tests/acceptance'),'-p','test_*.py'],
                     cwd=KIT,capture_output=True,text=True,timeout=50)
    alog=p.stdout+p.stderr
    (KIT/'LATEST_RC7_1_ACCEPTANCE.log').write_text(alog)
    if p.returncode or 'Ran 28 tests' not in alog or not alog.rstrip().endswith('OK'):
        raise RuntimeError('RC7.1 Fluidd/Zero3 acceptance failure: '+alog[-4000:])
    items.append('RC7_1_Zero3_Fluidd_STEP_Validator_28_of_28_PASS')
    # Rebuilding an already validated local RC7 checkout is allowed.
    # Previously generated dist/ images are NEVER accepted as a substitute
    # for a new source build; every build still links and verifies its own ELF.
    if (KIT/'dist').exists():
        items.append('Existing local dist retained; new BUILD requires fresh link and dictionary')
    report={'release':'P01_P17_RC8_0_LOCAL_SERVICES','status':'PASS_OFFLINE_ONLY','host_release':'RC7.6-KLIPPER-GRAPHICS','mcu_release':'P01-P17-RC8.0-LOCAL',
      'p4_journal':'r1/r2/1.5-HW2 + authenticated 1.6-RC2 r3; eight read-compatible baud codes, separate bootwait records, last valid baud, fallback 250000',
      'runtime_uart': [19200,57600,115200,250000,500000,750000,1000000,2000000],
      'optional_compiled_uart': [1000000,2000000],
      'high_baud_journal_mapping_verified':True,
      'boot_v16_sha256':'577d1780fec0d423af54662da2580139d438b1757ff7ba68712fa73143a665ec',
      'source_overlay_count':29,'macos_arm_link_verified':False,
      'unit_tests':592,'public_source_passed':591,'private_oem_reference_skipped':1,'acceptance_tests':28,'total_offline_python_tests':620,'fluidd_diagnostic_macros':29,'fluidd_optin_action_macros':19,
      'arm_helper_object':arm=='P4_USART6_AND_RC7_LOCAL_OLED_ARM_EABI_OBJECTS_PASS',
      'gnu_arm_full_link':False,'verified_new_ivb':False,
      'hardware_test':False,'safe_to_flash_as_precompiled':False,'zero3_runtime_test':False,
      'scope':items}
    (KIT/'VALIDATION_REPRODUCIBLE_RC7.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,zipfile.BadZipFile,subprocess.TimeoutExpired) as e:
        print('RC7.1 VALIDATION FAILED:',e,file=sys.stderr);sys.exit(1)
