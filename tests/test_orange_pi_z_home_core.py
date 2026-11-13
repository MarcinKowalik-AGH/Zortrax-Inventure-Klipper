"""Test the bounded planner independently of transport and MCU timings."""
import unittest

from orange_pi.inventure_factory_core import FactoryError, defaults
from orange_pi.inventure_z_home import (
    NOZZLE_CONTACT, Z_BOTTOM, Z_HOME_ACK, Z_STEPS_PER_MM,
    ZHomeProgram, z_home_parameters,
)


class PlanModel:
    def __init__(self, **options):
        self.p = defaults(0)
        self.settings = z_home_parameters(dict(Z_APPROACH_MM=3, ACK=Z_HOME_ACK,
                                               **options), self.p, required=True)
        self.program = ZHomeProgram(self.p, self.settings)
        self.position = 0
        self.actions = []
        self.contact_at = 2486
        self.release_at = None
        self.closed = False
        self.releases = 0
        self.change_after_release = {}
        self.bounce_after_release = False

    def _motion(self, action):
        steps = action['steps']
        direction = 1 if steps > 0 else -1
        self.actions.append((self.program.history[-1], action))
        for sent in range(abs(steps) + 1):
            bottom = int(self.position == 0)
            threshold = self.release_at if self.closed and self.release_at is not None else self.contact_at
            self.closed = threshold is not None and self.position >= threshold
            raw = bottom * Z_BOTTOM + int(not self.closed) * NOZZLE_CONTACT
            hit = action['stop_mask'] & ~(raw ^ action['stop_value'])
            if hit or sent == abs(steps):
                break
            self.position += direction
        opened = steps < 0 and hit == NOZZLE_CONTACT and not self.closed
        end_raw = dict(z_bottom=bottom, nozzle_contact=int(not self.closed))
        if opened:
            self.releases += 1
            if self.bounce_after_release:
                end_raw['nozzle_contact'] = 0
            self.release_at = self.change_after_release.get(self.releases, self.release_at)
        return dict(sent_steps=sent, hit=hit, reason='stop_input' if hit else 'completed',
                    end_raw=end_raw)

    def run(self, operation='HOME_Z'):
        generator = self.program.run(operation)
        response = None
        while True:
            action = generator.send(response)
            kind = action['kind']
            if kind == 'done':
                return action
            if kind == 'z_contact_move':
                response = self._motion(action)
            else:
                self.actions.append((self.program.history[-1] if self.program.history else '', action))
                response = dict(ok=True, a=0, b=0, starts=0, pulses=0,
                                pulse_age_ms=4294967295, fault=0)


class ZHomeCoreTests(unittest.TestCase):
    def test_parameters_require_measured_approach_and_pad_ack(self):
        p = defaults(0)
        preview = z_home_parameters({}, p)
        self.assertIsNone(preview['z_approach_mm'])
        self.assertEqual((preview['z_probe_x'], preview['z_probe_y']), (82.8, 68.))
        for params in ({'ACK': Z_HOME_ACK}, {'Z_APPROACH_MM': 3},
                       {'Z_APPROACH_MM': 249.5, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 80, 'Z_APPROACH_SPEED': .1, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 3, 'Z_PROBE_X': 156, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 3, 'Z_MAX_CYCLES': 101, 'ACK': Z_HOME_ACK}):
            with self.subTest(params=params), self.assertRaises(FactoryError):
                z_home_parameters(params, p, required=True)
        with self.assertRaisesRegex(FactoryError, 'T0'):
            z_home_parameters({}, defaults(1))

    def test_prepare_stops_at_pad_without_probe_and_clears_all_modules(self):
        model = PlanModel()
        done = model.run('Z_PREPARE')
        self.assertIsNone(done['z_home_result'])
        homes = [a['axis'] for _, a in model.actions if a['kind'] == 'home']
        self.assertEqual(homes, ['Z', 'Y', 'X'])
        self.assertEqual(model.program.xy, (82.8, 68.))
        self.assertEqual(model.position, 0)
        self.assertNotIn('z_contact_move', [a['kind'] for _, a in model.actions])
        stack = []
        for _, action in model.actions:
            if action.get('state') == 'start':
                stack.append(action['name'])
            elif action.get('state') == 'done':
                self.assertEqual(stack.pop(), action['name'])
        self.assertEqual(stack, [])

    def test_new_minimum_resets_counter_and_returns_to_best_release(self):
        model = PlanModel()
        model.change_after_release = {2: 2484, 3: 2486}
        result = model.run()['z_home_result']
        self.assertEqual((result['cycles'], result['non_improvements']), (104, 101))
        self.assertEqual(result['release_steps_from_bottom'], 2483)
        self.assertEqual(result['release_mm_from_bottom'], round(2483 / Z_STEPS_PER_MM, 6))
        self.assertEqual(result['probe_upper_steps'], 4 * Z_STEPS_PER_MM)
        self.assertFalse(result['coordinate_saved'])
        self.assertEqual(model.position, 2483)
        self.assertEqual(model.releases, 104)
        moves = [a for _, a in model.actions if a['kind'] == 'z_contact_move']
        self.assertEqual(moves[-1]['steps'], -2)
        self.assertEqual((moves[-1]['stop_mask'], moves[-1]['stop_value']),
                         (NOZZLE_CONTACT | Z_BOTTOM, Z_BOTTOM))

    def test_early_contact_opens_within_retraction_bound_then_fails(self):
        model = PlanModel()
        model.contact_at = 1200
        with self.assertRaisesRegex(FactoryError, 'z_contact_during_coarse_approach'):
            model.run()
        self.assertEqual((model.position, model.releases), (1199, 1))
        phase, action = model.actions[-1]
        self.assertEqual(phase, 'Z_CONTACT_RELEASE')
        self.assertEqual(action['steps'], -(1200 - (Z_STEPS_PER_MM + 1)))
        self.assertEqual(action['initial_raw'], dict(z_bottom=0, nozzle_contact=0))

    def test_stale_opening_and_missing_contact_cannot_establish_reference(self):
        model = PlanModel()
        model.bounce_after_release = True
        with self.assertRaisesRegex(FactoryError, 'input changed after latched stop'):
            model.run()
        self.assertEqual(model.releases, 1)
        model = PlanModel()
        model.contact_at = None
        with self.assertRaisesRegex(FactoryError, 'z_contact_not_found'):
            model.run()
        self.assertEqual(model.position, 4 * Z_STEPS_PER_MM)

    def test_cycle_cap_stops_after_last_completed_release(self):
        model = PlanModel(Z_MAX_CYCLES=102)
        model.change_after_release = {101: 2485}
        with self.assertRaisesRegex(FactoryError, 'z_contact_cycle_limit') as error:
            model.run()
        self.assertEqual(error.exception.details['cycles'], 102)
        self.assertEqual(model.releases, 102)
        self.assertFalse(model.closed)
