"""Prepare an isolated Inventure configuration; never touch a service or MCU.

The diagnostic RC7/RC8 skeleton contains no commissioned machine geometry.
Replacing it with the recorded field profile therefore requires an explicit
restore_field_profile=True. Existing native configurations keep their values,
includes, SAVE_CONFIG footer and operator macros. That explicit option also
restores this printer's published, saved nozzle calibration when it is absent.
Public CLI authorization, runtime validation, activation and rollback belong
to the calling installer.
"""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import inventure_config_layout as layout


ROOT = Path(__file__).resolve().parent
FIELD_PROFILE = ROOT / 'config/INVENTURE_R38_FIELD_RECOVERY.cfg'
PUBLISHED_PAD_SOURCE = 'docs/acceptance/INVENTURE_PAD_R23_2026-11-19_PROBA_2.json'
PUBLISHED_PAD_SOURCE_SHA256 = '75669f6ea0156dd500abe91de78007433889a852a8c4bc9b747ae17942c1afcf'
PUBLISHED_PAD_RUN_ID = '4eed5e30be704a6ca57440e1e7f27b44'
PUBLISHED_PAD_SHA256 = '23e7455115071b3f8500d94f658a8c87e40c25aee8abd6dff0b4ac6848b1d157'
STATE_FILES = (
    'inventure_menu_settings.json',
    'inventure_filament_settings.json',
    'inventure_nozzle_xy.json',
    'inventure_nozzle_pad.json',
    'inventure_platform_level.json',
)
OPTION = re.compile(r'^([ \t]*)([^#;:=\s][^:=]*?)[ \t]*[:=][ \t]*(.*?)(\r?\n)?$')
BOARD_HEATER_KEYS = tuple(zone + '_' + key for zone in ('t0', 't1', 'chamber_l', 'chamber_r')
                          for key in ('control', 'max_delta', 'pid_kp', 'pid_ki', 'pid_kd'))


def _updater():
    spec = importlib.util.spec_from_file_location(
        'inventure_bootstrap_updater', ROOT / 'install-orange-pi.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _regular_bytes(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular configuration file: ' + str(path))
    return path.read_bytes()


def _published_nozzle_pad_record():
    """Reconstruct only the authenticated field run's production SAVE record."""
    source = _regular_bytes(ROOT / PUBLISHED_PAD_SOURCE)
    if sha256(source).hexdigest() != PUBLISHED_PAD_SOURCE_SHA256:
        raise ValueError('Published nozzle calibration source checksum mismatch')
    accepted = json.loads(source)
    if (not isinstance(accepted, dict) or accepted.get('run_id') != PUBLISHED_PAD_RUN_ID
            or accepted.get('phase') != 'saved' or accepted.get('mode') != 'FULL'):
        raise ValueError('Published nozzle calibration is not the accepted saved FULL run')
    # These are the fields written by InventureMCU.cmd_nozzle_pad_calibrate
    # ACTION=SAVE. The acceptance status exposes prep/scan at its top level.
    record = {key: accepted[key] for key in (
        'run_id', 'measurement_target_c', 'sample_temperatures_c',
        'measurements', 'candidate', 'retract_e_steps')}
    record.update({key: accepted[key] for key in (
        'xy_measurements', 'z_release_diagnostics', 'hot_targets_c',
        'measurement_settings', 'elapsed_s') if key in accepted})
    record.update(prep=accepted['prep'], scan=accepted['scan'], schema=1)
    data = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode('utf-8')
    if sha256(data).hexdigest() != PUBLISHED_PAD_SHA256:
        raise ValueError('Restored nozzle calibration record checksum mismatch')
    return data, dict(source=PUBLISHED_PAD_SOURCE, run_id=PUBLISHED_PAD_RUN_ID,
                      source_sha256=PUBLISHED_PAD_SOURCE_SHA256,
                      sha256=PUBLISHED_PAD_SHA256)


