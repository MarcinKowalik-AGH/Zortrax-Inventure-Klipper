#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate exact board target, ELF load spans, BIN vectors, and IVB integrity."""
from __future__ import annotations
import argparse, hashlib, json, struct
from pathlib import Path
from ibtool_reference import pack, inspect, validate_vectors, BASE, LIMIT

EXPECTED={
 'CONFIG_MACH_STM32':'y','CONFIG_MACH_STM32F207':'y','CONFIG_INVENTURE_V9':'y',
 'CONFIG_STM32_FLASH_START_10000':'y','CONFIG_STM32_CLOCK_REF_25M':'y',
 'CONFIG_STM32_SERIAL_USART6_ALT_PG9_PG14':'y','CONFIG_SERIAL_BAUD':'250000',
 'CONFIG_CLOCK_FREQ':'120000000','CONFIG_FLASH_APPLICATION_ADDRESS':'0x8010000',
 'CONFIG_FLASH_SIZE':'0x2ff00','CONFIG_INITIAL_PINS':'""',
 'CONFIG_WANT_ADC':'y','CONFIG_WANT_SPI':'y','CONFIG_WANT_HARD_PWM':'y',
 'CONFIG_WANT_BUTTONS':'y',
}

def read_config(path: Path) -> dict:
    return dict(line.split('=',1) for line in path.read_text().splitlines()
                if line.startswith('CONFIG_') and '=' in line)

def check_config(path: Path) -> dict:
    cfg=read_config(path)
    for k,v in EXPECTED.items():
        actual=cfg.get(k)
        if k=='CONFIG_SERIAL_BAUD':
            ok=actual in ('250000','1000000','1500000','2000000')
        elif k in ['CONFIG_FLASH_APPLICATION_ADDRESS','CONFIG_FLASH_SIZE']:
            ok=actual is not None and int(actual,16)==int(v,16)
        else:ok=actual==v
        if not ok: raise ValueError(f'Invalid target: {k}={actual!r}, expected {v!r}')
    if cfg.get('CONFIG_WANT_STEPPER_OPTIMIZED_BOTH_EDGE')=='y':
        raise ValueError('Both-edge stepping is not verified on V9')
    return cfg

def check_elf(data: bytes, raw: bytes) -> dict:
    if len(data)<52 or data[:7]!=b'\x7fELF\x01\x01\x01':raise ValueError('Not ELF32 little-endian')
    hdr=struct.unpack_from('<16sHHIIIIIHHHHHH',data)
    _,typ,machine,version,entry,phoff,shoff,flags,ehsize,phsize,phnum,shsize,shnum,shstr=hdr
    if machine!=40 or typ!=2 or version!=1 or phsize!=32:raise ValueError('Unexpected ARM ELF header')
    if not phnum or phoff<52 or phoff+phsize*phnum>len(data):raise ValueError('Invalid or truncated program headers')
    # ARM Cortex-M boots using the vector table, *not* ELF e_entry.
    # Upstream armcm_link.lds.S intentionally does not specify ENTRY(); GNU ld
    # can set e_entry to the start of .text (the vector table itself).
    # The real reset entry is the second word of the flash image, Thumb bit=1.
    msp, reset_vector = validate_vectors(raw)
    reset_addr = reset_vector & ~1
    spans=[]; flash_bytes=0; exec_spans=[]
    for i in range(phnum):
        ptype,off,vaddr,paddr,filesz,memsz,pflags,align=struct.unpack_from('<8I',data,phoff+i*phsize)
        if ptype!=1:continue
        if filesz and (pflags & 1): exec_spans.append((vaddr,vaddr+filesz))
        if off+filesz>len(data) or filesz>memsz:raise ValueError('Invalid ELF load segment')
        if filesz:
            if not BASE<=paddr or paddr+filesz>BASE+LIMIT:raise ValueError('ELF writes outside application slot')
            flash_bytes += filesz
            start=paddr-BASE
            if start+filesz>len(raw) or raw[start:start+filesz]!=data[off:off+filesz]:
                raise ValueError('ELF/BIN load bytes differ')
        if memsz and not ((BASE<=vaddr and vaddr+memsz<=BASE+LIMIT) or
                          (0x20000000<=vaddr and vaddr+memsz<=0x20020000)):
            raise ValueError('ELF virtual address/RAM overflow')
        spans.append({'vaddr':hex(vaddr),'paddr':hex(paddr),'file_bytes':filesz,'memory_bytes':memsz})
    if not flash_bytes:raise ValueError('ELF has no application load bytes')
    if not any(a <= reset_addr < b for a,b in exec_spans):
        raise ValueError('Reset vector does not point into an executable ELF segment')
    symbol = find_symbol(data, shoff, shsize, shnum, 'ResetHandler')
    if symbol is not None and (symbol & ~1) != reset_addr:
        raise ValueError('ResetHandler symbol does not match the vector table')
    return {'elf_entry_informational_only':hex(entry), 'reset_vector':hex(reset_vector),
            'reset_address':hex(reset_addr), 'reset_symbol':None if symbol is None else hex(symbol),
            'initial_msp':hex(msp), 'segments':spans,
            'metadata_address':'0x0803ff00'}

