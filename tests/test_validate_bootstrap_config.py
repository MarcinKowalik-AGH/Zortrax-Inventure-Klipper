"""Exercise the real pinned loader against the migrated field configuration."""
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from inventure_bootstrap import prepare_host
from inventure_bootstrap_config import stage_config


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


class ProductionBootstrapValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = tempfile.TemporaryDirectory(prefix='bootstrap-validator-host-')
        cls.addClassCleanup(cls.runtime.cleanup)
        cls.host = Path(cls.runtime.name) / 'host'
        prepare_host(cls.host, repo=ROOT)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bootstrap-validator-config-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source/printer.cfg'
        source.parent.mkdir()
        source.write_text(DIAGNOSTIC)
        gcodes = self.root / 'gcodes'
        gcodes.mkdir()
        report = stage_config(self.host, self.root / 'staged', source,
                              serial='/dev/ttyS5', baud=750000, gcodes_path=gcodes,
                              restore_field_profile=True)
        self.config = Path(report['config_path'])

    def run_validator(self, guarded=False):
        command = [sys.executable, str(ROOT / 'ci/validate_bootstrap_config.py')]
        if guarded:
            wrapper = r'''
import runpy, sys, threading
from unittest.mock import patch
validator = sys.argv[1]
sys.path.insert(0, sys.argv[3] + '/klippy')
sys.argv = sys.argv[1:]
import reactor, mcu, gcode
before = set(threading.enumerate())
with patch.object(reactor.Reactor, 'run', side_effect=AssertionError('reactor started')), \
     patch.object(mcu.MCUConnectHelper, '_attach', side_effect=AssertionError('MCU attached')), \
     patch.object(mcu.MCUConfigHelper, '_connect', side_effect=AssertionError('MCU configured')), \
     patch.object(gcode.GCodeDispatch, 'run_script_from_command', side_effect=AssertionError('G-code executed')):
    try:
        runpy.run_path(validator, run_name='__main__')
    except SystemExit as exc:
        status = exc.code
assert set(threading.enumerate()) == before, 'I/O worker was not joined'
raise SystemExit(status)
'''
            # The wrapper keeps the validator's ordinary --klipper/--config CLI.
            command = [sys.executable, '-c', wrapper, str(ROOT / 'ci/validate_bootstrap_config.py')]
        result = subprocess.run(command + ['--klipper', str(self.host), '--config', str(self.config)],
                                capture_output=True, text=True, timeout=90)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            self.fail('Validator did not return one JSON result: ' + result.stdout + '\n' + result.stderr)
        return result, payload

    def test_real_field_profile_loads_without_hardware_gcode_or_config_writes(self):
        # Non-fixture PID and display values must remain valid; this validator
        # must not quietly become an assertion of the release CI's test state.
        control = self.config.parent / 'inventure_heater_control.cfg'
        changed, count = re.subn(r'(?m)^(\s*[a-z0-9_]*pid_kp\s*[:=]\s*)[^\n]+',
                                r'\g<1>28.5', control.read_text(), count=1)
        self.assertEqual(count, 1)
        control.write_text(changed)
        display = self.config.parent / 'inventure_display.cfg'
        changed, count = re.subn(r'(?m)^(\s*normal_contrast\s*[:=]\s*)[^\n]+',
                                r'\g<1>140', display.read_text(), count=1)
        self.assertEqual(count, 1)
        display.write_text(changed)
        before = {p: p.read_bytes() for p in self.config.parent.rglob('*') if p.is_file()}
        result, report = self.run_validator(guarded=True)
        self.assertEqual(result.returncode, 0, report.get('error', '') + '\n' + result.stderr)
        self.assertEqual(report['status'], 'VALIDATED_CONFIG')
        self.assertEqual(report['host_revision'], 'RC8-CONTROL-R7-HOMING-R38.6')
        self.assertEqual(report['serial'], '/dev/ttyS5')
        self.assertEqual(report['baud'], 750000)
        self.assertEqual(report['home_routes']['INVENTURE_HOME_ALL'], 'XYZ')
        self.assertFalse(report['mcu_connected'])
        self.assertFalse(report['gcode_executed'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.config.parent.rglob('*') if p.is_file()})

    def test_legacy_extra_xy_home_is_rejected_without_running_it(self):
        matches = [p for p in self.config.parent.glob('*.cfg')
                   if '[gcode_macro INVENTURE_HOME_ALL]' in p.read_text()]
        self.assertEqual(len(matches), 1)
        path = matches[0]
        text = path.read_text()
        needle = 'INVENTURE_HOME_REQUEST AXES=XYZ {rawparams}'
        self.assertIn(needle, text)
        path.write_text(text.replace(needle, 'G28 X Y\n    ' + needle, 1))
        result, report = self.run_validator(guarded=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('INVENTURE_HOME_ALL', report['error'])

    def test_parser_error_is_json_and_releases_the_io_worker(self):
        with self.config.open('a') as stream:
            stream.write('\n[invalid_bootstrap_test_section]\n')
        result, report = self.run_validator(guarded=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['status'], 'INVALID_CONFIG')
        self.assertIn('invalid_bootstrap_test_section', report['error'])


if __name__ == '__main__':
    unittest.main()
