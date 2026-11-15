"""Validate the pad geometry and guarded motion sequence without a board."""

import math
import unittest

from orange_pi.inventure_factory_core import FactoryError, parameters
from orange_pi.inventure_nozzle_xyz import (
    NozzleXYZProgram, PAD_MAX_Z_SPEED, pad_xyz_e_budget, pad_xyz_parameters,
)
from orange_pi.inventure_z_home import (
    NOZZLE_CONTACT, NOZZLE_Z_CLEARANCE_STEPS, Z_BOTTOM, Z_STEPS_PER_MM, z_home_parameters,
)


PARAMETERS = dict(T0_Z_MAX=139.35, T1_Z_MAX=139.15, T0_RETRACT_MM=25, T1_RETRACT_MM=100)


class SimulatedPad:
    def __init__(self, xyz):
        plans = [parameters(dict(TOOL='T%d' % tool, SPEED_PROFILE='OEM')) for tool in range(2)]
        z_settings = [z_home_parameters(dict(Z_APPROACH_MM=137, Z_PROBE_MM=9, Z_PROBE_X=x),
                                        plan, nozzle_test=True)
                      for x, plan in zip((82.8, 72.0), plans)]
        self.program = NozzleXYZProgram(plans[0], z_settings[0]).run_nozzle_xyz(
            plans[1], z_settings[1], xyz)
        self.position = 50 * Z_STEPS_PER_MM
        self.xy = (0., 0.)
        self.tool = 0
        self.ceiling = None
        self.trace = []
        self.arms = []
        self.centers = ((82.92, 67.91), (72.04, 68.01), (82.935, 67.90))
        self.contacts = [round(z * Z_STEPS_PER_MM) for z in (139.2, 139., 139.21)]
        self.unbounded_pad = False
        self.transient = False

    def _inside(self):
        return bool(self.arms and (self.unbounded_pad
                    or math.dist(self.xy, self.centers[len(self.arms) - 1]) <= 2.55))

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
        if (mask & NOZZLE_CONTACT and self._inside() and
                ((steps > 0 and not value & NOZZLE_CONTACT and self.position < contact <= target)
                 or (steps < 0 and value & NOZZLE_CONTACT and target < contact <= self.position))):
            sent, triggered = abs(self.position - (contact if steps > 0 else contact - 1)), NOZZLE_CONTACT
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
                self.arms.append(self.ceiling)
            elif kind in ('z_inputs', 'z_verify_open'):
                reply = self._inputs()
            elif kind == 'move':
                if action.get('stage') == 'pad_travel':
                    if self.ceiling - self.position < round(.5 * Z_STEPS_PER_MM) or not self._inputs()['nozzle_contact']:
                        raise AssertionError('Pad lateral move lacks clearance')
                self.xy = (action['x'], action['y'])
            elif kind == 'z_contact_move':
                reply = self._motion(action)


class NozzleXYZCoreTests(unittest.TestCase):
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
        self.assertEqual(list(result['measurements']), ['T0', 'T1', 'T0_RETURN'])
        for (key, measured), true_center in zip(result['measurements'].items(), model.centers):
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

    def test_blocked_edge_and_transient_stop_without_extra_lateral_move(self):
        for field, expected in [('unbounded_pad', 'outside the qualified scan box'),
                                ('transient', 'Unstable PC14 response')]:
            with self.subTest(field=field):
                model = SimulatedPad(pad_xyz_parameters(PARAMETERS))
                setattr(model, field, True)
                with self.assertRaisesRegex(FactoryError, expected):
                    model.run()
                self.assertEqual(len(model.arms), 1)
                _, _, last = model.trace[-1]
                self.assertEqual(last['kind'], 'z_contact_move')
                self.assertLess(last['steps'], 0)

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
