"""r0323 OEM point order, corrections, signed arithmetic and host persistence."""
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest

from orange_pi import inventure_platform_level as level
from test_orange_pi_door_policy import Command, adapter


class Transport:
    def __init__(self, run, heights):
        self.run = run
        self.heights = {i: deque(seq) for i, seq in heights.items()}
        self.events = []

    def event(self, name, data=None):
        self.events.append((name, data))
        yield name

    def prepare(self):
        return self.event('prepare')

    def await_operator(self, phase, instructions):
        self.run['_prompt_ready'] = True
        return self.event(phase, instructions)

    def heat_and_select_t0(self):
        return self.event('heat_and_select_t0')

    def center_reference(self):
        yield from self.event('reference')
        return 136 * 802 + 401, 802

    def prepare_sampling(self, steps):
        return self.event('sample_z3', steps)

    def probe(self, point, xy, depth):
        yield from self.event('probe', (point, xy))
        return self.heights[point].popleft()

    def park_for_operator(self):
        return self.event('park')

    def finish(self):
        return self.event('finish')


class EngineTests(unittest.TestCase):
    def simulate(self, heights):
        run = level._new_run()
        board = SimpleNamespace(platform_level_run=run)
        device = Transport(run, heights)
        for step in level.run_leveling(device, run):
            if step in level.PROMPTS:
                level.command(board, Command(ACTION='NEXT', RUN_ID=run['run_id'],
                                             PROMPT_ID=run['prompt_id']))
        self.assertEqual(run['phase'], 'finishing')
        self.assertIsNone(level.status(board)['candidate_depth_um'])
        self.assertNotIn('_pending_result', level.status(board))
        with self.assertRaisesRegex(RuntimeError, 'candidate'):
            level.command(board, Command(ACTION='SAVE', RUN_ID=run['run_id']))
        level.publish_candidate(run)
        return run, device.events

    def test_flat_tray_full_schedule_and_height(self):
        run, events = self.simulate({i: [0] for i in range(5)})
        self.assertEqual([e for e, value in events],
                         ['prepare', 'await_knobs', 'heat_and_select_t0', 'reference',
                          'sample_z3', 'probe', 'probe', 'probe', 'probe', 'probe', 'finish'])
        self.assertEqual([data for e, data in events if e == 'probe'],
                         [(0, (127.5, 13.)), (1, (7.5, 13.)), (4, (7.5, 118.)),
                          (3, (127.5, 118.)), (2, (67.5, 65.5))])
        self.assertEqual(run['candidate_depth_um'], 135500)
        self.assertIn(('sample_z3', 136 * 802 + 401), events)
        self.assertEqual(run['reference_datum_steps'], 802)
        self.assertEqual(run['phase'], 'candidate')
        self.assertFalse(run['deformed'])

    def test_two_rear_knobs_then_front_keep_initial_reference_and_reverse_pairs(self):
        heights = {0: [-300, 100], 1: [100, 100], 2: [100],
                   3: [-100, 250, 100], 4: [-100, 0, 100]}
        run, events = self.simulate(heights)
        probes = [data[0] for action, data in events if action == 'probe']
        self.assertEqual(probes, [0, 1, 4, 3, 3, 4, 4, 3, 0, 1, 2])
        instructions = [data[0] for state, data in events if state == 'adjust']
        self.assertEqual([(i['knob'], i['delta_steps'], i['turns'], i['direction'])
                          for i in instructions],
                         [('rear_left', -200, .5, 'clockwise'),
                          ('rear_right', -200, .5, 'clockwise'),
                          ('rear_right', 150, .5, 'counterclockwise'),
                          ('front', -200, .5, 'clockwise')])
        self.assertEqual(run['reference_steps'], 100)
        self.assertEqual(run['measurement_count'], 11)
        self.assertEqual(run['candidate_depth_um'], 135375)

    def test_asymmetric_inclusive_knob_bounds_and_signed_averaging(self):
        for low in (-160, 80):
            self.assertEqual(level.corrections({0: 0, 1: 0, 4: low, 3: low}, 0),
                             (None, []))
        for outside in (-161, 81):
            self.assertEqual(level.corrections({0: 0, 1: 0, 4: 0, 3: outside}, 0),
                             ('rear', [('rear_right', outside)]))
        for front, accepted in [((-160, -159), True), ((79, 80), True),
                                ((79, 81), False), ((-161, -159), False)]:
            state, _ = level.corrections({0: front[0], 1: front[1], 3: 0, 4: 0}, 0)
            self.assertEqual(state is None, accepted)
        self.assertEqual([level.turns(v) for v in [1, 126, 802]], [.25, .5, 1.75])

    def test_center_deformation_uses_delta_frame_and_signed_truncation(self):
        # The average raw corner height truncates to 9; the mean of D truncates
        # to 0. A raw-height implementation would misclassify this boundary.
        heights = {0: 10, 1: 10, 3: 10, 4: 9, 2: 411}
        check = level.final_result(heights, 10, 135500)
        self.assertEqual(check['center_error_steps'], 401)
        self.assertFalse(check['deformed'])
        for center in (412, -392):
            heights[2] = center
            self.assertTrue(level.final_result(heights, 10, 135500)['deformed'])

    def test_operator_notice_identifies_each_knob_and_direction(self):
        run, events = self.simulate({0: [0], 1: [0], 2: [0],
                                     3: [-300, 0], 4: [-300, 0]})
        reply = Command()
        board = SimpleNamespace(gcode=reply)
        for phase, data in events:
            if phase == 'adjust':
                level.notice(board, dict(phase=phase, instructions=data))
        self.assertEqual(len(reply.lines), 2)
        self.assertIn('Loosen the rear-left knob', reply.lines[0])
        self.assertIn('clockwise by 0.75 turns', reply.lines[0])
        self.assertIn('rear-right knob', reply.lines[1])
        level.notice(board, run)
        self.assertIn('RUN_ID=' + run['run_id'], reply.lines[-1])

    def test_instruction_budget_counts_individual_instructions(self):
        run = level._new_run()
        run['instruction_count'] = 39
        device = Transport(run, {0: [0], 1: [0], 3: [-500], 4: [-500]})
        with self.assertRaisesRegex(ValueError, 'instruction limit'):
            for step in level.run_leveling(device, run):
                if step in level.PROMPTS:
                    run['operator_confirmed'] = True
        self.assertEqual(run['instruction_count'], 41)
        self.assertIsNone(run['candidate_depth_um'])

    def test_invalid_contacts_never_create_candidate(self):
        for invalid in (True, None, 0xDEAD, 2**31 - 1, -(2**31)):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'PC14'):
                self.simulate({0: [0], 1: [invalid]})


