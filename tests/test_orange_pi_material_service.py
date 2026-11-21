"""Cassette profile persistence and operator moves on the real R7 host planner."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_orange_pi_factory_host as factory
import test_orange_pi_zortrax_menu as menu
from test_orange_pi_door_policy import Command as WireCommand
from door_test_host.inventure_material_service import MaterialService


class Command(WireCommand):
    def get_command_parameters(self):
        return dict(self.values)


class MaterialTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.fixture = factory.FactoryHostTests()
        self.h, _, _ = self.fixture.host('short', fast_motion=True, factory_motion_tools=3)
        self.h.printer.get_start_args = lambda: {'config_file': str(self.dir/'printer.cfg')}
        self.h.nozzle_service_target_max = 310.
        self.material = self.h.material_service = MaterialService(self.h)

    def command(self, action, **values):
        cmd = Command(ACTION=action, **values)
        self.material.command(cmd)
        return cmd.lines

    def save(self, tool='T0', **values):
        return self.command('SAVE', TOOL=tool, **values)

    def params(self, op, tool, **overrides):
        return self.h._factory_parameters(Command(TOOL=tool, SPEED_PROFILE='OEM', **overrides), op)

    def test_fluidd_cassette_choice_preserves_target_and_speed_for_either_tool(self):
        import test_orange_pi_control as control
        from test_orange_pi_zortrax_menu import Config, service_module
        self.h.gcode = control.gcode.GCodeDispatch(self.h.printer)
        out = []
        self.h.gcode.register_output_handler(out.append)
        menu = self.h.zortrax = service_module.ZortraxMenuService(self.h, Config())
        for action in ('LOAD', 'UNLOAD', 'JOG'):
            for index, tool in enumerate(('T0', 'T1'), start=1):
                with self.subTest(action=action, tool=tool):
                    self.h.control_reply.update(mode=1, armed=1)
                    self.command(action, TEMP='235', SPEED='1.8')
                    token = menu.view.token
                    self.assertTrue(any('prompt_button '+tool+'|' in line for line in out))
                    self.assertIsNone(self.h.load_test_context)
                    self.assertEqual(self.h.control_reply['mode'], 1)
                    menu.choose(token, index)
                    session = self.h.load_test_context
                    self.assertEqual(session['tool'], index-1)
                    self.assertEqual(session['target'], 235.)
                    self.assertIs(menu.active['object'], session)
                    self.assertEqual(self.h.control_reply['mode'], 2)
                    with self.assertRaises(ValueError):
                        menu.choose(token, index)
                    self.h.cmd_control_stop(Command())
                    menu._running_view()

    def test_service_handoff_refuses_native_heat_print_and_fault_before_switching(self):
        from types import SimpleNamespace
        h = self.h
        h.control_reply.update(mode=1, armed=1)
        for obj in ('heaters', 'virtual_sdcard', 'fault'):
            with self.subTest(obj=obj):
                if obj == 'heaters':
                    h.printer.objects[obj] = SimpleNamespace(heaters={'extruder': SimpleNamespace(
                        target_temp=210., last_pwm_value=.1)})
                elif obj == 'virtual_sdcard':
                    h.printer.objects[obj] = SimpleNamespace(is_active=lambda: True)
                else:
                    h.control_reply.update(fault=1, armed=0)
                before = list(h.device.packets)
                with self.assertRaises((RuntimeError, ValueError)):
                    self.command('LOAD', TOOL='T0', TEMP='235')
                self.assertEqual(h.device.packets, before)
                self.assertIsNone(h.load_test_context)
                if obj != 'fault':
                    h.printer.objects.pop(obj)

    def prompt(self):
        self.fixture.wait_phase(self.h, 'WAIT_FILAMENT')
        until = self.h.reactor.now + 40.
        while self.material.status()['prompt'] is None:
            self.assertLess(self.h.reactor.now, until)
            self.h.reactor.pause(self.h.reactor.now+.1)
        state = self.material.status()
        return dict(SESSION=state['session'], PROMPT=state['prompt'], TOOL=state['tool'])

    def test_partial_profiles_reload_and_reset_are_independent(self):
        self.save(LOAD_TEMP=235, LOAD_SPEED=2., UNLOAD_TEMP=250, UNLOAD_SPEED=.7)
        self.save('T1', LOAD_TEMP=260, UNLOAD_SPEED=2.5)
        self.h.material_service = self.material = MaterialService(self.h)
        self.assertEqual(self.params('AUTO', 'T0')['target'], 235.)
        self.assertEqual(self.params('UNLOAD', 'T0')['target'], 250.)
        self.assertEqual(self.params('AUTO', 'T1')['target'], 260.)
        self.assertEqual(self.params('UNLOAD', 'T1')['target'], 230.)
        self.assertEqual(self.params('UNLOAD', 'T1')['unload_speed1'], 2.5)
        self.save('T1', LOAD_TEMP='AUTO')
        self.assertEqual(self.params('AUTO', 'T1', MATERIAL='Z-SUPPORT')['target'], 235.)
        self.assertEqual(self.params('AUTO', 'T0')['target'], 235.)
        self.assertEqual(self.h.device.packets, [])
        details = json.loads(self.command('STATUS')[0].split(' ', 1)[1])
        self.assertEqual(details['effective']['T0']['unload_speed2'], .7)

    def test_invalid_write_preserves_disk_and_running_settings(self):
        self.save(LOAD_TEMP=230)
        data=self.material.path.read_bytes()
        for args in ({'LOAD_SPEED': 'nan'}, {'UNLOAD_TEMP':311}, {'LOAD_TEMP':True},
                     {'UNLOAD_SPEED':0}, {'LOAD_TEMP':250, 'TYPO':3}):
            with self.subTest(args=args), self.assertRaises(RuntimeError):
                self.save(**args)
            self.assertEqual(self.material.path.read_bytes(),data)
        with patch.object(self.h, '_write_calibration_file', side_effect=OSError('write failed')):
            with self.assertRaisesRegex(RuntimeError,'write failed'):
                self.save(LOAD_TEMP=245)
        self.assertEqual(self.params('AUTO','T0')['target'],230)
        self.assertEqual(self.h.device.packets, [])

    def test_precedence_geometry_and_fallback(self):
        self.save(LOAD_SPEED=2, UNLOAD_SPEED=.8, LOAD_TEMP=245)
        p=self.params('AUTO','T0',TARGET=235,SPEED=1.5)
        self.assertEqual(p['target'],235)
        self.assertEqual(p['e_speed'],1.5)
        self.assertAlmostEqual(p['purge_speed'] * 2073 / (36 * 960),1.5)
        self.assertEqual(p['travel_speed'],8000/60)
        self.assertEqual(p['switch_speed'],250/60)
        self.assertEqual(self.params('CLEAN','T0')['target'],230)
        self.assertEqual(self.params('UNLOAD','T0',UNLOAD_SPEED1=.5)['unload_speed1'],.5)
        self.assertEqual(self.params('UNLOAD','T0')['unload_speed2'],.8)
        self.assertEqual(self.params('MANUAL','T0',MANUAL_DIR='UNLOAD')['e_speed'],.8)
        self.assertEqual(self.params('AUTO','T0',SPEED=10)['purge_speed'],50.)

    def test_each_nozzle_homes_once_then_feeds_at_bin_and_finishes(self):
        for tool in ('T0','T1'):
            with self.subTest(tool=tool):
                self.command('LOAD',TOOL=tool,TEMP=230,SPEED=1.7)
                identity = self.prompt()
                f=self.h.load_test_context['factory']
                self.assertEqual(tuple(f['position'][:2]),(f['parameters']['center_x'],f['parameters']['center_y']))
                marker=len(self.h.device.packets)
                self.command('MORE',DIR='LOAD',MM=5,**identity)
                with self.assertRaises(RuntimeError):
                    self.command('MORE',DIR='LOAD',MM=5,**identity)
                next_id=self.prompt()
                self.assertGreater(next_id['PROMPT'],identity['PROMPT'])
                legs=[p for p in self.h.device.packets[marker:] if p[:2]==('motion',1)]
                self.assertEqual(len(legs),1)
                self.assertEqual(legs[0][3:8],(0,0,0,4800,1632))
                self.assertEqual(legs[0][2]>>3&1,int(tool[-1]))
                self.command('FINISH',**next_id)
                last=self.fixture.finish(self.h)
                self.assertEqual(last['phase'],'done',last.get('reason'))
                self.assertEqual(last['factory']['history'].count('HOME'),1)
                self.assertFalse(self.h.device.active())
                self.h.temperatures[int(tool[-1])]=25.

    def test_after_unload_manual_retract_and_forward_keep_direction_and_owner(self):
        self.save('T1', LOAD_SPEED=1.5, UNLOAD_SPEED=.8, UNLOAD_TEMP=230)
        self.command('UNLOAD',TOOL='T1')
        first=self.prompt()
        marker=len(self.h.device.packets)
        self.command('MORE',DIR='UNLOAD',MM=3,**first)
        second=self.prompt()
        with self.assertRaises(RuntimeError):
            self.command('FINISH',**first)
        self.command('MORE',DIR='LOAD',MM=2,**second)
        final=self.prompt()
        self.command('FINISH',**final)
        last=self.fixture.finish(self.h)
        self.assertEqual(last['phase'],'done',last.get('reason'))
        legs=[p for p in self.h.device.packets[marker:] if p[:2]==('motion',1) and p[6]]
        self.assertEqual([(p[2]>>3&1,p[6],p[7]) for p in legs],[(0,2880,768),(1,1920,1440)])

    def test_prompt_expires_and_does_not_accept_cancelled_or_stale_commands(self):
        self.command('LOAD',TOOL='T1')
        identity=self.prompt()
        with self.assertRaises(RuntimeError):
            self.command('MORE',DIR='LOAD',TOOL='T0',SESSION=identity['SESSION'],PROMPT=identity['PROMPT'])
        for mm in (0,21,'nan'):
            with self.assertRaises(RuntimeError):
                self.command('MORE',MM=mm,**identity)
        until=self.h.reactor.now
        last=self.fixture.finish(self.h,limit=125.)
        self.assertGreaterEqual(self.h.reactor.now-until,119.)
        self.assertEqual(last['phase'],'done')
        self.assertFalse(self.h.device.active())
        with self.assertRaises(RuntimeError):
            self.command('MORE',DIR='LOAD',**identity)

    def test_standalone_jog_uses_direction_settings_and_short_move_timeout(self):
        self.save('T1', LOAD_TEMP=235, UNLOAD_TEMP=245)
        for direction, target, bit in [('LOAD', 235, 1), ('UNLOAD', 245, 0)]:
            with self.subTest(direction=direction):
                self.h.temperatures[1] = 25.
                start = len(self.h.device.packets)
                self.command('JOG', TOOL='T1', DIR=direction, SPEED=.1, MM=5)
                self.assertEqual(self.h.load_test_context['target'], target)
                last = self.fixture.finish(self.h)
                self.assertEqual(last['phase'], 'done', last.get('reason'))
                self.assertEqual(last['factory']['history'].count('HOME'), 1)
                legs = [p for p in self.h.device.packets[start:] if p[:2] == ('motion', 1) and p[6]]
                self.assertEqual([(p[2] >> 3 & 1, p[6], p[7]) for p in legs], [(bit, 4800, 96)])
                self.assertFalse(self.h.device.active())

    def test_stop_while_waiting_cancels_prompt_and_heat(self):
        self.command('LOAD', TOOL='T0')
        identity = self.prompt()
        self.h.cmd_control_stop(Command())
        self.assertIsNone(self.material.status()['prompt'])
        with self.assertRaises(RuntimeError):
            self.command('MORE', **identity)
        self.h.reactor.pause(self.h.reactor.now + 6.)
        self.assertFalse(self.h.device.active())
        self.assertEqual(self.h.device.output(6)['duty'], 0)

    def test_oled_uses_saved_settings_and_submits_to_the_same_prompt(self):
        self.h, service = menu.MenuFixture().host()
        self.h.factory_motion_abi = 1
        self.h.factory_motion_tools = 3
        self.h.factory_motion_max_hz = 125000
        self.h.printer.get_start_args = lambda: {'config_file': str(self.dir/'printer.cfg')}
        self.h.nozzle_service_target_max = 310.
        self.material = self.h.material_service = MaterialService(self.h)
        self.save('T1', LOAD_TEMP=235, LOAD_SPEED=1.5)
        service.open('load_cartridge_t1')
        self.assertIn('235C', service.view.title)
        service.choose(service.view.token, confirm=True)
        self.assertEqual(self.h.load_test_context['target'], 235.)
        self.prompt()
        service._running_view()
        labels = [label for label, _ in service.view.options]
        service.choose(service.view.token, index=labels.index('Feed 5 mm'))
        self.prompt()
        service._running_view()
        labels = [label for label, _ in service.view.options]
        service.choose(service.view.token, index=labels.index('Finish / heaters off'))
        self.assertEqual(self.fixture.finish(self.h)['phase'], 'done')
        self.assertFalse(self.h.device.active())

    def test_noninteractive_legacy_and_oem_failure_do_not_wait(self):
        self.command('LOAD',TOOL='T0',INTERACTIVE=0)
        self.assertNotIn('WAIT_FILAMENT',self.fixture.finish(self.h)['factory']['history'])
        self.h.device.home_fail=4
        self.h.temperatures[0]=25.
        self.command('LOAD',TOOL='T0')
        last=self.fixture.finish(self.h)
        self.assertEqual(last['phase'],'failed')
        self.assertIsNone(self.material.status()['prompt'])
        self.assertFalse(self.h.device.active())


if __name__ == '__main__':
    unittest.main()
