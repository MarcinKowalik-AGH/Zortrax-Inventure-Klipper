#!/usr/bin/env python3
"""Install the verified Inventure host in the active Klipper checkout."""
import argparse
from collections import defaultdict, deque
import configparser
from hashlib import sha256
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
# Embedded startup and Moonraker checks import this script by absolute path.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_module('inventure_host_installer', ROOT/'orange_pi/install_thermal_test.py')
dashboard = load_module('inventure_dashboard_setup', ROOT/'setup-fluidd.py')


def service(action, name='klipper'):
    prefix = [] if os.geteuid() == 0 else ['sudo']
    subprocess.run(prefix+['systemctl',action,name],check=True,timeout=45)


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
    return load_module('inventure_klipper_configfile', target/'klippy/configfile.py')


def dashboard_runtime_current(revision):
    try:
        dashboard.live_names(dashboard.BASE, 0, revision)
    except dashboard.MCUConfigShutdown:
        # Restarting just the host cannot clear this MCU configuration latch.
        return True
    except (OSError, ValueError):
        return False
    return True


def configure_dashboard(target, config, revision):
    def installed_names():
        from inventure_config_layout import active_klipper
        parsed, _ = active_klipper(klipper_config_module(target), config, {})
        return dashboard.configured_names({'config': parsed.sections()})

    return dashboard.setup(dashboard.BASE, config.parent, apply=True, wait=60, english=True,
                           installed_names=installed_names, expected_revision=revision,
                           expected_pid=core.running_service_pid())


