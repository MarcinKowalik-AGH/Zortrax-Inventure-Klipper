import base64
import importlib
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
            self.assertEqual((record['width'], record['height'], record['frame_ms']), (50, 50, 500))
            self.assertEqual(len(record['frames']), 4)
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
        self.assertEqual(graphics.activity_index({'animated': True}, 2.52), 1)
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
