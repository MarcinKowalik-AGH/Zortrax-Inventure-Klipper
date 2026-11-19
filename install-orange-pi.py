#!/usr/bin/env python3
"""Install the verified Inventure host in the active Klipper checkout."""
import argparse
from collections import defaultdict
import configparser
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('inventure_host_installer',
    ROOT/'orange_pi/install_thermal_test.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def service(action):
    prefix = [] if os.geteuid() == 0 else ['sudo']
    subprocess.run(prefix+['systemctl',action,'klipper'],check=True,timeout=45)


def check_print_job():
    """Do not stop an active/paused print when Moonraker reports one."""
    try:
        with urllib.request.urlopen(
                'http://127.0.0.1:7125/printer/objects/query?print_stats',timeout=3) as r:
            value=json.load(r)
    except (OSError,ValueError,urllib.error.URLError):
        # The diagnostic skeleton may have no virtual_sdcard/print_stats.
        return
    state=value.get('result',{}).get('status',{}).get('print_stats',{}).get('state')
    if state in ('printing','paused'):
        raise ValueError('Finish or cancel the current print before updating the host')


def remove_config_section(data, name):
    """Move a whole section without flattening includes or multiline values."""
    lines = data.decode('utf-8').splitlines(keepends=True)
    start = end = None
    for index, line in enumerate(lines):
        header = configparser.RawConfigParser.SECTCRE.match(line.strip())
        if header is not None and header.group('header') == name:
            if start is not None:
                raise ValueError('Multiple section definitions while relocating '+name)
            start = index
        elif start is not None and end is None and (
                header is not None or line.startswith('#*#')):
            end = index
    if start is None:
        raise ValueError('Cannot locate the section to relocate: '+name)
    if end is None:
        end = len(lines)
    return (''.join(lines[:start]+lines[end:]).encode(),
            ''.join(lines[start:end]).encode())


def append_config_section(data, section):
    """Keep new active configuration before Klipper's SAVE_CONFIG footer."""
    marker = re.search(br'(?m)^#\*#', data)
    position = marker.start() if marker else len(data)
    active, autosave = data[:position], data[position:]
    return active.rstrip(b'\r\n')+b'\n\n'+section.rstrip(b'\r\n')+b'\n\n'+autosave


def klipper_config_module(target):
    reader_spec = importlib.util.spec_from_file_location(
        'inventure_klipper_configfile', target/'klippy/configfile.py')
    module = importlib.util.module_from_spec(reader_spec)
    reader_spec.loader.exec_module(module)
    return module


def nozzle_limit_updates(target, config, configure_nozzles=False):
    module = klipper_config_module(target)
    source_reader = module.ConfigFileReader()
    sources = {}
    section_sources = {}

    class TracingReader(module.ConfigFileReader):
        def append_fileconfig(self, fileconfig, data, filename):
            parsed = source_reader.build_fileconfig(data, filename)
            for section in parsed.sections():
                section_sources[section] = Path(filename).resolve()
                for option in parsed.options(section):
                    sources[section, option] = Path(filename).resolve()
            super().append_fileconfig(fileconfig, data, filename)

    reader = TracingReader()
    active = reader.build_fileconfig_with_includes(reader.read_config_file(config), str(config))
    by_file = defaultdict(dict)
    nozzles = {}
    for section in active.sections():
        pin = active.get(section, 'heater_pin', fallback='')
        if pin not in ('inventure_r5:HEATER_T0', 'inventure_r5:HEATER_T1'):
            continue
        tool = int(pin[-1])
        if (active.get(section, 'sensor_type', fallback='') != 'Inventure R5 ADC'
                or active.getint(section, 'sensor_channel', fallback=-1) != tool):
            raise ValueError('Stock Inventure heater requires its matching shared ADC in '+section)
        nozzles[tool] = section
        maximum = active.getfloat(section, 'max_temp')
        if maximum == 220 or configure_nozzles and maximum != 310:
            by_file[sources[section, 'max_temp']][section, 'max_temp'] = '310'
    if not nozzles:
        raise ValueError('The active configuration has no stock Inventure R5 nozzle heater')

    additions = b''
    relocate = None
    if configure_nozzles:
        if not active.has_section('inventure_mcu'):
            raise ValueError('The native nozzle setup requires [inventure_mcu]')
        if 0 not in nozzles:
            raise ValueError('The native nozzle setup requires the stock T0 heater')
        if 1 not in nozzles:
            if active.has_section('heater_generic inventure_t1'):
                raise ValueError('The name inventure_t1 is already used by another heater')
            additions = (ROOT/'config/INVENTURE_NATIVE_T1_HEATER.cfg').read_bytes()
            nozzles[1] = 'heater_generic inventure_t1'
        elif active.sections().index(nozzles[1]) < active.sections().index('inventure_mcu'):
            # R14/R15 prepended the generic T1 heater to printer.cfg. Klipper
            # loads it before the board can register its shared ADC factory.
            relocate = section_sources[nozzles[1]]
        heater_names = [nozzles[tool].split()[-1] for tool in (0, 1)]
        for section in active.sections():
            if not section.startswith('heater_fan '):
                continue
            pin = active.get(section, 'pin', fallback='')
            if pin not in ('inventure_r5:F0', 'inventure_r5:F2', 'inventure_r5:F3'):
                continue
            current = [name.strip() for name in active.get(section, 'heater', fallback='extruder').split(',')]
            combined = list(dict.fromkeys(current+heater_names))
            if current != combined:
                source = sources.get((section, 'heater'), section_sources[section])
                by_file[source][section, 'heater'] = ', '.join(combined)

    updates = {}
    for source, options in by_file.items():
        original = source.read_bytes()
        lines = original.decode('utf-8').splitlines(keepends=True)
        rewritten = []
        section = None
        found = set()
        continuation_indent = None
        for line in lines:
            stripped = line.strip()
            if continuation_indent is not None and stripped and not stripped.startswith(('#', ';')):
                if len(line)-len(line.lstrip()) > continuation_indent:
                    continue
                continuation_indent = None
            header = configparser.RawConfigParser.SECTCRE.match(line.strip())
            if header is not None:
                section = header.group('header')
                rewritten.append(line)
                for key, value in options.items():
                    if key[0] == section and key not in sources:
                        rewritten.append(key[1]+': '+value+'\n')
                        found.add(key)
                continue
            option = re.match(r'^([ \t]*([^#;:=\s][^:=]*?)[ \t]*[:=][ \t]*)([^\r\n]*)(\r?\n)?$', line)
            key = (section, option.group(2).strip().lower()) if option else None
            if key in options:
                if key in found:
                    raise ValueError('Multiple active option definitions in '+str(source))
                comment = re.search(r'[#;].*', option.group(3))
                line = (option.group(1).rstrip()+' '+options[key]
                        +(' '+comment.group() if comment else '')+(option.group(4) or ''))
                continuation_indent = len(line)-len(line.lstrip())
                found.add(key)
            rewritten.append(line)
        if found != set(options):
            raise ValueError('Cannot locate the active nozzle options in '+str(source))
        updates[source] = (original, ''.join(rewritten).encode())
    if relocate is not None:
        original = relocate.read_bytes()
        before, after = updates.get(relocate, (original, original))
        after, additions = remove_config_section(after, nozzles[1])
        updates[relocate] = (before, after)
    if additions:
        original = config.read_bytes()
        before, after = updates.get(config, (original, original))
        updates[config] = (before, append_config_section(after, additions))
    if configure_nozzles:
        class StagedReader(module.ConfigFileReader):
            def read_config_file(self, filename):
                path = Path(filename).resolve()
                if path in updates:
                    return updates[path][1].decode('utf-8').replace('\r\n', '\n')
                return super().read_config_file(filename)

        staged_reader = StagedReader()
        staged = staged_reader.build_fileconfig_with_includes(
            staged_reader.read_config_file(config), str(config))
        sections = staged.sections()
        for section in nozzles.values():
            if sections.index(section) < sections.index('inventure_mcu'):
                raise ValueError('[inventure_mcu] must precede the native nozzle heater '+section)
    return updates


def add_leveling_macros(target, config, updates):
    """Install the five Fluidd entry points once, respecting active includes."""
    reader = klipper_config_module(target).ConfigFileReader()
    original = config.read_bytes()
    before, staged = updates.get(config, (original, original))
    active = reader.build_fileconfig_with_includes(staged.decode('utf-8'), str(config))
    if not active.has_section('inventure_mcu'):
        raise ValueError('Platform leveling requires [inventure_mcu] in the active config')
    template = (ROOT/'config/INVENTURE_PLATFORM_LEVEL.cfg').read_text()
    present = {s.casefold(): s for s in active.sections()}
    for section in re.split(r'(?m)(?=^\[gcode_macro )', template):
        if not section.startswith('[gcode_macro '):
            continue
        wanted = reader.build_fileconfig(section, 'INVENTURE_PLATFORM_LEVEL.cfg')
        name, = wanted.sections()
        existing = present.get(name.casefold())
        if existing:
            if active.get(existing, 'gcode').strip() != wanted.get(name, 'gcode').strip():
                raise ValueError('A different Fluidd macro already uses ['+existing+']')
        else:
            staged = append_config_section(staged, section.encode('utf-8'))
    if staged != before:
        updates[config] = (before, staged)


def atomic_config_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.'+path.name+'.', delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            shutil.copymode(path, staged)
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def update(check_only=False, upgrade_nozzle_limits=False, configure_nozzles=False,
           configure_leveling=False):
    # Resolve the running stage before stopping it; ~/klipper can be another
    # checkout. Native nozzle changes are explicit installer options.
    target=core.running_klipper_target()
    config=core.running_printer_config()
    plan=core.install(target)
    config_updates=(nozzle_limit_updates(target,config,configure_nozzles)
                    if upgrade_nozzle_limits or configure_nozzles else {})
    if configure_leveling:
        add_leveling_macros(target, config, config_updates)
    identity={'host_revision':plan['host_revision'],'klipper_root':str(target),
              'printer_config':str(config),'MCU_changed':False,'config_changed':False}
    check_print_job()
    if check_only:
        print(json.dumps(dict(identity,status='CHECK_OK',
            planned_config_updates=list(map(str,config_updates))),indent=2))
        return
    if os.geteuid()!=0:
        subprocess.run(['sudo','-v'],check=True,timeout=120)
    service('stop')
    result=None
    config_backups={}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    try:
        # Recheck module hashes after the service is stopped. The core owns
        # exact backups, atomic staging and rollback of a partial copy.
        result=core.install(target,apply=True)
        for path,(before,after) in config_updates.items():
            if path.read_bytes()!=before:
                raise ValueError('The active configuration changed during the update: '+str(path))
            backup=path.with_name(path.name+'.pre-nozzle-limit-'+stamp+'.bak')
            shutil.copy2(path,backup)
            config_backups[path]=backup
            atomic_config_write(path,after)
        service('start')
        subprocess.run(['systemctl','is-active','--quiet','klipper'],check=True,timeout=10)
    except BaseException:
        if config_backups or result and result.get('status')=='INSTALLED':
            service('stop')
            for path,backup in config_backups.items():
                atomic_config_write(path,backup.read_bytes())
            if result and result.get('status')=='INSTALLED':
                core.install(target,apply=True,rollback=Path(result['backup']))
        service('start')
        raise
    status='HOST_RESTARTED' if result['status']=='ALREADY_INSTALLED' else 'HOST_UPDATED'
    identity.update(config_changed=bool(config_backups),
                    config_backups={str(path):str(backup) for path,backup in config_backups.items()})
    print(json.dumps(dict(identity,status=status,backup=result.get('backup')),
                     indent=2))
    print('In Klipper console: INVENTURE_VERSION')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Read-only validation')
    parser.add_argument('--upgrade-nozzle-limits',action='store_true',
                        help='Migrate the legacy 220 C stock R5 nozzle limit to 310 C')
    parser.add_argument('--configure-nozzles',action='store_true',
                        help='Configure both native nozzle heaters to 310 C and include T1 in cooling')
    args=parser.parse_args()
    parser.add_argument('--configure-leveling',action='store_true',
                        help='Add the ZORTRAX_LEVEL platform leveling buttons to Fluidd')
    try:update(args.check,args.upgrade_nozzle_limits,args.configure_nozzles,args.configure_leveling)
    except (OSError,ValueError,AssertionError,configparser.Error,subprocess.SubprocessError) as exc:
        raise SystemExit('UPDATE FAILED: '+str(exc))


if __name__=='__main__':
    main()