def _read_tree(module, main):
    """Read one consistent include tree and record exactly the bytes used."""
    files = {}

    class Reader(module.ConfigFileReader):
        def read_config_file(self, filename):
            path = Path(filename).absolute()
            canonical = path.resolve()
            if not canonical.is_relative_to(main.parent):
                raise ValueError('Configuration include is outside its directory: ' + str(path))
            value = _regular_bytes(path)
            path = canonical
            if path in files and files[path] != value:
                raise ValueError('Configuration changed while being read: ' + str(path))
            files[path] = value
            return value.decode('utf-8').replace('\r\n', '\n')

    reader = Reader()
    parsed = reader.build_fileconfig_with_includes(reader.read_config_file(main), str(main))
    return parsed, files


def _copy_includes(files, source_root, destination):
    for source, data in files.items():
        relative = source.relative_to(source_root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = data.decode('utf-8')
        # Includes must refer to the copied tree, including spellings with '..'.
        for name, start, _, _ in reversed(list(layout.blocks(text))):
            if not name.startswith('include '):
                continue
            spec = name[8:].strip()
            try:
                absolute = Path(os.path.abspath(os.path.join(source.parent, spec)))
                copied = absolute.relative_to(source_root)
            except ValueError as exc:
                raise ValueError('Include is outside its directory: ' + spec) from exc
            header = layout.HEADER.match(text, start)
            replacement = text[start:header.end()].replace(
                '[' + name + ']',
                '[include ' + os.path.relpath(destination / copied, target.parent) + ']', 1)
            text = text[:start] + replacement + text[header.end():]
        target.write_bytes(text.encode('utf-8'))


def _option_owners(module, main):
    owners, sections = {}, {}
    plain = module.ConfigFileReader()

    class Reader(module.ConfigFileReader):
        def append_fileconfig(self, fileconfig, data, filename):
            parsed = plain.build_fileconfig(data, filename)
            for section in parsed.sections():
                sections[section] = Path(filename).resolve()
                for option in parsed.options(section):
                    owners[section, option] = Path(filename).resolve()
            super().append_fileconfig(fileconfig, data, filename)

    reader = Reader()
    parsed = reader.build_fileconfig_with_includes(reader.read_config_file(main), str(main))
    return parsed, owners, sections


def _set_scalar(module, main, section, option, value):
    parsed, owners, sections = _option_owners(module, main)
    if not parsed.has_section(section):
        raise ValueError('Missing [' + section + '] in the staged configuration')
    path = owners.get((section, option), sections[section])
    text = path.read_text()
    matches = [b for b in layout.blocks(text) if b[0] == section]
    found = False
    for _, start, end, body in reversed(matches):
        lines = body.splitlines(keepends=True)
        for index in range(len(lines) - 1, 0, -1):
            match = OPTION.match(lines[index])
            if match and match[2].strip().lower() == option:
                comment = re.search(r'\s+[#;].*', match[3])
                lines[index] = (match[1] + option + ': ' + str(value)
                                + (comment[0] if comment else '') + (match[4] or '\n'))
                found = True
                break
        if found:
            text = text[:start] + ''.join(lines) + text[end:]
            break
    if not found:
        _, start, end, body = matches[-1]
        lines = body.splitlines(keepends=True)
        lines.insert(1, option + ': ' + str(value) + '\n')
        text = text[:start] + ''.join(lines) + text[end:]
    path.write_text(text)


def _remove_options(data, section, options):
    """Remove selected options including multiline LUT rows, retaining other text."""
    current, skipping = None, None
    result = []
    for line in data.splitlines(keepends=True):
        header = layout.HEADER.match(line.rstrip('\n'))
        if header:
            current = header[1]
            skipping = None
        if skipping is not None:
            stripped = line.strip()
            if not stripped or stripped.startswith(('#', ';')):
                result.append(line)
                continue
            if len(line) - len(line.lstrip()) > skipping:
                continue
            skipping = None
        match = OPTION.match(line)
        if current == section and match and match[2].strip().lower() in options:
            skipping = len(match[1])
            continue
        result.append(line)
    return ''.join(result)


def _split_board_options(module, main, options, filename):
    """Restore the existing layout's sensor and heater-profile file roles."""
    active, paths = layout.active_klipper(module, main, {})
    selected = {key: active.get('inventure_mcu', key)
                for key in options if active.has_option('inventure_mcu', key)}
    target = main.parent / filename
    if not selected or target in paths:
        return
    if target.exists():
        raise ValueError('Conflicting staged layout file: ' + str(target))
    for path in dict.fromkeys(paths):
        text = path.read_text()
        after = _remove_options(text, 'inventure_mcu', set(selected))
        if after != text:
            path.write_text(after)
    rows = [layout.MANAGED.rstrip(), '# Preserved field settings.', '[inventure_mcu]']
    for name, value in selected.items():
        if '\n' in value:
            rows.append(name + ':\n' + '\n'.join('    ' + v for v in value.splitlines() if v.strip()))
        else:
            rows.append(name + ': ' + value)
    target.write_text('\n'.join(rows) + '\n')
    updater = _updater()
    main.write_bytes(updater.append_config_section(
        main.read_bytes(), ('[include ' + filename + ']').encode()))


def stage_config(host_path, destination_directory, source_config, *, serial, baud,
                 gcodes_path, restore_field_profile=False):
    """Return an offline staged config and provenance manifest.

    ``destination_directory`` must not exist. ``source_config`` is the current
    host's main config. A native CoreXY config is copied; a diagnostic ``none``
    config requires explicit field recovery. UART and G-code paths are always
    supplied by the caller, never copied from the historical log. Only this new
    destination is written. Existing sidecar state is copied byte-for-byte.
    Explicit field recovery also restores the missing nozzle-pad file from
    this printer's authenticated published SAVE result, including for a native
    config. Other absent state stays absent. Parsing does not connect a MCU.
    """
    host = Path(host_path).expanduser().resolve()
    source = Path(source_config).expanduser().absolute()
    destination = Path(destination_directory).expanduser().absolute()
    gcodes = Path(gcodes_path).expanduser().absolute()
    if source.is_symlink() or source.parent.resolve() != source.parent:
        raise ValueError('Use the regular source configuration directory')
    if destination.exists() or destination.is_symlink():
        raise ValueError('The staging destination already exists: ' + str(destination))
    if destination.is_relative_to(source.parent):
        raise ValueError('Stage outside the existing configuration directory')
    if (not isinstance(serial, str) or not serial.startswith('/dev/')
            or any(c in serial for c in '\r\n#;[]') or any(c.isspace() for c in serial)):
        raise ValueError('Select one explicit /dev UART path')
    if type(baud) is not int or not 1 <= baud <= 4000000:
        raise ValueError('Select an explicit positive UART baud rate')
    if any(c in str(gcodes) for c in '\r\n#;'):
        raise ValueError('Invalid G-code directory')
    updater = _updater()
    module = updater.klipper_config_module(host)
    active, originals = _read_tree(module, source)
    kinematics = active.get('printer', 'kinematics', fallback='')
    if not active.has_section('inventure_mcu'):
        raise ValueError('The source is not an Inventure host configuration')
    recovering = kinematics == 'none'
    if recovering and not restore_field_profile:
        raise ValueError('Diagnostic configuration requires explicit --restore-field-profile')
    if not recovering and (kinematics != 'corexy' or not active.has_section('inventure_native_z')):
        raise ValueError('Expected the Inventure diagnostic or native CoreXY configuration')

    sidecars, missing = {}, []
    names = set(STATE_FILES) | {p.name for p in source.parent.glob('inventure_*.json')}
    for name in sorted(names):
        path = source.parent / name
        if path.exists() or path.is_symlink():
            sidecars[name] = _regular_bytes(path)
        else:
            missing.append(name)
    snapshot = {str(path): sha256(data).hexdigest() for path, data in originals.items()}
    snapshot.update({str(source.parent / name): sha256(data).hexdigest()
                     for name, data in sidecars.items()})
    snapshot.update({str(source.parent / name): None for name in missing})

    restored, restored_provenance = {}, {}
    pad_name = 'inventure_nozzle_pad.json'
    if restore_field_profile and pad_name in missing:
        restored[pad_name], restored_provenance[pad_name] = _published_nozzle_pad_record()
        missing.remove(pad_name)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.' + destination.name + '-', dir=destination.parent))
    try:
        if recovering:
            main = temporary / 'printer.cfg'
            main.write_bytes(FIELD_PROFILE.read_bytes())
        else:
            _copy_includes(originals, source.parent, temporary)
            main = temporary / source.name
        for name, data in (sidecars | restored).items():
            (temporary / name).write_bytes(data)
        _set_scalar(module, main, 'mcu', 'serial', serial)
        _set_scalar(module, main, 'mcu', 'baud', baud)
        staged, _ = layout.active_klipper(module, main, {})
        if staged.has_section('inventure_oled'):
            # Requested operator layout: status -> Klipper / Zortrax Inventure.
            # Other display preferences and the field tuning keep their owners.
            _set_scalar(module, main, 'inventure_oled', 'oem_menu', 'True')
            _set_scalar(module, main, 'inventure_oled', 'menu_profile', 'inventure')
            _set_scalar(module, main, 'inventure_oled', 'oem_start_screen', 'klipper')
        if not staged.has_section('virtual_sdcard'):
            main.write_bytes(updater.append_config_section(main.read_bytes(), b'[virtual_sdcard]'))
        _set_scalar(module, main, 'virtual_sdcard', 'path', str(gcodes))
        _split_board_options(module, main, layout.MAP_KEYS, 'inventure_temperature_sensors.cfg')
        _split_board_options(module, main, BOARD_HEATER_KEYS, 'inventure_heater_control.cfg')
        changes = updater.nozzle_limit_updates(host, main, True, True)
        updater.add_fluidd_controls(host, main, changes, dashboard=True)
        layout.organize_klipper(module, main, changes)
        from inventure_config_cleanup import normalize_layout
        cleanup = normalize_layout(module, main, changes)
        for path, (before, after) in changes.items():
            if not path.is_relative_to(temporary):
                raise ValueError('Configuration planner escaped the staging directory')
            current = path.read_bytes() if path.exists() else None
            if current != before:
                raise ValueError('Staged configuration changed during planning')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(after)
        effective, loaded = layout.active_klipper(module, main, {})
        relative_main = main.relative_to(temporary)
        relative_files = sorted(str(p.relative_to(temporary)) for p in temporary.rglob('*') if p.is_file())
        # Activation copies these files beside the existing main config. Also
        # cover an inactive old role file (or its absence) in the race check.
        for relative in relative_files:
            target = source.parent / relative
            if str(target) not in snapshot:
                snapshot[str(target)] = (sha256(_regular_bytes(target)).hexdigest()
                                         if target.exists() or target.is_symlink() else None)
        manifest = dict(
            config_path=str(destination / relative_main),
            profile_source=str(FIELD_PROFILE if recovering else source),
            recovery_profile_used=recovering,
            profile_sha256=sha256(FIELD_PROFILE.read_bytes()).hexdigest() if recovering else None,
            files=relative_files,
            loaded_configs=sorted({str(p.relative_to(temporary)) for p in loaded}),
            copied_state_files=sorted(sidecars), missing_state_files=missing,
            restored_state_files=restored_provenance,
            source_snapshot=snapshot,
            macros=sorted(s.split(None, 1)[1] for s in effective.sections()
                          if s.lower().startswith('gcode_macro ')),
            configuration_cleanup=cleanup,
            serial=serial, baud=baud, gcodes_path=str(gcodes),
        )
        if destination.exists() or destination.is_symlink():
            raise ValueError('The staging destination appeared during preparation')
        temporary.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(temporary)
        raise
