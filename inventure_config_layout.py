"""Organize the active Klipper and Moonraker configuration on Orange Pi."""
from collections import Counter, defaultdict
import configparser
import glob
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
HEADER = re.compile(r'^\[([^\]\n]+)\][ \t]*(?:[#;][^\n]*)?\r?$', re.M)
FOOTER = re.compile(r'^#\*#', re.M)
MANAGED = '# Inventure configuration layout v1\n'
MAP_KEYS = ('nozzle_reference_mv', 'nozzle_mv_per_c', 'thermistor_thresholds')
DISPLAY_DEFAULTS = {'dim_minutes': '5', 'sleep_minutes': '15', 'brightness': '16'}
SERVICES = {'inventure_native_z', 'inventure_operations', 'inventure_preflight',
            'inventure_filament', 'inventure_motion', 'inventure_nozzle_xy'}
HEATER_OPTIONS = re.compile(r'^(?:heater_pin|sensor_type|sensor_channel|pwm_cycle_time|smooth_time|'
                            r'max_power|min_temp|max_temp)[ \t]*[:=]', re.I)
PID_OPTIONS = re.compile(r'^(?:control|pid_kp|pid_ki|pid_kd)[ \t]*[:=]', re.I)
PID_BUTTONS = {'PID_HOTEND', 'PID_CHAMBER', 'PID_BED', 'PID_STATUS', 'PID_SAVE'}


def blocks(data):
    """Return sections with byte-preserving offsets; never parse the saved tail."""
    marker = FOOTER.search(data)
    active = data[:marker.start()] if marker else data
    headers = list(HEADER.finditer(active))
    for i, h in enumerate(headers):
        end = headers[i+1].start() if i+1 < len(headers) else len(active)
        yield h[1], h.start(), end, data[h.start():end]


def stage(updates, path, data):
    before = updates[path][0] if path in updates else (path.read_bytes() if path.exists() else None)
    if before != data:
        updates[path] = (before, data)
    else:
        updates.pop(path, None)


def active_klipper(module, main, updates):
    files = []
    pending = {p for p, (before, _) in updates.items() if before is None}

    class LayoutReader(module.ConfigFileReader):
        def read_config_file(self, filename):
            path = Path(filename).resolve()
            files.append(path)
            if path in updates:
                return updates[path][1].decode().replace('\r\n', '\n')
            return super().read_config_file(filename)

        def _resolve_include(self, source_filename, spec, parser, visited):
            pattern = os.path.join(os.path.dirname(source_filename), spec.strip())
            extra = {str(p) for p in pending if p.match(os.path.abspath(pattern))}
            if not extra:
                return super()._resolve_include(source_filename, spec, parser, visited)
            names = sorted(set(glob.glob(pattern)) | extra)
            for name in names:
                self._parse_config(self.read_config_file(name), name, parser, visited)
            return names

    rdr = LayoutReader()
    cfg = rdr.build_fileconfig_with_includes(rdr.read_config_file(main), str(main))
    return cfg, files


