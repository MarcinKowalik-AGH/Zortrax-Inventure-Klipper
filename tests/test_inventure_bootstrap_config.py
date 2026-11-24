"""Recover the field setup without modifying the running diagnostic installation."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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
        self.assertEqual(set(report['missing_state_files']),
                         set(bootstrap.STATE_FILES) - {'inventure_nozzle_pad.json'})
        self.assertEqual(report['copied_state_files'], [])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(report['source_snapshot'][str(self.source)], sha256(original).hexdigest())
        self.assertEqual(set(report['files']),
                         set(report['loaded_configs']) | {'inventure_nozzle_pad.json'})
        self.assertIn('inventure_services.cfg', report['files'])
        self.assertIn('inventure_display.cfg', report['files'])
        self.assertIn('inventure_heater_control.cfg', report['files'])
        self.assertIn(str(self.source.parent/'inventure_operations.cfg'), report['source_snapshot'])
        self.assertIsNone(report['source_snapshot'][str(self.source.parent/'inventure_operations.cfg')])

    def test_native_config_without_explicit_recovery_keeps_missing_state_absent(self):
        self.source.write_bytes(bootstrap.FIELD_PROFILE.read_bytes())
        report = self.stage(restore_field_profile=False)
        self.assertEqual(report['restored_state_files'], {})
        self.assertEqual(set(report['missing_state_files']), set(bootstrap.STATE_FILES))
        self.assertEqual(report['copied_state_files'], [])
        staged = Path(report['config_path']).parent
        self.assertFalse(any((staged/name).exists() for name in bootstrap.STATE_FILES))

    def test_explicit_recovery_restores_only_the_missing_published_pad_for_both_profiles(self):
        for profile in ('diagnostic', 'native'):
            with self.subTest(profile=profile):
                if profile == 'native':
                    text = bootstrap.FIELD_PROFILE.read_text().replace(
                        'coarse_speed = 8', 'coarse_speed = 7')
                    self.source.write_text(text)
                original = self.source.read_bytes()
                report = self.stage(profile)
                name = 'inventure_nozzle_pad.json'
                staged = Path(report['config_path']).parent
                data = (staged/name).read_bytes()
                self.assertEqual(sha256(data).hexdigest(),
                                 '23e7455115071b3f8500d94f658a8c87e40c25aee8abd6dff0b4ac6848b1d157')
                record = json.loads(data)
                self.assertEqual(record['run_id'], '4eed5e30be704a6ca57440e1e7f27b44')
                self.assertEqual(record['schema'], 1)
                self.assertEqual(len(record), 12)
                self.assertEqual(report['restored_state_files'], {name: {
                    'source': 'docs/acceptance/INVENTURE_PAD_R23_2026-11-19_PROBA_2.json',
                    'source_sha256': '75669f6ea0156dd500abe91de78007433889a852a8c4bc9b747ae17942c1afcf',
                    'run_id': record['run_id'], 'sha256': sha256(data).hexdigest()}})
                self.assertEqual(report['copied_state_files'], [])
                self.assertEqual(set(report['missing_state_files']), set(bootstrap.STATE_FILES) - {name})
                self.assertIsNone(report['source_snapshot'][str(self.source.parent/name)])
                self.assertFalse((self.source.parent/name).exists())
                self.assertEqual(self.source.read_bytes(), original)
                for missing in report['missing_state_files']:
                    self.assertFalse((staged/missing).exists())
                self.assertEqual(report['recovery_profile_used'], profile == 'diagnostic')
                if profile == 'native':
                    self.assertEqual(self.effective(report).getfloat('inventure_native_z', 'coarse_speed'), 7.)

    def test_existing_pad_is_preserved_even_when_published_recovery_source_is_unavailable(self):
        name = 'inventure_nozzle_pad.json'
        original = b'{\n "schema": 1, "run_id": "newer-local-calibration", "operator": true\n}\n'
        path = self.source.parent/name
        path.write_bytes(original)
        with mock.patch.object(bootstrap, 'PUBLISHED_PAD_SOURCE', str(self.root/'not-present.json')):
            report = self.stage()
        staged = Path(report['config_path']).parent/name
        self.assertEqual(staged.read_bytes(), original)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(report['copied_state_files'], [name])
        self.assertEqual(report['restored_state_files'], {})
        self.assertNotIn(name, report['missing_state_files'])
        self.assertEqual(report['source_snapshot'][str(path)], sha256(original).hexdigest())

    def test_modified_published_calibration_fails_before_creating_a_stage(self):
        changed = self.root/'changed-published-pad.json'
        changed.write_bytes((ROOT/bootstrap.PUBLISHED_PAD_SOURCE).read_bytes() + b'\n')
        before = self.source.read_bytes()
        with mock.patch.object(bootstrap, 'PUBLISHED_PAD_SOURCE', str(changed)):
            with self.assertRaisesRegex(ValueError, 'source checksum mismatch'):
                self.stage()
        self.assertFalse((self.root/'staged').exists())
        self.assertFalse((self.source.parent/'inventure_nozzle_pad.json').exists())
        self.assertEqual(self.source.read_bytes(), before)

    def test_restored_pad_matches_production_save_and_current_leveling_contract(self):
        report = self.stage()
        pad = Path(report['config_path']).with_name('inventure_nozzle_pad.json')
        before = pad.read_bytes()
        script = r'''
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0, str(Path(sys.argv[1])/'klippy'))
from extras.inventure_mcu import InventureMCU
from extras.inventure_leveling_transport import platform_reference
from extras.inventure_factory_core import parameters
from extras.inventure_nozzle_xyz import pad_xyz_candidate
accepted = json.loads(Path(sys.argv[3]).read_text())
restored = json.loads(Path(sys.argv[2]).read_text())
board = InventureMCU.__new__(InventureMCU)
board.nozzle_z_context = None
board.nozzle_pad_run = deepcopy(accepted)
board.nozzle_pad_run.update(phase='candidate', params={
    'prep': accepted['prep'], 'scan': accepted['scan']})
board.printer = SimpleNamespace(get_start_args=lambda: {'config_file': '/snapshot/printer.cfg'})
saved = []
board._write_calibration_file = lambda path, record: saved.append(record)
board._nozzle_pad_emit = lambda command: None
options = {'ACTION': 'SAVE', 'RUN_ID': accepted['run_id'], 'ACK': 'PAD_RESULT_REVIEWED'}
board.cmd_nozzle_pad_calibrate(SimpleNamespace(get=options.get, error=ValueError))
assert saved == [restored]
preset = {'TOOL': 'T0', 'SPEED_PROFILE': 'OEM', 'TARGET': 200., 'PROCEDURE_TIMEOUT': 3600.}
center, release = platform_reference(restored, parameters(SimpleNamespace(get=preset.get)))
assert pad_xyz_candidate(restored['measurements'], {
    'xy_repeat_mm': .08, 'z_repeat_mm': .05}) == restored['candidate']
assert restored['measurements'] == accepted['measurements']
print(json.dumps({'center': center, 'release_steps': release, 'fields': len(restored)}))
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.host), str(pad),
                                 str(ROOT/bootstrap.PUBLISHED_PAD_SOURCE)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+'\n'+result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {'center': [81.7, 68.34375], 'release_steps': 111802, 'fields': 12})
        self.assertEqual(pad.read_bytes(), before)

    def test_existing_sidecar_bytes_are_copied_and_other_missing_state_is_not_invented(self):
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
