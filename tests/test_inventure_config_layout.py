"""Verify field configuration migration and physical OLED input semantics."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from ci.check_native_nozzle_startup import prepare_runtime
import inventure_config_layout as layout
from test_orange_pi_update import update
from test_orange_pi_oled_fast import oled, t as transport
from test_orange_pi_zortrax_menu import MenuFixture, service_module


class ConfigLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.runtime = prepare_runtime(Path(cls.tmp.name))
        cls.parser = update.klipper_config_module(cls.runtime)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.main = self.directory/'printer.cfg'

    def plan(self):
        changes = {}
        layout.organize_klipper(self.parser, self.main, changes)
        return changes

    def test_oem_thresholds_and_linear_channels_match_controller_at_every_raw_code(self):
        from bisect import bisect_left
        support = layout.ROOT/'service_mcu/control_r6/TESTS/support/inventure_lut.h'
        reference = [(int(a), int(b)) for a, b in re.findall(r'\{(\d+), (-?\d+)\}', support.read_text())]
        self.assertEqual(len(reference), 205)
        self.main.write_text('[inventure_mcu]\n')
        conf = self.parser.ConfigFileReader()
        sensors = layout.ROOT/'config/inventure_temperature_sensors.cfg'
        parsed = conf.build_fileconfig_with_includes(sensors.read_text(), str(sensors))['inventure_mcu']
        rows = [tuple(map(int, line.split(','))) for line in parsed['thermistor_thresholds'].splitlines() if line]
        original = json.loads((self.runtime/'klippy/extras/inventure_lut.json').read_bytes())
        self.assertEqual(rows, list(map(tuple, original)))
        self.assertEqual(rows, reference)
        values = [a for a, _ in rows]
        firmware_thresholds = [a for a, _ in reference]
        for adc in range(4096):
            i, j = bisect_left(values, adc), bisect_left(firmware_thresholds, adc)
            self.assertEqual(rows[i][1] if i < len(rows) else -46,
                             reference[j][1] if j < len(reference) else -46)
            self.assertAlmostEqual(adc * parsed.getint('nozzle_reference_mv') / 4095 / parsed.getint('nozzle_mv_per_c'),
                                   adc * 660 / 4095)

    def test_site_override_and_autosave_kept_byte_for_byte_on_reinstall(self):
        custom = '[gcode_macro OWNER_MACRO]\r\ngcode:\r\n    M118 keep my macro\r\n'
        tail = '#*# <---------------------- SAVE_CONFIG ---------------------->\r\n#*# [extruder]\r\n#*# pid_kp = 23.017\r\n'
        self.main.write_bytes(('[inventure_mcu]\r\n[include *.inc]\r\n'+custom+tail).encode())
        display = self.directory/'screen.inc'
        display.write_bytes(b'[inventure_oled]\r\nsleep_timeout: 123\r\n')
        plan = self.plan()
        self.assertTrue(plan[self.main][1].endswith(tail.encode()))
        self.assertIn(custom.encode(), plan[self.main][1])
        for path, (before, after) in plan.items():
            path.parent.mkdir(exist_ok=True)
            update.atomic_config_write(path, after, create=before is None)
        self.assertEqual(self.plan(), {})
        opts = layout.active_klipper(self.parser, self.main, {})[0]['inventure_oled']
        self.assertEqual(opts.getint('sleep_timeout'), 123)
        self.assertEqual(opts.get('timer_policy'), 'inventure')

    def test_r34_split_tree_migrates_once_with_custom_pid_and_native_sleep(self):
        folder = self.directory / 'inventure'
        folder.mkdir()
        display = folder / 'display.cfg'
        display.write_text(layout.MANAGED + '[inventure_oled]\nspi_speed: 2000000\n'
                           'dim_minutes: 5\nsleep_minutes: 15\nbrightness: 16\ntimer_policy: minutes\n')
        (folder/'heaters.cfg').write_text(layout.MANAGED + '[extruder]\nheater_pin: inventure_r5:HEATER_T0\nsensor_type: Inventure R5 ADC\nsensor_channel: 0\n')
        self.main.write_text('[inventure_mcu]\n[include inventure/display.cfg]\n[extruder]\n'
                             'control: pid\npid_Kp: 29.37  # tuned on this machine\npid_Ki: 1.2\npid_Kd: 123\n'
                             '[include inventure/heaters.cfg]\n')
        for path, (_, data) in self.plan().items():
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
        self.assertEqual(self.plan(), {})
        opts = layout.active_klipper(self.parser, self.main, {})[0]
        self.assertEqual(opts.get('inventure_oled', 'timer_policy'), 'inventure')
        self.assertEqual(opts.getint('inventure_oled', 'spi_speed'), 2000000)
        self.assertEqual(opts.getfloat('inventure_mcu', 't0_pid_kp'), 29.37)
        self.assertIn('# tuned on this machine', (folder/'heater_control.cfg').read_text())
        self.assertFalse(opts.has_option('extruder', 'pid_kp'))
        display.write_text(display.read_text().replace('timer_policy: inventure','timer_policy: minutes'))
        self.assertEqual(self.plan(), {})

    def test_raw_table_matches_the_r0323_flash_bytes_at_080296b6(self):
        import hashlib
        import struct
        curve = (layout.ROOT/'config/inventure_temperature_sensors.cfg').read_text()
        rows = [struct.pack('<Hh',int(a),int(t)) for a,t in re.findall(r'^    (\d+), (-?\d+)$', curve, re.M)]
        self.assertEqual(len(rows), 205)
        self.assertEqual(hashlib.sha256(b''.join(rows)).hexdigest(),
                         'dbe1775fa947a9b697230e4045b9c8012d1778c3abef8f875f06ed5a1384dc9e')

    def test_conflicting_custom_map_and_external_symlink_do_not_write(self):
        self.main.write_text('[inventure_mcu]\n')
        folder = self.directory/'inventure'
        folder.mkdir()
        target = folder/'temperature_sensors.cfg'
        target.write_text('# Site sensor mapping\n')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.plan()
        self.assertEqual(target.read_text(), '# Site sensor mapping\n')
        target.unlink()
        target.symlink_to(self.main)
        with self.assertRaisesRegex(ValueError, 'destination'):
            self.plan()
        self.assertEqual(self.main.read_text(), '[inventure_mcu]\n')

    def test_existing_layout_glob_includes_each_new_file_only_once(self):
        self.main.write_text('[inventure_mcu]\n[inventure_oled]\n[include inventure/*.cfg]\n')
        for path, (before, after) in self.plan().items():
            path.parent.mkdir(exist_ok=True)
            update.atomic_config_write(path, after, create=before is None)
        sensor = self.directory/'inventure/temperature_sensors.cfg'
        sensor.write_text('# Site note\n'+sensor.read_text())
        self.assertEqual(self.plan(), {})
        _, files = layout.active_klipper(self.parser, self.main, {})
        self.assertEqual(files.count(sensor), 1)

    def test_moonraker_cleanup_respects_custom_updaters_and_effective_clients(self):
        root = self.directory/'moonraker.conf'
        auth = self.directory/'access.conf'
        web = self.directory/'fluidd'
        auth.write_text('[authorization]\ntrusted_clients:\n    127.0.0.1\n    127.0.0.1\n'
                        'cors_domains:\n    http://example.test\n    http://example.test\n')
        root.write_text('[server]\nport: 7125\n[include access.conf]\n'
                        '[update_manager fluidd]\ntype: web\nrepo: fluidd-core/fluidd\npath: '+str(web)+'\n'
                        '[update_manager fluidd-config]\ntype: git_repo\norigin: https://github.com/fluidd-core/fluidd-config.git\n'
                        '[update_manager site]\ntype: git_repo\norigin: https://example.test/site.git\n')
        staged = {}
        removed = layout.organize_moonraker(root, [self.directory/'fluidd.cfg'], web, staged)
        self.assertEqual(removed, ['update_manager fluidd'])
        self.assertIn(b'[update_manager fluidd-config]', staged[root][1])
        self.assertIn(b'example.test/site.git', staged[root][1])
        self.assertEqual(staged[auth][1].count(b'127.0.0.1'), 1)
        self.assertEqual(staged[auth][1].count(b'http://example.test'), 2)
        self.assertEqual(layout.organize_moonraker(root, [], self.directory/'other', {}),
                         ['update_manager fluidd-config'])
        for path, (_, data) in staged.items():
            path.write_bytes(data)
        again = {}
        self.assertEqual(layout.organize_moonraker(root, [self.directory/'fluidd.cfg'], web, again), [])
        self.assertFalse(again)

    def test_failed_moonraker_restart_rolls_back_old_files_and_removes_new_includes(self):
        self.main.write_text('[inventure_mcu]\n[inventure_oled]\n')
        moon = self.directory/'moonraker.conf'
        moon.write_text('[authorization]\ntrusted_clients:\n    127.0.0.1\n    127.0.0.1\n')
        originals = {p: p.read_bytes() for p in (self.main, moon)}
        def service(action, name='klipper'):
            if action == 'restart' and name == 'moonraker' and moon.read_bytes() != originals[moon]:
                raise ValueError('simulated startup failure')
        with patch.object(update.core, 'running_klipper_target', return_value=self.runtime), \
                patch.object(update.core, 'running_printer_config', return_value=self.main), \
                patch.object(update.core, 'install', return_value={'host_revision':'test','status':'ALREADY_INSTALLED'}), \
                patch.object(update, 'service', side_effect=service), patch.object(update, 'check_print_job'), \
                patch.object(update.os, 'geteuid', return_value=0), self.assertRaisesRegex(ValueError, 'simulated'):
            update.update_host(False, False, False, False, False, False, False, None, self.directory/'site', True, moon)
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(list((self.directory/'inventure').glob('*.cfg')), [])


class DisplayParityTests(unittest.TestCase):
    def test_factory_defaults_or_saved_minutes_or_legacy_seconds(self):
        _, panel = oled()
        _, menu = MenuFixture().host()
        panel.board.zortrax = menu
        panel._apply_display_settings()
        self.assertFalse(panel.minute_timers)
        self.assertEqual((panel.sleep_timeout, panel.sleep_notice), (300, 10))
        menu.settings.set(dim_min=7, timer_policy='minutes')
        panel._apply_display_settings()
        self.assertEqual((panel.dim_timeout, panel.sleep_timeout), (420, 600))
        menu.settings.set(idle_s=121, timer_policy='inventure')
        panel._apply_display_settings()
        self.assertFalse(panel.minute_timers)
        self.assertEqual(panel.sleep_timeout, 121)
        panel.cmd_display_control(service_module.Command(menu, DIM=8, SLEEP=16, LEVEL=9))
        panel._apply_display_settings()
        self.assertEqual((panel.dim_timeout, panel.sleep_timeout, panel.stream.awake_level), (480, 960, 8))

    def test_mcu_preference_is_retried_if_not_acknowledged_and_stops_after_readback(self):
        from types import SimpleNamespace
        import importlib
        board_cls = importlib.import_module(service_module.__package__ + '.inventure_mcu').InventureMCU
        board = board_cls.__new__(board_cls)
        clock = [1.]
        command = Mock()
        board.connected = True
        board.reactor = SimpleNamespace(monotonic=lambda: clock[0])
        board.commands = {'oled_preference': command}
        board.local_brightness = None
        board.local_brightness_retry = 0.
        board.local_oled_diag = {}
        board.set_local_oled_brightness(7)
        ack = dict(state=10, boot_revision=17, baud=750000, reason=0, last_incident=2,
                   guard_input=0, board_fault=0, display_fault=0, brightness=16)
        board._deliver_local_oled(ack, 1.)
        self.assertEqual(command.send.call_count, 1)
        clock[0] += 2.
        board._deliver_local_oled(ack, 3.)
        self.assertEqual(command.send.call_count, 2)
        clock[0] += 2.
        board._deliver_local_oled(dict(ack,brightness=7), 5.)
        self.assertEqual(command.send.call_count, 2)
        self.assertEqual(board.local_oled_diag['last_incident_name'], 'host_display_timeout')
        self.assertEqual(board.local_oled_state['baud'], 750000)

    def test_sleep_lighting_uses_the_inventure_controller_only_on_transition(self):
        _, panel = oled()
        cmd = Mock()
        panel.board.commands = {'oled_sleep_light': cmd}
        panel._display_phase('dim')
        cmd.send.assert_not_called()
        panel.sleep_light = 'dynamic'
        panel._display_phase('notice')
        panel._display_phase('off')
        panel._display_phase('awake')
        self.assertEqual([c.args[0] for c in cmd.send.call_args_list], [[1],[0]])
        panel.sleep_light = 'manual'
        panel._display_phase('notice')
        self.assertEqual(cmd.send.call_count, 2)

    def test_dark_ok_wakes_once_and_its_release_cannot_select(self):
        _, panel = oled()
        panel._select = Mock()
        panel.configured_policy = 'minutes'
        panel._apply_display_settings()
        panel.last_activity = 0.
        panel._service_display(900.)
        self.assertEqual(panel.sleep_phase, 'off')
        receipt = panel._input_receipt() + (False,)
        panel._buttons(901., 4, 1, 901., receipt)
        self.assertEqual(panel.sleep_phase, 'awake')
        self.assertTrue(panel.wake_gesture)
        panel.wake_wait = False  # physical awake frame has been acknowledged
        panel._buttons(901.2, 0, 2, 901.2, receipt)
        self.assertFalse(panel.wake_gesture)
        panel._select.assert_not_called()
        panel._buttons(901.5, 4, 3, 901.5, panel._input_receipt()+(True,))
        panel._select.assert_called_once()

    def test_confirmed_dim_selection_and_proportional_low_level_contrast(self):
        _, panel = oled()
        panel._apply_display_settings()
        panel.last_activity = 0.
        panel._active_work = lambda now: True
        panel._service_display(500.)
        panel._select = Mock()
        self.assertEqual(panel.sleep_phase, 'dim')
        panel._buttons(501., 4, 1, 501., panel._input_receipt()+(True,))
        self.assertEqual(panel.sleep_phase, 'awake')
        panel._select.assert_called_once()
        for level in range(16):
            ops = list(transport.mode_operations('awake','dim',False,level))
            self.assertEqual(ops[-1][1][1][0], max(1, round((level+1)*32/16)))

    def test_sleep_frame_matches_the_reference_moon_and_no_pixel_traffic_on_fade(self):
        from hashlib import sha256
        from test_orange_pi_oled_fast import o, ready_stream, run_frame, decode
        wire = o.sleep_wire()
        pixels = b''.join(wire[y*128+101:y*128+126] for y in range(7,57))
        self.assertEqual(sha256(pixels).hexdigest(), '56bd4351a045e02e673b0c0d53953f47eae010529b8d3c58e65a83a02e1b7bd1')
        stream = ready_stream(wire)
        stream.set_display_mode('dim')
        for step in range(16, 0, -1):
            stream.fade_step = step
            _, _, commands = run_frame(stream, now=17.-step)
            self.assertEqual([(args[0], decode(args)) for _, _, args in commands],
                             [(0,b'\xc1'),(1, bytes([1+round(31*(step-1)/15)]))])
        self.assertEqual(stream.completed_frames, 1)

    def test_wake_presents_the_first_fresh_frame_before_display_on(self):
        from test_orange_pi_oled_fast import ready_stream, run_frame, decode
        stream = ready_stream()
        stream.set_display_mode('off')
        run_frame(stream)
        stream.request(b'\x44'*8192, urgent=True)
        stream.set_display_mode('awake')
        now, panel, writes = run_frame(stream, now=2.)
        _, _, mode = run_frame(stream, now + .01, panel)
        commands = [decode(args) for _, _, args in writes+mode if args[0] == 0]
        self.assertLess(commands.index(b'\x5c'), commands.index(b'\xaf'))
        self.assertEqual(panel.ram, b'\x44'*8192)
        self.assertEqual(stream.display_mode, 'awake')
