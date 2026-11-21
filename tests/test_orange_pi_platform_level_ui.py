"""One operator prompt and run shared by OLED and Fluidd."""
import configparser
from pathlib import Path
import unittest
from jinja2 import Environment
from test_orange_pi_zortrax_menu import MenuFixture, core


class LevelingUiTests(MenuFixture, unittest.TestCase):
    def setUp(self):
        self.board, self.menu = self.host()
        self.run = {'phase': 'idle', 'run_id': None}
        self.calls = []
        self.board.platform_level_available = lambda: True
        self.board.platform_level_status = lambda: dict(self.run)
        self.board.cmd_platform_level = self.command

    def command(self, cmd):
        self.calls.append(cmd.values)
        action = cmd.get('ACTION')
        if action == 'START':
            self.run.update(phase='preparing', run_id='session')
        elif action == 'NEXT':
            self.run['awaiting_operator'] = False
        elif action in ('SAVE', 'CANCEL'):
            self.run['phase'] = {'SAVE': 'saved', 'CANCEL': 'cancelled'}[action]

    def choose(self, label):
        labels = [item[0] for item in self.menu.view.options]
        self.menu.choose(self.menu.view.token, index=labels.index(label))

    def tick(self):
        self.menu._tick(self.board.reactor.now)

    def test_oled_starts_with_automatic_T0_selection_then_accepts_knob_prompt(self):
        self.menu.open('leveling')
        self.assertEqual(self.menu.view.title, 'Start platform leveling?')
        self.assertEqual(self.menu.view.kind, 'confirm')
        self.assertIn(('Automatic T0 selection', None), self.menu.view.options)
        self.assertEqual(self.calls, [])
        self.menu.choose(self.menu.view.token, confirm=True)
        self.assertEqual(self.calls, [dict(ACTION='START')])
        self.run.update(phase='await_knobs', awaiting_operator=True, prompt_id=1)
        self.tick()
        self.choose('Knobs tightened / next')
        self.assertEqual(self.calls[-1], dict(ACTION='NEXT', RUN_ID='session', PROMPT_ID=1))
        self.assertNotIn('Knobs tightened / next', [row[0] for row in self.menu.view.options])

    def test_legacy_menu_start_parameter_does_not_control_the_selector(self):
        self.board.gcode.run_script_from_command('INVENTURE_ZORTRAX OP=OPEN ACTION=leveling ACTIVE_TOOL=T1')
        self.assertEqual(self.menu.view.title, 'Start platform leveling?')
        self.assertNotIn('ACTIVE_TOOL', self.menu.view.data['parameters'])
        self.menu.choose(self.menu.view.token, confirm=True)
        self.assertEqual(self.calls, [dict(ACTION='START')])

    def test_external_run_stale_adjustment_and_review_before_save(self):
        self.run.update(phase='adjust', run_id='external', awaiting_operator=True, prompt_id=4,
                        instructions=[dict(knob='rear_left', action='tighten',
                                           turns=0.25, direction='counterclockwise')])
        self.tick()
        self.assertEqual(self.menu.view.options[1][0], 'Rear Left: tighten')
        self.assertEqual(self.menu.view.options[2][0], '0.25 turns CCW')
        self.run['prompt_id'] = 5
        with self.assertRaisesRegex(core.MenuError, 'expired'):
            self.choose('Adjusted / next')
        self.assertEqual(self.calls, [])
        self.run.update(phase='candidate', deformed=False, candidate_depth_um=139000)
        self.tick()
        self.choose('Review / save result')
        self.assertEqual(self.calls, [])
        self.menu.choose(self.menu.view.token, confirm=True)
        self.assertEqual(self.calls, [dict(ACTION='SAVE', RUN_ID='external')])

    def test_deformed_center_shows_replacement_and_allows_cancel(self):
        self.run.update(phase='candidate', run_id='external', deformed=True, candidate_depth_um=138800)
        self.tick()
        labels = [row[0] for row in self.menu.view.options]
        self.assertIn('Replace deformed tray', labels)
        self.assertNotIn('Review / save result', labels)
        self.menu.panel_back()
        self.menu.choose(self.menu.view.token, confirm=True)
        self.assertEqual(self.calls, [dict(ACTION='CANCEL', RUN_ID='external')])

    def test_fluidd_shows_adjustment_once_and_closes_after_oled_confirmation(self):
        output = []
        self.board.gcode.register_output_handler(output.append)
        self.run.update(phase='adjust', run_id='external', awaiting_operator=True, prompt_id=8,
                        instructions=[dict(knob='front', action='loosen', turns=0.5, direction='clockwise')])
        self.tick()
        self.tick()
        self.assertEqual(output.count('// action:prompt_show'), 1)
        instruction = next(msg for msg in output if msg.startswith('// action:prompt_text'))
        for expected in ('Loosen the front knob', '0.50', 'clockwise'):
            self.assertIn(expected, instruction)
        self.assertIn('// action:prompt_button Continue|INVENTURE_PLATFORM_LEVEL ACTION=NEXT RUN_ID=external PROMPT_ID=8|primary', output)
        self.choose('Adjusted / next')
        self.tick()
        self.assertEqual(output[-1], '// action:prompt_end')

    def test_rotary_knob_access_waits_for_confirmed_release_on_both_interfaces(self):
        output = []
        self.board.gcode.register_output_handler(output.append)
        self.run.update(phase='await_knobs', run_id='external', prompt_id=1,
                        awaiting_operator=False, door_pause=True)
        self.tick()
        self.assertEqual(self.menu.view.title, 'Please wait')
        self.assertEqual([row[0] for row in self.menu.view.options], ['Stop / cancel'])
        self.assertFalse(any('action:prompt_' in message for message in output))
        self.run['awaiting_operator'] = True
        self.tick()
        labels = [row[0] for row in self.menu.view.options]
        self.assertIn('Access now permitted', labels)
        self.assertIn('Leave selector in place', labels)
        self.assertIn('Seat tray, close doors', labels)
        self.assertIn('Knobs tightened / next', labels)
        self.assertTrue(any('close the front door and lid' in message for message in output))
        self.assertTrue(any('ACTION=NEXT RUN_ID=external PROMPT_ID=1' in message for message in output))
        self.run.update(prompt_id=2, door_pause=False)
        self.tick()
        labels = [row[0] for row in self.menu.view.options]
        self.assertNotIn('Access now permitted', labels)
        self.assertNotIn('Seat tray, close doors', labels)
        self.assertIn('Knobs tightened / next', labels)


