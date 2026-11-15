"""R0323 nozzle Z grouping and the diagnostic result, without hardware."""
import json
import types
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
        self.fail_selector = False
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
                pressed = not (self.fail_selector and action.get('leg') == 'selector_press')
                response = dict(ok=pressed, a=0, b=0, starts=0, pulses=0,
                                pulse_age_ms=4294967295, fault=0)


class DualNozzleDevice(z_host_support.ZDevice):
    def __init__(self, host):
        super().__init__(host)
        self.contact_at = None
        self.motion_starts = []
        self.power_started = {}
        self.stepper_enabled = False
        self.switch_height = None

    def move(self, args):
        if args[0] == 1:
            self.motion_starts.append(dict(t=self.now(), arguments=tuple(args),
                duty=(self.output(6)['duty'], self.output(7)['duty']),
                temperatures=tuple(self.h.temperatures[:2])))
            self.stepper_enabled = True
        return super().move(args)

    def terminate(self, reason, heaters=True):
        self.stepper_enabled = False
        super().terminate(reason, heaters)

    def query_factory(self, args):
        if args[0] == 5:
            self._settle()
            retreat = self.z_moves[-1]
            assert self.stepper_enabled and not self.active_motion() and not self.contact
            assert retreat['direction'] == -1 and retreat['sent'] == 10 * Z_STEPS_PER_MM
            self.switch_height = self.z
        return super().query_factory(args)

    def refresh_heaters(self, context):
        # Slow enough that firmware must finish the coarse approach and then
        # wait for T1. Both independent PWM leases must continue during travel.
        for heater in context['factory']['dual_heaters']:
            tool = heater['tool']
            if tool not in self.power_started and self.output(6 + tool)['duty']:
                self.power_started[tool] = self.now()
            if tool in self.power_started:
                elapsed = self.now() - self.power_started[tool]
                target = min(heater['target'], 25. + elapsed * (14. if not tool else 6.))
                self.h.temperatures[tool] = target
                self.h.raw_adc[tool] = round(target * 4095. / 660.)


