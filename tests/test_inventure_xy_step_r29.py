"""Paired OEM CoreXY profile coverage with the authenticated host source."""
import logging
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import inventure_bootstrap as bootstrap
from inventure_xy_step_restore import TARGET
from test_install_new_host import installer, r24_mcu_identity
import test_inventure_xy_step_r28 as r28

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = dict(INVENTURE_XY_STEP_TIMER_ABI=2, INVENTURE_XY_STEP_MIN_TICKS=660,
                    INVENTURE_XY_STEP_HIGH_TICKS=294, CLOCK_FREQ=120000000)


class OEMXYStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = dict(math=math, logging=logging)
        for relative in ('xy_step_r28/inventure_xy_step.py', 'xy_step_r29/inventure_xy_profile.py'):
            source = ROOT/'restored'/relative
            exec(compile(source.read_bytes(), str(source), 'exec'), namespace)
        cls.route = namespace['InventureXYStep']

    def motion(self, **options):
        options.setdefault('constants', CAPABILITIES)
        return r28.CoreXYStepTests.motion(self, **options)

    def limits(self, route, delta, velocity=300., accel=3000.):
        length = math.sqrt(sum(axis * axis for axis in delta))
        move = SimpleNamespace(axes_r=[axis/length for axis in delta],
                               max_cruise_v2=velocity**2, accel=accel)
        result = []
        move.limit_speed = lambda speed, a: result.append((min(speed,velocity), min(a,accel)))
        route.check_move(move)
        return result[0] if result else (velocity,accel)

    def test_oem_motor_clamp_on_both_diagonals_with_mixed_acceleration(self):
        route, commands, _ = self.motion()
        self.assertEqual(commands, ['inventure_xy_step_config'])
        self.assertEqual(self.limits(route, (1,0,0)), (200.,1500.))
        self.assertEqual(self.limits(route, (0,-1,0)), (200.,1500.))
        for dx in (-1,1):
            for dy in (-1,1):
                speed, accel = self.limits(route, (dx,dy,0))
                self.assertAlmostEqual(speed, 200./math.sqrt(2.))
                self.assertEqual(accel, 1500.)
        self.assertEqual(self.limits(route, (1,0,0), 5., 200.), (5.,200.))
        self.assertEqual(self.limits(route, (0,0,1), 8.3, 20.), (8.3,20.))
        route, _, _ = self.motion(b_step_dist=1./1000)
        self.assertAlmostEqual(self.limits(route, (1,-1,0))[0],
                               120000000./660/1000/math.sqrt(2.))

    def test_fallback_requires_both_timer_channels_and_matched_wiring(self):
        for options in (dict(verified=1), dict(pulse=5.e-6), dict(constants={}),
                        dict(wiring=('!PA7','PA6'))):
            route, _, _ = self.motion(**options)
            self.assertEqual(self.limits(route, (1,1,0)), (30.,200.))

    def test_installer_accepts_only_matched_mcu_constants(self):
        mcu = r24_mcu_identity()
        constants = mcu['mcu_constants']
        constants.update(CAPABILITIES, INVENTURE_MCU_RELEASE=installer.MCU_OEM_XY_STEP_RELEASE,
                         INVENTURE_FACTORY_INFO_ABI=1, INVENTURE_FACTORY_COUNTER_ABI=1,
                         INVENTURE_OLED_COEXISTENCE_ABI=1, INVENTURE_UART_TIMER_WINDOW_ABI=1)
        self.assertEqual(installer.require_mcu(mcu)['mcu_release'], installer.MCU_OEM_XY_STEP_RELEASE)
        constants['INVENTURE_XY_STEP_HIGH_TICKS'] = 480
        with self.assertRaisesRegex(ValueError, '2.45 us'):
            installer.require_mcu(mcu)

    def test_host_upgrade_and_rollback_preserve_the_r28_manifest(self):
        with TemporaryDirectory() as directory, bootstrap.procedure_profile('operator-home-r24'):
            host = Path(directory)/'host'
            _, old_files, old_meta = bootstrap._plan(ROOT,use_xy_step_timers=True)
            bootstrap._write_files(host,old_files)
            marker = host/bootstrap.MANIFEST_NAME
            marker.write_bytes(bootstrap._metadata_bytes(old_meta))
            marker.chmod(0o644)
            for mode in ('r29', True):
                with bootstrap.xy_step_timers(mode):
                    update = bootstrap.prepare_runtime_update(host)
                    self.assertEqual(set(update['files']), {host/TARGET, marker})
                    for path, (_, new) in update['files'].items():
                        path.write_bytes(new)
                    self.assertEqual(bootstrap.verify_host(host)['status'],'VERIFIED_HOST')
            self.assertEqual(marker.read_bytes(), bootstrap._metadata_bytes(old_meta))


if __name__ == '__main__':
    unittest.main()