def organize_klipper(module, main, updates):
    main = main.resolve()
    folder = main.parent / 'inventure'
    if folder.is_symlink() or folder.exists() and not folder.is_dir():
        raise ValueError('Expected a local Inventure configuration directory: '+str(folder))
    active, paths = active_klipper(module, main, updates)
    if not active.has_section('inventure_mcu'):
        raise ValueError('Select the active Inventure Klipper configuration')
    texts = {p: (updates[p][1] if p in updates else p.read_bytes()).decode()
             for p in dict.fromkeys(paths)}
    counts = Counter(name for path in paths for name, *_ in blocks(texts[path]))
    published = {name.split(None, 1)[1].upper()
                 for directory in ('config', 'fluidd') for p in (ROOT / directory).glob('*.cfg')
                 for name, *_ in blocks(p.read_text()) if name.lower().startswith('gcode_macro ')}
    grouped = defaultdict(list)
    edits = defaultdict(list)
    included = set(paths)
    globs = [os.path.abspath(os.path.join(path.parent, name[8:].strip()))
             for path, data in texts.items() for name, *_ in blocks(data)
             if name.startswith('include ') and glob.has_magic(name[8:])]

    def already_included(path):
        return path in included or any(path.match(pattern) for pattern in globs)

    def destination(name):
        path = folder / name
        if path.is_symlink() or path.exists() and not path.is_file():
            raise ValueError('Unsupported layout destination: '+str(path))
        return path

    def own(path):
        if path in texts:
            content = texts[path]
        else:
            content = path.read_bytes().decode() if path.exists() else ''
        if content and not content.startswith(MANAGED):
            raise ValueError('Existing configuration uses the Inventure layout filename: '+str(path))
        return content

    def include(path, source):
        return '[include '+os.path.relpath(path, source.parent)+']\n'

    heater_pins = {'inventure_r5:HEATER_'+pin for pin in ('T0', 'T1', 'PC8', 'PC9')}
    heater_blocks = []
    adjustable = []
    for source, data in texts.items():
        if not source.is_relative_to(main.parent) or source.parent == folder:
            continue
        for name, start, end, body in blocks(data):
            if counts[name] != 1:
                continue  # Keep existing include overrides at their original positions.
            if active.has_section(name) and active.get(name, 'heater_pin', fallback='') in heater_pins:
                # Keep the original header for Klippy object initialization.
                # SAVE_CONFIG can comment tunable options only in the main file.
                lines = body.splitlines(keepends=True)
                pins = [line for line in lines[1:] if HEATER_OPTIONS.match(line)]
                tune = [line for line in lines[1:] if source != main and PID_OPTIONS.match(line)]
                if pins:
                    heater_blocks.append(lines[0].rstrip()+'\n'+''.join(pins))
                if tune:
                    adjustable.append(lines[0].rstrip()+'\n'+''.join(tune))
                if pins or tune:
                    edits[source].append((start, end, ''.join(line for line in lines if line not in pins+tune)))
                continue
            if name == 'inventure_oled':
                group = 'display.cfg'
            elif source == main and name in SERVICES:
                group = 'services.cfg'
            elif source == main and name.lower().startswith('gcode_macro '):
                macro = name.split(None, 1)[1].upper()
                if macro not in published:
                    continue
                group = 'calibration.cfg' if macro in PID_BUTTONS else 'operations.cfg'
            else:
                continue
            path = destination(group)
            first = not already_included(path) and not grouped[path]
            grouped[path].append(body)
            edits[source].append((start, end, include(path, source) if first else ''))

    def append_main(content):
        marker = FOOTER.search(texts[main])
        index = marker.start() if marker else len(texts[main])
        edits[main].append((index, index, '\n'+content+'\n'))

    def append_main_include(path):
        if not already_included(path):
            append_main(include(path, main))

    if adjustable:
        append_main('\n'.join(adjustable))
    if heater_blocks:
        heaters = destination('heaters.cfg')
        grouped[heaters].extend(heater_blocks)
        if heaters not in included:
            append_main_include(heaters)
    additions = defaultdict(dict)
    sensor_file = destination('temperature_sensors.cfg')
    sensor_data = (ROOT/'config/inventure_temperature_sensors.cfg').read_bytes()
    if sensor_file.exists():
        reader = module.ConfigFileReader()
        def curve(data):
            cfg = reader.build_fileconfig(data.decode(), str(sensor_file))
            if cfg.sections() != ['inventure_mcu'] or set(cfg.options('inventure_mcu')) != set(MAP_KEYS):
                return None
            options = cfg['inventure_mcu']
            rows = [tuple(map(int, row.split(','))) for row in options['thermistor_thresholds'].splitlines() if row.strip()]
            return options.getint('nozzle_reference_mv'), options.getint('nozzle_mv_per_c'), rows
        existing = sensor_file.read_bytes()
        if curve(existing) != curve(sensor_data):
            raise ValueError('Existing sensor map differs from the R13 MCU: '+str(sensor_file))
        sensor_data = existing
    if sensor_file not in included:
        if any(active.has_option('inventure_mcu', key) for key in MAP_KEYS):
            raise ValueError('Sensor conversion options already belong to another configuration')
        stage(updates, sensor_file, sensor_data)
        append_main_include(sensor_file)
        additions['inventure_mcu'].update(dict.fromkeys(MAP_KEYS))

    if active.has_section('inventure_oled'):
        display = destination('display.cfg')
        policy = 'inventure' if active.has_option('inventure_oled', 'sleep_timeout') else 'minutes'
        missing = {key: value for key, value in dict(DISPLAY_DEFAULTS, timer_policy=policy).items()
                   if not active.has_option('inventure_oled', key)}
        if missing:
            defaults = ''.join(key+': '+value+'\n' for key, value in missing.items())
            if grouped.get(display):
                group = grouped[display]
                group[-1] = group[-1].rstrip('\r\n')+'\n'+defaults
            else:
                # A display section in an external or already split file keeps its
                # device options in place; the local include supplies only defaults.
                grouped[display].append('[inventure_oled]\n'+defaults)
                if display not in included:
                    append_main_include(display)
            additions['inventure_oled'].update(missing)
    for path, parts in grouped.items():
        content = own(path) or MANAGED
        stage(updates, path, (content.rstrip()+'\n\n'+'\n\n'.join(p.rstrip('\r\n') for p in parts)+'\n').encode())
    for path, replacements in edits.items():
        content = texts[path]
        for start, end, replacement in sorted(replacements, key=lambda item: item[:2], reverse=True):
            content = content[:start]+replacement+content[end:]
        stage(updates, path, content.encode())

    after, final_paths = active_klipper(module, main, updates)
    for path in set(grouped) | {sensor_file}:
        if final_paths.count(path) != 1:
            raise ValueError('Multiple or missing includes for '+str(path))
    original = {name: dict(active.items(name)) for name in active.sections()}
    result = {name: dict(after.items(name)) for name in after.sections()}
    for section, keys in additions.items():
        for key in keys:
            del result[section][key]
    if result != original:
        raise ValueError('The organized Klipper tree changes existing settings')
    return final_paths


