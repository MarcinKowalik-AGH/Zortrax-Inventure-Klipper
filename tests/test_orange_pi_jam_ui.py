"""Filament recovery decisions share the same run across the panel and Fluidd."""
import configparser
from pathlib import Path
import types
import unittest

from jinja2 import Environment
from test_orange_pi_zortrax_menu import MenuFixture, core, menu_module


class JamUI(MenuFixture, unittest.TestCase):
    def setUp(self):
        self.board, self.menu = self.host()
        self.run = dict(run_id=None, prompt_id=0, phase='idle', tool=None,
                        source='UNKNOWN', active=False, allowed_actions=['SOURCE'],
                        sources={'T0': 'CASSETTE', 'T1': 'UNKNOWN'}, message='Ready')
        self.calls = []
        self.board.filament_jam = types.SimpleNamespace(
            cmd=self.command, get_status=lambda now: self.run.copy())
        self.output = []
        self.board.gcode.register_output_handler(self.output.append)

    def command(self, cmd):
        self.calls.append(cmd.get_command_parameters())
        if cmd.get('ACTION') == 'SOURCE' and self.run['active']:
            self.run.update(source=cmd.get('SOURCE'), prompt_id=2, phase='recovering',
                            allowed_actions=['CANCEL'], message='Checking transport')
        if cmd.get('ACTION') == 'CANCEL':
            self.run.update(active=False, phase='cancelled', allowed_actions=[],
                            message='Recovery cancelled. Print paused.')

    def tick(self):
        self.menu._tick(self.board.reactor.monotonic())

    def choose(self, label):
        index = [option[0] for option in self.menu.view.options].index(label)
        self.menu.choose(self.menu.view.token, index=index)

    def simulate_jam(self):
        self.run.update(phase='waiting', tool='T1', run_id='job1', prompt_id=1, active=True,
                        allowed_actions=['SOURCE', 'CANCEL'], message='Select the T1 material source.')
        self.tick()

    def test_source_setup_and_report_are_separate_explicit_choices(self):
        self.assertIn('filament_jam', [item.action for item in menu_module.MENU['material'][2]])
        self.menu.open('filament_jam')
        self.choose('T1 source: UNKNOWN')
        self.assertEqual(self.calls, [])
        self.choose('External spool')
        self.assertEqual(self.calls, [dict(ACTION='SOURCE', TOOL='T1', SOURCE='EXTERNAL')])
        self.run.update(allowed_actions=['REPORT'])
        self.menu.open('filament_jam')
        self.choose('Report filament jam')
        self.assertEqual(len(self.calls), 1)
        self.menu.choose(self.menu.view.token, confirm=True)
        self.assertEqual(self.calls[-1], dict(ACTION='REPORT'))

    def test_missing_and_confirmed_break_use_the_same_commands_in_both_interfaces(self):
        self.run.update(allowed_actions=['REPORT', 'EXCHANGE', 'MISSING', 'BROKEN'])
        for label, action in (('Broken filament', 'BROKEN'), ('Material missing', 'MISSING')):
            self.menu.open('filament_jam')
            self.choose(label)
            self.assertEqual(self.calls[-1], dict(ACTION=action))
        self.simulate_jam()
        self.run.update(reason='EXTRUDER_JAM', stage='R1', allowed_actions=['BROKEN', 'CANCEL'],
                        prompt_id=3, message='Inspect the guide tube.')
        self.tick()
        self.assertTrue(any('Broken filament|INVENTURE_FILAMENT_JAM ACTION=BROKEN' in m
                            or ('prompt_button Broken filament|' in m and 'ACTION=BROKEN' in m)
                            for m in self.output))
        self.choose('Broken filament')
        self.assertEqual(self.calls[-1], dict(ACTION='BROKEN', RUN_ID='job1', PROMPT_ID=3))

    def test_original_warning_numbers_require_the_corresponding_evidence(self):
        title = self.menu._jam_title
        base = dict(reason='EXTRUDER_JAM', phase='waiting', stage='R1')
        self.assertEqual(title(dict(base, tool='T0')), '#174 Extruder jam')
        self.assertEqual(title(dict(base, tool='T1')), 'Extruder jam')
        self.assertEqual(title(dict(base, tool='T1', pcb_class=1)), '#175 Extruder jam')
        self.assertEqual(title(dict(base, tool='T1', pcb_class=2)), '#178 Overheating')
        self.assertEqual(title(dict(base, tool='T0', phase='recovering')), 'Extruder jam')
        for tool, number in [('T0', 163), ('T1', 164)]:
            self.assertEqual(title(dict(tool=tool, reason='CARTRIDGE_ERROR')), '#%d Cartridge error' % number)

    def test_idle_exchange_menu_uses_the_same_fluidd_step(self):
        self.menu.open('filament_jam')
        self.choose('Replacement steps')
        self.choose('Unload filament')
        self.choose('T1 / External spool')
        self.choose('260 C')
        calls = []
        def dispatch(command):
            calls.append(command)
            self.board.load_test_context = {'factory': {}, 'tool': 1}
        self.board.gcode.run_script_from_command = dispatch
        self.board._load_test_report = lambda ctx: dict(phase='preparing', tool='T1',
            factory=dict(operation='MANUAL', phase='HOME'))
        self.menu.choose(self.menu.view.token, confirm=True)
        self.assertEqual(calls, ['INVENTURE_JAM_SERVICE_STEP TOOL=T1 SOURCE=EXTERNAL STEP=UNLOAD TARGET=260'])

    def test_idle_service_test_explicitly_selects_nozzle_source_and_temperature(self):
        self.menu.open('filament_jam')
        self.choose('Test recovery at idle')
        self.assertNotIn('T0 / External spool', [x[0] for x in self.menu.view.options])
        self.choose('T1 / External spool')
        self.choose('260 C')
        self.assertEqual(self.board.device.packets, [])
        self.assertIn(('Heat to 260 C', None), self.menu.view.options)
        service = []
        def start(cmd):
            service.append(cmd.get_command_parameters())
            self.board.load_test_context = {'factory': {}, 'tool': 1}
        self.board.cmd_procedure = start
        self.board._load_test_report = lambda ctx: dict(phase='preparing', tool='T1',
            factory=dict(operation='JAM_EXTERNAL', phase='HOME'))
        self.menu.choose(self.menu.view.token, confirm=True)
        self.assertEqual(service, [dict(ACTION='JAM_EXTERNAL', TOOL='T1', TARGET=260, SPEED_PROFILE='OEM')])
        self.assertIn(('T1 / EXTERNAL test', None), self.menu.view.options)

    def test_external_run_presents_same_one_time_source_choice_on_both_interfaces(self):
        self.simulate_jam()
        self.tick()
        self.assertEqual(self.output.count('// action:prompt_show'), 1)
        self.assertTrue(any('SOURCE=EXTERNAL' in msg and 'RUN_ID=job1' in msg
                            and 'PROMPT_ID=1' in msg and 'TOOL=T1' in msg for msg in self.output))
        self.assertEqual(self.menu.active['backend'], 'jam')
        self.menu.choose(self.menu.view.token, index=0)
        self.assertEqual(self.calls, [])
        self.choose('Use external spool')
        self.assertEqual(self.calls[-1], dict(ACTION='SOURCE', TOOL='T1', SOURCE='EXTERNAL',
                                             RUN_ID='job1', PROMPT_ID=1))
        self.tick()
        self.assertEqual(self.output[-1], '// action:prompt_end')
        self.assertNotIn('Flow verified / resume', [x[0] for x in self.menu.view.options])

    def test_stale_confirmation_does_not_transfer_to_new_prompt(self):
        self.simulate_jam()
        self.run.update(allowed_actions=['CONFIRM','CANCEL'], prompt_id=2,
                        message='Verify flow at the nozzle.')
        self.tick()
        self.run['prompt_id'] = 3
        with self.assertRaisesRegex(core.MenuError, 'expired'):
            self.choose('Flow verified / resume')
        self.assertEqual(self.calls, [])
        self.tick()
        self.choose('Flow verified / resume')
        self.assertEqual(self.calls[-1], dict(ACTION='CONFIRM', RUN_ID='job1', PROMPT_ID=3))

    def test_oled_shows_model_and_support_cartridge_warnings_in_first_visible_row(self):
        for tool, code in [('T0', 176), ('T1', 177)]:
            self.simulate_jam()
            self.run.update(tool=tool, reason='CASSETTE_JAM', source='CASSETTE', prompt_id=2,
                            allowed_actions=['RETRY','CANCEL'])
            self.tick()
            panel = self.menu.view_status()
            self.assertEqual(panel['labels'][0], '%s #%d Cartridge jam' % (tool, code))
            self.assertEqual(panel['window'], 0)
            self.assertTrue(any('action:prompt_begin #%d' % code in msg for msg in self.output))

    def test_back_and_cancel_leave_job_paused(self):
        self.simulate_jam()
        self.menu.panel_back()
        self.assertEqual(self.calls, [])
        self.menu.choose(self.menu.view.token, confirm=True)
        self.assertEqual(self.calls, [dict(ACTION='CANCEL', RUN_ID='job1', PROMPT_ID=1)])
        self.assertIn('paused', self.menu.view.data['reason'])
        self.tick()
        self.assertEqual(self.output[-1], '// action:prompt_end')


