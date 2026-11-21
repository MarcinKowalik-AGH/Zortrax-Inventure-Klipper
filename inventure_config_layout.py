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
ZONE_PINS = {'inventure_r5:HEATER_T0': 't0', 'inventure_r5:HEATER_T1': 't1',
             'inventure_r5:HEATER_PC9': 'chamber_l', 'inventure_r5:HEATER_PC8': 'chamber_r'}
TUNE_LINE = re.compile(r'^(control|pid_kp|pid_ki|pid_kd|max_delta)[ \t]*[:=]', re.I)
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


# Active filenames mirror the M300 installer. The base file holds the physical
# driver and standard Klipper status. R34–R36 used the keys on the left.
LAYOUT_NAMES = {
    'display.cfg': 'inventure_base.cfg', 'temperature_sensors.cfg': 'inventure_temperature_sensors.cfg',
    'services.cfg': 'inventure_services.cfg', 'operations.cfg': 'inventure_operations.cfg',
    'calibration.cfg': 'klipper_calibration.cfg', 'heaters.cfg': 'inventure_heaters.cfg',
    'heater_control.cfg': 'inventure_heater_control.cfg', 'chamber.cfg': 'inventure_chamber.cfg',
    'cooling.cfg': 'inventure_cooling.cfg', 'heater_monitors.cfg': 'inventure_heater_monitors.cfg',
}
DISPLAY_NAME = 'inventure_display.cfg'
FORWARD = '# Inventure R37 compatibility include\n'
DISPLAY_KEYS = {'dim_minutes': 'timeout', 'sleep_minutes': 'sleep_timeout',
                'sleep_notice': 'notice_seconds', 'sleep_timeout': 'idle_timeout',
                'brightness': 'brightness', 'sleep_light': 'sleep_light', 'timer_policy': 'timer_policy'}
OPTION = re.compile(r'^[ \t]*([a-zA-Z_][\w]*)[ \t]*[:=][ \t]*([^#;\r\n]*?)([ \t]*(?:[#;][^\r\n]*)?)(\r?\n|$)')


def local_destination(path):
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValueError('Unsupported layout destination: '+str(path))
    return path


def config_values(parsed):
    return {name: dict(parsed.items(name)) for name in parsed.sections()}


def upgrade_layout_names(module, main, updates):
    """Move R36 role files in situ, retaining include order and wildcard users."""
    before, files = active_klipper(module, main, updates)
    olddir = main.parent/'inventure'
    if olddir.is_symlink():
        raise ValueError('Unsupported legacy layout directory: '+str(olddir))
    active = set(files)
    moves = {}
    for oldname, newname in LAYOUT_NAMES.items():
        old = olddir/oldname
        if old not in active:
            continue
        local_destination(old)
        data = planned_bytes(old, updates)
        if data.startswith(FORWARD.encode()):
            continue
        if oldname == 'temperature_sensors.cfg':
            reader = module.ConfigFileReader()
            sensors = reader.build_fileconfig(data.decode(), str(old))
            if sensors.sections() != ['inventure_mcu'] or set(sensors.options('inventure_mcu')) != set(MAP_KEYS):
                continue
        elif not data.startswith(MANAGED.encode()):
            continue
        moves[old] = local_destination(main.parent/newname)
    if not moves:
        return
    texts = {p: planned_bytes(p, updates).decode() for p in dict.fromkeys(files)}
    globs = [str(src.parent / name[8:].strip()) for src, data in texts.items()
             for name, *_ in blocks(data) if name.startswith('include ') and glob.has_magic(name[8:])]
    def covered(path):
        return any(path.match(os.path.abspath(pattern)) for pattern in globs)

    for src, data in texts.items():
        if not src.is_relative_to(main.parent):
            continue
        dest = moves.get(src, src)
        for name, start, _, _ in reversed(list(blocks(data))):
            if not name.startswith('include '):
                continue
            target = Path(os.path.abspath(src.parent/name[8:].strip()))
            relocated = moves.get(target, target)
            if dest == src and relocated == target:
                continue
            old_header = '['+name+']'
            if relocated != target and covered(relocated):
                new_header = '# Included by the existing configuration wildcard'
            else:
                new_header = '[include '+os.path.relpath(relocated, dest.parent)+']'
            data = data[:start] + new_header + data[start+len(old_header):]
        if dest != src:
            if (dest.is_file() or dest in updates) and planned_bytes(dest, updates) != data.encode():
                raise ValueError('Both old and new configuration files contain settings: '+str(dest))
            shim = FORWARD + ('# Loaded by the existing wildcard: '+dest.name+'\n' if covered(dest)
                              else '[include '+os.path.relpath(dest, src.parent)+']\n')
            stage(updates, src, shim.encode())
        stage(updates, dest, data.encode())
    after, visited = active_klipper(module, main, updates)
    if config_values(after) != config_values(before) or any(visited.count(p) != 1 for p in moves.values()):
        raise ValueError('Moving the legacy configuration would change active settings or include order')


