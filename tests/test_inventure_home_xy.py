"""Protect the restored standalone Home X/Y routes and native G28 handler."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class InstalledHomeXYTests(unittest.TestCase):
    def test_fluidd_and_physical_oled_preserve_the_recorded_independent_axis_traces(self):
        with tempfile.TemporaryDirectory(prefix='inventure-home-xy-test-') as directory:
            out=Path(directory)/'verified'
            result=subprocess.run([sys.executable,str(ROOT/'ci/check_inventure_home_xy.py'),
                                   '--destination',str(out)],capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads((out/'HOME_XY_REFERENCE.json').read_text())
            self.assertEqual(report['status'],'PASS_INSTALLED_HOME_XY_REFERENCE')
            self.assertEqual(report['g28_handler'],'PrinterHoming.cmd_G28')
            self.assertEqual(report['overrides'],[])
            self.assertEqual(report['case_count'],24)
            self.assertEqual({r['origin'] for r in report['cases']},{'fluidd','oled'})
            self.assertEqual({r['initial_state'] for r in report['cases']},
                             {'NATIVE_REFERENCED','OFF_UNREFERENCED'})
            for row in report['cases']:
                self.assertTrue(row['reference_trace_equal'])
                self.assertTrue(row['targets_unchanged'])
                self.assertEqual([x['axis'] for x in row['completions']],row['sequence'])
                self.assertTrue(all(x['enable_held'] for x in row['completions']))
                self.assertFalse(row['motor_off'])
                self.assertFalse(row['service'])
                self.assertFalse(row['heating'])
            self.assertTrue(report['runtime_unchanged'])
            self.assertTrue(report['config_unchanged'])
            self.assertFalse(report['serial_opened'])
            self.assertFalse(report['firmware_flashed'])
            self.assertFalse(report['hardware_tested'])


if __name__=='__main__':
    unittest.main()
