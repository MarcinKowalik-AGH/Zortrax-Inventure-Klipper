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


def load_config(klipper, config, save_test_pid=False):
    # Read configuration only: do not identify/connect an MCU or run the reactor.
    sys.path.insert(0, str(klipper/'klippy'))
    import klippy
    import reactor
    printer = klippy.Printer(reactor.Reactor(), None, {
        'config_file': str(config), 'debuginput': True,
        'start_reason': 'startup', 'software_version': 'native-nozzle-startup-test',
    })
    try:
        printer._read_config()
        check_loaded(printer, config)
        if save_test_pid:
            board = printer.lookup_object('inventure_mcu')
            assert board.temperature_map_source == 'printer_config'
            saved = printer.lookup_object('configfile')
            for heater in ('extruder', 'heater_generic inventure_t1', 'heater_generic chamber_l', 'heater_generic chamber_r'):
                for key, value in dict(control='pid', pid_kp='18.125', pid_ki='0.718', pid_kd='110.5').items():
                    saved.set(heater, key, value)
            # Exercise the actual SAVE_CONFIG writer against the temporary field config.
            from types import SimpleNamespace
            printer.lookup_object('gcode').request_restart = lambda reason: None
            saved.autosave.cmd_SAVE_CONFIG(SimpleNamespace(error=ValueError))
    finally:
        # virtual_sdcard starts an I/O thread before Klipper connects.
        # This configuration-only test must release it without entering the reactor.
        dispatcher = printer.lookup_object('aio_executor', None)
        if dispatcher is not None:
            dispatcher._handle_disconnect()