class CommandTests(unittest.TestCase):
    def host(self, path):
        return SimpleNamespace(
            printer=SimpleNamespace(get_start_args=lambda: dict(config_file=str(path / 'printer.cfg'))),
            _write_calibration_file=adapter.InventureMCU._write_calibration_file,
            _start_platform_level=lambda gcmd, run: None,
            _stop_platform_level=lambda gcmd, run: None)

    def test_stale_or_double_next_and_cancel(self):
        board = self.host(Path('/'))
        level.command(board, Command(ACTION='START'))
        run = board.platform_level_run
        run.update(phase='await_knobs', prompt_id=3)
        with self.assertRaisesRegex(RuntimeError, 'pending'):
            level.command(board, Command(ACTION='NEXT'))
        run['_prompt_ready'] = True
        for args in (dict(RUN_ID='old'), dict(PROMPT_ID=2)):
            with self.assertRaises(RuntimeError):
                level.command(board, Command(ACTION='NEXT', **args))
        level.command(board, Command(ACTION='NEXT', PROMPT_ID=3))
        with self.assertRaisesRegex(RuntimeError, 'pending'):
            level.command(board, Command(ACTION='NEXT'))
        with self.assertRaisesRegex(RuntimeError, 'active'):
            level.command(board, Command(ACTION='START'))
        with self.assertRaisesRegex(RuntimeError, 'RUN_ID'):
            level.command(board, Command(ACTION='CANCEL', RUN_ID='old'))
        self.assertFalse(level.status(board)['awaiting_operator'])
        level.command(board, Command(ACTION='CANCEL'))
        self.assertEqual(level.status(board)['phase'], 'cancelled')
        self.assertNotIn('operator_confirmed', level.status(board))

    def test_atomic_host_save_matches_run_and_does_not_change_nozzle_offsets(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            board = self.host(path)
            pad = path / 'inventure_nozzle_pad.json'
            pad.write_text('previous head offsets\n')
            run, _ = EngineTests().simulate({i: [502 if i == 2 else 0] for i in range(5)})
            board.platform_level_run = run
            for args in (dict(RUN_ID='old'), dict(RUN_ID=run['run_id'])):
                with self.assertRaises(RuntimeError):
                    level.command(board, Command(ACTION='SAVE', **args))
            self.assertFalse((path / 'inventure_platform_level.json').exists())
            level.command(board, Command(ACTION='SAVE', RUN_ID=run['run_id'],
                                         DEFORMATION_ACK='ACCEPT_DEFORMED_TRAY'))
            saved = json.loads((path / 'inventure_platform_level.json').read_text())
            self.assertEqual(saved['depth_um'], run['candidate_depth_um'])
            self.assertTrue(saved['deformed'])
            self.assertEqual(saved['measurement_count'], 5)
            self.assertEqual(pad.read_text(), 'previous head offsets\n')
            self.assertEqual(run['phase'], 'saved')

    def test_refused_start_preserves_previous_report(self):
        board = self.host(Path('/'))
        previous = board.platform_level_run = dict(phase='saved', run_id='old')
        def reject(gcmd, run):
            raise gcmd.error('Tray missing')
        board._start_platform_level = reject
        with self.assertRaisesRegex(RuntimeError, 'Tray missing'):
            level.command(board, Command(ACTION='START'))
        self.assertIs(board.platform_level_run, previous)