def moonraker_files(main):
    """Follow Moonraker's includes. Do not treat backups as active configuration."""
    paths = {}
    def visit(path):
        path = path.resolve()
        if path in paths:
            return
        paths[path] = path.read_bytes().decode()
        for name, *_ in blocks(paths[path]):
            if name.startswith('include '):
                pattern = name.split(None, 1)[1]
                target = Path(pattern)
                matches = sorted(target.parent.glob(target.name) if target.is_absolute() else path.parent.glob(pattern))
                if not matches:
                    raise ValueError('No Moonraker files match '+name+' in '+str(path))
                for child in matches:
                    visit(child)
    visit(main)
    return paths


def clean_clients(body):
    lines = []
    seen = set()
    option_indent = None
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and not stripped.startswith(('#', ';')):
            indent = len(line.expandtabs()) - len(line.expandtabs().lstrip())
            if option_indent is not None and indent > option_indent:
                if stripped in seen:
                    continue
                seen.add(stripped)
            else:
                option_indent = indent if re.match(r'^trusted_clients\s*[:=]', stripped, re.I) else None
                seen.clear()
        lines.append(line)
    return ''.join(lines)


def organize_moonraker(main, klipper_files, web_root, updates):
    """Keep the effective access policy and custom updater destinations."""
    files = moonraker_files(main)
    counts = Counter(name for data in files.values() for name, *_ in blocks(data))
    removed = []
    for path, data in files.items():
        if not path.is_relative_to(main.parent):
            continue
        for name, start, end, body in reversed(list(blocks(data))):
            if counts[name] != 1:
                continue
            replacement = body
            if name == 'authorization':
                replacement = clean_clients(body)
            elif name in ('update_manager fluidd', 'update_manager fluidd-config'):
                opts = configparser.RawConfigParser(interpolation=None, inline_comment_prefixes=('#', ';'))
                opts.read_string(body)
                section = opts[name]
                repo = section.get('repo', section.get('origin', '')).rstrip('/').removesuffix('.git')
                local = section.get('path', '')
                location = Path(local).expanduser().resolve() if local else None
                if name == 'update_manager fluidd':
                    retire = (web_root is not None and section.get('type') == 'web'
                              and repo == 'fluidd-core/fluidd' and location in (None, web_root.resolve()))
                else:
                    retire = (repo == 'https://github.com/fluidd-core/fluidd-config'
                              and not any(p.name == 'fluidd.cfg' or 'fluidd-config' in p.parts or location and p.is_relative_to(location)
                                          for p in klipper_files))
                if retire:
                    removed.append(name)
                    replacement = ''
            if replacement != body:
                data = data[:start]+replacement+data[end:]
        stage(updates, path, data.encode())
    return removed
