"""R0323 nozzle Z grouping and the diagnostic result, without hardware."""
import json
import unittest
import test_orange_pi_z_home_host as z_host_support

from orange_pi.inventure_factory_core import FactoryError, parameters
from orange_pi.inventure_z_home import (
    Z_BOTTOM, Z_STEPS_PER_MM, ZHomeProgram, nozzle_z_comparison, z_home_parameters,
)
from test_orange_pi_z_home_core import PlanModel
from test_orange_pi_door_policy import Command
from test_orange_pi_load_test import Wire


class NozzlePlanModel(PlanModel):
    def __init__(self, tool, rises):
        super().__init__(SPEED_PROFILE='OEM')
        self.p = parameters(dict(TOOL=tool, SPEED_PROFILE='OEM'))
        self.pad = (82.8 if tool == 'T0' else 72., 68.)
        self.settings = z_home_parameters(dict(Z_APPROACH_MM=3, Z_PROBE_MM=1,
            Z_PROBE_X=self.pad[0], Z_PROBE_Y=self.pad[1]), self.p, nozzle_test=True)
        self.program = ZHomeProgram(self.p, self.settings)
        self.rises = iter(rises)
        self.sample_starts = []
        self.contact_at = None
        self.fail_retreat = False
        self.fail_bottom = False

    def run(self, prepare=False):
        generator = self.program.run_nozzle(prepare)
        response = None
        while True:
            action = generator.send(response)
            kind = action['kind']
            phase = self.program.history[-1] if self.program.history else ''
            if kind == 'done':
                self.actions.append((phase, action))
                return action
            if kind == 'z_contact_move':
                response = self._motion(action)
                if self.fail_retreat and action['steps'] < 0:
                    response.update(hit=Z_BOTTOM, reason='stop_input',
                                    end_raw=dict(z_bottom=1, nozzle_contact=1))
                if self.fail_bottom and phase == 'Z_RETURN_BOTTOM':
                    response.update(hit=0, reason='completed', sent_steps=abs(action['steps']),
                                    end_raw=dict(z_bottom=0, nozzle_contact=1))
                continue
            self.actions.append((phase, action))
            if kind == 'nozzle_z_refine':
                self.sample_starts.append(self.position)
                rise = next(self.rises)
                if rise is None:
                    generator.throw(FactoryError('z_contact_not_found'))
                self.position += rise
                response = dict(net_steps=rise, best_steps=rise, cycles=202,
                                non_improvements=201, up_steps=rise + 202, down_steps=202)
            else:
                if kind == 'home' and action['axis'] == 'Z':
                    self.position = 0
                response = dict(ok=True, a=0, b=0, starts=0, pulses=0,
                                pulse_age_ms=4294967295, fault=0)