def check_loaded(printer, config):
    heaters = printer.lookup_object('heaters')
    t0, t1 = [heaters.lookup_heater(name) for name in ('extruder', 'inventure_t1')]
    assert t0.max_temp == t1.max_temp == 310
    board = printer.lookup_object('inventure_mcu')
    assert len(board.adcs) == 8
    assert t0.sensor.channel == 0 and t1.sensor.channel == 1
    assert board.shared_adc_sensors[0] == [t0.sensor]
    assert board.shared_adc_sensors[1] == [t1.sensor]
    chamber = printer.lookup_object('inventure_chamber chamber')
    chamber.connect()
    left, right = chamber.zones
    if config.read_text().count('#*# pid_kp = 18.125') == 4:
        from extras.heaters import PID_PARAM_BASE
        for zone in (t0, t1, left, right):
            assert type(zone.control).__name__ == 'ControlPID'
            for attr, expected in (('Kp',18.125),('Ki',0.718),('Kd',110.5)):
                assert abs(getattr(zone.control, attr)*PID_PARAM_BASE - expected) < 1.e-8
    assert [z.sensor.channel for z in chamber.zones] == [2, 3]
    assert [z.mcu_pwm.name for z in chamber.zones] == ['HEATER_PC9', 'HEATER_PC8']
    assert all(type(z.control).__name__ == 'ControlPID' for z in chamber.zones)
    assert len(board.shared_adc_sensors[2]) == len(board.shared_adc_sensors[3]) == 1
    assert 'inventure_chamber chamber' in heaters.available_sensors
    assert printer.lookup_object('inventure_oled_preview')
    from extras.inventure_oled_graphics import status_footer
    native = printer.lookup_object('inventure_oled').native_status
    display = printer.lookup_object('display_status')
    now = printer.get_reactor().monotonic()
    assert 'display_status' in printer.lookup_object('configfile').get_status(now)['config']
    native_gcode = printer.lookup_object('gcode')
    native_gcode.ready_gcode_handlers['M117'](native_gcode.create_gcode_command('M117', 'M117 Display test', {}))
    native_gcode.ready_gcode_handlers['M73'](native_gcode.create_gcode_command('M73', 'M73 P25', {'P':'25'}))
    assert display.get_status(now) == {'message': 'Display test', 'progress': .25}
    display.progress = None
    for message in ('Ready', 'The selected nozzle is ready for the next calibration point.'):
        display.message = None if message == 'Ready' else message
        wire = native.render(now, board)
        assert wire[48*128:] == status_footer(message)
        assert wire[:48*128] == native._source_wire[:48*128] and any(wire[:48*128])
    display.message = None
    for _, fan in printer.lookup_objects('heater_fan'):
        fan.handle_ready()
        assert fan.heaters == ([t0,t1,left,right] if fan.fan.mcu_fan.name in ('F2','F3') else [t0,t1])
    for name in ('INVENTURE_LEVEL', 'INVENTURE_LEVEL_NEXT', 'INVENTURE_LEVEL_PROGRESS',
                 'INVENTURE_LEVEL_SAVE', 'INVENTURE_LEVEL_STOP', 'INVENTURE_DOOR_LOCK',
                 'INVENTURE_JAM_REPORT', 'INVENTURE_JAM_STATUS', 'INVENTURE_JAM_SOURCE',
                 'INVENTURE_JAM_RETRY', 'INVENTURE_JAM_CONFIRM_FLOW', 'INVENTURE_JAM_CANCEL',
                 'INVENTURE_JAM_SERVICE_TEST', 'INVENTURE_JAM_MISSING', 'INVENTURE_JAM_BROKEN',
                 'INVENTURE_JAM_CLEARED', 'INVENTURE_JAM_LOAD', 'INVENTURE_JAM_CONTINUE',
                 'INVENTURE_FILAMENT_SAVE', 'INVENTURE_CASSETTE_STATUS', 'INVENTURE_LOAD', 'INVENTURE_UNLOAD',
                 'INVENTURE_FEED_MORE', 'INVENTURE_RETRACT_MORE', 'INVENTURE_FILAMENT_FINISH', 'INVENTURE_CASSETTE_JOG'):
        assert printer.lookup_object('gcode_macro '+name)
    gcode = printer.lookup_object('gcode')
    for name in ('CANCEL_PRINT', 'END_PRINT', 'PID_HOTEND', 'PID_SAVE', 'PID_STATUS',
                 'OLED_TIMERS', 'INVENTURE_MAIN_MENU', 'INVENTURE_OLED_BRIGHTNESS', 'INVENTURE_OLED_ICONS',
                 'INVENTURE_HOME_ALL', 'INVENTURE_NOZZLE_SELECT', 'INVENTURE_PLATFORM_UP'):
        assert printer.lookup_object('gcode_macro '+name)
    for name in ('BED_MESH', 'PID_BED', 'START_PRINT'):
        assert printer.lookup_object('gcode_macro '+name, None) is None
    # Klipper defers rename_existing to connect. Exercise that hook without connecting an MCU.
    cancel = printer.lookup_object('gcode_macro CANCEL_PRINT')
    native_cancel = gcode.ready_gcode_handlers['CANCEL_PRINT']
    cancel.handle_connect()
    pause = printer.lookup_object('pause_resume')
    pause.is_paused = True
    alias = '_INVENTURE_CANCEL_BASE'
    gcode.ready_gcode_handlers[alias](gcode.create_gcode_command(alias, alias, {}))
    assert not pause.is_paused
    assert gcode.ready_gcode_handlers['CANCEL_PRINT'] != native_cancel
    assert 'INVENTURE_MATERIAL' in gcode.ready_gcode_handlers
    # Render the Fluidd buttons with Klipper's own Jinja environment. This also
    # checks the single-use identity passed from the open prompt to a button.
    prompt = dict(session=9542, prompt=3, tool='T1')
    context = dict(printer={'inventure_mcu': {'material_service': prompt}})
    for macro, arguments, expected in (
            ('INVENTURE_FILAMENT_SAVE', dict(TOOL='T1', LOAD_SPEED='1.4', UNLOAD_TEMP='235'),
             'ACTION=SAVE TOOL=T1 LOAD_SPEED=1.4 UNLOAD_TEMP=235'),
            ('INVENTURE_LOAD', dict(TOOL='T0'), 'ACTION=LOAD TOOL=T0 INTERACTIVE=1'),
            ('INVENTURE_UNLOAD', dict(TOOL='T1', TEMP='250'), 'ACTION=UNLOAD TOOL=T1 INTERACTIVE=1 TEMP=250'),
            ('INVENTURE_FEED_MORE', {}, 'ACTION=MORE DIR=LOAD TOOL=T1 SESSION=9542 PROMPT=3 MM=5'),
            ('INVENTURE_RETRACT_MORE', dict(MM='2'), 'ACTION=MORE DIR=UNLOAD TOOL=T1 SESSION=9542 PROMPT=3 MM=2'),
            ('INVENTURE_FILAMENT_FINISH', {}, 'ACTION=FINISH TOOL=T1 SESSION=9542 PROMPT=3'),
            ('INVENTURE_CASSETTE_JOG', dict(TOOL='T0'), 'ACTION=JOG TOOL=T0 DIR=LOAD MM=5'),
            ('INVENTURE_CASSETTE_STATUS', {}, 'ACTION=STATUS')):
        template = printer.lookup_object('gcode_macro '+macro).template
        rendered = template.render(dict(context, params=arguments))
        assert rendered.split() == ('INVENTURE_MATERIAL '+expected).split(), (macro, rendered)
    assert 'INVENTURE_PLATFORM_LEVEL' in gcode.ready_gcode_handlers
    assert 'INVENTURE_FILAMENT_JAM' in gcode.ready_gcode_handlers
    assert printer.lookup_object('pause_resume') is not None
    if config.parent.name == 'config':
        virtual_sd = printer.lookup_object('virtual_sdcard')
        assert virtual_sd.sdcard_dirname == str(config.parent.parent/'gcodes')
    from extras.inventure_fluidd import register_legacy_commands
    register_legacy_commands(printer, gcode)
    enabled = printer.lookup_object('gcode_macro INVENTURE_DIAG_VERSIONS', None)
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
    sys.path.insert(0, str(ROOT))
    group_spec = importlib.util.spec_from_file_location('dashboard_setup', ROOT/'setup-fluidd.py')
    groups = importlib.util.module_from_spec(group_spec)
    group_spec.loader.exec_module(groups)
    names = {section.split(None, 1)[1] for section, _ in printer.lookup_objects('gcode_macro')
             if section.startswith('gcode_macro ')}
    settings, assignments = groups.merge_groups({}, names)
    assert not names.intersection(('INVENTURE_HOME_XYZ', 'ZORTRAX_NOZZLE_PAD_CALIBRATE'))
    counts = {group: sum(value == group for value in assignments.values()) for group in groups.GROUPS}
    visible = sum(row['visible'] for row in settings['stored'])
    assert counts['Klipper'] == 9
    print('Dashboard macros:', counts, 'visible:', visible)
    print(json.dumps(dict(status='CONFIG_LOADED', limits_c=[t0.max_temp,t1.max_temp],
                         adc_readers=len(board.adcs), chamber_pid_zones=['chamber_l','chamber_r'], chamber_sensors=['PC1','PC2','PA4'])))


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
    patch_spec = importlib.util.spec_from_file_location('jam_operations_patch',
                    ROOT/'orange_pi/patch_jam_operations.py')
    patch = importlib.util.module_from_spec(patch_spec)
    patch_spec.loader.exec_module(patch)
    operations = klipper/'klippy/extras/inventure_operations.py'
    patched = patch.render(operations.read_bytes())
    assert sha256(patched).hexdigest() == patch.PATCHED_SHA256
    operations.write_bytes(patched)
    return klipper


