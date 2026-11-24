#!/usr/bin/env python3
"""Load the staged R38.6 configuration without connecting or running Klippy.

This is a startup parser check, not hardware acceptance.  In particular, no
connect/ready callbacks, G-code commands, or SAVE_CONFIG operations are run.
"""
import argparse
from contextlib import redirect_stdout
import inspect
import json
from pathlib import Path
import sys


EXPECTED_REVISION = 'RC8-CONTROL-R7-HOMING-R38.6'
HOME_ROUTES = {
    'INVENTURE_HOME_X': 'X',
    'INVENTURE_HOME_Y': 'Y',
    'INVENTURE_HOME_XY': 'XY',
    'INVENTURE_HOME_Z': 'Z',
    'INVENTURE_HOME_ALL': 'XYZ',
}
REQUIRED_MACROS = (
    'CANCEL_PRINT', 'END_PRINT', 'PID_HOTEND', 'PID_SAVE', 'PID_STATUS',
    'OLED_TIMERS', 'INVENTURE_MAIN_MENU', 'INVENTURE_OLED_BRIGHTNESS',
    'INVENTURE_OLED_ICONS', 'INVENTURE_NOZZLE_SELECT', 'INVENTURE_PLATFORM_UP',
    'INVENTURE_LEVEL', 'INVENTURE_LEVEL_NEXT', 'INVENTURE_LEVEL_SAVE',
    'INVENTURE_LEVEL_STOP', 'INVENTURE_LOAD', 'INVENTURE_UNLOAD',
    'INVENTURE_FILAMENT_SAVE', 'INVENTURE_JAM_STATUS', 'INVENTURE_JAM_CANCEL',
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_config(klipper, config):
    klipper = Path(klipper).expanduser().resolve(strict=True)
    config = Path(config).expanduser().resolve(strict=True)
    require(config.is_file(), 'Expected a configuration file')
    require((klipper / 'klippy/klippy.py').is_file(), 'Expected a Klipper host directory')
    sys.path.insert(0, str(klipper / 'klippy'))
    import klippy
    import reactor

    require(Path(klippy.__file__).resolve() == klipper / 'klippy/klippy.py',
            'A different Klipper runtime was already imported')
    loop = reactor.Reactor()
    printer = None
    try:
        printer = klippy.Printer(loop, None, {
            'config_file': str(config), 'debuginput': True,
            'start_reason': 'startup', 'software_version': 'offline-bootstrap-validation',
        })
        printer._read_config()
        effective = printer.lookup_object('configfile').get_status(0.)['config']
        require(effective['printer']['kinematics'] == 'corexy',
                'The complete native CoreXY profile is required')
        kin = printer.lookup_object('toolhead').get_kinematics()
        require(type(kin).__name__ == 'CoreXYKinematics', 'Native CoreXY did not load')

        board = printer.lookup_object('inventure_mcu')
        adapter = sys.modules[type(board).__module__]
        require(Path(adapter.__file__).resolve() == klipper / 'klippy/extras/inventure_mcu.py',
                'The Inventure adapter came from a different host directory')
        require(adapter.HOST_REVISION == EXPECTED_REVISION,
                'Expected ' + EXPECTED_REVISION + ', loaded ' + str(adapter.HOST_REVISION))
        require(board.allow_actuators, 'The diagnostic actuator-disabled profile is still active')
        native = printer.lookup_object('inventure_native_z')
        require(native.board is board, 'Native Home has a different MCU owner')
        for name in ('inventure_oled', 'inventure_oled_preview', 'inventure_display',
                     'display_status', 'inventure_chamber chamber', 'pause_resume',
                     'virtual_sdcard'):
            printer.lookup_object(name)
        heaters = printer.lookup_object('heaters')
        for name in ('extruder', 'inventure_t1', 'chamber_l', 'chamber_r'):
            heaters.lookup_heater(name)

        gcode = printer.lookup_object('gcode')
        for name in REQUIRED_MACROS:
            printer.lookup_object('gcode_macro ' + name)
        for name in ('INVENTURE_HOME_REQUEST', 'INVENTURE_NATIVE_HOME',
                     'INVENTURE_NATIVE_HOME_Z'):
            handler = gcode.ready_gcode_handlers.get(name)
            if inspect.isfunction(handler):
                # The pinned Klipper wraps extended-command handlers to parse
                # their parameters. Inspect that binding without calling it.
                handler = inspect.getclosurevars(handler).nonlocals.get('origfunc', handler)
            require(getattr(handler, '__self__', None) is native,
                    name + ' is not owned by native Inventure Home')
        for name, axes in HOME_ROUTES.items():
            macro = printer.lookup_object('gcode_macro ' + name)
            # Render only. Running these strings would move/heat the printer.
            rendered = macro.template.render({'params': {}, 'rawparams': ''})
            require(rendered.split() == ['INVENTURE_HOME_REQUEST', 'AXES=' + axes],
                    name + ' does not use the current single Home request')

        version_file = klipper / 'klippy/.version'
        version = version_file.read_text().strip() if version_file.is_file() else None
        return {
            'status': 'VALIDATED_CONFIG', 'host_revision': adapter.HOST_REVISION,
            'klipper_path': str(klipper), 'config_path': str(config),
            'software_version': version, 'kinematics': effective['printer']['kinematics'],
            'serial': effective['mcu']['serial'], 'baud': int(effective['mcu']['baud']),
            'macro_count': len(printer.lookup_objects('gcode_macro')),
            'home_routes': HOME_ROUTES, 'mcu_connected': False,
            'gcode_executed': False, 'hardware_acceptance': False,
        }
    finally:
        try:
            # virtual_sdcard allocates an I/O worker during configuration load.
            # Join it even when the parser or any release check above fails.
            dispatcher = printer.lookup_object('aio_executor', None) if printer else None
            if dispatcher is not None:
                dispatcher._handle_disconnect()
        finally:
            loop.finalize()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        # Keep stdout machine-readable, including on a loader error.
        with redirect_stdout(sys.stderr):
            result = validate_config(args.klipper, args.config)
    except Exception as exc:
        print(json.dumps({'status': 'INVALID_CONFIG', 'error': str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