def recent_shutdown_records(path, *, whole_file=False):
    # A connect-time rejection often follows a previous Klippy session. Show
    # the recorded lines verbatim; a prior shutdown may be a separate event.
    marker = re.compile(r"^(?:Transition to shutdown state:|MCU '[^']+' shutdown:|Lost communication with MCU)")
    try:
        candidates = [path]
        if not whole_file:
            rotated = (p for p in path.parent.glob(path.name+'.*') if p.is_file() and p.suffix != '.gz')
            latest = max(rotated, default=None, key=lambda p: p.stat().st_mtime)
            if latest is not None:
                candidates.append(latest)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            with candidate.open('rb') as log:
                if not whole_file:
                    offset = max(0, log.seek(0, os.SEEK_END) - 4*1024*1024)
                    log.seek(offset)
                    if offset:
                        log.readline()
                lines = deque(maxlen=8)
                events = deque(maxlen=4)
                samples = deque(maxlen=40)
                session_start = None
                for raw in log:
                    message = raw.decode('utf-8', 'replace').rstrip('\r\n')
                    if message.startswith('Start printer at ') or message == 'Starting Klippy...':
                        session_start = message
                        samples.clear()
                    elif message.startswith('Stats '):
                        stamp = message.split(':', 1)[0]
                        heater = re.search(r'\bextruder: target=\S+ temp=\S+ pwm=\S+', message)
                        mcu = re.search(r'\bmcu: ([^:]*?)(?= [\w.\-]+:|$)', message)
                        counters = (re.findall(r'\b(?:bytes_read|bytes_write|bytes_retransmit|bytes_invalid|receive_seq)=\d+',
                                               mcu[1]) if mcu else [])
                        samples.append(' '.join([stamp, *([heater[0]] if heater else []), *counters]))
                    elif marker.match(message):
                        lines.append(message)
                        if message.startswith('Transition to shutdown state:'):
                            chosen = dict.fromkeys((samples[0], samples[len(samples)//2], samples[-1])) if samples else ()
                            events.append(dict(message=message, session_start=session_start, prior_stats=list(chosen)))
            if lines:
                return dict(file=str(candidate), recent_records=list(lines), events=list(events))
    except OSError:
        if whole_file:
            raise
    return dict(file=str(path), recent_records=[], events=[])


def shutdown_log_evidence(config):
    path = config.parent.parent/'logs/klippy.log'
    try:
        log_file = dashboard.request(dashboard.BASE, '/printer/info').get('log_file')
        if log_file and Path(log_file).is_absolute():
            path = Path(log_file)
    except (OSError, ValueError, KeyError):
        pass
    return recent_shutdown_records(path)


def chamber_sections(active, sources, section_sources, by_file):
    """Reuse locally tuned native heaters, identified by their pins and sensors."""
    template = (ROOT/'config/INVENTURE_CHAMBER.cfg').read_text()
    sections = re.split(r'(?m)(?=^\[)', template)[1:]
    added, zones = [], []
    for name, pin, channel, body in zip(('chamber_l', 'chamber_r'), ('PC9', 'PC8'), (2, 3), sections):
        physical = 'inventure_r5:HEATER_'+pin
        found = [s for s in active.sections() if active.get(s, 'heater_pin', fallback='') == physical]
        if not found:
            if active.has_section('heater_generic '+name):
                raise ValueError('Conflicting chamber heater name: '+name)
            added.append(body)
        else:
            section = found[0]
            if (len(found) != 1 or not section.startswith('heater_generic ')
                    or active.get(section, 'sensor_type', fallback='') != 'Inventure R5 ADC'
                    or active.getint(section, 'sensor_channel', fallback=-1) != channel):
                raise ValueError('Chamber heater '+pin+' requires its matching shared ADC')
            name = section.split()[-1]
            if active.get(section, 'control', fallback='').lower() == 'watermark':
                edits = {'control': 'pid', 'max_delta': None,
                         'pid_kp': '22.2', 'pid_ki': '0.25', 'pid_kd': '228'}
                for key, value in edits.items():
                    if ((value is None and not active.has_option(section, key)) or
                            (key.startswith('pid_') and active.has_option(section, key))):
                        continue
                    source = sources.get((section, key), section_sources[section])
                    by_file[source][section, key] = value
        if not active.has_section('verify_heater '+name):
            added.append('[verify_heater '+name+']\ncheck_gain_time: 40\n')
        zones.append(name)
    group = 'inventure_chamber chamber'
    if active.has_section('heater_generic chamber'):
        raise ValueError('A heater already owns the chamber setpoint')
    if active.has_section(group):
        if [active.get(group, side+'_heater') for side in ('left', 'right')] != zones:
            raise ValueError('Active chamber heater mapping conflicts with the physical outputs')
    else:
        added.append('[inventure_chamber chamber]\nleft_heater: %s\nright_heater: %s\nmax_temp: 90\n' % tuple(zones))
    return zones, '\n'.join(added).encode()


def nozzle_limit_updates(target, config, configure_nozzles=False, configure_chamber=False):
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
        blower_pins = set()
        zone_names = []
        if configure_chamber:
            zone_names, chamber_config = chamber_sections(active, sources, section_sources, by_file)
            if chamber_config:
                additions += b'\n' + chamber_config
            if not active.has_section('inventure_oled'):
                additions += b'\n' + (ROOT/'config/OLED_FAST_RC7_5.cfg').read_bytes()
        for section in active.sections():
            if not section.startswith('heater_fan '):
                continue
            pin = active.get(section, 'pin', fallback='')
            if pin not in ('inventure_r5:F0', 'inventure_r5:F2', 'inventure_r5:F3'):
                continue
            current = [name.strip() for name in active.get(section, 'heater', fallback='extruder').split(',')]
            blower_pins.add(pin)
            sides = zone_names if pin in ('inventure_r5:F2', 'inventure_r5:F3') else []
            combined = list(dict.fromkeys(current+heater_names+sides))
            if current != combined:
                source = sources.get((section, 'heater'), section_sources[section])
                by_file[source][section, 'heater'] = ', '.join(combined)

        if configure_chamber:
            for index in (2, 3):
                pin = 'inventure_r5:F'+str(index)
                if pin in blower_pins:
                    continue
                if any(active.get(s, 'pin', fallback='') == pin for s in active.sections()):
                    raise ValueError('Chamber blower already has another owner: '+pin)
                additions += ('\n[heater_fan inventure_chamber_f%d]\npin: %s\ncycle_time: 0.1\n'
                              'heater: %s\nheater_temp: 40\n' % (
                                  index, pin, ', '.join(heater_names+zone_names))).encode()

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
                    if key[0] == section and key not in sources and value is not None:
                        rewritten.append(key[1]+': '+value+'\n')
                        found.add(key)
                continue
            option = re.match(r'^([ \t]*([^#;:=\s][^:=]*?)[ \t]*[:=][ \t]*)([^\r\n]*)(\r?\n)?$', line)
            key = (section, option.group(2).strip().lower()) if option else None
            if key in options:
                if key in found:
                    raise ValueError('Multiple active option definitions in '+str(source))
                if options[key] is None:
                    found.add(key)
                    continue
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
        after, moved = remove_config_section(after, nozzles[1])
        additions = moved.rstrip() + b"\n\n" + additions
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


def macro_sections(text):
    """Locate complete macro definitions without consuming comments or the SAVE_CONFIG footer."""
    headers = list(re.finditer(r'(?m)^[ \t]*\[([^\]\n]+)\].*$|^#\*#.*$', text))
    for index, header in enumerate(headers):
        section = header.group(1)
        if section is None:  # Klipper's generated footer
            break
        if not section.lower().startswith('gcode_macro '):
            continue
        limit = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        lines = text[header.start():limit].splitlines(keepends=True)
        while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith(('#', ';'))):
            lines.pop()
        body = ''.join(lines).rstrip('\r\n')
        yield section.split(None, 1)[1].upper(), header.start(), body


def add_leveling_macros(target, config, updates):
    add_fluidd_controls(target, config, updates, leveling=True)


def add_fluidd_controls(target, config, updates, *, leveling=False, jam=False, filament=False, dashboard=False):
    """Stage the selected controls in the active Klipper configuration and its includes."""
    config = config.resolve()
    sources = {}
    originals = {}
    module = klipper_config_module(target)

    class StagedReader(module.ConfigFileReader):
        def read_config_file(self, filename):
            path = Path(filename).resolve()
            if path not in sources:
                if path in updates:
                    originals[path], sources[path] = updates[path]
                else:
                    originals[path] = sources[path] = path.read_bytes()
            return sources[path].decode('utf-8').replace('\r\n', '\n')

    reader = StagedReader()

    def read_active():
        return reader.build_fileconfig_with_includes(reader.read_config_file(config), str(config))

    active = read_active()
    if not active.has_section('inventure_mcu'):
        raise ValueError('Inventure controls require [inventure_mcu] in the active config')

    level_config = ROOT/'config/INVENTURE_PLATFORM_LEVEL.cfg'
    jam_config = ROOT/'config/INVENTURE_FILAMENT_JAM.cfg'
    material_config = ROOT/'config/INVENTURE_MATERIAL.cfg'
    display_config = ROOT/'config/INVENTURE_DASHBOARD.cfg'
    klipper_config = ROOT/'config/INVENTURE_KLIPPER.cfg'
    home_config = ROOT/'config/INVENTURE_HOME.cfg'
    pad_config = ROOT/'config/INVENTURE_NOZZLE_PAD.cfg'
    xy_config = ROOT/'config/INVENTURE_NOZZLE_XY.cfg'
    diagnostics = ROOT/'fluidd/inventure_01_diagnostyka.cfg'
    if dashboard:
        leveling = jam = filament = True
    selected = {material_config} if filament else set()
    if leveling:
        selected.add(level_config)
    if jam:
        selected.add(jam_config)
        def add_native_section(name, body, commands):
            if active.has_section(name):
                return
            for section in active.sections():
                prefix, _, command = section.upper().partition(' ')
                if prefix == 'GCODE_MACRO' and command in commands:
                    if not active.has_option(section, 'rename_existing'):
                        raise ValueError('Cannot enable ['+name+'] alongside ['+section
                                         +'] without rename_existing')
            sources[config] = append_config_section(sources[config], body.encode('utf-8'))

        from inventure_bootstrap import current_procedure_profile
        if dashboard and current_procedure_profile() == 'operator-home-r24':
            add_native_section('inventure_information', (ROOT/'config/INVENTURE_INFORMATION.cfg').read_text(), set())
        add_native_section('pause_resume', '[pause_resume]',
                           {'PAUSE', 'RESUME', 'CLEAR_PAUSE', 'CANCEL_PRINT'})
        if dashboard and not active.has_section('display'):
            add_native_section('display_status', '[display_status]', set())
        gcodes = config.parent.parent/'gcodes'
        if config.parent.name == 'config' and gcodes.is_dir():
            add_native_section('virtual_sdcard', '[virtual_sdcard]\npath: '+str(gcodes),
                               {'M20', 'M21', 'M23', 'M24', 'M25', 'M26', 'M27', 'M28', 'M29',
                                'M30', 'SDCARD_RESET_FILE', 'SDCARD_PRINT_FILE'})
    active = read_active()
    if dashboard:
        selected.update((display_config, klipper_config, home_config, pad_config, xy_config, diagnostics))

    def available(name):
        if name in ('BED_MESH', 'PID_BED', 'CANCEL_PRINT'):
            return active.has_section({'BED_MESH': 'bed_mesh', 'PID_BED': 'heater_bed',
                                       'CANCEL_PRINT': 'pause_resume'}[name])
        if name == 'START_PRINT':
            return all(active.getboolean('inventure_preflight', option, fallback=False)
                       for option in ('enabled', 'p14_integration_qualified', 'start_qualified'))
        if name in ('M141', 'M191', 'PID_CHAMBER'):
            return active.has_section('inventure_chamber chamber')
        if name in ('PID_HOTEND', 'PID_STATUS', 'PID_SAVE'):
            return any(active.get(section, 'heater_pin', fallback='') in
                       ('inventure_r5:HEATER_T0', 'inventure_r5:HEATER_T1')
                       for section in active.sections())
        if name.startswith('INVENTURE_HOME_'):
            return active.has_section('inventure_native_z') and not (
                name == 'INVENTURE_HOME_XY' and active.has_section('inventure_motion'))
        if name.startswith('INVENTURE_PLATFORM_') and name.endswith(('_UP', '_DOWN')):
            return active.has_section('inventure_native_z') or active.getboolean('inventure_motion', 'enabled', fallback=False)
        if name in ('INVENTURE_MAIN_MENU','INVENTURE_SETTINGS','INVENTURE_INFORMATION','INVENTURE_LANGUAGE','INVENTURE_MODELS_SORT','INVENTURE_OLED_ICONS'):
            return active.getboolean('inventure_oled', 'oem_menu', fallback=False)
        if name.startswith(('INVENTURE_DISPLAY_', 'INVENTURE_OLED_')) or name in (
                'INVENTURE_SCREEN', 'INVENTURE_SLEEP_MODE', 'OLED_TIMERS'):
            return active.has_section('inventure_oled')
        return True

    templates = {}
    required = {}
    for path in (level_config, jam_config, material_config, home_config, pad_config, xy_config,
                 display_config, klipper_config, *sorted((ROOT/'fluidd').glob('*.cfg'))):
        for name, _, body in macro_sections(path.read_text()):
            templates[name] = body
            dependency = re.search(r"'(inventure_[a-z_]+)' in printer.configfile.settings", body)
            if (dashboard and path == diagnostics and dependency and name != 'INVENTURE_CAL_LEVELING'
                    and not active.has_section(dependency.group(1))):
                continue
            if path in selected and available(name):
                required[name] = (path, body)

    # Compare whole published sections. Extra options, comments or changed
    # Jinja belong to the operator. Leave overrides split across files intact.
    history = (ROOT/'config/fluidd_macro_history.sha256').read_text().splitlines()
    known = dict(line.split() for line in history if line and not line.startswith('#'))
    present = {section.casefold() for section in active.sections()}
    occurrences = defaultdict(list)
    for path, data in sources.items():
        for name, start, body in macro_sections(data.decode('utf-8')):
            occurrences[name].append((path, start, body))

    by_file = defaultdict(list)
    for name, matches in occurrences.items():
        if len(matches) != 1:
            continue
        path, start, body = matches[0]
        digest = sha256(body.replace('\r\n', '\n').encode('utf-8')).hexdigest()
        target_name = known.get(digest)
        if target_name not in templates or not available(target_name):
            continue
        key = 'gcode_macro ' + target_name.casefold()
        if target_name != name and key in present:
            replacement = ''  # retire only an exact published duplicate
        else:
            replacement = templates[target_name]
            present.add(key)
        if '\r\n' in body:
            replacement = replacement.replace('\n', '\r\n')
        by_file[path].append((start, len(body), replacement))

    for path, replacements in by_file.items():
        text = sources[path].decode('utf-8')
        for start, size, replacement in sorted(replacements, reverse=True):
            text = text[:start] + replacement + text[start + size:]
        sources[path] = text.encode('utf-8')

    active = read_active()
    present = {s.casefold(): s for s in active.sections()}
    for name, (_, section) in required.items():
        key = 'gcode_macro ' + name
        existing = present.get(key.casefold())
        if not existing:
            sources[config] = append_config_section(sources[config], section.encode('utf-8'))
        # Existing custom macros (including slicer start/end) keep their implementation.
        # Exact stock versions have already been updated in their original files.

    for path, staged in sources.items():
        if staged != originals[path]:
            updates[path] = (originals[path], staged)


def running_moonraker_config(printer_config):
    """Resolve the service command line, including KIAUH's EnvironmentFile."""
    try:
        result = subprocess.run(['systemctl', 'show', 'moonraker', '--property=MainPID', '--value'],
                                capture_output=True, check=True, text=True, timeout=10)
        process = Path('/proc')/str(int(result.stdout.strip()))
        args = [os.fsdecode(value) for value in (process/'cmdline').read_bytes().split(b'\0') if value]
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument('-c', '--configfile')
        parser.add_argument('-d', '--datapath')
        options, _ = parser.parse_known_args(args)
        try:
            env = dict(item.split(b'=', 1) for item in (process/'environ').read_bytes().split(b'\0')
                       if b'=' in item)
        except PermissionError:
            env = {}
        home = Path(os.fsdecode(env[b'HOME'])) if b'HOME' in env else printer_config.parent.parent.parent
        def resolve(value):
            if value.startswith('~/'):
                return (home/value[2:]).resolve()
            path = Path(value)
            return path.resolve() if path.is_absolute() else (process/'cwd'/path).resolve()
        config_file = options.configfile or os.fsdecode(env.get(b'MOONRAKER_CONFIG_PATH', b''))
        data_path = options.datapath or os.fsdecode(env.get(b'MOONRAKER_DATA_PATH', b''))
        if config_file:
            return resolve(config_file)
        if data_path:
            return resolve(data_path)/'config/moonraker.conf'
        if b'HOME' in env:
            return home/'printer_data/config/moonraker.conf'
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    fallback = printer_config.parent/'moonraker.conf'
    return fallback if fallback.is_file() else None


def moonraker_sections():
    try:
        with urllib.request.urlopen('http://127.0.0.1:7125/server/config', timeout=2) as response:
            value = json.load(response)['result']['orig']
        return set(value) if isinstance(value, dict) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def atomic_config_write(path, data, *, create=False):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.'+path.name+'.', delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            if create:
                os.chmod(staged, 0o644)
                os.link(staged, path)
            else:
                shutil.copymode(path, staged)
                staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def update(check_only=False, upgrade_nozzle_limits=False, configure_nozzles=False,
           configure_leveling=False, configure_jam=False, configure_filament=False, configure_fluidd=False,
           fluidd_root=None, fluidd_zip=None, organize_config=False, moonraker_config=None):
    with tempfile.TemporaryDirectory(prefix='inventure-fluidd-') as temp:
        source = root = None
        if configure_fluidd:
            from fluidd_web import release_bundle, web_root
            root = web_root(fluidd_root)
            if not check_only:
                source = release_bundle(Path(temp), fluidd_zip)
        return update_host(check_only, upgrade_nozzle_limits, configure_nozzles, configure_leveling,
                           configure_jam, configure_filament, configure_fluidd, source, root,
                           organize_config, moonraker_config)


def plan_klipper_docs(target, updates):
    """Moonraker registers <running klipper_path>/docs as a read-only root."""
    directory = target / 'docs'
    guide = directory / 'Inventure_Display.md'
    if directory.is_dir() and directory.resolve() != Path('/'):
        # Update a guide installed by R36. Respect site-owned directories and edits.
        if directory.is_symlink() or guide.is_symlink() or not guide.is_file():
            return None
        previous = guide.read_bytes()
        if sha256(previous).hexdigest() != '10f211ecf18b1a409df8d08362ddb795930b66c52f4a9f9ffde182869313e0bf':
            return None
        updated = (ROOT/'docs/Inventure_Display.md').read_bytes()
        if updated == previous:
            return None
        updates[guide] = (previous, updated)
        return guide
    if directory.exists() or directory.is_symlink():
        raise ValueError('The active Klipper docs path must point to a directory: '+str(directory))
    updates[guide] = (None, (ROOT/'docs/Inventure_Display.md').read_bytes())
    return guide


def update_host(check_only, upgrade_nozzle_limits, configure_nozzles, configure_leveling,
                configure_jam, configure_filament, configure_fluidd, source, root,
                organize_config=False, moonraker_config=None):
    # Resolve the running stage before stopping it; ~/klipper can be another
    # checkout. Native nozzle changes are explicit installer options.
    target=core.running_klipper_target()
    config=core.running_printer_config()
    plan=core.install(target)
    config_updates=(nozzle_limit_updates(target,config,configure_nozzles or configure_fluidd,configure_fluidd)
                    if upgrade_nozzle_limits or configure_nozzles or configure_fluidd else {})
    if configure_leveling or configure_jam or configure_filament or configure_fluidd:
        add_fluidd_controls(target, config, config_updates,
                           leveling=configure_leveling, jam=configure_jam, filament=configure_filament,
                           dashboard=configure_fluidd)
    identity={'host_revision':plan['host_revision'],'klipper_root':str(target),
              'printer_config':str(config),'MCU_changed':False,'config_changed':False}
    moon_paths = set()
    docs_guide = None
    restart_docs = False
    if configure_fluidd or organize_config:
        docs_guide = plan_klipper_docs(target, config_updates)
        restart_docs = bool(docs_guide and configure_fluidd)
        identity['moonraker_docs_root'] = str(target/'docs')
    expected_sections = None
    if configure_fluidd and not organize_config:
        from inventure_config_layout import split_display_preferences
        split_display_preferences(klipper_config_module(target), config.resolve(), config_updates)
    if organize_config:
        from inventure_config_layout import organize_klipper, organize_moonraker
        paths = organize_klipper(klipper_config_module(target), config, config_updates)
        chosen = moonraker_config or running_moonraker_config(config)
        if chosen:
            chosen = Path(chosen).expanduser().resolve()
            restart_docs = bool(docs_guide)
            moon_updates = {}
            managed_web = root
            if managed_web is None:
                from fluidd_web import web_root
                try:
                    installed_web = web_root(None)
                    if (installed_web/'.inventure-fluidd-build').is_file():
                        managed_web = installed_web
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
            retired = organize_moonraker(chosen, paths, managed_web, moon_updates)
            moon_paths = set(moon_updates)
            if moon_paths and not check_only:
                live = moonraker_sections()
                if live is not None:
                    expected_sections = live.difference(retired)
            if moon_paths.intersection(config_updates):
                raise ValueError('Klipper and Moonraker must use separate configuration files')
            config_updates.update(moon_updates)
            identity.update(moonraker_config=str(chosen), removed_stock_updaters=retired)
        else:
            identity['moonraker_config'] = None
        identity['klipper_includes'] = [str(path) for path in dict.fromkeys(paths)]
    check_print_job()
    if check_only:
        print(json.dumps(dict(identity,status='CHECK_OK',
            planned_config_updates=list(map(str,config_updates))),indent=2))
        return
    if os.geteuid()!=0:
        subprocess.run(['sudo','-v'],check=True,timeout=120)
    klipper_changes = set(config_updates).difference(moon_paths, {docs_guide})
    restart_host = (not configure_fluidd or plan['status'] != 'ALREADY_INSTALLED'
                    or bool(klipper_changes) or not dashboard_runtime_current(plan['host_revision']))
    if restart_host:
        service('stop')
    result=None
    settings=None
    config_backups={}
    created_docs_dir = False
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    try:
        # Recheck module hashes after the service is stopped. The core owns
        # exact backups, atomic staging and rollback of a partial copy.
        result = core.install(target,apply=True) if restart_host else plan
        # Install new include targets before publishing any references to them.
        for path, (before, after) in sorted(config_updates.items(), key=lambda item: item[1][0] is not None):
            if before is None:
                if path == docs_guide:
                    try:
                        path.parent.mkdir()
                        created_docs_dir = True
                    except FileExistsError:
                        if path.parent.is_symlink() or not path.parent.is_dir():
                            raise ValueError('The Klipper docs path changed during installation')
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                atomic_config_write(path, after, create=True)
                config_backups[path] = None
                continue
            if path.read_bytes() != before:
                raise ValueError('The active configuration changed during the update: '+str(path))
            backup = path.with_name(path.name+'.pre-nozzle-limit-'+stamp+'.bak')
            shutil.copy2(path, backup)
            atomic_config_write(path, after)
            config_backups[path] = backup
        if moon_paths or restart_docs:
            service('restart', 'moonraker')
            subprocess.run(['systemctl','is-active','--quiet','moonraker'],check=True,timeout=10)
            identity['moonraker_readback'] = 'systemd_active'
            if expected_sections is not None:
                deadline = time.monotonic() + 30
                while moonraker_sections() != expected_sections:
                    if time.monotonic() >= deadline:
                        raise ValueError('Moonraker did not reload the expected configuration')
                    time.sleep(.25)
                identity['moonraker_readback'] = 'server_config_verified'
        if restart_host:
            service('start')
        subprocess.run(['systemctl','is-active','--quiet','klipper'],check=True,timeout=10)
        if configure_fluidd:
            from fluidd_web import activate
            settings = configure_dashboard(target, config, plan['host_revision'])
            identity.update(fluidd_groups_status=settings['status'],
                            fluidd_groups_backup=settings.get('backup'),
                            **{key: settings[key] for key in ('macro_source', 'klipper_state', 'klipper_message',
                               'running_host_revision', 'firmware_restart_required') if key in settings})
            if settings.get('firmware_restart_required'):
                identity['shutdown_log'] = shutdown_log_evidence(config)
            identity.update(fluidd_root=str(root), previous_fluidd_site=activate(source, root))
    except BaseException:
        if config_backups or result and result.get('status')=='INSTALLED':
            if restart_host:
                service('stop')
            for path, backup in reversed(config_backups.items()):
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_config_write(path, backup.read_bytes())
            if result and result.get('status')=='INSTALLED':
                core.install(target,apply=True,rollback=Path(result['backup']))
        if created_docs_dir and not any(docs_guide.parent.iterdir()):
            docs_guide.parent.rmdir()
        if restart_host:
            service('start')
        if moon_paths.intersection(config_backups) or restart_docs and docs_guide in config_backups:
            service('restart', 'moonraker')
        if settings is not None and settings.get('backup'):
            dashboard.setup(dashboard.BASE, config.parent, apply=True, rollback=Path(settings['backup']))
        raise
    if identity.get('firmware_restart_required'):
        status = 'HOST_INSTALLED_MCU_SHUTDOWN'
    elif not restart_host:
        status = 'HOST_ALREADY_CURRENT'
    else:
        status = 'HOST_RESTARTED' if result['status']=='ALREADY_INSTALLED' else 'HOST_UPDATED'
    identity.update(config_changed=bool(config_backups),
                    created_files=[str(path) for path, backup in config_backups.items() if backup is None],
                    config_backups={str(path):str(backup) for path,backup in config_backups.items() if backup is not None})
    print(json.dumps(dict(identity,status=status,backup=result.get('backup')),
                     indent=2))
    if identity.get('firmware_restart_required'):
        print('Host and dashboard installed. MCU is still shut down. Review the original shutdown '
              'in klippy.log. Once its cause is corrected, run FIRMWARE_RESTART in Fluidd. '
              'Wait for Ready, then run INVENTURE_VERSION.')
    else:
        print('In Klipper console: INVENTURE_VERSION')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shutdown-log', type=Path, metavar='KLIPPY_LOG',
                        help='Read shutdown reasons, heater readings and link counters from an existing Klipper log, then exit')
    parser.add_argument('--check',action='store_true',help='Read-only validation')
    parser.add_argument('--upgrade-nozzle-limits',action='store_true',
                        help='Migrate the legacy 220 C stock R5 nozzle limit to 310 C')
    parser.add_argument('--configure-nozzles',action='store_true',
                        help='Configure both native nozzle heaters to 310 C and include T1 in cooling')
    parser.add_argument('--configure-leveling',action='store_true',
                        help='Add the leveling buttons and the global door menu to Fluidd')
    parser.add_argument('--configure-jam', action='store_true',
                        help='Add Inventure filament recovery controls and Klipper pause support')
    parser.add_argument('--configure-filament', action='store_true',
                        help='Add cassette settings, load, unload and manual feed controls to Fluidd')
    parser.add_argument('--configure-fluidd', action='store_true',
                        help='Install the Inventure dashboard and group its buttons in the active Fluidd instance')
    parser.add_argument('--fluidd-root', type=Path, help='Select the Inventure nginx web root on a multi-site host')
    parser.add_argument('--fluidd-zip', type=Path, help='Use the release web ZIP and adjacent SHA256SUMS offline')
    parser.add_argument('--organize-config', action='store_true',
                        help='Split the live Klipper configuration and clean matching Moonraker entries')
    parser.add_argument('--moonraker-config', type=Path, help='Select the active Moonraker config for --organize-config')
    args=parser.parse_args()
    if args.shutdown_log is not None:
        if any(v for k, v in vars(args).items() if k != 'shutdown_log'):
            parser.error('--shutdown-log is a separate read-only action')
        if not args.shutdown_log.is_file():
            parser.error('Cannot read the specified Klipper log: '+str(args.shutdown_log))
        try:
            records = recent_shutdown_records(args.shutdown_log, whole_file=True)
        except OSError as exc:
            parser.error(str(exc))
        print(json.dumps(records, indent=2))
        return
    if args.moonraker_config and not args.organize_config:
        parser.error('--moonraker-config requires --organize-config')
    try:update(args.check,args.upgrade_nozzle_limits,args.configure_nozzles,
               args.configure_leveling,args.configure_jam,args.configure_filament,args.configure_fluidd,
               args.fluidd_root,args.fluidd_zip,args.organize_config,args.moonraker_config)
    except (OSError,ValueError,AssertionError,configparser.Error,subprocess.SubprocessError) as exc:
        raise SystemExit('UPDATE FAILED: '+str(exc))


if __name__=='__main__':
    main()
