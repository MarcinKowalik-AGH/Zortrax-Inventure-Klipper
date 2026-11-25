"""The installed R24 route must retain accepted engines and conditional XY."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class OperatorHomeR24Tests(unittest.TestCase):
    def test_installed_fluidd_and_oled_independent_axes_then_z(self):
        with tempfile.TemporaryDirectory(prefix='r24-operator-home-test-') as temp:
            output=Path(temp)/'verified'
            result=subprocess.run([sys.executable,str(ROOT/'ci/check_inventure_home_r24.py'),
                                   '--destination',str(output)],capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads((output/'R24_HOME_CYCLES.json').read_text())
            self.assertEqual(report['status'],'PASS_INSTALLED_R24_HOME_CYCLES')
            self.assertEqual(report['case_count'],126)
            self.assertEqual(report['refusal_count'],30)
            for row in report['cases']:
                self.assertEqual(row['xy_seek'],row['xy_release'])
                self.assertTrue(row['enable_held'])
                self.assertTrue(row['targets_unchanged'])
            after_xy=[row for row in report['cases'] if row['sequence']==['X','Y','Z'] and row['request']=='Z']
            self.assertEqual(len(after_xy),6)
            self.assertTrue(all(row['xy_seek']==[] for row in after_xy))
            missing=[row for row in report['cases'] if row['sequence']==['Z']]
            self.assertTrue(all(row['xy_seek']==['Y','X'] for row in missing))
            self.assertTrue(report['configuration_unchanged'])
            self.assertFalse(report['hardware_tested'])

    def test_held_handoff_is_checked_in_actual_mcu_c(self):
        result=subprocess.run([sys.executable,str(ROOT/'ci/check_inventure_home_r24_mcu.py')],
                              capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(report['status'],'PASS_R24_HELD_SERVICE_REGISTER_TESTS')
        self.assertFalse(report['hardware_tested'])

    def test_installer_accepts_r24_only_with_its_capability_and_retains_old_validation(self):
        spec=importlib.util.spec_from_file_location('r24_mcu_identity_installer',ROOT/'install-new-host.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from test_install_new_host import mcu_identity
        # The existing R23 validator is exercised using the same fixture,
        # then R24 adds its independent held-entry capability requirement.
        fixture=mcu_identity()['mcu_constants']
        fixture.update(INVENTURE_NATIVE_SERVICE_HANDOFF_ABI=1,
            INVENTURE_NATIVE_THERMAL_PROFILE_ABI=1,INVENTURE_FAULT_REASON_ABI=1,
            INVENTURE_SCHEDULER_ABI=1,INVENTURE_SCHEDULER_NATIVE_HZ=10000,
            INVENTURE_SCHEDULER_SERVICE_HZ=20000,INVENTURE_SCHEDULER_GUARD_MS=10,
            INVENTURE_SERVICE_NATIVE_SPLIT_ABI=1,INVENTURE_POLICY_STATUS_ABI=1,
            INVENTURE_DISPLAY_ERROR_ABI=1,INVENTURE_LOCAL_OLED_ANIMATION_ABI=1,
            INVENTURE_LOCAL_OLED_NATIVE_GRAY4=1,INVENTURE_LOCAL_SPLASH_ABI=1,
            INVENTURE_OLED_HOSTLINK_TIMEOUT_MS=1500)
        data=dict(mcu_version='r24',mcu_constants=dict(fixture,
            INVENTURE_MCU_RELEASE='RC8-CONTROL-R7-HOME-R24-OLED',INVENTURE_NATIVE_HELD_SERVICE_ABI=1))
        self.assertEqual(module.require_mcu(data)['native_held_service_abi'],1)
        data['mcu_constants'].pop('INVENTURE_NATIVE_HELD_SERVICE_ABI')
        with self.assertRaisesRegex(ValueError,'held-service'):module.require_mcu(data)

if __name__=='__main__':unittest.main()
