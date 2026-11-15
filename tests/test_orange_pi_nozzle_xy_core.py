"""Exercise the XY pattern recipe before connecting it to the MCU executor."""
import unittest

from orange_pi.inventure_factory_core import E_STEPS_PER_MM, FactoryError
from orange_pi.inventure_nozzle_xy import (
    NozzleXYProgram, PATTERN_COUNT, xy_alignment_candidate, xy_alignment_parameters,
    xy_pattern_e_budget, xy_pattern_segments,
)
from orange_pi.inventure_z_home import NOZZLE_CONTACT, NOZZLE_Z_CLEARANCE_STEPS, Z_STEPS_PER_MM
from test_orange_pi_nozzle_dual_core import DualNozzlePlan


class XYPlan(DualNozzlePlan):
    def __init__(self, xy):
        super().__init__(initial_mm=50)
        self.program = NozzleXYProgram(self.p, self.settings)
        self.xy = xy
        self.stop_first_draw = False

    def _motion(self, action):
        if self.stop_first_draw and self.program.history[-1] == 'PRINT_T0_Y':
            return dict(sent_steps=0, hit=NOZZLE_CONTACT, reason='stop_input',
                        end_raw=dict(z_bottom=0, nozzle_contact=0))
        return super()._motion(action)

    def run(self):
        generator = self.program.run_nozzle_xy(self.t1_p, self.t1_settings, self.xy)
        reply = None
        while True:
            action = generator.send(reply)
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


class NozzleXYCoreTests(unittest.TestCase):
    def test_oem_comb_shapes_and_two_axis_differential_in_raw_frame(self):
        xy = xy_alignment_parameters({}, {'X': .12, 'Y': -.2})
        first, second = (xy_pattern_segments(tool, xy) for tool in (0, 1))
        self.assertEqual(len(first), PATTERN_COUNT)
        self.assertEqual(len(second), PATTERN_COUNT)
        for axis, lines in (('Y', first[:14]), ('X', first[14:])):
            self.assertEqual([line['axis'] for line in lines], [axis] * 14)
            self.assertEqual([line['line'] for line in lines], [None, *range(-6, 7)])
        self.assertEqual((first[0]['start'], first[0]['end'], first[0]['exit']),
                         ((25.3, 98.), (25.3, 38.), (25.3, 36.)))
        self.assertEqual((first[14]['start'], first[14]['end'], first[14]['exit']),
                         ((147.8, 45.5), (87.8, 45.5), (85.8, 45.5)))
        for line in (-6, 0, 6):
            i = line + 7
            self.assertAlmostEqual(second[i]['start'][0] - first[i]['start'][0],
                                   -10.8 - .12)
            self.assertAlmostEqual(second[i]['start'][1] - first[i]['start'][1],
                                   .2 + .05 * line, delta=1./640.)
            self.assertAlmostEqual(second[14+i]['start'][0] - first[14+i]['start'][0],
                                   -10.8 - .12 + .05 * line, delta=1./640.)

    def test_signed_selection_and_stock_x_neutral_constraint(self):
        newer = xy_alignment_candidate({'X': -2, 'Y': 3},
                                       xy_alignment_parameters({}, {'X': .08, 'Y': -.02}))
        self.assertEqual(newer['residual_mm'], {'X': .18, 'Y': -.17})
        self.assertEqual(newer['t1_carriage_adjust_mm'], {'X': -.18, 'Y': .17})
        self.assertEqual(newer['t1_total_nozzle_offset_mm'], {'X': 10.98, 'Y': -.17})
        stock = xy_alignment_parameters({'X_MODE': 'STOCK_REFERENCE'})
        rows = xy_pattern_segments(1, stock)[15:]
        self.assertAlmostEqual(rows[-1]['start'][0] - rows[0]['start'][0], 60.)
        self.assertEqual(xy_alignment_candidate({'X': 0, 'Y': 0}, stock)['residual_mm'],
                         {'X': 0., 'Y': 0.})
        with self.assertRaisesRegex(FactoryError, 'X line 0'):
            xy_alignment_candidate({'X': -1}, stock)
        with self.assertRaisesRegex(FactoryError, r'-6\.\.\+6'):
            xy_alignment_candidate({'Y': True}, stock)
        with self.assertRaisesRegex(FactoryError, r'-6\.\.\+6'):
            xy_alignment_candidate({'Y': 7}, stock)

    def test_preflight_rejects_unprintable_shift_and_over_budget(self):
        model = XYPlan(xy_alignment_parameters({'PATTERN_SHIFT_X': 5.}))
        with self.assertRaisesRegex(FactoryError, '135 mm build area'):
            model.run()
        self.assertEqual(model.timeline, [])
        with self.assertRaisesRegex(FactoryError, 'extrusion budget'):
            xy_pattern_e_budget(xy_alignment_parameters(dict(T0_PROFILE='MATERIAL14', T0_FLOW=150)))
        with self.assertRaisesRegex(FactoryError, 'PATTERN_E_SPEED'):
            xy_alignment_parameters({'PATTERN_E_SPEED': 'nan'})

    def test_pattern_executes_after_each_probe_with_exact_budget_and_tool_clearance(self):
        xy = xy_alignment_parameters({'T0_PROFILE': 'MATERIAL14', 'T1_FLOW': 125})
        model = XYPlan(xy)
        outcome = model.run()
        trace = model.timeline
        homes = [a['axis'] for kind, _, _, a in trace if kind == 'home']
        self.assertEqual(homes, ['Y', 'X', 'Z'])
        self.assertEqual(outcome['printed_segments'], {'T0': 28, 'T1': 28})
        self.assertEqual([len(rows) for rows in outcome['references'].values()], [2, 3])
        self.assertEqual(sum(round(abs(a.get('e', 0.)) * E_STEPS_PER_MM) for _, _, _, a in trace),
                         xy_pattern_e_budget(xy))
        self.assertEqual([a['name'] for kind, _, _, a in trace if kind == 'phase' and a['name'].startswith('PRINT')],
                         ['PRINT_T0_Y', 'PRINT_T0_X', 'PRINT_T1_Y', 'PRINT_T1_X'])
        switch = next(i for i, (kind, _, _, _) in enumerate(trace) if kind == 'dual_switch_tool')
        latest_move = next(a for kind, _, _, a in reversed(trace[:switch]) if kind in ('move', 'z_contact_move'))
        self.assertEqual(latest_move['steps'], -NOZZLE_Z_CLEARANCE_STEPS)
        for tool in (0, 1):
            sequence = [entry for entry in trace if entry[1] == tool]
            final_probe = max(i for i, entry in enumerate(sequence) if entry[0] == 'nozzle_z_refine')
            first_e = next(i for i, entry in enumerate(sequence) if entry[3].get('e'))
            self.assertGreater(first_e, final_probe)
        t1_release = outcome['references']['T1'][-1]['release_steps_from_bottom']
        self.assertEqual(model.position, t1_release - round(1.1 * Z_STEPS_PER_MM) - NOZZLE_Z_CLEARANCE_STEPS)

    def test_contact_guard_prevents_starting_first_printed_line(self):
        model = XYPlan(xy_alignment_parameters({}))
        model.stop_first_draw = True
        with self.assertRaisesRegex(FactoryError, 'Z stopped before'):
            model.run()
        extrusion = [a['e'] for _, _, _, a in model.timeline if a.get('e')]
        # Initial retract can happen at the pad. No positive E on a guard stop.
        self.assertEqual(extrusion, [-1.])
        self.assertFalse(any(kind == 'dual_switch_tool' for kind, _, _, _ in model.timeline))


if __name__ == '__main__':
    unittest.main()
