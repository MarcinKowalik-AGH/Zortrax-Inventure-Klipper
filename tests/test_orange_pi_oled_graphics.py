import base64
import importlib
import json
from hashlib import sha256
from pathlib import Path
import unittest
import zlib
from unittest.mock import patch

from test_orange_pi_door_policy import adapter
from test_orange_pi_oled_fast import oled, Panel, run_frame

graphics = importlib.import_module(adapter.__package__ + '.inventure_oled_graphics')
native = graphics.native
menus = importlib.import_module(adapter.__package__ + '.inventure_oem_menu')
transport = importlib.import_module(adapter.__package__ + '.inventure_oled_uifast_transport')


class Graphics(unittest.TestCase):
    def test_project_gallery_matches_the_shaded_m300_masters_on_every_frame(self):
        root = Path(__file__).resolve().parents[1]
        art = root/'docs/assets/oled'
        masters = json.loads((art/'project_activity_frames.json').read_bytes())
        self.assertEqual(masters['style'], 'OEM_NATIVE_TONES_V1')
        groups = [('activity', 24, 'c64d5aa3dd4425b2c435c8bd97dd411a022dbd8cef93fe84490efdc0924703df'),
                  ('menu', 5, 'baf8e39497f52a8d9f0957d4a2898bd27d103c545d14617cd1433b49b7cedf3a')]
        self.assertEqual(len(graphics.project_symbols()), 29)
        for group, count, digest in groups:
            file = root/'orange_pi'/('inventure_project_%s_sprites.json' % group)
            self.assertEqual(sha256(file.read_bytes()).hexdigest(), digest)
            symbols = json.loads(file.read_bytes())['symbols']
            self.assertEqual(len(symbols), count)
            tones = set()
            for name, record in symbols.items():
                self.assertEqual((record['width'], record['height'], record['frame_ms']), (50, 50, 500))
                self.assertEqual(len({f['sha256'] for f in record['frames']}), 4)
                view = dict(labels=['Back'], selected=0, icon='project:' + name, animated=True)
                for i, frame in enumerate(record['frames']):
                    self.assertEqual(graphics.activity_index(view, .5*i), i)
                    src = graphics._unpack(frame, name)
                    if group == 'activity':
                        self.assertEqual(src, bytes.fromhex(masters['symbols'][name]['frames'][i]))
                    else:
                        self.assertTrue((art/'menu'/('%s_%d.svg' % (name, i))).is_file())
                    wire = graphics.menu_wire(view, index=i)
                    for y in range(50):
                        offset = (13+y)*128 + 101
                        self.assertEqual(wire[offset] >> 4, 0)
                        self.assertEqual(wire[offset+25] & 15, 0)
                        row = bytes((a << 4 & 255) | (b >> 4) for a,b in
                                    zip(wire[offset:offset+25], wire[offset+1:offset+26]))
                        self.assertEqual(row, src[y*25:y*25+25])
                    tones.update(v for b in src for v in (b >> 4, b & 15))
            self.assertEqual(tones, set(masters['palette']) if group == 'activity' else {0, 15})

    def test_oem_pages_and_cursor_match_the_reference_pixel_for_pixel(self):
        menu = menus.OEMMenu('en', operational=True)
        for page in menus.MENU:
            menu.page, menu.selected, menu.window = page, 0, 1
            for _ in menu.items():
                view = menu.view()
                if not (view.get('icon') or '').startswith('activity:'):
                    self.assertEqual(graphics.menu_wire(view), native.Frame().oem_menu(view).wire_bytes())
                menu.move(1)
        for name in native.get_oem_assets().icons:
            for notice in (None, True):
                view = dict(labels=['Back', 'Original Inventure artwork'], selected=1, icon=name, notice=notice)
                self.assertEqual(graphics.menu_wire(view), native.Frame().oem_menu(view).wire_bytes())

    def test_all_activity_frames_preserve_the_source_nibbles(self):
        atlas = native.get_oem_assets()
        for name, record in graphics._activity_catalog().items():
            self.assertEqual((record['width'], record['height']), (50, 50))
            self.assertEqual(len(record['frames']), {'progress': 9, 'wait': 4}.get(name, 1))
            for index, spec in enumerate(record['frames']):
                packed = zlib.decompress(base64.b64decode(spec['zlib_b64']))
                # Independent oracle blits each source nibble using the original column-major renderer.
                frame = native.Frame()
                frame.oem_text(19, 0, 'Back', atlas, right=193)
                for dy in range(-4, 5):
                    frame.pixel(8 + abs(dy), 5 + dy)
                for y in range(50):
                    for x in range(50):
                        value = packed[y * 25 + x // 2]
                        frame.pixel(203 + x, 13 + y, value & 15 if x % 2 else value >> 4)
                view = dict(labels=['Back'], selected=0, icon='activity:' + name)
                self.assertEqual(graphics.menu_wire(view, index=index), frame.wire_bytes())
        self.assertEqual(graphics.activity_index({'animated': True, 'icon': 'activity:heat_nozzle'}, 2.52), 5)
        self.assertEqual(graphics.activity_index({'animated': True, 'icon': 'activity:cooling'}, 2.52), 1)
        self.assertEqual(graphics.activity_index({'animated': True, 'icon': 'activity:settings'}, 2.52), 0)
        for name in ('material', 'maintenance', 'settings', 'information', 'filecheck'):
            p = graphics._unpack(graphics._activity_catalog()[name]['frames'][0], name)
            self.assertEqual(bytes(n for b in p for n in (b >> 4, b & 15)), atlas.icons[name])
        self.assertEqual(graphics.activity_index({}, 2.52), 0)

    def test_status_footer_preserves_top_rows_and_retains_long_messages(self):
        rows = bytes(range(256)) * 32
        def init(obj, cfg):
            obj.draw_text = lambda *args: None
        message = ['Ready']
        def render(obj, now, board):
            if message[0] is not None:
                obj.draw_text(3, 0, message[0], now)
            return rows
        with patch.object(native.KlipperStatus, '__init__', init), patch.object(native.KlipperStatus, 'render', render):
            status = graphics.KlipperStatus(None)
            wire = status.render(0., None)
            expected = native.Frame()
            expected.text(2, 52, 'Ready')
            self.assertEqual(wire, rows[:6144] + expected.wire_bytes()[6144:])
            self.assertIs(status.render(.2, None), wire)
            message[0] = '0123456789012345 one two three four five six seven eight nine'
            before = status.render(.4, None)
            message[0] += ' TEN'
            after = status.render(.6, None)
            self.assertNotEqual(before[56 * 128:], after[56 * 128:])
            self.assertEqual(after[:6144], rows[:6144])
            message[0] = None
            self.assertEqual(status.render(.8, None), rows)
        expected = native.Frame()
        expected.text(2, 52, '100° → ?')
        self.assertEqual(graphics.status_footer('100~degrees~ ~right_arrow~ ~custom~'), expected.wire_bytes()[6144:])

    def test_cursor_leaves_cached_icon_out_of_uart_windows(self):
        view = dict(labels=['Back', 'Load material', 'Remove material'], selected=1, icon='material')
        first = graphics.menu_wire(view)
        view['selected'] = 2
        with patch.object(graphics, '_icon_rows', side_effect=AssertionError('repacked an unchanged icon')):
            second = graphics.menu_wire(view)
        windows = transport.diff_regions(first, second)
        ops = transport.optimized_frame_operations(first, second, windows)
        panel = Panel(first)
        for kind, args, delay in ops:
            panel.apply((kind, 0, args))
        self.assertEqual(panel.ram, second)
        self.assertTrue(all(w[3] < 203 // 4 for w in windows))
        self.assertEqual(transport.diff_regions(second, second), ())
        self.assertEqual(transport.optimized_frame_operations(second, second, ()), ())

    def test_animation_updates_only_the_icon_region_in_panel_ram(self):
        view = dict(labels=['Back', 'Heat Nozzle', 'Previous', 'Next'], selected=3,
                    icon='project:heat_nozzle', animated=True)
        previous = graphics.menu_wire(view)
        panel = Panel(previous)
        for index in (1,2,3,0):
            wire = graphics.menu_wire(view, index=index)
            windows = transport.diff_regions(previous, wire)
            self.assertTrue(windows)
            for top, bottom, left, right in windows:
                self.assertTrue(13 <= top <= bottom <= 62)
                self.assertTrue(203 // 4 <= left <= right <= 252 // 4)
            for kind, args, _ in transport.optimized_frame_operations(previous, wire, windows):
                panel.apply((kind, 0, args))
            self.assertEqual(panel.ram, wire)
            previous = wire

    def test_physical_key_metrics_require_a_changed_frame_and_its_ack(self):
        printer, screen = oled()
        screen.oem_menu = menus.OEMMenu('en', operational=True)
        screen.oem_display_mode = 'menu'
        screen._render(10.)
        run_frame(screen.stream, 10.)
        screen._publish_input_receipt()
        printer.reactor.now = 12.010
        screen._buttons(12., 2, 1, 11.995, printer.board._oled_input_receipt)
        screen._render(printer.reactor.now)
        self.assertAlmostEqual(screen._input_to_render_ms[-1], 15)
        self.assertFalse(screen._input_to_ack_ms)
        run_frame(screen.stream, 12.020)
        screen._pump(12.10)
        self.assertEqual(len(screen._input_to_ack_ms), 1)
        self.assertGreater(screen._input_to_ack_ms[0], 15)
        screen._pump(12.12)
        self.assertEqual(len(screen._input_to_ack_ms), 1)
