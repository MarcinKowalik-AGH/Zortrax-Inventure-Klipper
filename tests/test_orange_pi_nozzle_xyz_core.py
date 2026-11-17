"""Validate the pad geometry and guarded motion sequence without a board."""

import ast
import math
import unittest

from orange_pi.inventure_factory_core import FactoryError, XY_STEPS_PER_MM, parameters
from orange_pi.inventure_nozzle_xyz import (
    NozzleXYZProgram, PAD_EDGE_TAP_HEADROOM_STEPS, PAD_MAX_SWEEP_MM, PAD_MAX_SWEEP_SPEED,
    PAD_MAX_Z_SPEED, PAD_MIN_SWEEP_CLEARANCE_STEPS, pad_xyz_e_budget, pad_xyz_parameters,
)
from orange_pi.inventure_z_home import (
    NOZZLE_CONTACT, NOZZLE_Z_CLEARANCE_STEPS, Z_BOTTOM, Z_STEPS_PER_MM, z_home_parameters,
)


PARAMETERS = dict(T0_Z_MAX=139.35, T1_Z_MAX=139.15, T0_RETRACT_MM=25, T1_RETRACT_MM=100,
                  PAD_DIAMETER_MM=5.)
COLD_SMALL_PAD = dict(T0_Z_MAX=139.50, PAD_DIAMETER_MM=5.)


