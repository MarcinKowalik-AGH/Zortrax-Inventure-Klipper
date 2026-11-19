#!/usr/bin/env python3
"""Reproduce the field failure and load fixed configs with the pinned Klipper."""
import argparse
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def load_config(klipper, config):
    # Read configuration only: do not identify/connect an MCU or run the reactor.
    sys.path.insert(0, str(klipper/'klippy'))
    import klippy
    import reactor
    printer = klippy.Printer(reactor.Reactor(), None, {
        'config_file': str(config), 'debuginput': True,
        'start_reason': 'startup', 'software_version': 'native-nozzle-startup-test',
    })
    printer._read_config()
    heaters = printer.lookup_object('heaters')
    t0, t1 = [heaters.lookup_heater(name) for name in ('extruder', 'inventure_t1')]
    assert t0.max_temp == t1.max_temp == 310
    board = printer.lookup_object('inventure_mcu')
    assert len(board.adcs) == 8
    assert t0.sensor.channel == 0 and t1.sensor.channel == 1
    assert board.shared_adc_sensors[0] == [t0.sensor]
    assert board.shared_adc_sensors[1] == [t1.sensor]
    for _, fan in printer.lookup_objects('heater_fan'):
        fan.handle_ready()
        assert fan.heaters == [t0, t1]
    for name in ('ZORTRAX_LEVEL', 'ZORTRAX_LEVEL_NEXT', 'ZORTRAX_LEVEL_STATUS',
                 'ZORTRAX_LEVEL_SAVE', 'ZORTRAX_LEVEL_CANCEL', 'ZORTRAX_DOOR_LOCK'):
        assert printer.lookup_object('gcode_macro '+name)
    gcode = printer.lookup_object('gcode')
    assert 'ZORTRAX_PLATFORM_LEVEL' in gcode.ready_gcode_handlers
    from extras.inventure_fluidd import register_legacy_commands
    register_legacy_commands(printer, gcode)
    enabled = printer.lookup_object('gcode_macro INV_D_VERSIONS', None)
    if enabled is None:
        assert 'INV_D_WERSJE' not in gcode.ready_gcode_handlers
    else:
        assert 'INV_D_WERSJE' in gcode.ready_gcode_handlers
        assert printer.lookup_object('gcode_macro INV_D_WERSJE', None) is None
        register_legacy_commands(printer, gcode)  # keep existing command bindings
        calls = []
        enabled.template.run_gcode_from_command = calls.append
        alias = gcode.create_gcode_command('INV_D_WERSJE', 'INV_D_WERSJE SAMPLE=1', {})
        gcode.ready_gcode_handlers['INV_D_WERSJE'](alias)
        assert calls[0]['params'] == {'SAMPLE': '1'} and calls[0]['rawparams'] == 'SAMPLE=1'
        assert 'INV_R_WYBIERZ_T1' not in gcode.ready_gcode_handlers
    print(json.dumps(dict(status='CONFIG_LOADED', limits_c=[t0.max_temp,t1.max_temp],
                         adc_readers=len(board.adcs), fan_heaters=['extruder','inventure_t1'])))


def prepare_runtime(destination):
    source = b''.join(p.read_bytes() for p in sorted((ROOT/'source/parts').glob('part*.bin')))
    expected, _ = (ROOT/'source/SHA256SUMS').read_text().strip().split()
    assert sha256(source).hexdigest() == expected
    with ZipFile(io.BytesIO(source)) as kit:
        pinned = kit.read('baseline/SOURCE_FULL_KLIPPER_PINNED_P17_RC2.zip')
    with ZipFile(io.BytesIO(pinned)) as archive:
        for name in archive.namelist():
            assert not Path(name).is_absolute() and '..' not in Path(name).parts
        archive.extractall(destination)
    klipper = destination/'klipper-inventure'
    # Apply the same baseline host overlay and current adapter as the live stage.
    for folder in ('host','orange_pi'):
        for path in (ROOT/folder).iterdir():
            if not path.is_file() or path.suffix not in ('.py','.json'):
                continue
            target = klipper/'klippy/serialhdl.py' if path.name == 'serialhdl.py' else klipper/'klippy/extras'/path.name
            shutil.copyfile(path,target)
    return klipper


def check():
    spec = importlib.util.spec_from_file_location('nozzle_startup_updater',ROOT/'install-orange-pi.py')
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    with tempfile.TemporaryDirectory(prefix='inventure-nozzle-startup-') as directory:
        root = Path(directory)
        klipper = prepare_runtime(root)

        def run(config):
            return subprocess.run([sys.executable,__file__,'--klipper',str(klipper),
                                   '--config',str(config)],capture_output=True,text=True,timeout=90)

        field = root/'printer.cfg'
        field.write_bytes((ROOT/'tests/fixtures/native_nozzles_r15_startup.cfg').read_bytes())
        broken = run(field)
        assert broken.returncode != 0
        assert "Unknown temperature sensor 'Inventure R5 ADC'" in broken.stderr
        print('REPRODUCED: R15 field startup fails before shared ADC registration')
        for mode in ('repair_field','fresh_include'):
            if mode == 'fresh_include':
                (root/'hardware.cfg').write_bytes((ROOT/'config/INVENTURE_NATIVE_Z_TEST.cfg').read_bytes())
                (root/'fluidd.cfg').write_bytes((ROOT/'fluidd/inventure_01_diagnostyka.cfg').read_bytes())
                field.write_text('[include hardware.cfg]\n[include fluidd.cfg]\n\n'
                    '#*# <---------------------- SAVE_CONFIG ---------------------->\n'
                    '#*# [stepper_z]\n#*# position_endstop = 139.35\n')
            updates = updater.nozzle_limit_updates(klipper,field,True)
            updater.add_leveling_macros(klipper,field,updates)
            assert updates
            for path,(before,after) in updates.items():
                assert path.read_bytes() == before
                updater.atomic_config_write(path,after)
            assert updater.nozzle_limit_updates(klipper,field,True) == {}
            result = run(field)
            assert result.returncode == 0, result.stdout+result.stderr
            print(mode+': '+result.stdout.strip())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper',type=Path)
    parser.add_argument('--config',type=Path)
    args = parser.parse_args()
    if args.klipper is not None and args.config is not None:
        load_config(args.klipper,args.config)
    elif args.klipper is not None or args.config is not None:
        parser.error('--klipper and --config must be supplied together')
    else:
        check()