class NozzleZTests(unittest.TestCase):
    def test_cold_prepare_selects_each_nozzle_without_moving_up_or_heating(self):
        for tool in ('T0', 'T1'):
            with self.subTest(tool=tool):
                model = NozzlePlanModel(tool, ())
                result = model.run(prepare=True)
                actions = [row for _, row in model.actions]
                self.assertEqual(result['terminal'], 'NOZZLE_Z_PREPARE')
                self.assertEqual((result['tool'], result['readings']), (tool, []))
                self.assertEqual(model.program.xy, model.pad)
                self.assertEqual(model.position, 0)
                self.assertTrue(any(row.get('leg') == 'selector_press' for row in actions))
                self.assertFalse(any(row['kind'] in ('heat', 'z_contact_move', 'nozzle_z_refine')
                                     or row.get('e') for row in actions))

    def test_samples_return_to_same_start_and_do_not_average(self):
        start = 3 * Z_STEPS_PER_MM
        results = {}
        for tool, rises in (('T0', (251, 240)), ('T1', (220, 211, 216))):
            with self.subTest(tool=tool):
                model = NozzlePlanModel(tool, rises)
                result = model.run()
                results[tool] = result
                actions = [row for _, row in model.actions]
                samples = [row for row in actions if row['kind'] == 'nozzle_z_refine']
                self.assertEqual([row['start_steps'] for row in samples], [start] * len(rises))
                self.assertEqual(model.sample_starts, [start] * len(rises))
                self.assertTrue(all(row['repeats'] == 201 and row['interval_us'] == 4000
                                    and row['span'] == Z_STEPS_PER_MM for row in samples))
                retreats = [row['steps'] for phase, row in model.actions
                            if phase.startswith('NOZZLE_Z_SAMPLE_')
                            and row['kind'] == 'z_contact_move' and row['steps'] < 0]
                self.assertEqual(retreats, [-rise for rise in rises[:-1]])
                return_to_limit, = [row for phase, row in model.actions
                    if phase == 'Z_RETURN_BOTTOM' and row['kind'] == 'z_contact_move']
                self.assertEqual(return_to_limit['steps'], -start - rises[-1] - Z_STEPS_PER_MM)
                self.assertEqual(return_to_limit['stop_mask'], Z_BOTTOM)
                self.assertEqual([row['release_steps_from_bottom'] for row in result['readings']],
                                 [start + rise for rise in rises])
                self.assertEqual((result['terminal'], model.position), ('NOZZLE_Z_MEASURE', 0))
        comparison = nozzle_z_comparison(results, .15)
        self.assertEqual(comparison['delta_steps'], 24)
        self.assertEqual(comparison['t1_lower_by_mm'], round(24 / Z_STEPS_PER_MM, 6))
        self.assertFalse(comparison['coordinate_saved'])

    def test_host_runs_owned_samples_and_cuts_both_heaters(self):
        host=z_host_support.ZHomeHostTests().host()
        device=host.device
        device.contact_at=None
        starts=[]
        state={}

        def local_refine(args):
            start,owner,token,span,interval,repeats=args
            if start:
                device._settle()
                tool=device.session['tool']
                self.assertEqual(owner,device.session['token'])
                self.assertLess(device.z,147*Z_STEPS_PER_MM)
                self.assertEqual((interval,repeats),(4000,201))
                starts.append((tool,device.z,owner,token))
                state.update(owner=owner,token=token,tool=tool,active=1,status=0,
                             reject=0,net_steps=0,best_steps=0,cycles=0,
                             non_improvements=0,up_steps=0,down_steps=0)
            else:
                self.assertEqual((owner,token),(state['owner'],state['token']))
                if state['active']:
                    rise=250-40*state['tool']
                    device.z+=rise
                    device.motion=None
                    state.update(active=0,status=1,net_steps=rise,best_steps=rise,
                                 cycles=202,non_improvements=201,up_steps=rise+202,down_steps=202)
            return dict(state)

        start=Command(ACTION='START',ACK='BOTH_NOZZLES_OVER_SAME_PAD',T1_PAD_X=72,T1_PAD_Y=68)
        with self.assertRaisesRegex(RuntimeError,'NOZZLE-Z'):host.cmd_nozzle_z_test(start)
        host.nozzle_z_test_abi=1
        host.commands['nozzle_z_test']=Wire(local_refine)
        with self.assertRaisesRegex(RuntimeError,'verified T1'):
            host.cmd_nozzle_z_test(Command(ACTION='START',ACK='BOTH_NOZZLES_OVER_SAME_PAD'))
        self.assertEqual(device.packets,[])
        host.cmd_nozzle_z_test(start)
        while host.nozzle_z_context is not None:
            self.assertLess(host.reactor.now,600.)
            host.reactor.pause(host.reactor.now+.1)
        report=json.loads(start.lines[-1].split(' ',1)[1])
        self.assertEqual(report['phase'],'done',report)
        self.assertEqual(report['measurement']['delta_steps'],40)
        self.assertEqual([(tool,z) for tool,z,_,_ in starts],
                         [(tool,137*Z_STEPS_PER_MM) for tool in (0,0,1,1,1)])
        self.assertEqual(len({token for _,_,_,token in starts}),5)
        self.assertEqual(len({owner for _,_,owner,_ in starts}),2)
        self.assertEqual((device.z,device.output(6)['duty'],device.output(7)['duty']),(0,0,0))

    def test_failing_stages_cannot_report_success(self):
        for rises, fault, error in (
                ((220, None), None, 'z_contact_not_found'),
                ((0, 215), None, 'Nozzle release outside the positive approach window'),
                ((220, 215), 'fail_retreat', 'Failed to return to the shared nozzle approach height'),
                ((220, 215), 'fail_bottom', 'Nozzle test did not reach the PE12 bottom limit')):
            with self.subTest(error=error):
                model = NozzlePlanModel('T0', rises)
                if fault is not None:
                    setattr(model, fault, True)
                with self.assertRaisesRegex(FactoryError, error):
                    model.run()
                self.assertFalse(any(row['kind'] == 'done' for _, row in model.actions))

    def test_comparison_preserves_negative_sign_and_rejects_spread_or_missing_samples(self):
        def tool(*steps):
            return dict(readings=[dict(release_steps_from_bottom=step) for step in steps])

        results = dict(T0=tool(3000, 3010), T1=tool(3020, 3030, 3040))
        self.assertEqual(nozzle_z_comparison(results, .15)['delta_steps'], -30)
        with self.assertRaisesRegex(FactoryError, 'spread exceeds'):
            nozzle_z_comparison(results, .02)
        results['T1'] = tool(3020, 3030)
        with self.assertRaisesRegex(FactoryError, 'Missing nozzle release samples'):
            nozzle_z_comparison(results, .15)