def find_symbol(data:bytes,shoff:int,shsize:int,shnum:int,target:str)->int|None:
    # Check the normal ELF32 SHT_SYMTAB when available (unstripped Klipper ELF).
    # A stripped file still needs a valid flash vector and executable PT_LOAD.
    if not shnum or not shoff: return None
    if shsize < 40 or shoff < 52 or shoff+shsize*shnum > len(data):
        raise ValueError('Invalid ELF section headers')
    sections=[struct.unpack_from('<10I',data,shoff+i*shsize) for i in range(shnum)]
    for section in sections:
        _,stype,_,_,off,size,link,_,_,entsize=section
        if stype!=2:continue  # SHT_SYMTAB
        if link>=shnum or entsize<16 or off+size>len(data):
            raise ValueError('Invalid ELF symbol table')
        strings=sections[link]
        if strings[1]!=3 or strings[4]+strings[5]>len(data):
            raise ValueError('Invalid ELF string table')
        st=data[strings[4]:strings[4]+strings[5]]
        for i in range(off,off+size,entsize):
            if i+16>len(data):raise ValueError('Truncated ELF symbol record')
            name,value,_,_,_,_=struct.unpack_from('<IIIBBH',data,i)
            if name>=len(st):continue
            end=st.find(b'\0',name)
            if end<0:continue
            if st[name:end].decode('utf-8',errors='replace')==target:return value
    return None

def check_rc5_p4_protocol(dictionary: dict) -> dict:
    """Validate the ACTUAL compiled MCU dictionary, not a shortened report.

    These constants are emitted by DECL_CONSTANT in stm32/serial.c and the
    command/response schemas are emitted by DECL_COMMAND_FLAGS/sendf.
    Missing or changed values must stop packaging BEFORE any IVB is written.
    """
    conf = dictionary.get('config', {})
    expected = {
        'INVENTURE_P4_BOOT_JOURNAL_ABI': 1,
        'INVENTURE_P4_BOOT_BASE': 0x08000000,
        'INVENTURE_P4_JOURNAL_BASE': 0x08007F00,
    }
    for name, value in expected.items():
        raw = conf.get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            raise ValueError(f'RC5 MCU dictionary missing or invalid {name}: {raw!r}')
        try:
            parsed = int(raw, 0) if isinstance(raw, str) else raw
        except ValueError:
            raise ValueError(f'RC5 MCU dictionary invalid numeric value {name}: {raw!r}')
        if parsed != value:
            raise ValueError(f'RC5 MCU dictionary mismatch {name}: {raw!r} (expected {value})')
    command = 'inventure_p4_baud_query'
    response = 'inventure_p4_baud baud=%u revision=%c saved=%c index=%c slots=%hu invalid=%hu'
    if command not in dictionary.get('commands', {}):
        raise ValueError('RC5 MCU dictionary missing P4 query command')
    if response not in dictionary.get('responses', {}):
        raise ValueError('RC5 MCU dictionary missing P4 baud response schema')
    return {'RC5_constants': expected,
            'RC5_command_schema': command,
            'RC5_response_schema': response}

