"""Exercise the installed renderer using the real pinned Klipper startup."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from inventure_bootstrap import prepare_host
from inventure_bootstrap_config import stage_config


class RestoredOledRuntimeTests(unittest.TestCase):
    def test_published_pixels_targets_sensor_freshness_footer_and_wire(self):
        with tempfile.TemporaryDirectory(prefix='inventure-oled-restoration-') as directory:
            root = Path(directory)
            host = prepare_host(root / 'host', repo=ROOT)
            source = root / 'original/printer.cfg'
            source.parent.mkdir()
            source.write_text('[mcu]\nserial: /dev/ttyS5\nbaud: 750000\n'
                              '[printer]\nkinematics: none\nmax_velocity: 30\n'
                              'max_accel: 200\n[inventure_mcu]\nallow_actuators: False\n')
            gcodes = root / 'gcodes'
            gcodes.mkdir()
            report = stage_config(host, root / 'staged', source,
                                  serial='/dev/ttyS5', baud=750000, gcodes_path=gcodes,
                                  restore_field_profile=True)
            before = {p: p.read_bytes() for p in (root / 'staged').rglob('*') if p.is_file()}
            png = root / 'simulated-oled.png'
            result = subprocess.run(
                [sys.executable, str(ROOT / 'tests/support/check_restored_oled.py'),
                 '--klipper', str(host), '--config', report['config_path'],
                 '--output-png', str(png)], capture_output=True, text=True, timeout=90)
            try:
                proof = json.loads(result.stdout)
            except ValueError:
                self.fail('OLED proof did not return JSON: ' + result.stdout + '\n' + result.stderr)
            self.assertEqual(result.returncode, 0, proof.get('error', '') + '\n' + result.stderr)
            self.assertEqual(proof['status'], 'VERIFIED_RESTORED_OLED')
            self.assertEqual(len(proof['checks']), 7)
            self.assertGreaterEqual(proof['pixel_oracle_comparisons'], 40)
            self.assertEqual(proof['sample_temperatures_c'], [91, 34, 28, 32])
            self.assertEqual(proof['ready_pixel_bounds'], [2, 52, 30, 58])
            self.assertTrue(proof['simulated_data'])
            self.assertFalse(proof['hardware_tested'])
            self.assertFalse(proof['reactor_run'])
            self.assertFalse(proof['gcode_executed'])
            self.assertTrue(png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(before, {p: p.read_bytes() for p in (root / 'staged').rglob('*')
                                      if p.is_file()})


if __name__ == '__main__':
    unittest.main()