class NozzleZTests(unittest.TestCase):
    def _record_oled_holds(self,host,initially_held=False):
        calls=[]
        host.thermal_display_held=initially_held
        host.printer.objects['inventure_oled']=types.SimpleNamespace(set_thermal_test_hold=calls.append)
        return calls

    def _dual_host(self):
        host = z_host_support.ZHomeHostTests().host()
        host.device = device = DualNozzleDevice(host)
        host._fresh_inputs = device.inputs
        host.commands.update(control=Wire(device.control), output=Wire(device.out),
            motion=Wire(device.move), motion_driver=Wire(device.driver),
            factory_session=Wire(device.query_factory))
        return host

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

    def _run_owned_samples(self,t1_failure=None,service_loss=False):
        host=self._dual_host()
        holds=self._record_oled_holds(host)
        device=host.device
        device.z=20*Z_STEPS_PER_MM
        starts=[]
        state={}

        def local_refine(args):
            self.assertTrue(host.thermal_display_held)
            start,owner,token,span,interval,repeats=args
            if start:
                device._settle()
                tool=device.session['tool']
                self.assertEqual(owner,device.session['token'])
                self.assertLess(device.z,147*Z_STEPS_PER_MM)
                self.assertEqual((interval,repeats),(4000,201))
                self.assertTrue(all(198.<=value<=202. for value in host.temperatures[:2]))
                starts.append((tool,device.z,owner,token))
                state.update(owner=owner,token=token,tool=tool,active=1,status=0,
                             reject=0,net_steps=0,best_steps=0,cycles=0,
                             non_improvements=0,up_steps=0,down_steps=0)
                if service_loss and tool==0 and len(starts)==2:
                    running=dict(state)
                    state.update(active=0,status=4,cycles=84,net_steps=240,
                                 best_steps=238,up_steps=460,down_steps=220)
                    # Model a control-guard trip mid-probe: the next read
                    # sees OFF before a factory lease renewal can run.
                    host.control_reply.update(mode=0,armed=0)
                    host.uart_buffer_stats=dict(dropped=2,received_at=host.reactor.now)
                    device.terminate(6)
                    return running
                if tool==1 and t1_failure is not None:
                    running=dict(state)
                    status,cycles=t1_failure
                    state.update(active=0,status=status,cycles=cycles,net_steps=210,
                                 best_steps=207,up_steps=1240,down_steps=1030)
                    # MCU terminates the factory session between the START
                    # response and the next host lease renewal.
                    device.terminate(2)
                    return running
            else:
                self.assertEqual((owner,token),(state['owner'],state['token']))
                if state['active']:
                    rise=250-40*state['tool']
                    device.z+=rise
                    device.motion=None
                    state.update(active=0,status=1,net_steps=rise,best_steps=rise,
                                 cycles=202,non_improvements=201,up_steps=rise+202,down_steps=202)
            return dict(state)

        start=Command(ACTION='START',ACK='BOTH_NOZZLES_OVER_SAME_PAD')
        with self.assertRaisesRegex(RuntimeError,'NOZZLE-Z'):host.cmd_nozzle_z_test(start)
        host.nozzle_z_test_abi=1
        host.commands['nozzle_z_test']=Wire(local_refine)
        with self.assertRaisesRegex(RuntimeError,'NOZZLE-Z-DUAL'):
            host.cmd_nozzle_z_test(start)
        host.nozzle_z_dual_abi=1
        with self.assertRaisesRegex(RuntimeError,'T1 pad coordinates must be given together'):
            host.cmd_nozzle_z_test(Command(ACTION='START',ACK='BOTH_NOZZLES_OVER_SAME_PAD',T1_PAD_X=72))
        self.assertEqual(device.packets,[])
        self.assertEqual(holds,[])
        host.cmd_nozzle_z_test(start)
        while host.nozzle_z_context is not None:
            self.assertLess(host.reactor.now,600.)
            self.assertTrue(host.thermal_display_held)
            self.assertEqual(holds,[True])
            host.reactor.pause(host.reactor.now+.1)
        self.assertEqual(holds,[True,False])
        self.assertFalse(host.thermal_display_held)
        report=json.loads(start.lines[-1].split(' ',1)[1])
        return report,device,starts

    def test_host_runs_owned_samples_and_cuts_both_heaters(self):
        report,device,starts=self._run_owned_samples()
        self.assertEqual(report['phase'],'done',report)
        self.assertEqual(report['measurement']['delta_steps'],40)
        self.assertEqual(report['tools']['T0']['pad_raw_xy'],[82.8,68.])
        self.assertEqual(report['tools']['T1']['pad_raw_xy'],[72.,68.])
        self.assertEqual(report['tools']['T0']['selector_press'],
            dict(start_raw_xy=[139.5,154.5],target_raw_xy=[154.5,154.5],reason='completed'))
        self.assertEqual(report['tools']['T1']['selector_press'],
            dict(start_raw_xy=[15.,154.5],target_raw_xy=[0.,154.5],reason='completed'))
        self.assertEqual([(tool,z) for tool,z,_,_ in starts],
                         [(tool,137*Z_STEPS_PER_MM) for tool in (0,0,1,1,1)])
        self.assertEqual(len({token for _,_,_,token in starts}),5)
        self.assertEqual(len({owner for _,_,owner,_ in starts}),1)
        self.assertEqual({packet[9] for packet in device.factory_packets},{starts[0][2]})
        self.assertEqual([packet[0] for packet in device.factory_packets
                          if packet[0] in (1,4,5)],[4,5])
        self.assertEqual(device.switch_height,127*Z_STEPS_PER_MM+250)
        self.assertEqual((device.z,device.output(6)['duty'],device.output(7)['duty']),
                         (127*Z_STEPS_PER_MM+210,0,0))
        homing = [move for move in device.motion_starts
                  if move['arguments'][9]==move['arguments'][10] and move['arguments'][9] in (1,2,4)]
        self.assertEqual([move['arguments'][9] for move in homing],[2,1,4])
        self.assertEqual(homing[0]['duty'],(0,0))
        self.assertEqual(homing[1]['duty'],(0,0))
        self.assertTrue(all(homing[2]['duty']))
        self.assertLess(max(device.power_started.values()),homing[2]['t']+1.)
        coarse_rise = next(move for move in device.motion_starts
                           if move['arguments'][4]>100*Z_STEPS_PER_MM and move['arguments'][1]&4)
        self.assertTrue(all(coarse_rise['duty']))
        self.assertLess(coarse_rise['temperatures'][1], 198.)

    def test_host_reports_t1_probe_failure_after_factory_owner_disappears(self):
        for status,cycles,reason in ((5,512,'z_contact_cycle_limit'),
                                     (4,458,'z_contact_mcu_guard')):
            with self.subTest(status=status):
                report,device,starts=self._run_owned_samples((status,cycles))
                self.assertEqual(report['phase'],'failed',report)
                self.assertEqual((report['tool'],report['reason']),(1,reason))
                self.assertEqual(len(report['tools']['T0']['readings']),2)
                self.assertNotIn('measurement',report)
                self.assertIn('factory guard/ownership rejected',report['details']['secondary_error'])
                row=report['details']['nozzle_z_state']
                self.assertEqual((row['tool'],row['status'],row['cycles']),(1,status,cycles))
                self.assertEqual((row['owner'],row['token']),starts[-1][2:])
                self.assertEqual([tool for tool,_,_,_ in starts],[0,0,1])
                self.assertEqual((device.output(6)['duty'],device.output(7)['duty']),(0,0))

    def test_service_loss_during_second_t0_sample_preserves_diagnostics(self):
        report,device,starts=self._run_owned_samples(service_loss=True)
        self.assertEqual((report['phase'],report['tool'],report['reason']),
                         ('failed',0,'z_contact_mcu_guard'))
        self.assertEqual([tool for tool,_,_,_ in starts],[0,0])
        self.assertEqual(report['tools'],{})
        details=report['details']
        self.assertIn('Select INVENTURE_CONTROL MODE=SERVICE first',details['secondary_error'])
        self.assertEqual(details['control_before_stop']['mode'],'OFF')
        self.assertEqual(details['last_cable_pd11_raw'],0)
        self.assertEqual(details['uart']['dropped'],2)
        self.assertEqual(details['nozzle_z_state']['cycles'],84)
        self.assertEqual((device.output(6)['duty'],device.output(7)['duty']),(0,0))

    def test_active_sample_paces_reports_and_leases_but_keeps_heater_and_cable_checks(self):
        host=self._dual_host()
        device=host.device
        host.nozzle_z_test_abi=1
        host.nozzle_z_dual_abi=1
        times={name:[] for name in ('control','factory_session','T0','T1','probe','pid')}
        for name in ('control','factory_session','output'):
            callback=host.commands[name].callback

            def record(args,key=name,send=callback):
                if key!='output':times[key].append(host.reactor.now)
                elif args[0]==0:times['T%d'%(args[1]-6)].append(host.reactor.now)
                return send(args)

            host.commands[name]=Wire(record)

        pid=host._load_test_pid

        def record_pid(context,temperature,now):
            times['pid'].append(now)
            return pid(context,temperature,now)

        host._load_test_pid=record_pid
        sample=[]
        state={}

        def refine(args):
            start,owner,token,span,interval,repeats=args
            if start:
                sample.append(host.reactor.now)
                state.update(owner=owner,token=token,tool=0,active=1,status=0,
                             reject=0,cycles=0,non_improvements=0,best_steps=0,
                             net_steps=0,up_steps=0,down_steps=0)
            else:
                self.assertEqual((owner,token),(state['owner'],state['token']))
                times['probe'].append(host.reactor.now)
                if host.status['cable']:
                    state.update(active=0,status=6)
            return dict(state)

        host.commands['nozzle_z_test']=Wire(refine)
        host.cmd_nozzle_z_test(Command(ACTION='START',ACK='BOTH_NOZZLES_OVER_SAME_PAD'))
        while not sample:
            self.assertLess(host.reactor.now,250.)
            host.reactor.pause(host.reactor.now+.05)
        for trace in times.values():trace.clear()
        host._control_poll(host.reactor.now)
        self.assertEqual(times['control'],[])
        host.reactor.pause(sample[0]+1.05)
        self.assertEqual(len(times['probe']),3)
        self.assertEqual(times['control'],times['probe'])
        self.assertEqual(times['factory_session'],times['probe'])
        self.assertEqual(times['T0'],times['T1'])
        self.assertGreaterEqual(len(times['T0']),3)
        self.assertEqual(len(times['pid']),20)
        for trace,period in ((times['probe'],.3),(times['T0'],.3),(times['pid'][::2],.1)):
            for before,after in zip(trace,trace[1:]):
                self.assertAlmostEqual(after-before,period)

        # An open PD11 stops the service run on the next heater tick, before
        # the probe's next scheduled status/renewal request would be due.
        host.status['cable']=1
        host.reactor.pause(sample[0]+1.15)
        self.assertIsNone(host.load_test_context)
        self.assertEqual(len(times['factory_session']),3)
        self.assertAlmostEqual(times['probe'][-1]-sample[0],1.1)
        host.reactor.pause(host.reactor.now+.3)
        self.assertIn('Extruder cable',host.nozzle_z_last['reason'])
        self.assertEqual(host.nozzle_z_last['details']['last_cable_pd11_raw'],1)
        self.assertEqual((device.output(6)['duty'],device.output(7)['duty']),(0,0))

    def test_t1_inherits_t0_pad_correction_and_accepts_explicit_pair(self):
        for override,expected in (({},(73.2,67.)),
                                  (dict(T1_PAD_X=72.9,T1_PAD_Y=67.25),(72.9,67.25))):
            with self.subTest(override=override):
                host=z_host_support.ZHomeHostTests().host()
                holds=self._record_oled_holds(host)
                host.nozzle_z_test_abi=1
                host.commands['nozzle_z_test']=Wire(lambda args: self.fail('Probe started during PREPARE'))
                prepare=Command(ACTION='PREPARE',TOOL='T1',T0_PAD_X=84,T0_PAD_Y=67,**override)
                host.cmd_nozzle_z_test(prepare)
                settings=host.nozzle_z_context['parameters'][1][1]
                self.assertAlmostEqual(settings['z_probe_x'],expected[0])
                self.assertEqual(settings['z_probe_y'],expected[1])
                while host.nozzle_z_context is not None:
                    self.assertLess(host.reactor.now,250.)
                    host.reactor.pause(host.reactor.now+.1)
                self.assertEqual(host.nozzle_z_last['phase'],'prepared')
                self.assertFalse(host.thermal_display_held)
                self.assertEqual(holds,[])
                self.assertFalse(any(packet[:2]==('output',1) for packet in host.device.packets))

    def test_stop_restores_the_prior_oled_hold(self):
        for initially_held in (False,True):
            with self.subTest(initially_held=initially_held):
                host=z_host_support.ZHomeHostTests().host()
                holds=self._record_oled_holds(host,initially_held)
                host.nozzle_z_test_abi=1
                host.nozzle_z_dual_abi=1
                host.commands['nozzle_z_test']=Wire(lambda args: self.fail('Unexpected probe'))
                host.cmd_nozzle_z_test(Command(ACTION='START',ACK='BOTH_NOZZLES_OVER_SAME_PAD'))
                host.reactor.pause(host.reactor.now+.5)
                self.assertTrue(host.thermal_display_held)
                host.cmd_nozzle_z_test(Command(ACTION='STOP'))
                self.assertIsNone(host.load_test_context)
                self.assertEqual(host.nozzle_z_last['phase'],'stopped')
                host.reactor.pause(host.reactor.now+.5)
                self.assertEqual(host.thermal_display_held,initially_held)
                if initially_held:
                    self.assertNotIn(False,holds)
                else:
                    self.assertEqual(holds,[True,False])

    def test_turning_off_the_other_heater_cancels_dual_calibration(self):
        host=z_host_support.ZHomeHostTests().host()
        self._record_oled_holds(host)
        host.nozzle_z_test_abi=host.nozzle_z_dual_abi=1
        host.commands['nozzle_z_test']=Wire(lambda args: self.fail('Unexpected probe'))
        host.cmd_nozzle_z_test(Command(ACTION='START',ACK='BOTH_NOZZLES_OVER_SAME_PAD'))
        host.reactor.pause(host.reactor.now+.5)
        host.cmd_output(Command(ACTION='OFF',CHANNEL='T1'))
        self.assertIsNone(host.load_test_context)
        self.assertEqual(host.nozzle_z_last['reason'],'heater_off')
        self.assertFalse(host.thermal_display_held)
        before=len(host.device.packets)
        host.reactor.pause(host.reactor.now+.5)
        self.assertFalse(any(p[:2]==('output',1) for p in host.device.packets[before:]))

    def test_failing_stages_cannot_report_success(self):
        for rises, fault, error in (
                ((), 'fail_selector', 'Factory motion did not complete'),
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
                if fault == 'fail_selector':
                    self.assertEqual(model.position,0)
                    self.assertEqual(model.sample_starts,[])

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
