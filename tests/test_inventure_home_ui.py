"""Home presentation must match M300 artwork and leave the owner unchanged."""
import base64
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ui = module('inventure_test_home_ui', ROOT/'restored/operator_home_r24/inventure_home_ui.py')


class HomePresentationTests(unittest.TestCase):
    def test_every_axis_and_animation_frame_matches_the_active_m300_renderer(self):
        source = ROOT/'tests/fixtures/m300_cartesian_graphics.py'
        reference = module('m300_reference_home_graphics', source)
        artwork = json.loads((ROOT/'restored/operator_home_r24/inventure_home_sprites.json').read_bytes())
        self.assertEqual(artwork['source_sha256'], sha256(source.read_bytes()).hexdigest())
        self.assertEqual(set(artwork['symbols']), {'home_'+axis for axis in ('x','y','z','xy','xyz')})
        for name, symbol in artwork['symbols'].items():
            self.assertEqual(symbol['frame_ms'], 500)
            self.assertEqual(len(symbol['frames']), 4)
            for index, frame in enumerate(symbol['frames']):
                expected = reference.CartesianGraphics().get(name, index*.5)
                raw = zlib.decompress(base64.b64decode(frame['zlib_b64'], validate=True))
                self.assertEqual((symbol['width'], symbol['height'], raw), expected)
                self.assertEqual(sha256(raw).hexdigest(), frame['sha256'])

    def fixture(self):
        calls = []
        native = types.SimpleNamespace(sequence_running=False)
        native._run_home_axes = lambda *args, **kw: calls.append(('run', args, kw)) or 'accepted-result'
        native._home_limit = lambda *args, **kw: calls.append(('limit', args, kw)) or 'limit-result'
        native._operator_clearance = lambda *args, **kw: calls.append(('clearance', args, kw)) or 'clearance-result'
        native._stage = lambda *args, **kw: calls.append(('stage', args, kw)) or 'stage-result'
        menu = types.SimpleNamespace(_wake=lambda: None, board=types.SimpleNamespace(load_test_context=None))
        observer = ui.HomePresentation(native, menu)
        return native, observer, calls

    def test_presentation_failure_calls_the_same_owner_once_with_unchanged_arguments(self):
        native, observer, calls = self.fixture()
        command = types.SimpleNamespace(get=lambda name, default=None: 'Z')
        with patch.object(observer, '_phase', side_effect=RuntimeError('display unavailable')), \
             patch.object(ui.logging, 'exception'):
            self.assertEqual(native._run_home_axes(command, operator=True), 'accepted-result')
            self.assertEqual(native._home_limit('Y', .5), 'limit-result')
            self.assertEqual(native._operator_clearance(10), 'clearance-result')
            self.assertEqual(native._stage('PC14 refinement', timeout=42), 'stage-result')
        self.assertEqual(calls, [('run', (command, True), {}), ('limit', ('Y', .5), {}),
                                 ('clearance', (10,), {}), ('stage', ('PC14 refinement',), {'timeout': 42})])
        self.assertFalse(observer.busy)

    def test_cached_interpreter_phase_never_queries_the_mcu(self):
        native, observer, _ = self.fixture()
        observer._begin('Z')
        observer.menu.board._control_query = lambda: self.fail('UI polled MCU control')
        observer.menu.board._fresh_inputs = lambda: self.fail('UI polled MCU inputs')
        observer.menu.board.load_test_context = {'factory': {'module_stack': ['select_tool', 'clean']}}
        view = observer.render()
        self.assertEqual(view['icon'], 'home:home_z')
        self.assertTrue(view['animated'])
        self.assertEqual(view['labels'], ['Home Z', 'Cleaning nozzle T0', 'Please wait'])

    def test_home_pixels_continue_during_hold_without_changing_its_query_schedule(self):
        class OriginalOLED:
            def _tick(self, now):
                if now >= self.next_query:
                    self.queries.append(now)
                    self.next_query = now + .5
                return now + .1
            def _active_work(self, now):
                return False
        class OLED(ui.HomeOLEDUI, OriginalOLED):
            pass
        oled = OLED()
        home = types.SimpleNamespace(busy=True)
        oled.board = types.SimpleNamespace(zortrax=types.SimpleNamespace(home_ui=home))
        oled.connected, oled.shutdown_latched, oled.thermal_test_hold, oled.fast = True, False, True, True
        oled.next_query = oled.render_at = 0.
        oled.queries, rendered, pumped = [], [], []
        oled._render = rendered.append
        oled._pump = pumped.append
        oled.stream = types.SimpleNamespace(enabled=True, active_work=False, next_wakeup=lambda now: now+.1)
        for now in (0., .1, .2, .3, .4, .5):
            oled._tick(now)
        self.assertEqual(oled.queries, [0., .5])
        self.assertEqual(rendered, [0., .5])
        self.assertEqual(len(pumped), 6)
        self.assertTrue(oled.thermal_test_hold)
        self.assertTrue(oled.stream.active_work)
        home.busy = False
        oled._tick(.6)
        self.assertEqual(rendered, [0., .5])
        self.assertEqual(len(pumped), 6)


if __name__ == '__main__':
    unittest.main()
