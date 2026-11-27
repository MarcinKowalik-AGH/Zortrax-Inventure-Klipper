"""Regression coverage for the paired native STEP path and existing Home runtime."""
import logging
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import inventure_bootstrap as bootstrap
from inventure_xy_step_restore import TARGET
from test_install_new_host import installer, r24_mcu_identity


ROOT = Path(__file__).resolve().parents[1]
CONSTANTS = dict(INVENTURE_XY_STEP_TIMER_ABI=1, INVENTURE_XY_STEP_MIN_TICKS=1200,
                 CLOCK_FREQ=120000000)


class CoreXYStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ROOT/'restored/xy_step_r28/inventure_xy_step.py'
        namespace = dict(math=math, logging=logging)
        exec(compile(source.read_bytes(), str(source), 'exec'), namespace)
        cls.route = namespace['InventureXYStep']

    def motion(self, verified=3, constants=CONSTANTS, pulse=4.e-6,
               b_step_dist=1./640, wiring=('PA7', 'PA6')):
        commands, handlers, calls = [], {}, []
        response = dict(verified=verified, enabled=int(verified == 3 and pulse == 4.e-6),
                        active=0, blocks_a=3, blocks_b=4, steps_a=600, steps_b=400,
                        aborted=0, errors=0, peak_late=50)

        def query(command, reply):
            self.assertEqual(command, 'inventure_xy_step_query')
            self.assertIn('verified=%c enabled=%c active=%c', reply)
            return SimpleNamespace(send=lambda: response.copy())

        mcu = SimpleNamespace(get_constants=lambda: constants.copy(),
                              add_config_cmd=commands.append,
                              lookup_query_command=query, is_fileoutput=lambda: False,
                              register_config_callback=calls.append)
        gcode = SimpleNamespace(register_command=lambda name, handler, **_: handlers.update({name: handler}))
        printer = SimpleNamespace(lookup_object=lambda _: gcode,
                                  register_event_handler=lambda _, cb: calls.append(cb))
        sections = {'stepper_x': dict(step_pin=wiring[0], dir_pin='PB2'),
                    'stepper_y': dict(step_pin=wiring[1], dir_pin='PB1')}
        config = SimpleNamespace(get_printer=lambda: printer,
                                 getsection=lambda name: sections[name])
        rails = []
        for distance in (1./640, b_step_dist):
            stepper = SimpleNamespace(get_mcu=lambda: mcu, get_pulse_duration=lambda: (pulse, False),
                                      get_step_dist=lambda distance=distance: distance)
            rails.append(SimpleNamespace(get_steppers=lambda s=stepper: [s]))
        route = self.route(config, rails, 30.)
        for callback in calls:
            callback()
        return route, commands, handlers

    def speed(self, route, delta, requested):
        length = math.sqrt(sum(axis * axis for axis in delta))
        move = SimpleNamespace(axes_r=[axis / length for axis in delta],
                               max_cruise_v2=requested ** 2, accel=200.)
        limited = []
        move.limit_speed = lambda speed, accel: limited.append((speed, accel))
        route.check_move(move)
        for _, accel in limited:
            self.assertEqual(accel, 200.)
        return limited[0][0] if limited else requested

    def test_corexy_both_diagonals_and_mixed_axis_limits(self):
        route, commands, handlers = self.motion()
        self.assertEqual(commands, ['inventure_xy_step_config'])
        self.assertIn('INVENTURE_XY_STEP_STATUS', handlers)
        for x in (-1, 1):
            for y in (-1, 1):
                self.assertAlmostEqual(self.speed(route, (x, y, 0), 300.),
                                       100000 / (640 * math.sqrt(2)))
        self.assertAlmostEqual(self.speed(route, (1, 0, 0), 300.), 156.25)
        self.assertAlmostEqual(self.speed(route, (2, 1, 2), 300.), 156.25)
        self.assertEqual(self.speed(route, (0, 0, 1), 8.3), 8.3)
        self.assertEqual(self.speed(route, (1, 1, 0), 30.), 30.)
        self.assertEqual(self.speed(route, (1, 0, 0), 5.), 5.)
        route, _, _ = self.motion(b_step_dist=1./800)
        self.assertAlmostEqual(self.speed(route, (1, -1, 0), 300.),
                               100000 / (800 * math.sqrt(2)))

    def test_no_automatic_speed_increase_with_missing_timer_or_changed_pulse(self):
        for options in (dict(verified=1), dict(pulse=5.e-6)):
            route, _, _ = self.motion(**options)
            self.assertEqual(self.speed(route, (1, 1, 0), 300.), 30.)
        for options in (dict(constants={}), dict(wiring=('!PA7', 'PA6'))):
            route, commands, _ = self.motion(**options)
            self.assertEqual(commands, [])
            self.assertEqual(self.speed(route, (1, 1, 0), 30.), 30.)

    def test_status_surfaces_steps_for_both_driver_channels(self):
        _, _, handlers = self.motion()
        report = []
        handlers['INVENTURE_XY_STEP_STATUS'](SimpleNamespace(respond_info=report.append))
        self.assertIn('verified=3 enabled=1 active=0', report[0])
        self.assertIn('steps_a=600 steps_b=400', report[0])

    def test_paired_host_upgrades_and_reverts_a_single_runtime_source(self):
        with tempfile.TemporaryDirectory() as temporary, bootstrap.procedure_profile('operator-home-r24'):
            host = Path(temporary)/'host'
            _, old_files, old_meta = bootstrap._plan(ROOT)
            bootstrap._write_files(host, old_files)
            marker = host/bootstrap.MANIFEST_NAME
            marker.write_bytes(bootstrap._metadata_bytes(old_meta))
            marker.chmod(0o644)
            for enabled in (True, False):
                with bootstrap.xy_step_timers(enabled):
                    plan = bootstrap.prepare_runtime_update(host)
                    self.assertEqual(set(plan['files']), {host/TARGET, marker})
                    for path, (_, new) in plan['files'].items():
                        path.write_bytes(new)
                    self.assertEqual(bootstrap.verify_host(host)['status'], 'VERIFIED_HOST')
            self.assertEqual(marker.read_bytes(), bootstrap._metadata_bytes(old_meta))
            self.assertEqual((host/TARGET).read_bytes(), old_files[TARGET][0])

    def test_mcu_identity_requires_rate_bound_and_all_prior_abis(self):
        mcu = r24_mcu_identity()
        constants = mcu['mcu_constants']
        constants.update(CONSTANTS, INVENTURE_MCU_RELEASE=installer.MCU_XY_STEP_RELEASE,
                         INVENTURE_FACTORY_INFO_ABI=1, INVENTURE_FACTORY_COUNTER_ABI=1,
                         INVENTURE_OLED_COEXISTENCE_ABI=1, INVENTURE_UART_TIMER_WINDOW_ABI=1)
        self.assertEqual(installer.require_mcu(mcu)['mcu_release'], installer.MCU_XY_STEP_RELEASE)
        del constants['INVENTURE_XY_STEP_MIN_TICKS']
        with self.assertRaisesRegex(ValueError, 'CoreXY STEP'):
            installer.require_mcu(mcu)


if __name__ == '__main__':
    unittest.main()