def check_dictionary(path: Path) -> dict:
    d=json.loads(path.read_text());text=json.dumps(d)
    abi=d.get('config',{}).get('INVENTURE_ABI')
    if str(abi) not in ('3','3.0'):
        raise ValueError('MCU dictionary ABI is not 3: '+str(abi))
    if str(d.get('config',{}).get('INVENTURE_FANS_ABI')) not in ('1','1.0'):
        raise ValueError('MCU dictionary requires INVENTURE_FANS_ABI=1')
    if str(d.get('config',{}).get('INVENTURE_TRANSPORT_ABI')) not in ('1','1.0'):
        raise ValueError('MCU dictionary requires INVENTURE_TRANSPORT_ABI=1')
    transport_commands=[
        'inventure_transport_profile channel=%c cartridge=%c duration_ms=%hu budget_steps=%u',
        'inventure_transport_lease acknowledge=%u',
        'inventure_transport_mode channel=%c mode=%c monitor=%c',
        'inventure_transport_select channel=%c',
        'inventure_transport_assist channel=%c clock=%u duration_hi=%u duration_lo=%u',
        'inventure_transport_query']
    transport_response='inventure_transport_status clock=%u channel=%c live=%c a=%c b=%c pulses=%u last_pulse_clock=%u starts=%u mode=%c running=%c direction=%c retry=%c fault=%c budget=%i forward=%u reverse=%u assist=%c locks=%c'
    if any(f not in d.get('commands',{}) for f in transport_commands) or transport_response not in d.get('responses',{}):raise ValueError('P13 exact transport ABI1 schema mismatch')
    fan_fmt='inventure_fan_status clock=%u window_clock=%u sequence=%u channel=%c duty=%c window_duty=%c edges=%u normalized=%u bad=%u peak=%u fault_mask=%c mixed=%c required_mask=%c'
    if fan_fmt not in d.get('responses',{}):
        raise ValueError('P06 exact fan response schema mismatch in built dictionary')
    if str(d.get('config',{}).get('INVENTURE_OLED_ABI')) not in ('2','2.0'):
        raise ValueError('MCU dictionary requires INVENTURE_OLED_ABI=2')
    oled_commands=[
        'inventure_oled_begin generation_hi=%u generation_lo=%u token=%u',
        'inventure_oled_write generation_hi=%u generation_lo=%u token=%u dc=%c data=%*s',
        'inventure_oled_control generation_hi=%u generation_lo=%u token=%u reset=%c gate=%c',
        'inventure_oled_ping generation_hi=%u generation_lo=%u',
        'inventure_oled_stop','inventure_oled_query','inventure_oled_config rate=%u']
    oled_responses=[
        'inventure_oled_ack generation_hi=%u generation_lo=%u token=%u result=%c',
        'inventure_oled_state clock=%u generation_hi=%u generation_lo=%u configured=%c active=%c latched=%c fault=%c reset=%c']
    for group,formats in [('commands',oled_commands),('responses',oled_responses)]:
        for fmt in formats:
            if fmt not in d.get(group,{}):raise ValueError('OLED_ABI2 exact schema mismatch: '+fmt)
    required=['inventure_heartbeat','inventure_arm','inventure_query','inventure_feeder',
        'inventure_lock','inventure_uart_send','spi2_inventure_oled','spi3_inventure_sd',
        'PG9','PG14','INVENTURE_ABI','inventure_oled_write',
        'INVENTURE_FANS_ABI','inventure_fan_query','inventure_fan_status',
        'inventure_oled_control','inventure_oled_config','inventure_inputs_query','inventure_rgb']
    missing=[x for x in required if x not in text]
    if missing:raise ValueError('MCU command dictionary missing: '+', '.join(missing))
    # RC4: a P13/RC3 dictionary must NEVER be packaged as a new OLED/UART update.
    rc4_constants={
        'INVENTURE_TX_RING_ABI': 1,
        'INVENTURE_UART_TX_BYTES': 1024,
        'INVENTURE_UART_RX_BYTES': 240,
        'INVENTURE_FAN_BATCH_ABI': 1,
        'INVENTURE_OLED_REPLAY_ABI': 1,
        'INVENTURE_OLED_SPI_BURST_ABI': 1,
    }
    conf=d.get('config',{})
    for name, value in rc4_constants.items():
        if str(conf.get(name)) != str(value):
            raise ValueError(f'RC4 binary dictionary mismatch: {name}={conf.get(name)!r} (expected {value})')
    new_commands=[
        'inventure_fan_query_batch group=%c',
        'inventure_uart_buffers_query',
    ]
    new_responses=[
        'inventure_uart_buffers pending=%hu highwater=%hu dropped=%u rxpending=%c',
    ]
    for fmt in new_commands:
        if fmt not in d.get('commands',{}):raise ValueError('RC4 missing MCU command: '+fmt)
    for fmt in new_responses:
        if fmt not in d.get('responses',{}):raise ValueError('RC4 missing MCU response: '+fmt)
    rc5 = check_rc5_p4_protocol(d)
    if str(d.get('config',{}).get('INVENTURE_RC7_ADC_SNAPSHOT_ABI')) not in ('1','1.0'):
        raise ValueError('RC7 MCU dictionary missing ADC snapshot ABI')
    forced=d.get('config',{}).get('INVENTURE_P4_FORCED_BAUD')
    if str(forced) not in ('0','1000000','1500000','2000000'):
        raise ValueError('RC7 MCU dictionary wrong forced baud')
    new_config = {'INVENTURE_LOCAL_OLED_ABI': 1,
                  'INVENTURE_LOCAL_OLED_RECOVERY_ABI':1,
                  'INVENTURE_LOCAL_OLED_BUFFER_BYTES':128,
                  'INVENTURE_LOCAL_COOLING_ABI':1,
                  'INVENTURE_LOCAL_COOLING_ON_C':50,
                  'INVENTURE_LOCAL_COOLING_OFF_C':45,
                  'INVENTURE_MCU_RELEASE': 'RC8-CONTROL-R7-FACTORY-SPEED'}
    for name, value in new_config.items():
        if str(conf.get(name)) != str(value):
            raise ValueError(f'RC7 firmware version/local OLED dictionary mismatch: {name}')
    if (str(conf.get('INVENTURE_FACTORY_ABI')) != '1'
            or str(conf.get('INVENTURE_FACTORY_FEED_CLOCK')) != '1'):
        raise ValueError('R7 missing leased factory procedure ABI')
    factory_motion = {'INVENTURE_FACTORY_MOTION_ABI': 1,
                      'INVENTURE_FACTORY_MOTION_MAX_HZ': 125000,
                      'INVENTURE_CONTROL_MAX_HZ': 20000}
    for name, value in factory_motion.items():
        if conf.get(name) != value:
            raise ValueError(f'R7 factory motion dictionary mismatch: {name}')
    factory_command='inventure_factory_session action=%c tool=%c mode=%c monitor=%c duration_ms=%hu budget_steps=%u lease_ms=%hu min_c=%hi max_c=%hi token=%u sequence=%u'
    factory_response='inventure_factory_state token=%u active=%c reason=%c reject=%c tool=%c mode=%c running=%c a=%c b=%c pulses=%u pulse_age_ms=%u starts=%u fault=%c sequence=%u remaining_ms=%u'
    if factory_command not in d.get('commands',{}):
        raise ValueError('R7 missing exact factory command schema')
    if factory_response not in d.get('responses',{}):
        raise ValueError('R7 missing exact factory status schema')
    motion_command = ('inventure_motion action=%c directions=%c a=%u b=%u z=%u e=%u rate=%u '
                      'pulse_us=%hu max_ms=%u stop_mask=%hu stop_value=%hu hold_ms=%u token=%u')
    motion_response = ('inventure_motion_state run=%u token=%u phase=%c reason=%c reject=%c '
                       'active=%c events=%u elapsed_ms=%u hit=%hu')
    if motion_command not in d.get('commands', {}) or motion_response not in d.get('responses', {}):
        raise ValueError('R7 factory motion must preserve the exact motion protocol')
    timing_command = 'inventure_factory_motion_timing_query'
    timing_response = 'inventure_factory_motion_timing peak_callback_ticks=%u peak_late_ticks=%u'
    if timing_command not in d.get('commands', {}) or timing_response not in d.get('responses', {}):
        raise ValueError('R7 missing exact factory timing diagnostic schema')
    if 'inventure_cooling_query' not in d.get('commands',{}):
        raise ValueError('Missing RC8 cooling diagnostic command')
    if 'inventure_cooling_state clock=%u mode=%c reason=%c valid=%c mask=%c max_temp=%hi native_owner=%c adc_failures=%u gpio_updates=%u' not in d.get('responses',{}):
        raise ValueError('Missing RC8 cooling response schema')
    if 'inventure_local_oled_query' not in d.get('commands', {}):
        raise ValueError('Missing local OLED diagnostic command')
    if 'inventure_local_oled_status state=%c boot_revision=%c baud=%u' not in d.get('responses', {}):
        raise ValueError('Missing local OLED diagnostic response')

    return {'required_features_present':required,'OLED_ABI':2,'TRANSPORT_ABI':1,'FACTORY_ABI':1,'factory_exact_command_schema':factory_command,'factory_exact_response_schema':factory_response,'transport_exact_command_schemas':transport_commands,'transport_exact_response_schema':transport_response,
            'OLED_exact_command_schemas':oled_commands,'OLED_exact_response_schemas':oled_responses,
            'RC4_constants':rc4_constants,'RC4_new_commands':new_commands,'RC4_new_responses':new_responses, 'RC7_local_constants':new_config,
            'RC7_local_OLED_schema':'inventure_local_oled_query', 'RC7_forced_baud':int(forced),
            'FACTORY_MOTION_constants':factory_motion, **rc5}

