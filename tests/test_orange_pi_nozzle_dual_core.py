"""Check the single-reference dual-nozzle plan independently of serial I/O."""
import unittest

from orange_pi.inventure_factory_core import FactoryError, parameters
from orange_pi.inventure_z_home import (
    NOZZLE_Z_CLEARANCE_STEPS, Z_STEPS_PER_MM, ZHomeProgram,
    nozzle_z_comparison, z_home_parameters,
)
from test_orange_pi_z_home_core import PlanModel


class DualNozzlePlan(PlanModel):
    def __init__(self, initial_mm):
        super().__init__(SPEED_PROFILE='OEM')
        self.p = parameters(dict(TOOL='T0', SPEED_PROFILE='OEM'))
        self.t1_p = parameters(dict(TOOL='T1', SPEED_PROFILE='OEM'))
        settings = dict(Z_APPROACH_MM=137, Z_PROBE_MM=9)
        self.settings = z_home_parameters(settings, self.p, nozzle_test=True)
        self.t1_settings = z_home_parameters(dict(settings, Z_PROBE_X=72.), self.t1_p,
                                             nozzle_test=True)
        self.program = ZHomeProgram(self.p, self.settings)
        self.position = initial_mm * Z_STEPS_PER_MM
        self.start = 137 * Z_STEPS_PER_MM
        self.rises = [iter((955, 956)), iter((745, 747, 746))]
        self.contact_at = self.start + 960
        self.active_tool = 0
        self.timeline = []

    def run(self):
        program = self.program.run_nozzle_dual(self.t1_p, self.t1_settings)
        reply = None
        while True:
            action = program.send(reply)
            kind = action['kind']
            self.timeline.append((kind, self.active_tool, self.position, action.copy()))
            reply = dict(ok=True, a=0, b=0, starts=0, pulses=0,
                         pulse_age_ms=4294967295, fault=0)
            if kind == 'done':
                return action
            if kind == 'z_inputs':
                reply = dict(z_bottom=int(not self.position),
                             nozzle_contact=int(self.position < self.contact_at))
            elif kind == 'z_contact_move':
                reply = self._motion(action)
            elif kind == 'home' and action['axis'] == 'Z':
                self.position = 0
            elif kind == 'nozzle_z_refine':
                rise = next(self.rises[self.active_tool])
                self.position += rise
                reply = dict(net_steps=rise, best_steps=rise, cycles=207,
                             non_improvements=201, up_steps=rise + 207, down_steps=207)
            elif kind == 'dual_switch_tool':
                self.active_tool = 1
                self.contact_at = self.start + 752


class NozzleDualCoreTests(unittest.TestCase):
    def test_shared_reference_both_heaters_and_bounded_switch(self):
        # Starting above the bottom forces a cold clearance gesture without
        # assigning a reference, once, before any XY movement.
        model = DualNozzlePlan(initial_mm=50)
        outcome = model.run()
        timeline = model.timeline
        actions = [entry[3] for entry in timeline]
        kinds = [entry[0] for entry in timeline]
        self.assertEqual(kinds.count('dual_heat_start'), 1)
        self.assertEqual(kinds.count('dual_heat_wait'), 2)
        homes = [(i, a['axis']) for i, a in enumerate(actions) if a['kind'] == 'home']
        self.assertEqual([axis for _, axis in homes], ['Y', 'X', 'Z'])
        clearance, = [i for i, a in enumerate(actions) if a.get('stage') == 'clearance']
        self.assertLess(clearance, homes[0][0])
        self.assertEqual(actions[clearance]['steps'], -NOZZLE_Z_CLEARANCE_STEPS)
        self.assertTrue(actions[homes[-1][0]]['dual_z_home'])

        selectors = [i for i, a in enumerate(actions) if a.get('leg') == 'selector_press']
        pads = [i for i, a in enumerate(actions) if a.get('leg') == 'z_contact_pad']
        self.assertEqual([(actions[i]['x'], actions[i]['y']) for i in selectors],
                         [(154.5, 154.5), (0., 154.5)])
        self.assertEqual([(actions[i]['x'], actions[i]['y']) for i in pads],
                         [(82.8, 68.), (72., 68.)])
        first_heat = kinds.index('dual_heat_start')
        first_ready = kinds.index('dual_heat_wait')
        first_fine = kinds.index('nozzle_z_refine')
        self.assertEqual(sorted((homes[1][0], selectors[0], pads[0], first_heat,
                                 homes[2][0], first_ready, first_fine)),
                         [homes[1][0], selectors[0], pads[0], first_heat,
                          homes[2][0], first_ready, first_fine])
        self.assertTrue(any(a.get('stage') == 'coarse' and a['steps'] > 0
                            for a in actions[homes[2][0]+1:first_ready]))

        switch = kinds.index('dual_switch_tool')
        second_ready = kinds.index('dual_heat_wait', first_ready + 1)
        second_fine = kinds.index('nozzle_z_refine', switch)
        self.assertEqual(sorted((switch, selectors[1], pads[1], second_ready, second_fine)),
                         [switch, selectors[1], pads[1], second_ready, second_fine])
        self.assertEqual([(tool, pos) for kind, tool, pos, _ in timeline
                          if kind == 'nozzle_z_refine'],
                         [(tool, model.start) for tool in (0, 0, 1, 1, 1)])
        results = {'T%d' % action['tool']: action for action in actions
                   if action['kind'] == 'nozzle_z_record'}
        self.assertEqual(nozzle_z_comparison(results, .15)['delta_steps'], 210)
        t0_release = results['T0']['readings'][-1]['release_steps_from_bottom']
        t1_release = results['T1']['readings'][-1]['release_steps_from_bottom']
        self.assertEqual(timeline[switch][2], t0_release - NOZZLE_Z_CLEARANCE_STEPS)
        self.assertEqual(model.position, t1_release - NOZZLE_Z_CLEARANCE_STEPS)
        self.assertFalse(outcome['table_at_bottom'])

    def test_existing_pe12_clearance_does_not_move_z_before_xy(self):
        model = DualNozzlePlan(initial_mm=0)
        model.run()
        kinds = [entry[0] for entry in model.timeline]
        self.assertNotIn('z_contact_move', kinds[:kinds.index('home')])

    def test_stuck_contact_at_start_prevents_xy(self):
        model = DualNozzlePlan(initial_mm=50)
        model.contact_at = 0
        with self.assertRaisesRegex(FactoryError, 'still closed'):
            model.run()
        self.assertFalse(any(kind == 'home' for kind, _, _, _ in model.timeline))

    def test_early_contact_while_heating_never_reaches_fine_probe(self):
        model = DualNozzlePlan(initial_mm=0)
        model.contact_at = model.start - 30
        with self.assertRaisesRegex(FactoryError, 'coarse'):
            model.run()
        kinds = [entry[0] for entry in model.timeline]
        self.assertIn('dual_heat_start', kinds)
        self.assertNotIn('nozzle_z_refine', kinds)
        self.assertTrue(any(action.get('stage') == 'coarse' and action['steps'] < 0
                            for _, _, _, action in model.timeline))


if __name__ == '__main__':
    unittest.main()
