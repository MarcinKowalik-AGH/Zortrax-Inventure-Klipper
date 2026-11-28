"""Check the paired 224-tick host, its fallback, and the R29 upgrade path."""
import logging
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import inventure_bootstrap as bootstrap
from inventure_xy_step_restore import TARGET
from test_install_new_host import installer, r24_mcu_identity
import test_inventure_xy_step_r28 as r28
import test_inventure_xy_step_r29 as r29


ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = dict(CLOCK_FREQ=120000000, INVENTURE_XY_STEP_TIMER_ABI=3,
                    INVENTURE_XY_STEP_MIN_TICKS=448, INVENTURE_XY_STEP_HIGH_TICKS=294,
                    INVENTURE_XY_STEP_LOW_FLOOR_TICKS=90)


class InventureFastXYTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = dict(math=math, logging=logging)
        for path in ('xy_step_r28/inventure_xy_step.py', 'xy_step_r29/inventure_xy_profile.py',
                     'xy_step_r30/inventure_xy_profile.py'):
            source = ROOT/'restored'/path
            exec(compile(source.read_bytes(), str(source), 'exec'), namespace)
        cls.route = namespace['InventureXYStep']

    def test_unmatched_timers_and_software_pulse_fall_back(self):
        cases = (dict(constants=r29.CAPABILITIES), dict(pulse=5.e-6),
                 dict(wiring=('!PA7', 'PA6')), dict(verified=1))
        for case in cases:
            with self.subTest(case=case):
                options = dict(constants=CAPABILITIES)
                options.update(case)
                route, commands, _ = r28.CoreXYStepTests.motion(self, **options)
                self.assertEqual(commands, ['inventure_xy_step_config'] if 'verified' in case else [])
                limits = r29.OEMXYStepTests.limits(self, route, (1, -1, 0))
                self.assertEqual(limits, (30., 200.))

    def test_installer_requires_the_exact_r30_timer_bounds(self):
        mcu = r24_mcu_identity()
        constants = mcu['mcu_constants']
        constants.update(CAPABILITIES, INVENTURE_MCU_RELEASE=installer.MCU_XY_STEP_R30_RELEASE,
                         INVENTURE_FACTORY_INFO_ABI=1, INVENTURE_FACTORY_COUNTER_ABI=1,
                         INVENTURE_OLED_COEXISTENCE_ABI=1, INVENTURE_UART_TIMER_WINDOW_ABI=1)
        self.assertEqual(installer.require_mcu(mcu)['mcu_release'], installer.MCU_XY_STEP_R30_RELEASE)
        constants['INVENTURE_XY_STEP_LOW_FLOOR_TICKS'] = 60
        with self.assertRaisesRegex(ValueError, '224-tick CoreXY STEP'):
            installer.require_mcu(mcu)

    def test_upgrade_and_rollback_replace_only_the_paired_kinematics_source(self):
        with TemporaryDirectory() as directory, bootstrap.procedure_profile('operator-home-r24'):
            host = Path(directory)/'host'
            _, old_files, old_meta = bootstrap._plan(ROOT, use_xy_step_timers='r29')
            bootstrap._write_files(host, old_files)
            marker = host/bootstrap.MANIFEST_NAME
            marker.write_bytes(bootstrap._metadata_bytes(old_meta))
            marker.chmod(0o644)
            for revision in ('r30', 'r29'):
                with bootstrap.xy_step_timers(revision):
                    update = bootstrap.prepare_runtime_update(host)
                    self.assertEqual(set(update['files']), {host/TARGET, marker})
                    for path, (_, new) in update['files'].items():
                        path.write_bytes(new)
                    self.assertEqual(bootstrap.verify_host(host)['status'], 'VERIFIED_HOST')
            self.assertEqual(marker.read_bytes(), bootstrap._metadata_bytes(old_meta))
            self.assertEqual((host/TARGET).read_bytes(), old_files[TARGET][0])


if __name__ == '__main__':
    unittest.main()