class SimulatedPad:
    def __init__(self, xyz):
        plans = [parameters(dict(TOOL='T%d' % tool, SPEED_PROFILE='OEM')) for tool in range(2)]
        z_settings = [z_home_parameters(dict(Z_APPROACH_MM=137, Z_PROBE_MM=9, Z_PROBE_X=x),
                                        plan, nozzle_test=True)
                      for x, plan in zip((82.8, 72.0), plans)]
        program = NozzleXYZProgram(plans[0], z_settings[0])
        self.program = (program.run_pad_center(xyz) if xyz['prep'] == 'COLD' else
                        program.run_nozzle_xyz(plans[1], z_settings[1], xyz))
        self.position = 50 * Z_STEPS_PER_MM
        self.xy = (0., 0.)
        self.tool = 0
        self.ceiling = None
        self.trace = []
        self.arms = []
        self.centers = ((82.92, 67.91), (72.04, 68.01), (82.935, 67.90))
        self.contact_radius_mm = 2.55
        self.contacts = [round(z * Z_STEPS_PER_MM) for z in (139.2, 139., 139.21)]
        self.scan = xyz['scan']
        self.phase = None
        self.cold = xyz['prep'] == 'COLD'
        self.sweep_without_opening = set()
        self.contact_plane = None
        self.swept = False
        self.sweep_transient = False
        self.unbounded_pad = False
        self.transient = False
        self.cold_settle_misses = 0
        self.cold_intermittent_contact = None

    def _inside(self):
        return bool(self.arms and (self.unbounded_pad
                    or (self.cold_intermittent_contact is not None and self.cold_intermittent_contact(self.xy))
                    or math.dist(self.xy, self.centers[len(self.arms) - 1]) <= self.contact_radius_mm))

    def _inputs(self):
        return dict(z_bottom=int(self.position == 0),
                    nozzle_contact=int(not self._inside() or self.position < self.contacts[len(self.arms) - 1]))

    def _motion(self, action):
        if not action['speed'] <= PAD_MAX_Z_SPEED:
            raise AssertionError('Pad Z exceeded MCU cap')
        expected = action['initial_raw']
        if any(self._inputs()[k] != v for k, v in expected.items()):
            raise AssertionError('Stale input assumption')
        steps, mask, value = (action[k] for k in ('steps', 'stop_mask', 'stop_value'))
        direction = 1 if steps > 0 else -1
        sent, triggered = abs(steps), 0
        target = self.position + steps
        if direction > 0 and self.ceiling is not None and target > self.ceiling:
            raise AssertionError('Tapping exceeded absolute qualified cap')
        contact = self.contacts[len(self.arms) - 1]
        if self.scan == 'SWEEP' and not self.cold and direction > 0 and target >= contact and not self._inside():
            if (not self.phase.endswith('_TOUCH') or self.contact_plane is None
                    or target > self.contact_plane + PAD_EDGE_TAP_HEADROOM_STEPS):
                raise AssertionError('Hot edge tap exceeded the verified pad plane')
        if (mask & NOZZLE_CONTACT and self._inside() and
                ((steps > 0 and not value & NOZZLE_CONTACT and self.position < contact <= target)
                 or (steps < 0 and value & NOZZLE_CONTACT and target < contact <= self.position))):
            sent, triggered = abs(self.position - (contact if steps > 0 else contact - 1)), NOZZLE_CONTACT
            if steps > 0:
                self.contact_plane = contact
        if mask & Z_BOTTOM and value & Z_BOTTOM and target <= 0:
            if self.position <= sent:
                sent, triggered = self.position, Z_BOTTOM
        self.position += direction * sent
        raw = self._inputs()
        if (self.transient and triggered == NOZZLE_CONTACT and steps > 0
                and len(self.arms) == 1):
            raw['nozzle_contact'] = 1
        return dict(sent_steps=sent, hit=triggered,
                    reason='stop_input' if triggered else 'completed', end_raw=raw)

    def _sweep(self, action):
        if (self._inputs()['nozzle_contact'] or self.position != self.contact_plane
                or action['e'] or action['speed'] > PAD_MAX_SWEEP_SPEED):
            raise AssertionError('Sweep must start at the first stable contact')
        start, target = (tuple(round(v * XY_STEPS_PER_MM) for v in point)
                         for point in (self.xy, (action['x'], action['y'])))
        axes = [axis for axis in (0, 1) if start[axis] != target[axis]]
        if len(axes) != 1:
            raise AssertionError('Sweep must move along one axis')
        axis = axes[0]
        step = 1 if target[axis] > start[axis] else -1
        if abs(target[axis] - start[axis]) > PAD_MAX_SWEEP_MM * XY_STEPS_PER_MM:
            raise AssertionError('Sweep exceeded the hard span')
        position = list(start)
        for value in range(start[axis]+step, target[axis]+step, step):
            position[axis] = value
            self.xy = tuple(v / XY_STEPS_PER_MM for v in position)
            if (self._inputs()['nozzle_contact'] and (axis, step) not in self.sweep_without_opening):
                self.swept = True
                return dict(ok=True, reason='stop_input', hit=NOZZLE_CONTACT, position=self.xy)
        return dict(ok=True, reason='completed', hit=0, position=self.xy)

    def run(self):
        reply = None
        while True:
            action = self.program.send(reply)
            self.trace.append((self.position, self.tool, action))
            kind = action['kind']
            reply = dict(ok=True, a=0, b=0, pulses=0, starts=0,
                         pulse_age_ms=0xffffffff, fault=0)
            if kind == 'done':
                return action
            if kind == 'home' and action['axis'] == 'Z':
                self.position = 0
            elif kind == 'xyz_switch_tool':
                self.tool = action['tool']
                self.ceiling = None
            elif kind == 'xyz_arm':
                if self.tool != action['tool']:
                    raise AssertionError('Wrong tool at arm')
                self.ceiling = action['ceiling_steps']
                self.contact_plane = None
                self.arms.append(self.ceiling)
            elif kind in ('z_inputs', 'z_verify_open', 'z_verify_closed'):
                reply = self._inputs()
                if kind == 'z_verify_closed' and self.cold_settle_misses:
                    reply['nozzle_contact'] = 1
                    self.cold_settle_misses -= 1
                if kind == 'z_verify_closed' and self.transient:
                    reply['nozzle_contact'] = 1
                if (kind == 'z_verify_closed' and self.cold_intermittent_contact is not None
                        and self.cold_intermittent_contact(self.xy)):
                    reply['nozzle_contact'] = 1
                if kind == 'z_verify_open' and self.swept:
                    self.swept = False
                    if self.sweep_transient:
                        reply['nozzle_contact'] = 0
            elif kind == 'move':
                if action.get('stage') == 'pad_sweep':
                    reply = self._sweep(action)
                    continue
                if action.get('stage') == 'pad_travel':
                    if self.ceiling - self.position < round(.5 * Z_STEPS_PER_MM) or not self._inputs()['nozzle_contact']:
                        raise AssertionError('Pad lateral move lacks clearance')
                    if self.scan == 'SWEEP' and self.contact_plane is not None:
                        if self.contact_plane - self.position < PAD_MIN_SWEEP_CLEARANCE_STEPS:
                            raise AssertionError('Return to center exceeds measured clearance plane')
                self.xy = (action['x'], action['y'])
            elif kind == 'z_contact_move':
                reply = self._motion(action)
            elif kind == 'phase':
                self.phase = action['name']


