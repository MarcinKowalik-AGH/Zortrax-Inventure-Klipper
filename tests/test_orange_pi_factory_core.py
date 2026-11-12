"""Factory planner boundaries, OEM decision ordering and genuine feedback."""
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('factory_core',
    Path(__file__).resolve().parents[1] / 'orange_pi/inventure_factory_core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class Command:
    def __init__(self, **values):self.values = values
    def get(self, key, default=None):return self.values.get(key, default)


class Executor:
    """Actions complete normally; hooks supply independent transport evidence."""
    def __init__(self, p=None, hook=None, operation='AUTO', planner=None):
        self.p = core.defaults(0) if p is None else p
        self.program = (planner or core).FactoryProgram(self.p)
        self.generator = self.program.run() if operation=='AUTO' else self.program.run(operation)
        self.hook = hook
        self.actions = []
        self.phase = None
        self.snap = dict(a=1, b=1, pulses=100, pulse_age_ms=10., starts=100, fault=0)
        self.done = None

    def run(self):
        result = None
        while True:
            try:action = self.generator.send(result)
            except StopIteration:return self
            self.actions.append(dict(action, phase=self.phase))
            kind = action['kind']
            if kind == 'phase':self.phase = action['name']
            if kind == 'done':self.done = action
            if kind in ('phase', 'home', 'home_release', 'heat', 'done'):
                result = None
            else:
                result = dict(self.snap)
                if kind.startswith(('wait_', 'feed_', 'retract_')):
                    result.update(ok=True, sent_steps=0)
                if kind == 'feed_until_edges':
                    result['pulses'] += action['count']
                    result['sent_steps'] = 12345
                if kind == 'retract_until_quiet':
                    result.update(ok=False, sent_steps=round(action['mm'] * 960))
                if kind == 'retract_until_a':result['a'] = 0
            if self.hook is not None:
                changed = self.hook(self, action, result)
                if changed is not None:result = changed
            if isinstance(result, dict):
                self.snap.update({k:result[k] for k in self.snap if k in result})
            if len(self.actions) > 10000:raise AssertionError('Unbounded factory generator')

    def in_phase(self, phase, kind=None):
        return [a for a in self.actions if a['phase'] == phase
                and (kind is None or a['kind'] == kind)]


def short_feedback(ex, action, result):
    if result is not None and ex.phase == 'L1':
        result['pulse_age_ms'] = 3000.
        if action['kind'] == 'move':result['starts'] = 106


def long_feedback(ex, action, result):
    if result is None:return
    if ex.phase == 'L1':result.update(starts=106, pulse_age_ms=3000.01)
    elif ex.phase == 'L3' and action['kind'] == 'feed_until_edges':
        result['pulse_age_ms'] = 10.
    elif ex.phase == 'L4' and action['kind'] == 'move' and action['e']:
        result['starts'] = 109


class FactoryCoreTests(unittest.TestCase):
    def test_defaults_and_parameter_tool_mapping_are_independent(self):
        a, b = core.defaults('T0'), core.parameters(Command(TOOL='T1'))
        self.assertEqual((a['tool'], b['tool']), (0, 1))
        self.assertEqual((a['approach_x'], a['switch_x'], a['service_x']), (139.5, 154.5, 150.3))
        self.assertEqual((a['switch_y'], a['service_y'], a['wipe_y']), (154.5, 2.5, 154.5))
        self.assertEqual((a['travel_speed'], a['purge_speed']), (30., 30.))
        self.assertEqual((b['approach_x'], b['switch_x'], b['service_x']), (10.5, -4.5, 0.))
        a['l1_budget'] = 0
        self.assertEqual(core.defaults(0)['l1_budget'], 500.)

    def test_tuning_is_typed_and_frozen_as_a_per_program_snapshot(self):
        p = core.parameters(Command(TOOL='T1', PULSE_COUNT='9', E_SPEED='2.5',
            L1_PORTION='12', L1_BUDGET='120', FEEDER_MS='800', FEEDER_K='1.5', FALLBACK='0'))
        plan = core.FactoryProgram(p)
        p['e_speed'] = 9.
        self.assertEqual(plan.p['e_speed'], 2.5)
        self.assertEqual(plan.p['pulse_count'], 9)
        self.assertIs(type(plan.p['pulse_count']), int)
        self.assertEqual((plan.p['feeder_ms'], plan.p['fallback']), (800, 0))

    def test_OEM_profile_uses_stock_XY_feedrates_without_changing_E_or_homing(self):
        service = core.parameters(Command(TOOL='T0'))
        oem = core.parameters(Command(TOOL='T0', SPEED_PROFILE='OEM'))
        self.assertEqual(service['speed_profile'], 'SERVICE')
        self.assertEqual(oem['speed_profile'], 'OEM')
        self.assertEqual(tuple(oem[key] for key in ('travel_speed', 'purge_speed', 'switch_speed')),
                         (8000/60, 3000/60, 250/60))
        for field in ('home_speed', 'z_speed', 'e_speed', 'unload_speed1', 'unload_speed2',
                      'l1_portion', 'long_mm', 'final_mm', 'purge_e_steps'):
            self.assertEqual(oem[field], service[field], field)

    def test_OEM_feedrate_overrides_follow_configuration_precedence(self):
        configured = core.profiles(common={'PURGE_SPEED': 24},
                                   t0={'SPEED_PROFILE': 'OEM', 'TRAVEL_SPEED': 85})
        inherited = core.parameters(Command(TOOL='T0'), configured)
        self.assertEqual((inherited['speed_profile'], inherited['travel_speed'], inherited['purge_speed']),
                         ('OEM', 85., 24.))
        overridden = core.parameters(Command(TOOL='T0', TRAVEL_SPEED=90, PURGE_SPEED=45), configured)
        self.assertEqual((overridden['travel_speed'], overridden['purge_speed']), (90., 45.))

    def test_OEM_profile_rejects_rates_above_stock_for_both_tools(self):
        for tool in ('T0', 'T1'):
            for values in ({'TRAVEL_SPEED': 134}, {'PURGE_SPEED': 51},
                           {'SWITCH_SPEED': 4.2}, {'PURGE_E_STEPS': 2074}):
                with self.subTest(tool=tool, values=values):
                    with self.assertRaises(core.FactoryError):
                        core.parameters(Command(TOOL=tool, SPEED_PROFILE='OEM', **values))

    def test_t1_oem_translates_its_own_selector_and_service_positions(self):
        p = core.parameters(Command(TOOL='T1', SPEED_PROFILE='OEM'))
        self.assertEqual(p['xy_input_frame'], 'OEM')
        self.assertEqual(tuple(p[k] for k in ('center_x', 'center_y')), (72., 152.5))
        self.assertEqual(tuple(p[k] for k in ('approach_x', 'switch_x', 'switch_y')), (15., 0., 154.5))
        self.assertEqual(tuple(p[k] for k in ('service_x', 'service_y', 'wipe_y')), (4.5, 2.5, 154.5))
        self.assertEqual(tuple(p[k] for k in ('travel_speed', 'purge_speed', 'switch_speed')),
                         (8000/60, 3000/60, 250/60))
        for frame, override, expect in (('OEM', 3, 7.5), ('RAW', 3, 3.)):
            result = core.parameters(Command(TOOL='T1', SPEED_PROFILE='OEM',
                                             XY_INPUT_FRAME=frame, SERVICE_X=override))
            self.assertEqual(result['service_x'], expect)
        with self.assertRaisesRegex(core.FactoryError, 'negative X'):
            core.parameters(Command(TOOL='T1', SPEED_PROFILE='OEM', SWITCH_X=10.5))

    def test_t1_oem_uses_cassette_temperature_and_unload_records(self):
        records = (
            ('UNKNOWN', 230, 30, 70), ('Z-SUPPORT', 235, 50, 50),
            ('Z-SUPPORT-PLUS', 240, 30, 70), ('Z-SUPPORT PREMIUM', 230, 30, 70),
            ('BASF Ultrafuse BVOH', 230, 30, 70),
        )
        for name, target, u1, u2 in records:
            with self.subTest(material=name):
                p = core.parameters(Command(TOOL='T1', SPEED_PROFILE='OEM', MATERIAL=name))
                self.assertEqual((p['target'], p['unload_l1'], p['unload_l2']), (target, u1, u2))
                self.assertEqual((p['feeder_ms'], p['feeder_k']), (1000, 1.75))
        overridden = core.parameters(Command(TOOL='T1', SPEED_PROFILE='OEM', MATERIAL='Z-SUPPORT',
                                             TARGET=220, UNLOAD_L2=45))
        self.assertEqual((overridden['target'], overridden['unload_l2']), (220, 45))
        for params in (dict(TOOL='T0', MATERIAL='Z-SUPPORT'),
                       dict(TOOL='T1', MATERIAL='Z-SUPPORT'),
                       dict(TOOL='T1', SPEED_PROFILE='OEM', MATERIAL='Z-PLA'),
                       dict(TOOL='T1', SPEED_PROFILE='OEM', MATERIAL='EXTERNAL-SUPPORT')):
            with self.subTest(params=params):
                with self.assertRaisesRegex(core.FactoryError, 'MATERIAL'):
                    core.parameters(Command(**params))

    def test_t0_legacy_tuned_points_translate_to_raw_release_coordinates(self):
        configured = core.profiles(t0={'APPROACH_X': 135, 'SWITCH_X': 150,
                                       'SERVICE_X': 135})
        fields = ('approach_x', 'switch_x', 'service_x')
        raw = (139.5, 154.5, 150.3)
        inherited = core.parameters(Command(TOOL='T0'), configured)
        self.assertEqual(tuple(inherited[field] for field in fields), raw)
        overridden = core.parameters(Command(TOOL='T0', XY_INPUT_FRAME='RAW',
                                             APPROACH_X=139.5, SWITCH_X=154.5, SERVICE_X=150.3))
        self.assertEqual(tuple(overridden[field] for field in fields), raw)

    def test_nonfinite_bad_integer_and_unsafe_parameters_refuse_before_yield(self):
        for values in ({'TOOL':'T2'}, {'E_SPEED':'nan'}, {'TRAVEL_SPEED':float('inf')},
                {'PURGE_SPEED':31.26}, {'TRAVEL_SPEED':31.26}, {'PULSE_COUNT':1.5}, {'FEEDER_MS':1501},
                {'FALLBACK':2}, {'MONITOR':2}, {'MONITOR':.5}, {'L1_PORTION':50, 'L1_BUDGET':20},
                {'UNLOAD_L1':0, 'UNLOAD_L2':0}, {'MIN_X':0, 'TOOL':'T1'},
                {'MAX_X':100}, {'CENTER_Y':156}, {'MIN_Y':1}, {'FEEDER_K':True}):
            with self.subTest(values=values):
                with self.assertRaises(core.FactoryError):core.parameters(Command(**values))

    def test_all_possible_geometry_is_validated_before_first_action(self):
        for key, value in (('switch_x', 156.), ('service_y', -1.), ('approach_x', 156.),
                           ('center_x', 20.), ('switch_y', 156.)):
            p = core.defaults(0);p[key] = value
            with self.subTest(key=key):
                with self.assertRaises(core.FactoryError):core.FactoryProgram(p)

    def test_t0_selector_rejects_zero_or_negative_press_stroke(self):
        for switch_x in (135, 134):
            with self.subTest(switch_x=switch_x):
                with self.assertRaisesRegex(core.FactoryError, 'positive X'):
                    core.parameters(Command(APPROACH_X=135, SWITCH_X=switch_x))

    def test_t1_profile_requires_park_geometry_only_for_park(self):
        configured = core.profiles(t1={'WIPE_SPAN': 60, 'MIN_X': -5, 'MAX_X': 110,
                                       'PARK_SPAN': 160})
        command = Command(TOOL='T1')
        p = core.parameters(command, configured, 'AUTO')
        self.assertEqual(Executor(p, short_feedback).run().done['terminal'], 'L1')
        with self.assertRaisesRegex(core.FactoryError, 'geometry'):
            core.parameters(command, configured, 'PARK')
        with self.assertRaisesRegex(core.FactoryError, 'geometry'):
            next(core.FactoryProgram(p).run('PARK'))

    def test_each_possible_trajectory_fits_MCU_duration_before_any_action(self):
        for values in ({'HOME_Z_MAX':300, 'Z_SPEED':.1},
                {'HOME_XY_MAX':250, 'HOME_SPEED':.1},
                {'LONG_MM':500, 'E_SPEED':.1},
                {'UNLOAD_L1':250, 'UNLOAD_SPEED1':.1},
                {'UNLOAD_L2':250, 'UNLOAD_SPEED2':.1},
                {'TRAVEL_SPEED':.1}, {'SWITCH_SPEED':.1, 'APPROACH_X':0}):
            with self.subTest(values=values):
                with self.assertRaisesRegex(core.FactoryError, '595 seconds'):
                    core.parameters(Command(**values))
        # A 59.5mm pure feed at .1mm/s is exactly the reserved boundary.
        p = core.parameters(Command(LONG_MM=59.5, E_SPEED=.1))
        self.assertEqual(p['long_mm'] / p['e_speed'], 595.)

    def test_feeder_budget_uses_float32_and_rejects_zero_before_any_action(self):
        p = core.defaults(0)
        self.assertEqual(core.feeder_budget(p), 20160)
        p.update(feeder_ms=1500, feeder_k=3.)
        self.assertEqual(core.feeder_budget(p), 51840)
        with self.assertRaisesRegex(core.FactoryError, 'positive E budget'):
            core.parameters(Command(FEEDER_MS=1, FEEDER_K=.01))

    def test_full_homing_and_heat_precede_owned_modes_and_E_for_both_tools(self):
        for tool in (0, 1):
            ex = Executor(core.defaults(tool), short_feedback).run()
            homes = [a for a in ex.actions if a['kind'] == 'home']
            self.assertEqual([a['axis'] for a in homes],
                             ['Z', 'Y', 'X'] if tool == 0 else ['Z', 'X', 'Y'])
            releases = [a for a in ex.actions if a['kind'] == 'home_release']
            self.assertEqual([(a['axis'], a['max_mm'], a['speed']) for a in releases],
                             [('Y', 5000/640., 1.), ('X', 5000/640., 1.)] if tool == 0 else [])
            self.assertEqual((homes[0]['max_mm'], homes[0]['speed']), (250., 2.))
            self.assertEqual([a['speed'] for a in homes[1:]], [10., 10.] if tool == 0 else [5., 5.])
            heat = next(i for i,a in enumerate(ex.actions) if a['kind'] == 'heat')
            self.assertFalse(any(a['kind']=='mode' or a.get('e') for a in ex.actions[:heat]))
            parked = [(a['x'],a['y']) for a in ex.actions[:heat] if a['kind']=='move']
            self.assertEqual(parked, [(32.8,152.5), (82.8,152.5)] if tool==0 else [])
            switch = ex.in_phase('SWITCH', 'move')
            expected = ([(82.8, 152.5), (139.5, 154.5), (154.5, 154.5), (82.8, 152.5)]
                        if tool == 0 else
                        [(67.5, 150.), (10.5, 152.), (-4.5, 152.), (67.5, 150.)])
            self.assertEqual([(a['x'], a['y']) for a in switch], expected)
            self.assertEqual(switch[2]['speed'], 250/60. if tool == 0 else 4.)
            self.assertFalse(any(a['e'] for a in switch))

    def test_cold_T0_select_homes_to_release_and_executes_full_latch_stroke(self):
        ex = Executor(operation='SELECT').run()
        homing = [(a['kind'], a['axis']) for a in ex.actions
                  if a['kind'] in ('home', 'home_release')]
        self.assertEqual(homing, [('home', 'Z'), ('home', 'Y'), ('home_release', 'Y'),
                                  ('home', 'X'), ('home_release', 'X')])
        approach, press, leave = [a for a in ex.actions if a['kind'] == 'move'][-3:]
        self.assertEqual((approach['x'], approach['y']), (139.5, 154.5))
        self.assertEqual((press['x'] - approach['x'], press['y'] - approach['y']), (15., 0.))
        self.assertEqual(press['speed'], 250/60.)
        self.assertEqual((leave['x'], leave['y']), (82.8, 152.5))
        self.assertFalse(any(a['kind'] == 'heat' or a.get('e') for a in ex.actions))

    def test_cold_T0_sweep_repeats_the_L1_X_path_without_extrusion(self):
        ex = Executor(operation='SWEEP').run()
        l1 = Executor(hook=short_feedback).run().in_phase('L1', 'move')
        sweep = ex.in_phase('SWEEP', 'move')
        self.assertTrue(sweep)
        self.assertEqual([(a['x'], a['y'], a['speed']) for a in sweep],
                         [(a['x'], a['y'], a['speed']) for a in l1])
        self.assertEqual((sweep[-1]['x'], sweep[-1]['y']), (82.8, 152.5))
        self.assertFalse(any(a['kind'] == 'heat' or a.get('e')
                             or (a['kind'] == 'mode' and a['mode']) for a in ex.actions))

    def test_t1_oem_select_and_sweep_are_cold_and_use_the_released_reference(self):
        p = core.parameters(Command(TOOL='T1', SPEED_PROFILE='OEM'))
        select = Executor(p, operation='SELECT').run()
        homing = [(a['kind'], a['axis']) for a in select.actions
                  if a['kind'] in ('home', 'home_release')]
        self.assertEqual(homing, [('home', 'Z'), ('home', 'Y'), ('home_release', 'Y'),
                                  ('home', 'X'), ('home_release', 'X')])
        selector = [a for a in select.actions if a.get('leg', '').startswith('selector_')]
        self.assertEqual([(a['x'], a['y'], a['speed']) for a in selector],
                         [(15., 154.5, 8000/60), (0., 154.5, 250/60), (72., 152.5, 8000/60)])
        sweep = Executor(p, operation='SWEEP').run()
        moves = sweep.in_phase('SWEEP', 'move')
        self.assertEqual([(a['x'], a['y'], a['speed']) for a in moves],
                         [(x, 152.5, 50.) for x in [90., 54.] * 5 + [72.]])
        for ex in (select, sweep):
            self.assertFalse(any(a['kind']=='heat' or a.get('e')
                                 or (a['kind']=='mode' and a['mode']) for a in ex.actions))

    def test_t1_oem_auto_tries_l1_even_with_no_a_and_uses_stock_parking(self):
        def feedback(ex, action, result):
            long_feedback(ex, action, result)
            if action['kind'] == 'wait_a':result['a'] = 1
        p = core.parameters(Command(TOOL='T1', SPEED_PROFILE='OEM'))
        ex = Executor(p, feedback)
        ex.snap['a'] = 0
        ex.run()
        self.assertEqual(ex.done['terminal'], 'L4')
        self.assertEqual(ex.program.history,
                         ['HOME','PARK','HEAT','SWITCH','L1','L2','L3','L4','DONE'])
        self.assertEqual([(a['x'],a['y']) for a in ex.in_phase('PARK','move')],
                         [(22.,152.5),(72.,152.5)])
        self.assertEqual([(a['x'],a['y']) for a in ex.in_phase('L2','move')], [(4.5,2.5)])
        self.assertEqual([(a['x'],a['y']) for a in ex.in_phase('L3','move')],
                         [(22.,152.5),(72.,152.5)])
        self.assertEqual([round(a['e']*960) for a in ex.in_phase('L1','move')[:2]], [1036,2073])
        final = ex.in_phase('L4','move')
        self.assertEqual([(a['x'],a['y'],a['speed']) for a in final if a['y']==154.5],
                         [(x,154.5,8000/60) for x in (22.,122.,95.)])
        self.assertEqual([(a['x'],a['y']) for a in final[-2:]], [(122.,152.5),(72.,152.5)])
        self.assertEqual(round(sum(a['e'] for a in final)*960), 96394)
        self.assertEqual(core.procedure_modules('AUTO', 1, 'OEM'),
                         ('home','park','heat','select_tool','load_l1_l4'))

    def test_short_path_uses_exact_integer_kernel_and_stops_without_long_path(self):
        for tool in (0, 1):
            ex = Executor(core.defaults(tool), short_feedback).run()
            moves = ex.in_phase('L1', 'move')
            extrusions = [a for a in moves if a['e']]
            self.assertEqual(len(extrusions), 10)
            self.assertEqual([round(a['e'] * 960) for a in extrusions], [1036] + [2073] * 9)
            self.assertEqual(sum(round(a['e'] * 960) for a in extrusions), 19693)
            self.assertAlmostEqual(extrusions[0]['x'], 64.8 if tool == 0 else 85.5)
            self.assertAlmostEqual(extrusions[1]['x'], 100.8 if tool == 0 else 49.5)
            self.assertEqual((moves[-1]['x'], moves[-1]['y']),
                             (82.8, 152.5) if tool == 0 else (67.5, 150.))
            self.assertEqual(ex.done['terminal'], 'L1')
            self.assertFalse(any(a['kind'] == 'feed_until_edges' for a in ex.actions))
            self.assertFalse(any(a['phase'] == 'L4' for a in ex.actions))

    def test_load_monitor_defaults_off_and_is_explicitly_tunable_in_both_AUTO_stages(self):
        self.assertEqual(core.defaults(0)['monitor'], 0)
        for monitor in (0, 1):
            p = core.parameters(Command(MONITOR=str(monitor)))
            ex = Executor(p, long_feedback).run()
            automatic = [a for a in ex.actions if a['kind'] == 'mode' and a['mode'] == 1]
            self.assertEqual([a['phase'] for a in automatic], ['L1', 'L4'])
            self.assertEqual([a['monitor'] for a in automatic], [monitor, monitor])

    def test_L1_six_actual_starts_win_over_budget_at_exact_quiet_boundary(self):
        p = core.defaults(0);p.update(l1_portion=20., l1_budget=20.)
        ex = Executor(p, short_feedback).run()
        self.assertEqual(ex.done['terminal'], 'L1')

    def test_L1_issued_steps_and_filament_pulses_cannot_replace_auxiliary_starts(self):
        p = core.defaults(0);p.update(l1_portion=20., l1_budget=20.)
        def feedback(ex, action, result):
            if result is not None and ex.phase == 'L1':
                result.update(pulses=1000000, starts=105, pulse_age_ms=10.)
        ex = Executor(p, feedback)
        with self.assertRaisesRegex(core.FactoryError, 'l1_budget_exhausted'):ex.run()
        self.assertIsNone(ex.done)

    def test_L1_quiet_recovery_has_priority_over_starts_and_budget(self):
        p = core.defaults(0);p.update(l1_portion=20., l1_budget=20.)
        ex = Executor(p, long_feedback).run()
        self.assertIn('L2', ex.program.history)
        self.assertEqual(ex.done['terminal'], 'L4')

    def test_counter_wrap_is_supported_and_backward_reset_is_refused(self):
        for backward in (False, True):
            def feedback(ex, action, result):
                if result is not None and ex.phase == 'L1':
                    if action['kind'] == 'mode':result['starts'] = 0xfffffffc
                    else:result['starts'] = 0xfffffffb if backward else 2
                    result['pulse_age_ms'] = 1.
            ex = Executor(hook=feedback)
            if backward:
                with self.assertRaisesRegex(core.FactoryError, 'Backward'):ex.run()
            else:
                ex.run();self.assertEqual(ex.done['terminal'], 'L1')

    def test_long_path_moves_to_tool_specific_front_and_requires_verified_edges(self):
        for tool in (0, 1):
            p = core.defaults(tool);p.update(long_mm=180., e_speed=2., pulse_count=9, pulse_quiet=1.5)
            ex = Executor(p, long_feedback).run()
            front = [a for a in ex.in_phase('L2', 'move')]
            self.assertEqual((front[-1]['x'], front[-1]['y']),
                             (150.3, 2.5) if tool == 0 else (0., 0.))
            action = ex.in_phase('L3', 'feed_until_edges')[0]
            self.assertEqual((action['mm'], action['speed'], action['count'], action['quiet_s']),
                             (180., 2., 9, 1.5))
            self.assertNotIn('x', action)
            self.assertNotIn('y', action)
            self.assertEqual(ex.done['terminal'], 'L4')

    def test_L2_timeout_and_L3_budget_exhaustion_never_claim_success(self):
        for failed_kind, code in (('wait_a', 'l2_a_timeout'), ('feed_until_edges', 'l3_no_transport')):
            def feedback(ex, action, result):
                long_feedback(ex, action, result)
                if action['kind'] == failed_kind:result['ok'] = False
            ex = Executor(hook=feedback)
            with self.subTest(kind=failed_kind):
                with self.assertRaisesRegex(core.FactoryError, code):ex.run()
                self.assertIsNone(ex.done)
                self.assertNotIn('L4', ex.program.history)

    def test_failed_homing_or_partial_motion_never_reaches_extrusion(self):
        for failure in ('home', 'home_release', 'move'):
            def feedback(ex, action, result):
                if action['kind'] == failure:
                    return dict(ex.snap, ok=False)
            ex = Executor(hook=feedback)
            with self.subTest(kind=failure):
                with self.assertRaises(core.FactoryError):ex.run()
                self.assertFalse(any(a['kind'] == 'move' and a['e'] for a in ex.actions))
                if failure.startswith('home'):self.assertNotIn('HEAT', ex.program.history)

    def test_L2_positive_ack_requires_actual_A_high(self):
        def feedback(ex, action, result):
            long_feedback(ex, action, result)
            if action['kind'] == 'wait_a':result.update(ok=True, a=0)
        ex = Executor(hook=feedback)
        with self.assertRaisesRegex(core.FactoryError, 'l2_a_timeout'):ex.run()
        self.assertNotIn('L3', ex.program.history)

    def test_L3_ack_without_actual_edges_or_with_stale_edge_is_rejected(self):
        for stale in (False, True):
            def feedback(ex, action, result):
                long_feedback(ex, action, result)
                if action['kind'] == 'feed_until_edges':
                    if stale:result['pulse_age_ms'] = 2000.01
                    else:result['pulses'] = ex.snap['pulses']
            ex = Executor(hook=feedback)
            with self.assertRaisesRegex(core.FactoryError, 'l3_unverified_transport_result'):ex.run()

    def test_final_clean_uses_remaining_oscillation_sign_including_tuned_budget_parity(self):
        for tool, budget, expected in (
                (0, 100., [132.8, 32.8, 59.8]), (1, 100., [17.5, 117.5, 90.5]),
                (0, 50., [32.8, 132.8, 105.8]), (1, 50., [117.5, 17.5, 44.5])):
            p = core.defaults(tool);p['final_mm'] = budget
            ex = Executor(p, long_feedback).run()
            moves = ex.in_phase('L4', 'move')
            wipe = [a for a in moves if a['y'] == p['wipe_y']]
            self.assertEqual([a['x'] for a in wipe[-3:]], expected)
            self.assertTrue(all(a['e']==0 for a in wipe))
            if tool == 0:
                self.assertEqual(len(wipe),3)
                side=32.8 if expected[-1]<82.8 else 132.8
                self.assertEqual([(a['x'],a['y']) for a in moves[-2:]],
                                 [(side,152.5), (82.8,152.5)])
                self.assertEqual([(a['x'],a['y']) for a in ex.in_phase('L3','move')],
                                 [(132.8,152.5), (82.8,152.5)])
                if budget==100.:
                    self.assertEqual(round(sum(a['e'] for a in moves)*960),96394)

    def test_missing_final_starts_invokes_bounded_fallback_once_and_keeps_failure(self):
        def feedback(ex, action, result):
            long_feedback(ex, action, result)
            if result is not None and ex.phase == 'L4':result['starts'] = 108
        ex = Executor(hook=feedback)
        with self.assertRaisesRegex(core.FactoryError, 'fallback_completed') as error:ex.run()
        self.assertTrue(error.exception.details['sensor_empty_proven'])
        self.assertIsNone(ex.done)
        self.assertEqual(ex.program.history[-4:], ['U0', 'U1', 'U2', 'U3'])
        portions = [a for a in ex.actions if a['kind'] == 'retract_until_quiet']
        self.assertEqual([a['mm'] for a in portions], [30., 70.])
        self.assertEqual([a['reset_quiet'] for a in portions], [True, False])
        self.assertEqual(sum(a['kind'] == 'wait_unload' for a in ex.actions), 1)
        self.assertEqual([a for a in ex.actions if a['kind']=='mode'][-1]['mode'], 0)

    def test_T0_unload_quiet_retains_one_U2_pulse_and_A_timeout_does_not_prove_empty(self):
        def feedback(ex, action, result):
            long_feedback(ex, action, result)
            if result is None:return
            if ex.phase == 'L4':result['starts'] = 108
            if action['kind'] == 'retract_until_quiet':result.update(ok=True, sent_steps=1)
            if action['kind'] == 'retract_until_a':result.update(ok=False, a=1)
        ex = Executor(hook=feedback)
        with self.assertRaises(core.FactoryError) as error:ex.run()
        self.assertFalse(error.exception.details['sensor_empty_proven'])
        self.assertIn('U2', ex.program.history)
        u2=ex.in_phase('U2','retract_until_quiet')
        self.assertEqual([(a['mm'],a.get('min_steps')) for a in u2],[(1./960,1)])

    def test_t1_oem_unload_keeps_u2_first_pulse_when_u1_ends_on_silence(self):
        def feedback(ex, action, result):
            if action['kind']=='retract_until_quiet':
                result.update(ok=True, sent_steps=1)
        p = core.parameters(Command(TOOL='T1', SPEED_PROFILE='OEM', MATERIAL='Z-SUPPORT'))
        ex = Executor(p, feedback, operation='UNLOAD').run()
        self.assertEqual(ex.done['terminal'], 'UNLOAD')
        self.assertEqual(ex.in_phase('U0','wait_unload')[0]['timeout_s'], 10.)
        self.assertEqual(ex.in_phase('U1','retract_until_quiet')[0]['mm'], 50.)
        u2 = ex.in_phase('U2','retract_until_quiet')
        self.assertEqual([(a['mm'],a['min_steps']) for a in u2], [(1./960,1)])
        self.assertEqual(ex.in_phase('U3','retract_until_a')[0]['timeout_s'], 6.)
        self.assertEqual(core.procedure_modules('UNLOAD', 1, 'OEM'),
                         ('home','park','heat','select_tool','unload'))

    def test_disabled_fallback_stops_at_final_verification(self):
        p = core.defaults(0);p['fallback'] = 0
        def feedback(ex, action, result):
            long_feedback(ex, action, result)
            if result is not None and ex.phase == 'L4':result['starts'] = 108
        ex = Executor(p, feedback)
        with self.assertRaisesRegex(core.FactoryError, '^l4_starts_missing$'):ex.run()
        self.assertNotIn('U0', ex.program.history)

    def test_invalid_or_faulted_fresh_snapshots_abort_instead_of_advancing(self):
        for update in ({'fault':3}, {'starts':True}, {'a':None}, {'pulse_age_ms':-1},
                       {'pulse_age_ms':float('nan')}, {'pulses':-1}, {'b':2}):
            def feedback(ex, action, result):
                if result is not None:result.update(update)
            with self.subTest(update=update):
                with self.assertRaises(core.FactoryError):Executor(hook=feedback).run()


if __name__ == '__main__':unittest.main(verbosity=2)