def split_display_preferences(module, main, updates):
    """Use a single display preference file. Leave SPI and MCU ownership in the base."""
    parsed, files = active_klipper(module, main, updates)
    if not parsed.has_section('inventure_oled') and not parsed.has_section('inventure_display'):
        return files
    dest = local_destination(main.parent/DISPLAY_NAME)
    template = (ROOT/'config'/DISPLAY_NAME).read_text()
    files = list(dict.fromkeys(files))
    texts = {p: planned_bytes(p, updates).decode() for p in files}
    expected = config_values(parsed)
    canonical = expected.get('inventure_display', {}).copy()
    extracted = {}
    outside = set()
    # Do not disturb site-owned include overlays outside the configuration tree.
    # Their legacy values still feed the display through the physical driver.
    for src, data in texts.items():
        if not src.is_relative_to(main.parent):
            for name, _, _, body in blocks(data):
                if name == 'inventure_oled':
                    outside.update(m[1].lower() for row in body.splitlines() if (m := OPTION.match(row)))
    for src, data in list(texts.items()):
        if not src.is_relative_to(main.parent):
            continue
        for name, start, end, body in reversed(list(blocks(data))):
            if name != 'inventure_oled':
                continue
            keep = []
            for row in body.splitlines(keepends=True):
                match = OPTION.match(row)
                key = match[1].lower() if match else None
                if key not in DISPLAY_KEYS or key in outside:
                    keep.append(row)
                    continue
                target = DISPLAY_KEYS[key]
                # Use the value selected by Klipper even across multiple includes.
                value = parsed.get(name, key)
                if key in ('dim_minutes', 'sleep_minutes'):
                    value = str(60*int(value))
                extracted[target] = (value, match[3] if match[2].strip() == parsed.get(name, key) else '')
                expected[name].pop(key, None)
            data = data[:start]+''.join(keep)+data[end:]
        texts[src] = data
    adopt = dest.exists() and dest not in files
    if adopt:
        existing = dest.read_bytes().decode()
        if [name for name, *_ in blocks(existing)] != ['inventure_display'] or canonical:
            raise ValueError('Conflicting display configuration at '+str(dest))
        local = module.ConfigFileReader().build_fileconfig(existing, str(dest))
        canonical.update(local.items('inventure_display'))
        texts[dest] = existing
    # If a hand-written canonical section already exists, keep explicit choices
    # and move a unique local section to its role file with its original comments.
    local_sections = [(p,start,end,body) for p,data in texts.items()
                      for name,start,end,body in blocks(data) if name == 'inventure_display']
    covered = any(dest.match(os.path.abspath(src.parent/name[8:].strip())) for src,data in texts.items()
                  for name, *_ in blocks(data) if name.startswith('include ') and glob.has_magic(name[8:]))
    need_include = dest not in files and not covered
    content = texts.get(dest, MANAGED+'\n[inventure_display]\n')
    if len(local_sections) == 1:
        src,start,end,body = local_sections[0]
        if src != dest and src.is_relative_to(main.parent) and dest not in files:
            ins = '[include '+os.path.relpath(dest, src.parent)+']\n' if need_include else ''
            texts[src] = texts[src][:start]+ins+texts[src][end:]
            content = MANAGED+'\n'+body
            need_include = False
    rendered = []
    for line in template.splitlines(keepends=True):
        match = OPTION.match(line)
        if not match:
            rendered.append(line)
            continue
        key = match[1]
        if key in canonical or any(DISPLAY_KEYS.get(k) == key for k in outside):
            continue
        value, comment = extracted.get(key, (match[2].strip(), match[3]))
        # A customized normal limit also bounds the implicit dim contrast.
        if key == 'dim_contrast' and 'normal_contrast' in canonical:
            value = str(min(int(value), int(canonical['normal_contrast'])))
        rendered.append(key+': '+value+comment+'\n')
        canonical[key] = value
    if not local_sections and dest not in files:
        content = ''.join(rendered)
    else:
        additions = [line for line in rendered if OPTION.match(line)]
        if additions:
            sections = [(start,end) for name,start,end,_ in blocks(content) if name == 'inventure_display']
            pos = sections[-1][1] if sections else len(content)
            prefix = '' if sections else '\n[inventure_display]\n'
            content = content[:pos].rstrip()+'\n'+prefix+''.join(additions)+'\n'+content[pos:]
    if need_include:
        data = texts[main]
        tail = FOOTER.search(data)
        pos = tail.start() if tail else len(data)
        texts[main] = data[:pos].rstrip()+'\n\n[include '+dest.name+']\n\n'+data[pos:]
    texts[dest] = content
    for p, data in texts.items():
        stage(updates, p, data.encode())
    expected['inventure_display'] = canonical
    after, paths = active_klipper(module, main, updates)
    if config_values(after) != expected or paths.count(dest) != 1:
        raise ValueError('Splitting the display would change the effective panel configuration')
    return paths


