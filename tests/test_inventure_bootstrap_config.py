"""Recover the field setup without modifying the running diagnostic installation."""
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ci.check_native_nozzle_startup import prepare_runtime
import inventure_bootstrap_config as bootstrap
import inventure_config_layout as layout


DIAGNOSTIC = '''[mcu]
serial: /dev/ttyS5
baud: 750000
restart_method: command
[printer]
kinematics: none
max_velocity: 30
max_accel: 200
[inventure_mcu]
allow_actuators: False
'''


class BootstrapConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime_temp = tempfile.TemporaryDirectory(prefix='inventure-config-runtime-')
        cls.addClassCleanup(cls.runtime_temp.cleanup)
        cls.host = prepare_runtime(Path(cls.runtime_temp.name))
        cls.updater = bootstrap._updater()
        cls.parser = cls.updater.klipper_config_module(cls.host)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='inventure-config-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source/printer.cfg'
        self.source.parent.mkdir()
        self.source.write_text(DIAGNOSTIC)
        self.gcodes = self.root/'gcodes'
        self.gcodes.mkdir()

    def stage(self, name='staged', **overrides):
        arguments = dict(serial='/dev/ttyS5', baud=750000, gcodes_path=self.gcodes,
                         restore_field_profile=True)
        arguments.update(overrides)
        return bootstrap.stage_config(self.host, self.root/name, self.source, **arguments)

    def effective(self, report):
        return layout.active_klipper(self.parser, Path(report['config_path']), {})[0]

    def test_diagnostic_needs_explicit_recovery_and_invalid_inputs_never_stage(self):
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, '--restore-field-profile'):
            self.stage(restore_field_profile=False)
        for changes in ({'serial':'/dev/ttyS5\n[mcu extra]'}, {'baud':True}, {'baud':0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.stage(**changes)
        self.assertFalse((self.root/'staged').exists())
        self.assertEqual(self.source.read_bytes(), original)

    def test_recovery_restores_field_parameters_current_actions_and_external_paths(self):
        original = self.source.read_bytes()
        report = self.stage(serial='/dev/ttyS7', baud=500000)
        active = self.effective(report)
        self.assertEqual(active.get('mcu','serial'), '/dev/ttyS7')
        self.assertEqual(active.getint('mcu','baud'), 500000)
        self.assertEqual(active.get('mcu','restart_method'), 'command')
        self.assertEqual(active.get('virtual_sdcard','path'), str(self.gcodes))
        self.assertEqual(active.get('printer','kinematics'), 'corexy')
        self.assertEqual(active.getfloat('stepper_z','position_endstop'), 139.35)
        self.assertEqual(active.getfloat('stepper_z','rotation_distance'), 3.99002493766)
        self.assertEqual(active.getfloat('inventure_native_z','coarse_speed'), 8)
        self.assertEqual(active.getfloat('inventure_native_z','fine_speed'), .3117206983)
        self.assertEqual(active.getint('inventure_native_z','repeat_count'), 101)
        self.assertEqual(active.getfloat('inventure_mcu','chamber_l_pid_ki'), .25)
        self.assertEqual(active.getint('inventure_display','normal_contrast'), 159)
        self.assertEqual(active.getint('inventure_display','sleep_timeout'), 900)
        expected = self.parser.ConfigFileReader().build_fileconfig(
            (ROOT/'config/INVENTURE_HOME.cfg').read_text(), 'current-home.cfg')
        for name in ('INVENTURE_HOME_Z','INVENTURE_HOME_ALL','INVENTURE_HOME_XY'):
            section = 'gcode_macro '+name
            self.assertEqual(dict(active[section]), dict(expected[section]))
        self.assertNotIn('START_PRINT', report['macros'])
        self.assertFalse(any(s.startswith('delayed_gcode ') for s in active.sections()))
        self.assertEqual(set(report['missing_state_files']), set(bootstrap.STATE_FILES))
        self.assertEqual(report['copied_state_files'], [])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(report['source_snapshot'][str(self.source)], sha256(original).hexdigest())
        self.assertEqual(set(report['files']), set(report['loaded_configs']))
        self.assertIn('inventure_services.cfg', report['files'])
        self.assertIn('inventure_display.cfg', report['files'])
        self.assertIn('inventure_heater_control.cfg', report['files'])
        self.assertIn(str(self.source.parent/'inventure_operations.cfg'), report['source_snapshot'])
        self.assertIsNone(report['source_snapshot'][str(self.source.parent/'inventure_operations.cfg')])

    def test_existing_sidecar_bytes_are_copied_and_missing_state_is_not_invented(self):
        data = b'{"schema":1,"tools":{"T0":{"load_speed":1.7},"T1":{}}}\n'
        sidecar = self.source.parent/'inventure_filament_settings.json'
        sidecar.write_bytes(data)
        extra = self.source.parent/'inventure_operator_record.json'
        extra.write_bytes(b'{"operator":"local value"}\n')
        report = self.stage()
        staged = Path(report['config_path']).parent
        self.assertEqual((staged/sidecar.name).read_bytes(), data)
        self.assertEqual((staged/extra.name).read_bytes(), extra.read_bytes())
        self.assertEqual(report['source_snapshot'][str(sidecar)], sha256(data).hexdigest())
        self.assertNotIn(sidecar.name, report['missing_state_files'])
        for name in report['missing_state_files']:
            self.assertFalse((staged/name).exists())

    def test_native_include_tree_save_block_tuning_and_operator_macro_are_preserved(self):
        first = self.stage('native')
        self.source = Path(first['config_path'])
        extra = self.source.parent/'local-tuning.cfg'
        extra.write_text('# Operator comments remain here.\n[stepper_z]\nposition_min: -0.45\n'
                         '[inventure_display]\nbrightness: 7\n'
                         '[gcode_macro OWNER_CHECK]\ngcode:\n    M118 operator\n')
        saved = ('#*# <---------------------- SAVE_CONFIG ---------------------->\n'
                 '#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n'
                 '#*#\n#*# [extruder]\n#*# control = pid\n#*# pid_kp = 23.017\n'
                 '#*# pid_ki = 0.719\n#*# pid_kd = 111.125\n')
        with self.source.open('a') as handle:
            handle.write('\n[include '+str(extra)+'] # Operator include\n\n'+saved)
        before = {p: p.read_bytes() for p in self.source.parent.rglob('*') if p.is_file()}
        report = self.stage('upgraded', restore_field_profile=False)
        self.assertFalse(report['recovery_profile_used'])
        parsed = self.effective(report)
        self.assertEqual(parsed.getfloat('stepper_z','position_min'), -.45)
        self.assertEqual(parsed.getint('inventure_display','brightness'), 7)
        self.assertEqual(parsed.get('gcode_macro OWNER_CHECK','gcode').strip(), 'M118 operator')
        staged = Path(report['config_path'])
        self.assertTrue(staged.read_text().endswith(saved))
        self.assertIn('[include local-tuning.cfg] # Operator include', staged.read_text())
        self.assertEqual((staged.parent/extra.name).read_bytes(), extra.read_bytes())
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_no_source_directory_overwrite_external_include_or_sidecar_symlink(self):
        with self.assertRaisesRegex(ValueError, 'already exists'):
            bootstrap.stage_config(self.host,self.source.parent,self.source,
                                   serial='/dev/ttyS5',baud=750000,gcodes_path=self.gcodes)
        with self.assertRaisesRegex(ValueError, 'outside the existing'):
            bootstrap.stage_config(self.host,self.source.parent/'stage',self.source,
                                   serial='/dev/ttyS5',baud=750000,gcodes_path=self.gcodes)
        outside = self.root/'outside.cfg'
        outside.write_text('[gcode_macro EXTERNAL]\ngcode: M118 outside\n')
        self.source.write_text(DIAGNOSTIC+'\n[include '+str(outside)+']\n')
        with self.assertRaisesRegex(ValueError, 'outside its directory'):
            self.stage()
        self.source.write_text(DIAGNOSTIC)
        (self.source.parent/'inventure_menu_settings.json').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'regular configuration file'):
            self.stage()
        self.assertFalse((self.root/'staged').exists())

    def test_actual_pinned_klipper_loads_without_mcu_or_save_config(self):
        report = self.stage()
        directory = Path(report['config_path']).parent
        before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        result = subprocess.run(
            [sys.executable,str(ROOT/'ci/check_native_nozzle_startup.py'),
             '--klipper',str(self.host),'--config',report['config_path']],
            capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('CONFIG_LOADED',result.stdout)
        self.assertEqual(before,{p:p.read_bytes() for p in before})


if __name__ == '__main__':
    unittest.main()
