"""Canonical M300-style roles without changing effective machine settings."""
import inventure_config_layout as layout

BASE = {'mcu','printer','inventure_mcu','idle_timeout',
        'stepper_x','stepper_y','stepper_z'}
SERVICES = {'pause_resume','virtual_sdcard'}
GEOMETRY = {'step_pin','dir_pin','enable_pin','microsteps','full_steps_per_rotation',
            'rotation_distance','step_pulse_duration','nozzle_diameter','filament_diameter',
            'max_extrude_only_distance','max_extrude_only_velocity','max_extrude_only_accel',
            'max_extrude_cross_section','min_extrude_temp'}
ORDER = ('inventure_base.cfg','inventure_temperature_sensors.cfg',
         'inventure_heater_control.cfg','inventure_heaters.cfg',
         'inventure_heater_monitors.cfg','inventure_cooling.cfg',
         'inventure_chamber.cfg','inventure_services.cfg','inventure_operations.cfg',
         'klipper_calibration.cfg','inventure_display.cfg')
HEADER = '# Zortrax Inventure. Hardware and procedures are in the included role files.\n'


def normalize_layout(module, main, updates):
    """Move physical settings once; keep the editable SAVE_CONFIG owner in main.

    Local custom sections/includes survive. Only known explicit role includes
    are ordered. The effective configuration is compared before accepting any
    edit, and the saved tail is copied byte for byte.
    """
    before, _ = layout.active_klipper(module, main, updates)
    expected = layout.config_values(before)
    source = layout.planned_bytes(main, updates).decode()
    marker = layout.FOOTER.search(source)
    active, saved = (source[:marker.start()], source[marker.start():]) if marker else (source, '')
    moved_base, moved_services, remaining, includes = [], [], [], set()
    for name, _, _, body in layout.blocks(active):
        if name in BASE:
            moved_base.append(body.strip())
        elif name in SERVICES:
            moved_services.append(body.strip())
        elif name == 'extruder':
            lines = body.splitlines(keepends=True)
            geometry, keep = [], [lines[0]]
            for line in lines[1:]:
                match = layout.OPTION.match(line)
                (geometry if match and match[1].lower() in GEOMETRY else keep).append(line)
            if geometry:
                moved_base.append('[extruder]\n'+''.join(geometry).strip())
            remaining.append(''.join(keep).strip())
        elif name.startswith('include ') and name[8:].strip() in ORDER:
            includes.add(name[8:].strip())
        else:
            remaining.append(body.strip())
    for filename, blocks in (('inventure_base.cfg', moved_base),
                             ('inventure_services.cfg', moved_services)):
        path = main.parent/filename
        content = layout.planned_bytes(path, updates).decode() if path in updates or path.exists() else layout.MANAGED
        if content and not content.startswith(layout.MANAGED):
            raise ValueError('Canonical configuration would replace an unmanaged role file: '+str(path))
        if blocks:
            # Board/axes precede the OLED in the base role. Services preserve
            # their current order and gain only the former main-file sections.
            data = (layout.MANAGED+'\n'+'\n\n'.join(blocks)+'\n\n'
                    +content.removeprefix(layout.MANAGED).strip()+'\n')
            layout.stage(updates, path, data.encode())
            includes.add(filename)
    if moved_base:
        includes.add('inventure_base.cfg')
    ordered = ['[include '+name+']' for name in ORDER if name in includes]
    result = HEADER+'\n'+'\n'.join(ordered)+'\n\n'+'\n\n'.join(filter(None,remaining))+'\n\n'+saved
    layout.stage(updates, main, result.encode())
    after, _ = layout.active_klipper(module, main, updates)
    if layout.config_values(after) != expected:
        raise ValueError('Canonical layout changed an effective printer setting')
    if saved and not layout.planned_bytes(main, updates).endswith(saved.encode()):
        raise ValueError('Canonical layout changed the saved calibration block')
    return dict(roles=[name for name in ORDER if name in includes],
                effective_settings_preserved=True, save_config_preserved=True)
