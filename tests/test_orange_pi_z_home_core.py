"""Test the bounded planner independently of transport and MCU timings."""
import unittest

from orange_pi.inventure_factory_core import FactoryError, defaults
from orange_pi.inventure_z_home import (
    NOZZLE_CONTACT, OEM_Z_BOTTOM_RELEASE_SPEED, OEM_Z_BOTTOM_SPEED,
    Z_BOTTOM, Z_HOME_ACK, Z_STEPS_PER_MM,
    ZHomeProgram, z_home_parameters,
)


class PlanModel:
    def __init__(self, **options):
        self.p = defaults(0)
        self.p['speed_profile'] = options.get('SPEED_PROFILE', 'SERVICE')
        command = dict(Z_APPROACH_MM=3, ACK=Z_HOME_ACK)
        command.update(options)
        self.settings = z_home_parameters(command, self.p, required=True)
        self.program = ZHomeProgram(self.p, self.settings)
        self.position = 0
        self.actions = []
        self.contact_at = 2486
        self.release_at = None
        self.closed = False
        self.releases = 0
        self.change_after_release = {}
        self.bounce_after_release = False
        self.transient_fine_attempts = set()
        self.fine_attempts = 0

    def _motion(self, action):
        steps = action['steps']
        direction = 1 if steps > 0 else -1
        phase = self.program.history[-1]
        self.actions.append((phase, action))
        if phase == 'Z_CONTACT_REFERENCE' and direction > 0:
            self.fine_attempts += 1
            if self.fine_attempts in self.transient_fine_attempts:
                target = min(self.position + abs(steps), self.contact_at - 1)
                sent = target - self.position
                self.position = target
                return dict(sent_steps=sent, hit=NOZZLE_CONTACT, reason='stop_input',
                            end_raw=dict(z_bottom=0, nozzle_contact=1))
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
            elif kind == 'z_verify_open':
                response = dict(nozzle_contact=int(not self.closed), z_bottom=int(self.position == 0))
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
        self.assertEqual(preview['z_stable_cycles'], 101)
        self.assertEqual((preview['z_transient_retries'], preview['z_transient_total']), (3, 10))
        self.assertEqual((preview['z_release_recovery_steps'], preview['z_sample_clearance_mm']), (16, .05))
        for params in ({'ACK': Z_HOME_ACK}, {'Z_APPROACH_MM': 3},
                       {'Z_APPROACH_MM': 249.5, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 80, 'Z_APPROACH_SPEED': .1, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 3, 'Z_PROBE_X': 156, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 3, 'Z_MAX_CYCLES': 101, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 3, 'Z_STABLE_CYCLES': 1.5, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 3, 'Z_TRANSIENT_RETRIES': None, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 3, 'Z_RELEASE_RECOVERY_STEPS': 17, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 3, 'Z_SAMPLE_CLEARANCE_MM': .6, 'ACK': Z_HOME_ACK},
                       {'Z_APPROACH_MM': 3, 'Z_TRANSIENT_BACKOFF_MM': 3, 'ACK': Z_HOME_ACK}):
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
        phase, action = next((phase, action) for phase, action in reversed(model.actions)
                             if action['kind'] == 'z_contact_move')
        self.assertEqual(phase, 'Z_CONTACT_RELEASE')
        self.assertEqual(action['steps'], -(1200 - (Z_STEPS_PER_MM + 1)))
        self.assertEqual(action['initial_raw'], dict(z_bottom=0, nozzle_contact=0))

    def test_stale_opening_and_missing_contact_cannot_establish_reference(self):
        model = PlanModel(Z_TRANSIENT_RETRIES=0, Z_RELEASE_RECOVERY_STEPS=0)
        model.bounce_after_release = True
        with self.assertRaisesRegex(FactoryError, 'z_contact_retry_limit') as error:
            model.run()
        self.assertEqual(error.exception.details['transient_openings'], 1)
        self.assertEqual(error.exception.details['qualified_cycles'], 0)
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

    def test_transient_closure_retries_from_lower_position_and_resets_stability(self):
        model = PlanModel(Z_STABLE_CYCLES=2)
        model.transient_fine_attempts = {3}
        result = model.run()['z_home_result']
        self.assertEqual(result['method'], 'minimum_release_2_non_improvements')
        self.assertEqual((result['cycles'], result['non_improvements']), (4, 2))
        self.assertEqual((result['transient_closures'], model.releases), (1, 4))
        self.assertEqual(result['release_steps_from_bottom'], 2485)
        moves = [a for _, a in model.actions if a['kind'] == 'z_contact_move']
        backoff_index = next(index for index, action in enumerate(moves) if action['steps'] == -160)
        backoff = moves[backoff_index]
        self.assertEqual(backoff['steps'], -160)
        self.assertEqual((backoff['stop_mask'], backoff['stop_value']),
                         (Z_BOTTOM, Z_BOTTOM))
        self.assertEqual(backoff['initial_raw'], dict(z_bottom=0))
        self.assertGreater(moves[backoff_index + 1]['steps'], 0)
        modules = [a['name'] for _, a in model.actions if a.get('state') == 'done']
        self.assertEqual(modules.count('z_contact_retry'), 1)
        self.assertIn('z_contact_approach', modules)
        self.assertIn('z_contact_repeat', modules)
        self.assertIn('z_contact_settle', modules)
        progress = [a for _, a in model.actions if a['kind'] == 'z_progress']
        self.assertEqual([(a['qualified_cycles'], a['non_improvements'], a['transient_closures'],
                           a['consecutive_transients']) for a in progress],
                         [(1, 0, 0, 0), (2, 1, 0, 0), (2, 0, 1, 1), (3, 1, 1, 0), (4, 2, 1, 0)])

    def test_transient_caps_are_independent_and_exclude_qualification(self):
        cases = ((dict(Z_TRANSIENT_RETRIES=1), {1, 2}, 2, 2, 0),
                 (dict(Z_TRANSIENT_TOTAL=1), {1, 3}, 2, 1, 1),
                 (dict(Z_TRANSIENT_RETRIES=0), {1}, 1, 1, 0))
        for options, attempts, count, consecutive, qualified in cases:
            with self.subTest(options=options):
                model = PlanModel(Z_STABLE_CYCLES=2, **options)
                model.transient_fine_attempts = attempts
                with self.assertRaisesRegex(FactoryError, 'z_contact_retry_limit') as error:
                    model.run()
                self.assertEqual(error.exception.details['transient_closures'], count)
                self.assertEqual(error.exception.details['consecutive_transients'], consecutive)
                self.assertEqual(error.exception.details['qualified_cycles'], qualified)
                self.assertEqual(model.releases, qualified)
                phase, progress = model.actions[-1]
                self.assertEqual((phase, progress['kind']), ('Z_CONTACT_REFERENCE', 'z_progress'))
                self.assertEqual((progress['transient_closures'], progress['qualified_cycles']),
                                 (count, qualified))

    def test_oem_speed_defaults_and_bottom_bound_only_affect_z_modules(self):
        model = PlanModel(SPEED_PROFILE='OEM', Z_STABLE_CYCLES=1)
        model.run()
        bottom = next(a for _, a in model.actions if a['kind'] == 'home' and a['axis'] == 'Z')
        self.assertEqual((bottom['speed'], bottom['max_mm']), (OEM_Z_BOTTOM_SPEED, 150.))
        legs = [a for _, a in model.actions if a['kind'] == 'z_contact_move']
        self.assertEqual([(a['steps'], a['speed']) for a in legs[:3]],
                         [(5000, OEM_Z_BOTTOM_RELEASE_SPEED), (802, 20.), (1603, 20.)])
        self.assertEqual(legs[3]['speed'], .2)
        self.assertEqual(legs[4]['speed'], .2)
        with self.assertRaisesRegex(FactoryError, 'exceeds the Z travel bound'):
            PlanModel(SPEED_PROFILE='OEM', Z_APPROACH_MM=143, Z_PROBE_MM=9)
        with self.assertRaisesRegex(FactoryError, 'Z bottom seek exceeds 595 seconds'):
            PlanModel(SPEED_PROFILE='OEM', Z_BOTTOM_SPEED=.1)
        accepted = PlanModel(SPEED_PROFILE='OEM', Z_APPROACH_MM=135, Z_PROBE_MM=9)
        self.assertEqual(accepted.settings['z_approach_mm'] + accepted.settings['z_probe_mm'], 144.)