def check():
    spec = importlib.util.spec_from_file_location('nozzle_startup_updater',ROOT/'install-orange-pi.py')
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    with tempfile.TemporaryDirectory(prefix='inventure-nozzle-startup-') as directory:
        root = Path(directory)
        klipper = prepare_runtime(root)
        subprocess.run([sys.executable, str(ROOT/'ci/check_oled_navigation.py'),
                        '--klipper', str(klipper), '--output', str(root/'oled-navigation.json')],
                       check=True)

        def run(config, *options):
            return subprocess.run([sys.executable,__file__,'--klipper',str(klipper),
                                   '--config',str(config),*options],capture_output=True,text=True,timeout=90)

        field = root/'printer.cfg'
        field.write_bytes((ROOT/'tests/fixtures/native_nozzles_r15_startup.cfg').read_bytes())
        broken = run(field)
        assert broken.returncode != 0
        assert "Unknown temperature sensor 'Inventure R5 ADC'" in broken.stderr
        print('REPRODUCED: R15 field startup fails before shared ADC registration')
        for mode in ('repair_field','fresh_include'):
            if mode == 'fresh_include':
                field = root/'printer_data/config/printer.cfg'
                field.parent.mkdir(parents=True)
                (field.parent.parent/'gcodes').mkdir()
                (field.parent/'hardware.cfg').write_bytes((ROOT/'config/INVENTURE_NATIVE_Z_TEST.cfg').read_bytes())
                (field.parent/'fluidd.cfg').write_bytes((ROOT/'fluidd/inventure_01_diagnostyka.cfg').read_bytes())
                field.write_text('[include hardware.cfg]\n[include fluidd.cfg]\n\n'
                    +updater.klipper_config_module(klipper).AUTOSAVE_HEADER
                    +'#*# [stepper_z]\n#*# position_endstop = 139.35\n')
            updates = updater.nozzle_limit_updates(klipper,field,True,True)
            updater.add_fluidd_controls(klipper,field,updates,dashboard=True)
            assert updates
            for path,(before,after) in updates.items():
                assert path.read_bytes() == before
                updater.atomic_config_write(path,after)
            assert updater.nozzle_limit_updates(klipper,field,True,True) == {}
            second = {}
            updater.add_fluidd_controls(klipper,field,second,dashboard=True)
            assert second == {}
            result = run(field)
            assert result.returncode == 0, result.stdout+result.stderr
            print(mode+': '+result.stdout.strip())
            from importlib import import_module
            sys.path.insert(0, str(ROOT))
            layout = import_module('inventure_config_layout')
            organized = {}
            layout.organize_klipper(updater.klipper_config_module(klipper), field, organized)
            for path, (before, after) in organized.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                updater.atomic_config_write(path, after, create=before is None)
            assert not updater.nozzle_limit_updates(klipper, field, True, True)
            again = {}
            updater.add_fluidd_controls(klipper, field, again, dashboard=True)
            layout.organize_klipper(updater.klipper_config_module(klipper), field, again)
            assert again == {}, list(again)
            for options in (('--save-test-pid',), ()):
                result = run(field, *options)
                assert result.returncode == 0, result.stdout+result.stderr
            assert field.read_text().count('#*# pid_kp = 18.125') == 4
            print(mode+': organized config loads and all four test PIDs survive SAVE_CONFIG and reload')

        # Check optional installation routes independently from the stock field hardware.
        tmpcfg = root/'optional.cfg'
        prefix = '[inventure_mcu]\n[pause_resume]\n[bed_mesh]\n[heater_bed]\n'
        owner = '[gcode_macro END_PRINT]\ngcode: M118 site-specific shutdown\n'
        for authorized in (False, True):
            tmpcfg.write_text(prefix + owner + '[inventure_preflight]\nenabled: true\n'
                              'p14_integration_qualified: true\nstart_qualified: '+str(authorized)+'\n')
            changes = {}
            updater.add_fluidd_controls(klipper,tmpcfg,changes,dashboard=True)
            content = changes[tmpcfg][1].decode()
            assert owner in content and content.count('[gcode_macro END_PRINT]') == 1
            assert '[gcode_macro PID_BED]' in content and '[gcode_macro BED_MESH]' in content
            assert ('[gcode_macro START_PRINT]' in content) == authorized
            assert '[gcode_macro INVENTURE_OLED_TIMERS]' not in content
            assert '[gcode_macro INVENTURE_TEST_' not in content
        print('Optional controls follow configured heaters, mesh and qualified P15. Site END_PRINT retained.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper',type=Path)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--save-test-pid', action='store_true')
    args = parser.parse_args()
    if args.klipper is not None and args.config is not None:
        load_config(args.klipper,args.config,args.save_test_pid)
    elif args.klipper is not None or args.config is not None:
        parser.error('--klipper and --config must be supplied together')
    else:
        check()