def validate(root: Path, destination: Path) -> dict:
    cfg=check_config(root/'.config')
    raw=(root/'out/klipper.bin').read_bytes()
    image=pack(raw);info=inspect(image)
    elf=check_elf((root/'out/klipper.elf').read_bytes(),raw)
    dictionary=check_dictionary(root/'out/klipper.dict')
    requested=int(cfg['CONFIG_SERIAL_BAUD'])
    expected_forced=0 if requested==250000 else requested
    if int(dictionary['RC7_forced_baud'])!=expected_forced:
        raise ValueError('RC7 actual MCU P4 baud mode not as configured')
    destination.mkdir(parents=True,exist_ok=False)
    import shutil
    for name in ['klipper.bin','klipper.elf','klipper.dict','klipper.map']:
        source=root/'out'/name
        if not source.is_file():raise ValueError('Missing build artifact: '+name)
        shutil.copy2(source,destination/name)
    shutil.copy2(root/'.config',destination/'build.config')
    shutil.copy2(root/'.inventure-v9-patch.json',destination/'source_manifest.json')
    (destination/'INVENTUR.IVB').write_bytes(image)
    report={'status':'BUILD_VALIDATED_NOT_HARDWARE_TESTED','ivb':info,'elf':elf,'dictionary':dictionary,
            'physical_tests':False,'macos_executed_by_assistant':False,
            'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.iterdir() if p.is_file()}}
    (destination/'BUILD_REPORT.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--config-only',action='store_true');p.add_argument('--output',type=Path)
    a=p.parse_args()
    try:
        if a.config_only:check_config(a.root/'.config');print('Target configuration PASS')
        else:
            if a.output is None:p.error('--output required')
            print(json.dumps(validate(a.root,a.output),indent=2))
    except (OSError,ValueError,struct.error) as e:raise SystemExit('VALIDATION FAILED: '+str(e))
