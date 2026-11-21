"""Exercise the real host file installer with an isolated systemd/proc model."""
import importlib.util
import configparser
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('orange_pi_update',ROOT/'install-orange-pi.py')
update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)


class OrangePiInstallerCLITests(unittest.TestCase):
    def test_help_lists_leveling_jam_and_filament_options(self):
        result = subprocess.run([sys.executable, str(ROOT/'install-orange-pi.py'), '--help'],
                                capture_output=True, text=True, check=True)
        self.assertIn('--configure-leveling', result.stdout)
        self.assertIn('--configure-jam', result.stdout)
        self.assertIn('--configure-filament', result.stdout)
        self.assertIn('--configure-fluidd', result.stdout)

    def test_cli_passes_requested_options_to_installer(self):
        for args, expected in (
                ([], (False, False, False, False, False, False)),
                (['--configure-leveling'], (False, False, False, True, False, False)),
                (['--check', '--configure-leveling'], (True, False, False, True, False, False)),
                (['--upgrade-nozzle-limits', '--configure-nozzles'], (False, True, True, False, False, False)),
                (['--configure-jam'], (False, False, False, False, True, False)),
                (['--configure-filament'], (False, False, False, False, False, True)),
                (['--configure-jam', '--configure-filament'], (False, False, False, False, True, True))):
            with self.subTest(args=args), patch.object(sys, 'argv', ['install-orange-pi.py', *args]), \
                    patch.object(update, 'update') as install:
                update.main()
                install.assert_called_once_with(*expected, False, None, None, False, None)


class OrangePiUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.live=self.root/'inventure-rc72-stage/klipper-inventure'
        self.extras=self.live/'klippy/extras';self.extras.mkdir(parents=True)
        self.script=self.live/'klippy/klippy.py';self.script.write_text('# active stage\n')
        self.config=self.root/'printer_data/config/printer.cfg';self.config.parent.mkdir(parents=True)
        self.config.write_text('[mcu]\nserial: /dev/ttyS1\nbaud: 750000\n[printer]\nkinematics: none\n')
        self.original_config=self.config.read_bytes()
        self.old=(ROOT/'tests/fixtures/inventure_mcu_chamber75_r3.py').read_bytes()
        self.dest=self.extras/'inventure_mcu.py';self.dest.write_bytes(self.old)
        self.factory_dest=self.extras/'inventure_factory_core.py'
        for name in ('inventure_oled.py','serialhdl.py'):
            p=self.live/'klippy/serialhdl.py' if name=='serialhdl.py' else self.extras/name
            p.write_bytes((ROOT/'orange_pi'/name).read_bytes())
        self.other=self.root/'klipper/klippy/extras/inventure_mcu.py'
        self.other.parent.mkdir(parents=True);self.other.write_text('# unrelated checkout\n')
        self.proc=self.root/'proc';self.process=self.proc/'421';self.process.mkdir(parents=True)
        self.argv=['/usr/bin/python3',str(self.script),str(self.config),'-l',str(self.root/'klippy.log')]
        (self.process/'cmdline').write_bytes(b'\0'.join(a.encode() for a in self.argv)+b'\0')
        self.active=True;self.calls=[]
        original_target=update.core.running_klipper_target
        original_config=update.core.running_printer_config
        for obj,name,value in ((update.core,'running_klipper_target',lambda:original_target(proc_root=self.proc)),
                               (update.core,'running_printer_config',lambda:original_config(proc_root=self.proc)),
                               (update.subprocess,'run',self.run_system),
                               (update,'check_print_job',lambda:None),
                               (update.os,'geteuid',lambda:1000)):
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)

    def run_system(self,args,check=False,**kw):
        self.calls.append(tuple(args))
        if args[:2]==['systemctl','show']:
            return types.SimpleNamespace(returncode=0,stdout='421\n')
        if args==['sudo','-v']:rc=0
        elif args[-3:]==['systemctl','stop','klipper']:self.active=False;rc=0
        elif args[-3:]==['systemctl','start','klipper']:self.active=True;rc=0
        elif args==['systemctl','is-active','--quiet','klipper']:rc=0 if self.active else 3
        else:raise AssertionError('Unexpected system request: '+str(args))
        if check and rc:raise subprocess.CalledProcessError(rc,args)
        return types.SimpleNamespace(returncode=rc)

    def factory_package(self):
        """Exercise the new-module path even while release hashes are staged."""
        package=self.root/'package';package.mkdir()
        metadata=json.loads((ROOT/'orange_pi/THERMAL_HOST_MANIFEST.json').read_text())
        for name,key in (('inventure_mcu.py','module_sha256'),
                         ('inventure_oled.py','oled_module_sha256'),('serialhdl.py','serialhdl_sha256')):
            data=(ROOT/'orange_pi'/name).read_bytes();(package/name).write_bytes(data)
            metadata[key]=hashlib.sha256(data).hexdigest()
        core=ROOT/'orange_pi/inventure_factory_core.py'
        data=core.read_bytes() if core.is_file() else b'# isolated installer factory-core fixture\n'
        (package/'inventure_factory_core.py').write_bytes(data)
        metadata['factory_core_sha256']=hashlib.sha256(data).hexdigest()
        for name in metadata.get('extra_modules',{}):
            dest = update.core.source_path(package, name)
            dest.parent.mkdir(exist_ok=True)
            dest.write_bytes(update.core.source_path(ROOT/'orange_pi', name).read_bytes())
        old=(ROOT/'tests/fixtures/inventure_mcu_t1.py').read_bytes()
        metadata['allowed_existing_sha256'].append(hashlib.sha256(old).hexdigest())
        (package/'THERMAL_HOST_MANIFEST.json').write_text(json.dumps(metadata))
        return package

    def test_upgrade_targets_the_running_stage_and_preserves_config_and_backup(self):
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertEqual(self.other.read_text(),'# unrelated checkout\n')
        backups=list((self.live/'.inventure-thermal-test-r2-backups').glob('*/inventure_mcu.py'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),self.old)
        self.assertIn(('sudo','systemctl','stop','klipper'),self.calls)
        self.assertTrue(self.active)

    def legacy_nozzle_config(self):
        data=b''.join(path.read_bytes() for path in sorted((ROOT/'source/parts').glob('part*.bin')))
        with ZipFile(io.BytesIO(data)) as kit:
            with ZipFile(io.BytesIO(kit.read('baseline/SOURCE_FULL_KLIPPER_PINNED_P17_RC2.zip'))) as pinned:
                (self.script.parent/'configfile.py').write_bytes(
                    pinned.read('klipper-inventure/klippy/configfile.py'))
        self.config.write_text('[include heaters.cfg]\n[heater_bed]\nmax_temp: 110\n')
        heaters=self.config.with_name('heaters.cfg')
        heaters.write_text(
            '[extruder]\nheater_pin: inventure_r5:HEATER_T0\nsensor_type: Inventure R5 ADC\n'
            'sensor_channel: 0\nmax_temp: 220 # legacy Z test\nmin_temp: 0\n\n'
            '[extruder1]\nheater_pin: inventure_r5:HEATER_T1\nsensor_type: Inventure R5 ADC\n'
            'sensor_channel: 1\nmax_temp: 220\nmin_temp: 0\n')
        return heaters

    def test_level_buttons_install_alongside_nozzle_updates_and_preserve_includes(self):
        heaters = self.legacy_nozzle_config()
        heaters.write_text(heaters.read_text().split('[extruder1]')[0])
        self.config.write_text('[inventure_mcu]\n[include heaters.cfg]\n'
                               '[include buttons.cfg]\n#*# SAVE_CONFIG\n')
        template = (ROOT/'config/INVENTURE_PLATFORM_LEVEL.cfg').read_text()
        start = template.index('[gcode_macro INVENTURE_LEVEL_PROGRESS]')
        end = template.index('[gcode_macro ', start+1)
        buttons = self.config.with_name('buttons.cfg')
        buttons.write_text(template[start:end])
        updates = update.nozzle_limit_updates(self.live, self.config, True)
        update.add_leveling_macros(self.live, self.config, updates)
        self.assertNotIn(buttons, updates)
        for path, (before, after) in updates.items():
            self.assertEqual(path.read_bytes(), before)
            path.write_bytes(after)
        installed = self.config.read_text()
        self.assertNotIn('[gcode_macro INVENTURE_LEVEL_PROGRESS]', installed)
        self.assertIn('[gcode_macro INVENTURE_LEVEL]', installed)
        self.assertIn('heater_generic inventure_t1', installed)
        self.assertLess(installed.index('[gcode_macro INVENTURE_LEVEL_STOP]'),
                        installed.index('#*# SAVE_CONFIG'))
        repeated = {}
        update.add_leveling_macros(self.live, self.config, repeated)
        self.assertEqual(repeated, {})

    def test_level_upgrade_replaces_stock_buttons_inside_active_includes_only(self):
        self.legacy_nozzle_config()
        nested = self.config.parent/'groups'
        nested.mkdir()
        level = nested/'level.cfg'
        old_level = (ROOT/'tests/fixtures/platform_level_buttons_r26_1.cfg').read_text()
        level.write_bytes(old_level.replace('\n', '\r\n').encode())
        self.config.write_text('[inventure_mcu]\n[include menus.cfg]\n#*# SAVE_CONFIG\n')
        self.config.with_name('menus.cfg').write_text('[include groups/*.cfg]\n')
        enabled = nested/'optional.cfg'
        edited = ('[gcode_macro INV_D_STATUS]\ndescription: My status button\n'
                  'gcode:\n    M118 user status\n')
        version = ('[gcode_macro INV_D_WERSJE]\n'
                   'description: Inventure / wersja aplikacji MCU, bootloader i ustawienie UART P4\n'
                   'gcode:\n    INVENTURE_VERSION\n')
        nozzle = ('[gcode_macro ZORTRAX_NOZZLE_PAD_CALIBRATE]\n'
                  'description: Kalibracja serwisowa XYZ dysz T0 i T1 na polu stykowym\n'
                  'gcode:\n    INVENTURE_NOZZLE_PAD_CALIBRATE {rawparams}\n')
        enabled.write_text(edited + '\n' + version + '\n# My note\n' + nozzle)
        inactive = self.config.with_name('unused.cfg')
        inactive.write_text(version)
        plan = {}
        update.add_leveling_macros(self.live, self.config, plan)
        self.assertEqual(set(plan), {enabled, level})
        for path, (before, after) in plan.items():
            self.assertEqual(path.read_bytes(), before)
            path.write_bytes(after)
        self.assertEqual(level.read_text(), (ROOT/'config/INVENTURE_PLATFORM_LEVEL.cfg').read_text())
        self.assertIn(b'\r\n', level.read_bytes())
        self.assertEqual(inactive.read_text(), version)
        self.assertIn(edited, enabled.read_text())
        self.assertIn('# My note\n', enabled.read_text())
        self.assertIn('gcode_macro INVENTURE_DIAG_VERSIONS', enabled.read_text())
        self.assertNotIn('INV_D_WERSJE', enabled.read_text())
        self.assertIn('Measure T0 and T1 XYZ offsets', enabled.read_text())
        self.assertNotIn('INV_R_HOME_X', enabled.read_text())
        repeated = {}
        update.add_leveling_macros(self.live, self.config, repeated)
        self.assertEqual(repeated, {})

    def test_level_upgrade_preserves_colliding_and_custom_optional_buttons(self):
        self.legacy_nozzle_config()
        old = ('[gcode_macro INV_D_WERSJE]\n'
               'description: Inventure / wersja aplikacji MCU, bootloader i ustawienie UART P4\n'
               'gcode:\n    INVENTURE_VERSION\n')
        custom = '[gcode_macro INV_D_VERSIONS]\ngcode:\n    M118 custom\n'
        self.config.write_text('[inventure_mcu]\n' + old + custom)
        plan = {}
        update.add_leveling_macros(self.live, self.config, plan)
        staged = plan[self.config][1].decode()
        self.assertIn(custom, staged)
        self.assertIn('[gcode_macro INVENTURE_DIAG_VERSIONS]', staged)
        self.assertEqual(staged.count('[gcode_macro INV_D_VERSIONS]'), 1)

    def test_level_buttons_preserve_existing_custom_command(self):
        self.legacy_nozzle_config()
        self.config.write_text('[inventure_mcu]\n[gcode_macro zortrax_level]\n'
                               'gcode:\n    M118 custom\n')
        before = self.config.read_bytes()
        updates = {}
        update.add_leveling_macros(self.live, self.config, updates)
        self.assertIn(b'[gcode_macro zortrax_level]', updates[self.config][1])
        self.assertIn(b'[gcode_macro INVENTURE_LEVEL]', updates[self.config][1])
        self.assertEqual(self.config.read_bytes(), before)

    def test_jam_install_preserves_included_pause_settings_and_user_footer(self):
        self.legacy_nozzle_config()
        self.config.write_text('[inventure_mcu]\n[include operator.cfg]\n#*# SAVE_CONFIG\n')
        operator = self.config.with_name('operator.cfg')
        original = '[pause_resume]\nrecover_velocity: 17\n'
        operator.write_text(original)
        staged = {}
        update.add_fluidd_controls(self.live, self.config, staged, jam=True)
        self.assertEqual(set(staged), {self.config})
        config = staged[self.config][1].decode()
        self.assertLess(config.index('[gcode_macro INVENTURE_JAM_STATUS]'), config.index('#*#'))
        self.assertNotIn('[pause_resume]', config)
        self.assertNotIn('[gcode_macro INVENTURE_LEVEL]', config)
        self.config.write_text(config)
        repeated = {}
        update.add_fluidd_controls(self.live, self.config, repeated, jam=True)
        self.assertEqual(repeated, {})
        self.assertEqual(operator.read_text(), original)

    def test_jam_install_adds_pause_and_rejects_command_collision_before_writing(self):
        self.legacy_nozzle_config()
        gcodes = self.config.parent.parent/'gcodes'
        gcodes.mkdir()
        self.config.write_text('[inventure_mcu]\n')
        updates = {}
        update.add_fluidd_controls(self.live, self.config, updates, jam=True)
        self.assertEqual(updates[self.config][1].count(b'[pause_resume]'), 1)
        self.assertIn(('[virtual_sdcard]\npath: '+str(gcodes)).encode(), updates[self.config][1])
        self.config.write_text('[inventure_mcu]\n[virtual_sdcard]\npath: /mnt/existing/prints\n')
        updates = {}
        update.add_fluidd_controls(self.live, self.config, updates, jam=True)
        self.assertEqual(updates[self.config][1].count(b'[virtual_sdcard]'), 1)
        self.assertIn(b'path: /mnt/existing/prints', updates[self.config][1])
        self.config.write_text('[inventure_mcu]\n[gcode_macro PAUSE]\ngcode: M118 paused\n')
        with self.assertRaisesRegex(ValueError, 'rename_existing'):
            update.add_fluidd_controls(self.live, self.config, {}, jam=True)
        self.assertNotIn('[pause_resume]', self.config.read_text())

    def test_pinned_native_owner_updates_and_rolls_back_with_adapter(self):
        data = b''.join(path.read_bytes() for path in sorted((ROOT/'source/parts').glob('part*.bin')))
        with ZipFile(io.BytesIO(data)) as kit:
            with ZipFile(io.BytesIO(kit.read('baseline/SOURCE_FULL_KLIPPER_PINNED_P17_RC2.zip'))) as pinned:
                original = pinned.read('klipper-inventure/klippy/extras/inventure_operations.py')
        owner = self.extras/'inventure_operations.py'
        owner.write_bytes(original)
        self.active = False
        with patch('sys.stdout', new=io.StringIO()):
            check = update.core.install(self.live)
            self.assertEqual(owner.read_bytes(), original)
            self.assertIn('inventure_operations.py', check['after_sha256'])
            installed = update.core.install(self.live, apply=True)
            self.assertIn(b'def paused_print_move(', owner.read_bytes())
            self.assertEqual(update.core.install(self.live)['status'], 'ALREADY_INSTALLED')
            update.core.install(self.live, apply=True, rollback=Path(installed['backup']))
        self.assertEqual(owner.read_bytes(), original)

    def test_migrate_legacy_native_nozzle_limits_in_the_active_include(self):
        heaters=self.legacy_nozzle_config()
        before=heaters.read_bytes()
        root_before=self.config.read_bytes()
        with patch('sys.stdout',new=io.StringIO()):
            update.update(check_only=True,upgrade_nozzle_limits=True)
        self.assertEqual(heaters.read_bytes(),before)
        with patch('sys.stdout',new=io.StringIO()):
            update.update(upgrade_nozzle_limits=True)
            update.update(upgrade_nozzle_limits=True)
        self.assertEqual(heaters.read_bytes(),before.replace(b'max_temp: 220',b'max_temp: 310'))
        self.assertEqual(self.config.read_bytes(),root_before)
        backup,=heaters.parent.glob('heaters.cfg.pre-nozzle-limit-*.bak')
        self.assertEqual(backup.read_bytes(),before)

    def test_failed_service_start_restores_native_nozzle_limits_and_host(self):
        heaters=self.legacy_nozzle_config()
        before=heaters.read_bytes()
        service=update.service
        failed=False

        def fail_once(action):
            nonlocal failed
            if action=='start' and not failed:
                failed=True
                raise subprocess.CalledProcessError(1,['systemctl','start','klipper'])
            service(action)

        with self.assertRaises(subprocess.CalledProcessError), patch.object(
                update,'service',side_effect=fail_once), patch('sys.stdout',new=io.StringIO()):
            update.update(upgrade_nozzle_limits=True)
        self.assertTrue(self.active)
        self.assertEqual(heaters.read_bytes(),before)
        self.assertEqual(self.dest.read_bytes(),self.old)

    def test_complete_native_nozzle_setup_is_read_only_in_check_and_idempotent(self):
        self.legacy_nozzle_config()
        self.config.write_text('[include native.cfg]\n')
        native = self.config.with_name('native.cfg')
        before = (ROOT/'config/INVENTURE_NATIVE_Z_TEST.cfg').read_text().replace(
            'max_temp: 310', 'max_temp: 220 # original native limit').replace(
            'heater: extruder\n', 'heater:\n  extruder\n')
        native.write_text(before)
        root_before = self.config.read_bytes()
        with patch('sys.stdout',new=io.StringIO()):
            update.update(check_only=True,configure_nozzles=True)
        self.assertEqual(self.config.read_bytes(),root_before)
        self.assertEqual(native.read_text(),before)
        with patch('sys.stdout',new=io.StringIO()):
            update.update(configure_nozzles=True)
        self.assertIn('[heater_generic inventure_t1]',self.config.read_text())
        self.assertIn('max_temp: 310 # original native limit',native.read_text())
        self.assertEqual(native.read_text().count('heater: extruder, inventure_t1'),3)
        self.assertNotIn('\n  extruder\n',native.read_text())
        configured = self.config.read_bytes(),native.read_bytes()
        with patch('sys.stdout',new=io.StringIO()):
            update.update(configure_nozzles=True)
        self.assertEqual((self.config.read_bytes(),native.read_bytes()),configured)
        self.assertTrue(self.active)
        self.assertIn('baud: 750000',native.read_text())
        self.assertEqual(update.nozzle_limit_updates(self.live,self.config,True),{})

    def test_failed_complete_nozzle_setup_restores_t1_include_and_fans(self):
        self.legacy_nozzle_config()
        self.config.write_text('[include native.cfg]\n')
        native = self.config.with_name('native.cfg')
        native.write_bytes((ROOT/'config/INVENTURE_NATIVE_Z_TEST.cfg').read_bytes())
        before = self.config.read_bytes(),native.read_bytes()
        service = update.service
        failed = False
        def fail_once(action):
            nonlocal failed
            if action == 'start' and not failed:
                failed = True
                raise subprocess.CalledProcessError(1,['systemctl','start','klipper'])
            service(action)
        with self.assertRaises(subprocess.CalledProcessError), patch.object(
                update,'service',side_effect=fail_once), patch('sys.stdout',new=io.StringIO()):
            update.update(configure_nozzles=True)
        self.assertEqual((self.config.read_bytes(),native.read_bytes()),before)
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertTrue(self.active)

    def test_repair_the_field_t1_startup_failure_without_changing_heater_settings(self):
        self.legacy_nozzle_config()
        before = (ROOT/'tests/fixtures/native_nozzles_r15_startup.cfg').read_bytes()
        self.config.write_bytes(before)
        with patch('sys.stdout',new=io.StringIO()):
            update.update(check_only=True,configure_nozzles=True)
        self.assertEqual(self.config.read_bytes(),before)
        with patch('sys.stdout',new=io.StringIO()):
            update.update(configure_nozzles=True)
        after = self.config.read_bytes()
        self.assertLess(after.index(b'[inventure_mcu]'),after.index(b'[heater_generic inventure_t1]'))
        # The field file already has the correct limits and both-heater fans.
        # Relocating T1 must preserve every section's values, including motion.
        parser = configparser.RawConfigParser()
        parser.read_string(before.decode())
        fixed = configparser.RawConfigParser()
        fixed.read_string(after.decode())
        self.assertEqual({s:dict(parser.items(s)) for s in parser.sections()},
                         {s:dict(fixed.items(s)) for s in fixed.sections()})
        backup, = self.config.parent.glob('printer.cfg.pre-nozzle-limit-*.bak')
        self.assertEqual(backup.read_bytes(),before)
        with patch('sys.stdout',new=io.StringIO()):
            update.update(configure_nozzles=True)
        self.assertEqual(self.config.read_bytes(),after)
        self.assertEqual(update.nozzle_limit_updates(self.live,self.config,True),{})

    def test_new_and_repaired_t1_follow_nested_includes_and_precede_save_config(self):
        self.legacy_nozzle_config()
        native_dir = self.config.parent/'native'
        native_dir.mkdir()
        native = native_dir/'hardware.cfg'
        native.write_bytes((ROOT/'config/INVENTURE_NATIVE_Z_TEST.cfg').read_bytes())
        include = self.config.with_name('nested.cfg')
        include.write_text('[include native/hardware.cfg]\n')
        footer = b'#*# <---------------------- SAVE_CONFIG ---------------------->\n#*# [stepper_z]\n#*# position_endstop = 139.35\n'
        t1 = (ROOT/'config/INVENTURE_NATIVE_T1_HEATER.cfg').read_bytes()
        for existing in (False,True):
            with self.subTest(existing_t1=existing):
                self.config.write_bytes((t1+b'\n' if existing else b'')+
                                        b'[include nested.cfg]\n\n'+footer)
                changes = update.nozzle_limit_updates(self.live,self.config,True)
                for path,(before,after) in changes.items():
                    self.assertEqual(path.read_bytes(),before)
                    path.write_bytes(after)
                result = self.config.read_bytes()
                self.assertTrue(result.endswith(footer))
                self.assertLess(result.index(b'[include nested.cfg]'),result.index(b'[heater_generic inventure_t1]'))
                self.assertLess(result.index(b'[heater_generic inventure_t1]'),result.index(b'#*#'))
                self.assertEqual(result.count(b'[heater_generic inventure_t1]'),1)
                self.assertEqual(update.nozzle_limit_updates(self.live,self.config,True),{})

    def test_invalid_t0_load_order_is_rejected_before_stopping_or_writing(self):
        self.legacy_nozzle_config()
        data = (ROOT/'tests/fixtures/native_nozzles_r15_startup.cfg').read_bytes()
        remaining, board = update.remove_config_section(data,'inventure_mcu')
        self.config.write_bytes(update.append_config_section(remaining,board))
        before = self.config.read_bytes()
        with self.assertRaisesRegex(ValueError,'must precede'), patch('sys.stdout',new=io.StringIO()):
            update.update(configure_nozzles=True)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertFalse(any('stop' in a for a in self.calls))

    def test_motor_r1_adapter_and_oled_upgrade_together_in_the_active_stage(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_motor_r1.py').read_bytes())
        oled=self.extras/'inventure_oled.py'
        oled.write_bytes((ROOT/'tests/fixtures/inventure_oled_motor_r1.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(oled.read_bytes(),(ROOT/'orange_pi/inventure_oled.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_motor_r2_upgrade_accepts_the_exact_installed_door_policy_module(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_motor_r2.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_motor_r3_upgrade_accepts_the_physically_validated_seek_module(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_motor_r3.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_motor_r4_upgrade_preserves_config_and_reloads_python(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_motor_r4.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_dry_run_does_not_stop_service_or_write_files(self):
        with patch('sys.stdout',new=io.StringIO()):update.update(True)
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertFalse(any('stop' in a for a in self.calls))
        self.assertFalse((self.live/'.inventure-thermal-test-r2-backups').exists())

    def test_zlimit_host_upgrades_to_service_without_config_or_mcu_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_zlimit.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_service_host_upgrades_to_sensor_readbacks_without_config_or_mcu_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_service.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_sensors_host_upgrades_to_realtime_feeder_run_without_changing_config(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_sensors.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_feedrun_host_upgrades_to_50_second_limit_without_changing_config(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_feedrun.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_control_r5_upgrade_preserves_mcu_config_and_reloads_python(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_feed50_host_upgrades_to_load230_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_feed50.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_load230_upgrades_to_R2_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_load230.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_load230_R2_upgrades_to_PID_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_load230_r2.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_load230_R3_upgrades_to_LOAD15_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_load230_r3.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_LOAD15_upgrades_to_cassette_load_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_control_r5_load15.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_CASSETTE1_upgrades_to_smooth_host_without_config_or_MCU_changes(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_cassette1.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_smooth_upgrades_to_retraction_without_changing_MCU_or_config(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_smooth.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_installed_retract_upgrades_to_both_tools_without_changing_MCU_or_config(self):
        self.dest.write_bytes((ROOT/'tests/fixtures/inventure_mcu_retract.py').read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),(ROOT/'orange_pi/inventure_mcu.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_T1_upgrade_adds_verified_factory_core_and_records_previous_absence(self):
        previous=(ROOT/'tests/fixtures/inventure_mcu_t1.py').read_bytes()
        self.dest.write_bytes(previous);package=self.factory_package()
        with patch.object(update.core,'ROOT',package),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.factory_dest.read_bytes(),(package/'inventure_factory_core.py').read_bytes())
        self.assertEqual(self.config.read_bytes(),self.original_config)
        backups=list((self.live/'.inventure-thermal-test-r2-backups').glob('*/INSTALL_MANIFEST.json'))
        self.assertEqual(len(backups),1)
        state=json.loads(backups[0].read_text())['files']['inventure_factory_core.py']
        self.assertEqual(state,{'exists':False,'sha256':None})
        self.assertEqual((backups[0].parent/'inventure_mcu.py').read_bytes(),previous)
        self.assertFalse((backups[0].parent/'inventure_factory_core.py').exists())

    def test_factory_upgrade_rollback_restores_exact_previous_module_and_missing_core(self):
        previous=(ROOT/'tests/fixtures/inventure_mcu_t1.py').read_bytes()
        self.dest.write_bytes(previous);package=self.factory_package();self.active=False
        with patch.object(update.core,'ROOT',package),patch('sys.stdout',new=io.StringIO()):
            result=update.core.install(self.live,apply=True)
            self.assertTrue(self.factory_dest.is_file())
            update.core.install(self.live,apply=True,rollback=Path(result['backup']))
        self.assertEqual(self.dest.read_bytes(),previous)
        self.assertFalse(self.factory_dest.exists())
        self.assertEqual(self.config.read_bytes(),self.original_config)

    def test_existing_verified_factory_core_survives_rollback(self):
        package=self.factory_package();expected=(package/'inventure_factory_core.py').read_bytes()
        self.factory_dest.write_bytes(expected);self.active=False
        with patch.object(update.core,'ROOT',package),patch('sys.stdout',new=io.StringIO()):
            result=update.core.install(self.live,apply=True)
            update.core.install(self.live,apply=True,rollback=Path(result['backup']))
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertEqual(self.factory_dest.read_bytes(),expected)

    def test_published_R7_core_upgrades_and_rollback_restores_the_exact_previous_core(self):
        previous=(ROOT/'tests/fixtures/factory_r7_core.py').read_bytes()
        self.factory_dest.write_bytes(previous);self.active=False
        self.assertIn('5e3600c8f9632b654cb2c8a8437e6648640f51f1b084033a5ffd6944b152311e',
            json.loads((ROOT/'orange_pi/THERMAL_HOST_MANIFEST.json').read_text())['allowed_existing_sha256'])
        with patch('sys.stdout',new=io.StringIO()):
            result=update.core.install(self.live,apply=True)
            self.assertEqual(self.factory_dest.read_bytes(),(ROOT/'orange_pi/inventure_factory_core.py').read_bytes())
            update.core.install(self.live,apply=True,rollback=Path(result['backup']))
        self.assertEqual(self.factory_dest.read_bytes(),previous)
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertEqual(self.config.read_bytes(),self.original_config)

    def test_failed_modular_install_restores_the_existing_R7_core(self):
        previous=(ROOT/'tests/fixtures/factory_r7_core.py').read_bytes()
        self.factory_dest.write_bytes(previous);original=Path.replace
        def interrupt_core(path,other):
            if path.name=='.inventure_factory_core.py.thermal-r2.new':raise KeyboardInterrupt()
            return original(path,other)
        with patch.object(Path,'replace',interrupt_core):
            with self.assertRaises(KeyboardInterrupt),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertEqual(self.factory_dest.read_bytes(),previous)
        self.assertEqual(self.config.read_bytes(),self.original_config)
        self.assertTrue(self.active)

    def test_menu_dependencies_upgrade_together_and_rollback_preserves_existing_OEM_menu(self):
        previous=(ROOT/'tests/fixtures/inventure_oem_menu_bench.py').read_bytes()
        dest=self.extras/'inventure_oem_menu.py';dest.write_bytes(previous);self.active=False
        with patch('sys.stdout',new=io.StringIO()):
            result=update.core.install(self.live,apply=True)
            for name in ('inventure_menu_core.py','inventure_menu_service.py','inventure_oem_menu.py'):
                self.assertEqual((self.extras/name).read_bytes(),(ROOT/'orange_pi'/name).read_bytes())
            update.core.install(self.live,apply=True,rollback=Path(result['backup']))
        self.assertEqual(dest.read_bytes(),previous)
        self.assertFalse((self.extras/'inventure_menu_core.py').exists())
        self.assertFalse((self.extras/'inventure_menu_service.py').exists())
        self.assertEqual(self.config.read_bytes(),self.original_config)

    def test_unknown_installed_menu_is_not_overwritten_or_stopped(self):
        dest=self.extras/'inventure_oem_menu.py';dest.write_text('# local menu change\n')
        with self.assertRaisesRegex(ValueError,'inventure_oem_menu.py differs'):update.update()
        self.assertEqual(dest.read_text(),'# local menu change\n')
        self.assertFalse(any('stop' in args for args in self.calls))

    def test_unknown_existing_factory_core_is_refused_before_stopping(self):
        package=self.factory_package();self.factory_dest.write_text('# local change\n')
        with patch.object(update.core,'ROOT',package):
            with self.assertRaisesRegex(ValueError,'factory_core.py differs'):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertEqual(self.factory_dest.read_text(),'# local change\n')
        self.assertFalse(any('stop' in args for args in self.calls))

    def test_interruption_at_factory_module_restores_old_files_and_previous_absence(self):
        package=self.factory_package();original=Path.replace
        def interrupt_core(path,other):
            if path.name=='.inventure_factory_core.py.thermal-r2.new':raise KeyboardInterrupt()
            return original(path,other)
        with patch.object(update.core,'ROOT',package),patch.object(Path,'replace',interrupt_core):
            with self.assertRaises(KeyboardInterrupt),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertFalse(self.factory_dest.exists());self.assertTrue(self.active)
        self.assertEqual(self.config.read_bytes(),self.original_config)

    def test_backup_absence_marker_cannot_hide_an_unexpected_core_file(self):
        package=self.factory_package();self.active=False
        with patch.object(update.core,'ROOT',package),patch('sys.stdout',new=io.StringIO()):
            result=update.core.install(self.live,apply=True)
            backup=Path(result['backup']);(backup.parent/'inventure_factory_core.py').write_bytes((package/'inventure_factory_core.py').read_bytes())
            with self.assertRaisesRegex(ValueError,'Backup presence differs'):
                update.core.install(self.live,apply=True,rollback=backup)
        self.assertEqual(self.dest.read_bytes(),(package/'inventure_mcu.py').read_bytes())
        self.assertEqual(self.factory_dest.read_bytes(),(package/'inventure_factory_core.py').read_bytes())

    def test_same_disk_version_restarts_python_without_rewriting_modules(self):
        expected=(ROOT/'orange_pi/inventure_mcu.py').read_bytes()
        self.dest.write_bytes(expected)
        if 'factory_core_sha256' in json.loads((ROOT/'orange_pi/THERMAL_HOST_MANIFEST.json').read_text()):
            self.factory_dest.write_bytes((ROOT/'orange_pi/inventure_factory_core.py').read_bytes())
        for name in json.loads((ROOT/'orange_pi/THERMAL_HOST_MANIFEST.json').read_text()).get('extra_modules',{}):
            (self.extras/name).write_bytes(update.core.source_path(ROOT/'orange_pi', name).read_bytes())
        with patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),expected)
        self.assertIn(('sudo','systemctl','stop','klipper'),self.calls)
        self.assertTrue(self.active)
        self.assertFalse((self.live/'.inventure-thermal-test-r2-backups').exists())

    def test_unknown_local_changes_fail_before_stopping(self):
        modified=self.old+b'\n# local user change\n';self.dest.write_bytes(modified)
        with self.assertRaisesRegex(ValueError,'differs'):update.update()
        self.assertEqual(self.dest.read_bytes(),modified)
        self.assertFalse(any('stop' in a for a in self.calls))

    def test_r6_t1_upgrade_preserves_legacy_transport_and_rollback_removes_new_module(self):
        previous_host=(ROOT/'tests/fixtures/inventure_mcu_t1.py').read_bytes()
        self.dest.write_bytes(previous_host)
        previous_transport=(ROOT/'host/inventure_oled_transport.py').read_bytes()+b'\n# Older local OLED variant\n'
        legacy=self.extras/'inventure_oled_transport.py'
        legacy.write_bytes(previous_transport)
        new_name='inventure_oled_uifast_transport.py'
        new_transport=self.extras/new_name
        with patch('sys.stdout',new=io.StringIO()):
            update.update()
            self.assertEqual(new_transport.read_bytes(),(ROOT/'orange_pi'/new_name).read_bytes())
            self.assertEqual(legacy.read_bytes(),previous_transport)
            self.assertTrue(self.active)
            backup, = (self.live/'.inventure-thermal-test-r2-backups').glob('*/inventure_mcu.py')
            self.active=False
            update.core.install(self.live,apply=True,rollback=backup)
        self.assertEqual(self.dest.read_bytes(),previous_host)
        self.assertEqual(legacy.read_bytes(),previous_transport)
        self.assertFalse(new_transport.exists())
        self.assertEqual(self.config.read_bytes(),self.original_config)

    def test_unknown_transport_is_refused_before_stopping_the_running_service(self):
        dest=self.extras/'inventure_oled_uifast_transport.py';dest.write_text('# unknown local edits\n')
        with self.assertRaisesRegex(ValueError,'inventure_oled_uifast_transport.py differs'),patch('sys.stdout',new=io.StringIO()):
            update.update()
        self.assertFalse(any('stop' in a for a in self.calls))
        self.assertEqual(dest.read_text(),'# unknown local edits\n')

    def test_print_in_progress_fails_before_stopping(self):
        with patch.object(update,'check_print_job',side_effect=ValueError('current print')):
            with self.assertRaisesRegex(ValueError,'current print'),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old)
        self.assertFalse(any('stop' in a for a in self.calls))

    def test_failed_start_rolls_back_files_and_restarts_old_host(self):
        base=self.run_system;failed=False
        def fail_first_start(args,**kw):
            nonlocal failed
            if args[-3:]==['systemctl','start','klipper'] and not failed:
                failed=True;raise subprocess.CalledProcessError(1,args)
            return base(args,**kw)
        with patch.object(update.subprocess,'run',side_effect=fail_first_start):
            with self.assertRaises(subprocess.CalledProcessError),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old);self.assertTrue(self.active)

    def test_interrupted_partial_copy_is_restored_before_service_restart(self):
        original=Path.replace
        def interrupt_second(path,other):
            if path.name=='.inventure_oled.py.thermal-r2.new':raise KeyboardInterrupt()
            return original(path,other)
        with patch.object(Path,'replace',interrupt_second):
            with self.assertRaises(KeyboardInterrupt),patch('sys.stdout',new=io.StringIO()):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old);self.assertTrue(self.active)
        self.assertEqual(self.config.read_bytes(),self.original_config)

    def test_ambiguous_running_process_is_refused_before_write(self):
        (self.process/'cmdline').write_bytes(b'/usr/bin/python3\0wrapper.py\0')
        with self.assertRaisesRegex(ValueError,'klippy.py'):update.update()
        self.assertEqual(self.dest.read_bytes(),self.old)


class PrintGuardTests(unittest.TestCase):
    def test_printing_and_paused_states_refuse_updates(self):
        for state in ('printing','paused'):
            data=json.dumps({'result':{'status':{'print_stats':{'state':state}}}}).encode()
            with patch.object(update.urllib.request,'urlopen',return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError,'current print'):update.check_print_job()


if __name__=='__main__':unittest.main(verbosity=2)