class FluiddLevelingMacros(unittest.TestCase):
    def setUp(self):
        config = configparser.RawConfigParser()
        config.read(Path(__file__).resolve().parents[1] / 'config/INVENTURE_PLATFORM_LEVEL.cfg')
        env = Environment(variable_start_string='{', variable_end_string='}')
        self.templates = {name.split()[1]: env.from_string(config.get(name, 'gcode')) for name in config.sections()}
        self.messages = []

    def render(self, name, run=None, **params):
        def error(message):
            raise ValueError(message)
        def info(message):
            self.messages.append(message)
            return ''
        return self.templates[name].render(params=params, printer={'inventure_mcu': {'platform_level': run or {}}},
                                           action_respond_info=info, action_raise_error=error).strip()

    def test_start_selects_T0_automatically_even_with_legacy_tool_parameter(self):
        for params in ({}, {'ACTIVE_TOOL': 'T0'}, {'ACTIVE_TOOL': 'T1'}):
            with self.subTest(params=params):
                self.assertEqual(self.render('INVENTURE_LEVEL', **params),
                                 'INVENTURE_PLATFORM_LEVEL ACTION=START')
                self.assertFalse(self.messages)

    def test_door_menu_button_is_a_separate_read_only_entry(self):
        self.assertEqual(self.render('INVENTURE_DOOR_LOCK'), 'INVENTURE_DOOR_POLICY MENU=1')
        self.assertFalse(self.messages)

    def test_next_and_save_bind_active_run_and_cancel_is_immediate(self):
        self.assertEqual(self.render('INVENTURE_LEVEL_NEXT', dict(run_id='abc', prompt_id=2, awaiting_operator=True)),
                         'INVENTURE_PLATFORM_LEVEL ACTION=NEXT RUN_ID=abc PROMPT_ID=2')
        self.assertEqual(self.render('INVENTURE_LEVEL_SAVE', dict(run_id='abc', phase='candidate')),
                         'INVENTURE_PLATFORM_LEVEL ACTION=SAVE RUN_ID=abc')
        with self.assertRaises(ValueError):
            self.render('INVENTURE_LEVEL_SAVE', dict(phase='measuring'))
        self.assertEqual(self.render('INVENTURE_LEVEL_STOP'), 'INVENTURE_PLATFORM_LEVEL ACTION=CANCEL')
        self.assertEqual(self.render('INVENTURE_LEVEL_PROGRESS'), 'INVENTURE_PLATFORM_LEVEL ACTION=STATUS')