class NozzleXYZCoreTests(unittest.TestCase):
    def test_cold_center_backs_off_and_reuses_the_same_ceiling_after_contact_chatter(self):
        xyz = pad_xyz_parameters(COLD_SMALL_PAD, center_only=True)
        model = SimulatedPad(xyz)
        model.cold_settle_misses = 1
        result = model.run()
        self.assertEqual(result['terminal'], 'COLD_PAD_CENTER')
        self.assertEqual(model.arms, [math.floor(139.50 * Z_STEPS_PER_MM)])
        settles = [i for i, (_, _, a) in enumerate(model.trace) if a['kind'] == 'z_verify_closed']
        self.assertEqual(len(settles), 5)
        z_moves = [(pos, a['steps']) for pos, _, a in model.trace[settles[0]+1:settles[1]]
                   if a['kind'] == 'z_contact_move']
        self.assertLess(z_moves[0][1], 0)
        self.assertEqual(sum(z_moves[1]), model.arms[0])
        self.assertEqual(model.trace[settles[0]][0], model.trace[settles[1]][0])

    def test_cold_center_checks_missing_y_edge_with_fixed_height_taps(self):
        xyz = pad_xyz_parameters(COLD_SMALL_PAD, center_only=True)
        model = SimulatedPad(xyz)
        model.sweep_without_opening.add((1, -1))
        with self.assertLogs(level='INFO') as logs:
            result = model.run()
        self.assertTrue(any('Y- edge_mm=' in row and 'method=tap' in row for row in logs.output))
        brackets = [ast.literal_eval(row.split('confirmed edge bracket ', 1)[1].removesuffix(' mm'))
                    for row in logs.output if 'confirmed edge bracket ' in row]
        self.assertEqual(len(brackets), 2)
        for low, high in brackets:
            self.assertLessEqual(round((high - low) * XY_STEPS_PER_MM), 32)
        self.assertLess(math.dist(result['measurements']['T0']['center_xy_mm'], model.centers[0]), .02)
        trace = [a for _, _, a in model.trace]
        last_sweep = max(i for i, a in enumerate(trace) if a.get('stage') == 'pad_sweep')
        probes = [(pos, a['steps']) for pos, _, a in model.trace[last_sweep+1:]
                  if a['kind'] == 'z_contact_move' and a['steps'] > 0]
        self.assertGreater(len(probes), 2)
        self.assertEqual(len({pos + steps for pos, steps in probes}), 1)
        self.assertLessEqual(sum(probes[0]), model.arms[0])

    def test_cold_center_leaves_clearance_when_pc14_stays_closed_at_scan_limit(self):
        xyz = pad_xyz_parameters({'T0_Z_MAX': 139.50}, center_only=True)
        model = SimulatedPad(xyz)
        model.unbounded_pad = True
        with self.assertRaisesRegex(FactoryError, 'PC14 nadal zwarty na końcu zakresu'):
            model.run()
        self.assertEqual(model._inputs()['nozzle_contact'], 1)
        self.assertLessEqual(model.position, model.contacts[0] - PAD_MIN_SWEEP_CLEARANCE_STEPS)

    def test_cold_center_can_verify_a_genuine_edge_near_the_start(self):
        xyz = pad_xyz_parameters(dict(COLD_SMALL_PAD, SCAN_RADIUS_MM=5.5), center_only=True)
        model = SimulatedPad(xyz)
        model.centers = ((80.26, 67.91),)
        result = model.run()['measurements']['T0']
        self.assertEqual(result['scan_method'], 'SWEEP+TAP')
        self.assertLess(math.dist(result['center_xy_mm'], model.centers[0]), .02)

    def test_cold_center_confirms_open_next_to_an_unstable_scan_limit(self):
        xyz = pad_xyz_parameters(COLD_SMALL_PAD, center_only=True)
        self.assertEqual(xyz['edge_tolerance_mm'], .05)
        model = SimulatedPad(xyz)
        model.sweep_without_opening.add((0, -1))
        model.cold_intermittent_contact = lambda xy: math.isclose(xy[0], 77.8)
        result = model.run()['measurements']['T0']
        self.assertEqual(result['scan_method'], 'SWEEP+TAP')
        self.assertLess(abs(result['center_xy_mm'][0] - model.centers[0][0]), .05)
        self.assertTrue(any(a.get('stage') == 'pad_travel' and math.isclose(a['x'], 77.9)
                            for _, _, a in model.trace))

    def test_cold_center_rejects_an_unresolved_wide_edge(self):
        model = SimulatedPad(pad_xyz_parameters(COLD_SMALL_PAD, center_only=True))
        model.sweep_without_opening.add((0, -1))
        model.cold_intermittent_contact = lambda xy: 80. <= xy[0] <= 81.
        with self.assertRaisesRegex(FactoryError, 'Przedział krawędzi PC14') as exc:
            model.run()
        low, high = exc.exception.details['edge_interval_mm']
        self.assertGreater(high - low, .05)
        self.assertEqual(model._inputs()['nozzle_contact'], 1)
        self.assertFalse(any(a['kind'] == 'done' for _, _, a in model.trace))

    def test_edge_continues_after_uncertain_taps_outside_the_current_bracket(self):
        kwargs = dict(TOOL='T0', SPEED_PROFILE='OEM')
        plan = parameters(kwargs)
        settings = z_home_parameters({}, plan, nozzle_test=True)
        program = NozzleXYZProgram(plan, settings)

        def steps(mm):
            return round(mm * XY_STEPS_PER_MM)

        def edge_tap(point, safe_z, ceiling, tap_speed):
            state = yield point[1]
            return state, None

        program._edge_tap = edge_tap
        # Replay the intermittent Y+ positions from the warm T0 run of R11.
        uncertain_mm = (71.95, 71.325, 71.0125, 72.2625, 72.2234375, 72.3015625)
        uncertain = tuple(map(steps, uncertain_mm))
        expected_edge = steps(72.26)
        search = program._edge((steps(81.6796875), steps(70.7)), 1, steps(73.2),
                               138 * Z_STEPS_PER_MM, round(139.44 * Z_STEPS_PER_MM),
                               32, .25, confirm=True)
        responses = {True: [], False: [], None: []}
        answer = None
        with self.assertLogs(level='INFO') as logs:
            for _ in range(32):
                try:
                    pos = search.send(answer)
                except StopIteration as completed:
                    edge = completed.value
                    break
                answer = None if pos in uncertain else pos <= expected_edge
                responses[answer].append(pos)
            else:
                self.fail('Edge search did not finish')

        self.assertEqual(responses[None], list(uncertain))
        low = max(responses[True])
        high = min(responses[False])
        self.assertLessEqual(high - low, 32)
        self.assertEqual(edge, (low + high) / 2.)
        self.assertTrue(any('confirmed edge bracket' in row for row in logs.output))

    def test_cold_center_requires_measured_contact_inside_the_chord(self):
        xyz = pad_xyz_parameters({'T0_Z_MAX': 139.50}, center_only=True)
        model = SimulatedPad(xyz)
        model.centers = ((95., 68.),)
        with self.assertRaisesRegex(FactoryError, 'PC14 nie potwierdził zwarcia w próbach osi X'):
            model.run()
        self.assertEqual(model._inputs()['nozzle_contact'], 1)
        self.assertFalse(any(a.get('stage') == 'pad_sweep' for _, _, a in model.trace))

    def test_cold_center_accepts_the_measured_contact_width_of_the_stock_pad(self):
        xyz = pad_xyz_parameters({'T0_Z_MAX': 139.50}, center_only=True)
        self.assertEqual(xyz['pad_diameter_mm'], 7.)
        self.assertEqual(xyz['scan_radius_mm'], 5.)
        model = SimulatedPad(xyz)
        model.contact_radius_mm = 7.54 / 2.
        model.sweep_without_opening.add((0, -1))
        result = model.run()['measurements']['T0']
        self.assertEqual(result['scan_method'], 'SWEEP+TAP')
        for measured_width in result['electrical_diameter_mm']:
            self.assertAlmostEqual(measured_width, 7.54, delta=.1)
        self.assertLess(math.dist(result['center_xy_mm'], model.centers[0]), .05)
        self.assertEqual(model.ceiling, math.floor(139.50 * Z_STEPS_PER_MM))

    def test_cold_center_still_rejects_an_oversize_contact_outline(self):
        model = SimulatedPad(pad_xyz_parameters({'T0_Z_MAX': 139.50}, center_only=True))
        model.contact_radius_mm = 4.5
        with self.assertRaisesRegex(FactoryError, 'Szerokość styku X .* poza zakresem .*8.5 mm'):
            model.run()
        self.assertEqual(model._inputs()['nozzle_contact'], 1)

    def test_full_preparation_and_reseat_on_single_home(self):
        xyz = pad_xyz_parameters(PARAMETERS)
        model = SimulatedPad(xyz)
        result = model.run()
        self.assertEqual([a['axis'] for _, _, a in model.trace if a['kind'] == 'home'], ['Y', 'X', 'Z'])
        self.assertEqual([a['tool'] for _, _, a in model.trace if a['kind'] == 'xyz_switch_tool'],
                         [1, 0, 1, 0])
        self.assertEqual(model.arms, [math.floor(n * Z_STEPS_PER_MM) for n in (139.35, 139.15, 139.35)])
        retracts = [(tool, a['e']) for _, tool, a in model.trace if a.get('e')]
        self.assertEqual(retracts, [(0, -25.), (1, -100.)])
        self.assertEqual(pad_xyz_e_budget(xyz), 120000)
        phases = [a['name'] for _, _, a in model.trace if a['kind'] == 'phase']
        self.assertEqual([p for p in phases if '_WIPE_' in p], [
            'XYZ_T0_WIPE_HOT', 'XYZ_T1_WIPE_HOT', 'XYZ_T0_WIPE_160', 'XYZ_T1_WIPE_160',
        ])
        bin_x = (82.8, 72.0)
        for index, (_, tool, action) in enumerate(model.trace):
            clean_done = action == dict(kind='module', name='clean', state='done')
            waiting = action['kind'] in ('xyz_heat_start', 'xyz_heat_wait',
                                         'xyz_cool_start', 'xyz_cool_wait')
            if waiting:
                moves = [a for _, _, a in model.trace[:index] if a['kind'] == 'move']
                self.assertEqual((moves[-1]['x'], moves[-1]['y']), (bin_x[tool], 152.5))
            if clean_done:
                brush = next(a for _, _, a in reversed(model.trace[:index]) if a['kind'] == 'move')
                following = [a for _, _, a in model.trace[index+1:]
                             if a['kind'] != 'module']
                exit_move, park = following[:2]
                self.assertEqual([a['kind'] for a in (exit_move, park)], ['move', 'move'])
                self.assertEqual(brush['y'], 154.5)
                self.assertEqual((exit_move['x'], exit_move['y']), (brush['x'], 152.5))
                self.assertEqual((park['x'], park['y']), (bin_x[tool], 152.5))
        self.assertEqual(list(result['measurements']), ['T0', 'T1', 'T0_RETURN'])
        for (key, measured), true_center in zip(result['measurements'].items(), model.centers):
            self.assertEqual(measured['scan_method'], 'SWEEP')
            self.assertLess(math.dist(measured['center_xy_mm'], true_center), .02, key)
            self.assertLess(max(abs(d - 5.1) for d in measured['electrical_diameter_mm']), .04)
        candidate = result['candidate']
        self.assertAlmostEqual(candidate['t1_offset_mm']['X'], 10.8875, delta=.02)
        self.assertAlmostEqual(candidate['t1_carriage_shift_mm']['Y'], .105, delta=.02)
        self.assertAlmostEqual(candidate['t1_lower_by_mm'], .205, delta=.003)
        self.assertAlmostEqual(candidate['t0_reseat_mm']['Z'], .01, delta=.003)
        self.assertEqual(result['measurement_temperature_c'], 160.)
        for index, (pos, _, action) in enumerate(model.trace):
            if action['kind'] == 'xyz_switch_tool' and pos:
                self.assertEqual(pos, NOZZLE_Z_CLEARANCE_STEPS)
                prior_move = next(a for _, _, a in reversed(model.trace[:index])
                                  if a['kind'] in ('move', 'z_contact_move'))
                self.assertEqual(prior_move['steps'], -NOZZLE_Z_CLEARANCE_STEPS)

    def test_missing_caps_and_verify_mode(self):
        with self.assertRaisesRegex(FactoryError, 'requires T1_Z_MAX'):
            pad_xyz_parameters(dict(PARAMETERS, T1_Z_MAX=None))
        xyz = pad_xyz_parameters(dict(PARAMETERS, T1_Z_MAX=None, MODE='VERIFY_T0'))
        result = SimulatedPad(xyz).run()
        self.assertEqual(list(result['measurements']), ['T0'])
        self.assertIsNone(result['candidate'])

    def test_empty_nozzles_use_zero_extrusion_and_only_measurement_wipes(self):
        for mode, scans, wipes in (
            ('VERIFY_T0', ['T0'], ['XYZ_T0_WIPE_160']),
            ('FULL', ['T0', 'T1', 'T0_RETURN'], ['XYZ_T0_WIPE_160', 'XYZ_T1_WIPE_160']),
        ):
            with self.subTest(mode=mode):
                settings = dict(PREP='EMPTY', MODE=mode, T0_Z_MAX=139.35)
                if mode == 'FULL':
                    settings['T1_Z_MAX'] = 139.15
                xyz = pad_xyz_parameters(settings)
                self.assertEqual(xyz['prep'], 'EMPTY')
                self.assertEqual(xyz['retract_mm'], (0., 0.))
                self.assertEqual(pad_xyz_e_budget(xyz), 0)
                model = SimulatedPad(xyz)
                model.contact_radius_mm = 7.54 / 2.
                result = model.run()
                self.assertEqual(list(result['measurements']), scans)
                self.assertEqual(result['retract_e_steps'], {'T0': 0, 'T1': 0})
                actions = [action for _, _, action in model.trace]
                self.assertFalse(any(action.get('e') for action in actions))
                phases = [action['name'] for action in actions if action['kind'] == 'phase']
                self.assertFalse(any('_RETRACT' in phase for phase in phases))
                self.assertEqual([phase for phase in phases if '_WIPE_' in phase], wipes)

    def test_unverified_contact_stops_before_the_next_tool(self):
        for field, expected in [('unbounded_pad', 'PC14 nadal zwarty na końcu zakresu'),
                                ('transient', 'PC14 nie potwierdził zwarcia')]:
            with self.subTest(field=field):
                model = SimulatedPad(pad_xyz_parameters(PARAMETERS))
                setattr(model, field, True)
                with self.assertRaisesRegex(FactoryError, expected):
                    model.run()
                self.assertEqual(len(model.arms), 1)
                _, _, last = model.trace[-1]
                self.assertEqual(last['kind'], 'z_contact_move')
                self.assertLess(last['steps'], 0)

    def test_sweep_counts_and_retract_uses_measured_plane(self):
        xyz = pad_xyz_parameters(dict(PARAMETERS, MODE='VERIFY_T0', Z_CLEARANCE_MM=.5))
        model = SimulatedPad(xyz)
        result = model.run()
        sweeps = [pos for pos, _, action in model.trace if action.get('stage') == 'pad_sweep']
        self.assertEqual(sweeps, [model.contacts[0]] * 8)
        self.assertEqual(result['measurements']['T0']['scan_method'], 'SWEEP')

    def test_explicit_legacy_tap_keeps_point_to_point_probe(self):
        xyz = pad_xyz_parameters(dict(PARAMETERS, MODE='VERIFY_T0', SCAN='TAP'))
        self.assertEqual(xyz['z_tap_speed'], .8)
        self.assertEqual(xyz['edge_tolerance_mm'], .02)
        model = SimulatedPad(xyz)
        result = model.run()
        self.assertEqual(result['measurements']['T0']['scan_method'], 'TAP')
        self.assertFalse(any(action.get('stage') == 'pad_sweep' for _, _, action in model.trace))

    def test_reseat_drift_rejects_candidate(self):
        model = SimulatedPad(pad_xyz_parameters(PARAMETERS))
        model.centers = (*model.centers[:2], (83.2, 67.9))
        with self.assertRaisesRegex(FactoryError, 'did not repeat after reselect'):
            model.run()
        self.assertEqual(len(model.arms), 3)

    def test_scan_limits_and_quantisation_are_validated_before_motion(self):
        with self.assertRaisesRegex(FactoryError, 'must allow the 20 mm selector stage'):
            pad_xyz_parameters(dict(PARAMETERS, T0_Z_MAX=20.0005))
        invalid = pad_xyz_parameters(PARAMETERS)
        invalid['z_max_mm'] = (151., 139.15)
        model = SimulatedPad(invalid)
        with self.assertRaisesRegex(FactoryError, 'ceiling is outside'):
            model.run()
        self.assertEqual(model.trace, [])


if __name__ == '__main__':
    unittest.main()