def organize_klipper(module, main, updates):
    main = main.resolve()
    upgrade_layout_names(module, main, updates)
    folder = main.parent
    managed_files = {folder/name for name in (*LAYOUT_NAMES.values(), DISPLAY_NAME)}
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
        return local_destination(folder / LAYOUT_NAMES[name])

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
        if not source.is_relative_to(main.parent) or source in managed_files:
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
            if name == 'inventure_chamber chamber':
                group = 'chamber.cfg'
            elif name.startswith('heater_fan ') and active.get(name, 'pin', fallback='').startswith('inventure_r5:'):
                group = 'cooling.cfg'
            elif name.startswith('verify_heater ') and any(
                    h.split()[-1] == name.split()[-1] and active.get(h, 'heater_pin', fallback='') in heater_pins
                    for h in active.sections()):
                group = 'heater_monitors.cfg'
            elif name in ('inventure_oled', 'display_status'):
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
    if sensor_file.exists() or sensor_file in updates:
        reader = module.ConfigFileReader()
        def curve(data):
            cfg = reader.build_fileconfig(data.decode(), str(sensor_file))
            if cfg.sections() != ['inventure_mcu'] or set(cfg.options('inventure_mcu')) != set(MAP_KEYS):
                return None
            options = cfg['inventure_mcu']
            rows = [tuple(map(int, row.split(','))) for row in options['thermistor_thresholds'].splitlines() if row.strip()]
            return options.getint('nozzle_reference_mv'), options.getint('nozzle_mv_per_c'), rows
        existing = planned_bytes(sensor_file, updates)
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
        add_status = not any(active.has_section(name) for name in ('display_status', 'display'))
        if add_status:
            if not grouped[display] and not already_included(display):
                append_main_include(display)
            grouped[display].append('[display_status]')
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
    if active.has_section('inventure_oled') and add_status:
        original['display_status'] = {}
    result = {name: dict(after.items(name)) for name in after.sections()}
    for section, keys in additions.items():
        for key in keys:
            del result[section][key]
    if result != original:
        raise ValueError('The organized Klipper tree changes existing settings')
    migrate_display_defaults(display if active.has_section('inventure_oled') else None, updates)
    split_display_preferences(module, main, updates)
    return organize_heater_profiles(module, main, folder, updates)


def planned_bytes(path, updates):
    return updates[path][1] if path in updates else path.read_bytes()


def migrate_display_defaults(path, updates):
    if path is None or not (path in updates or path.is_file()):
        return
    data = planned_bytes(path, updates)
    # R34 emitted this exact contiguous block for a display without timer options.
    # Keep a customized file and any explicit choices in the separate settings JSON.
    signature = b'dim_minutes: 5\nsleep_minutes: 15\nbrightness: 16\ntimer_policy: minutes\n'
    if not data.startswith(MANAGED.encode()):
        return
    if data.count(signature) == 1:
        data = data.replace(signature, b'brightness: 16\ntimer_policy: inventure\n')
    text = data.decode()
    for name, start, end, body in reversed(list(blocks(text))):
        if name == 'inventure_oled':
            # Both spellings select the same launcher in the running driver.
            body = re.sub(r'^(menu_profile[ \t]*[:=][ \t]*)m300(?=[ \t]*(?:[#;]|\r?$))',
                          r'\1inventure', body, flags=re.I | re.M)
            text = text[:start] + body + text[end:]
    sections = list(blocks(text))
    duplicates = [(start, end, body) for name, start, end, body in sections if name == 'inventure_oled']
    if len(duplicates) > 1 and not any(name.startswith('include ') for name, *_ in sections):
        # Keep preferences inside the physical OLED section even if the
        # independent display_status section lies between R34 defaults.
        first_end = duplicates[0][1]
        options = []
        for start, end, body in reversed(duplicates[1:]):
            options.append(body[HEADER.match(body).end():])
            text = text[:start] + text[end:]
        text = text[:first_end] + ''.join(reversed(options)) + text[first_end:]
    stage(updates, path, text.encode())


