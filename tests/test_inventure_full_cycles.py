"""Acceptance of complete operator cycles in the actual installed R22 host."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstalledCycleTests(unittest.TestCase):
    def test_repeated_operator_cycles_and_faults_keep_one_owner(self):
        with tempfile.TemporaryDirectory(prefix='test-inventure-full-cycles-') as tmp:
            report = Path(os.environ.get('INVENTURE_CYCLE_REPORT', str(Path(tmp) / 'cycles.json')))
            result = subprocess.run(
                [sys.executable, str(ROOT / 'ci/check_inventure_full_cycles.py'),
                 '--output', str(report)], capture_output=True, text=True, timeout=150)
            self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
            proof = json.loads(report.read_text())
            self.assertEqual(proof['status'], 'PASS_INSTALLED_R22_FULL_CYCLES')
            self.assertEqual(proof['cycle_count'], 36)
            self.assertEqual([s['handoffs'] for s in proof['sessions']], [6, 6])
            self.assertEqual(len(proof['refusals']), 9)
            self.assertTrue(all(r['status'] == 'PASS' for r in proof['refusals']))
            self.assertTrue(proof['config_unchanged'])
            self.assertFalse(proof['serial_opened'])
            self.assertFalse(proof['firmware_flashed'])
            self.assertFalse(proof['hardware_tested'])


if __name__ == '__main__':
    unittest.main()