class JamMacroTests(unittest.TestCase):
    def setUp(self):
        config = configparser.RawConfigParser()
        config.read(Path(__file__).resolve().parents[1] / 'config/INVENTURE_FILAMENT_JAM.cfg')
        env = Environment(variable_start_string='{', variable_end_string='}')
        self.macros = {s.split()[1]: env.from_string(config.get(s, 'gcode')) for s in config.sections()}

    def render(self, name, run=None, **params):
        def error(msg):
            raise ValueError(msg)
        if run is None:
            run = dict(active=True, prompt_id=4, run_id='A', allowed_actions=['RETRY', 'CANCEL'])
        return self.macros['INVENTURE_JAM_' + name].render(
            params=params, printer=dict(inventure_mcu=dict(filament_jam=run)), action_raise_error=error).strip()

    def test_missing_and_broken_macros_bind_to_the_current_prompt(self):
        self.assertEqual(self.render('MISSING'), 'INVENTURE_FILAMENT_JAM ACTION=MISSING')
        self.assertEqual(self.render('BROKEN', run={'active': False}), 'INVENTURE_FILAMENT_JAM ACTION=BROKEN')
        state = dict(active=True, allowed_actions=['BROKEN'], run_id='B', prompt_id=2)
        self.assertEqual(self.render('BROKEN', run=state), 'INVENTURE_FILAMENT_JAM ACTION=BROKEN RUN_ID=B PROMPT_ID=2')
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.render('BROKEN')

    def test_service_uses_an_explicit_material_source_tool_and_temperature(self):
        for params in [{}, dict(TOOL='T0', SOURCE='EXTERNAL', TARGET=360),
                       dict(TOOL='T2', SOURCE='CASSETTE', TARGET=240),
                       dict(TOOL='T1', SOURCE='UNKNOWN', TARGET=230)]:
            with self.assertRaises(ValueError):
                self.render('SERVICE_TEST', **params)
        self.assertEqual(self.render('SERVICE_TEST', TOOL='T1', SOURCE='EXTERNAL', TARGET=265),
                         'INVENTURE_PROCEDURE ACTION=JAM_EXTERNAL TOOL=T1 TARGET=265.0 SPEED_PROFILE=OEM')

    def test_service_step_routes_each_tool_and_source_to_the_factory_module(self):
        for tool in ('T0','T1'):
            for source in ('EXTERNAL','CASSETTE'):
                for step in ('UNLOAD','LOAD','WIPE'):
                    command = self.render('SERVICE_STEP', TOOL=tool, SOURCE=source, TARGET=260, STEP=step)
                    expected = ('CLEAN' if step == 'WIPE' else 'MANUAL' if source == 'EXTERNAL'
                                else 'AUTO' if step == 'LOAD' else 'UNLOAD')
                    self.assertIn('ACTION=' + expected + ' TOOL=' + tool, command)
                    if expected == 'MANUAL':
                        self.assertIn('MANUAL_DIR=' + step + ' MANUAL_MM=20 FEEDER=0', command)
        with self.assertRaises(ValueError):
            self.render('SERVICE_STEP', TOOL='T0', SOURCE='UNKNOWN', TARGET=235, STEP='LOAD')

    def test_flow_confirmation_and_retry_are_guarded(self):
        self.assertEqual(self.render('RETRY'), 'INVENTURE_FILAMENT_JAM ACTION=RETRY RUN_ID=A PROMPT_ID=4')
        with self.assertRaises(ValueError):
            self.render('CONFIRM_FLOW')
        with self.assertRaises(ValueError):
            self.render('SOURCE')
        self.assertIn('TOOL=T1 SOURCE=CASSETTE RUN_ID=A PROMPT_ID=4',
                      self.render('SOURCE', TOOL='T1', SOURCE='CASSETTE'))