def organize_heater_profiles(module, main, folder, updates):
    """Move each board heater's baseline. Native SAVE_CONFIG overrides it after tuning."""
    parsed, paths = active_klipper(module, main, updates)
    path = folder / LAYOUT_NAMES['heater_control.cfg']
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValueError('Unsupported heater profile destination: '+str(path))
    texts = {p: planned_bytes(p, updates).decode() for p in dict.fromkeys(paths)}
    candidates = defaultdict(list)
    for source, data in texts.items():
        for name, start, end, body in blocks(data):
            zone = ZONE_PINS.get(parsed.get(name, 'heater_pin', fallback=''))
            if not zone or parsed.get(name, 'sensor_type', fallback='') != 'Inventure R5 ADC':
                continue
            for line in body.splitlines(keepends=True):
                opt = TUNE_LINE.match(line)
                if opt:
                    candidates[name].append((source, start, end, line, opt[1].lower()))
    marker = FOOTER.search(texts[main])
    tail = texts[main][marker.start():] if marker else ''
    first_section = re.search(r'^#\*# \[', tail, re.M)
    saved_tail = tail[first_section.start():] if first_section else ''
    saved = module.ConfigFileReader().build_fileconfig(
        '\n'.join(line[4:] for line in saved_tail.splitlines() if line.startswith('#*# ')), str(main))
    existing = planned_bytes(path, updates).decode() if path in updates or path.exists() else ''
    desired = {name: dict(parsed.items(name)) for name in parsed.sections()}
    additions = []
    removals = defaultdict(set)
    for name, entries in candidates.items():
        zone = ZONE_PINS[parsed.get(name, 'heater_pin')]
        keys = [key for *_, key in entries]
        profiles = desired['inventure_mcu']
        if (len(set(keys)) != len(keys) or 'control' not in keys
                or any(zone+'_'+key in profiles or saved.has_option(name, key) for key in keys)
                or any(not src.is_relative_to(main.parent) for src, *_ in entries)):
            continue  # Keep an existing overlay or a site-owned external configuration.
        mode = parsed.get(name, 'control')
        allowed = {'pid': {'control','pid_kp','pid_ki','pid_kd'}, 'watermark': {'control','max_delta'}}
        if not set(keys) <= allowed.get(mode, set()):
            continue
        additions.append('# '+name+' ('+zone+')')
        for source, start, end, line, key in entries:
            option = zone+'_'+key
            additions.append(TUNE_LINE.sub(lambda m: option+m[0][len(m[1]):], line, count=1).rstrip('\r\n'))
            profiles[option] = desired[name].pop(key)
            removals[(source, start, end)].add(line)
    if not additions:
        return paths
    if existing and not existing.startswith(MANAGED):
        raise ValueError('Existing heater profile file is site-owned: '+str(path))
    for (source, start, end), lines in sorted(removals.items(), reverse=True):
        data = texts[source]
        body = ''.join(line for line in data[start:end].splitlines(keepends=True) if line not in lines)
        texts[source] = data[:start]+body+data[end:]
    if path not in paths and not any(
            Path(path).match(os.path.abspath(os.path.join(source.parent, name[8:].strip())))
            for source, data in texts.items() for name, *_ in blocks(data) if name.startswith('include ')):
        text = texts[main]
        marker = FOOTER.search(text)
        pos = marker.start() if marker else len(text)
        texts[main] = text[:pos].rstrip()+'\n\n[include '+path.name+']\n\n'+text[pos:]
    for source, data in texts.items():
        stage(updates, source, data.encode())
    header = existing.rstrip() if existing else MANAGED+'# Native heater baselines. Klipper SAVE_CONFIG has precedence.\n[inventure_mcu]'
    stage(updates, path, (header+'\n'+'\n'.join(additions)+'\n').encode())
    final, files = active_klipper(module, main, updates)
    if files.count(path) != 1 or {s: dict(final.items(s)) for s in final.sections()} != desired:
        raise ValueError('Heater profile migration changed the effective settings')
    return files


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
