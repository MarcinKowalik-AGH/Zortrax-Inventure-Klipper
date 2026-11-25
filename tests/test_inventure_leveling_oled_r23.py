"""The installed leveling wizard uses r0323 messages, pixels and run tokens."""
import importlib
import atexit
import ast
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import inventure_bootstrap as bootstrap
import test_orange_pi_platform_level_ui as baseline
from test_orange_pi_zortrax_menu import service_module, core, control
from test_orange_pi_zortrax_menu import Config
from test_orange_pi_oled_fast import o as oled, oled as oled_fixture

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType('leveling_r23_test')
_stage = tempfile.TemporaryDirectory(prefix='inventure-installed-leveling-ui-')
atexit.register(_stage.cleanup)
_, _files, _ = bootstrap._plan(ROOT)
for _name, (_data, _) in _files.items():
    if _name.startswith('klippy/extras/'):
        _path = Path(_stage.name)/_name
        _path.parent.mkdir(parents=True,exist_ok=True)
        _path.write_bytes(_data)
package.__path__ = [str(Path(_stage.name)/'klippy/extras'), str(ROOT/'tests/fixtures/host_dependencies')]
sys.modules[package.__name__] = package
deployed_core = importlib.import_module(package.__name__+'.inventure_menu_core')
deployed_core.MenuError = core.MenuError
service = importlib.import_module(package.__name__+'.inventure_menu_service')
graphics = importlib.import_module(package.__name__+'.inventure_oled_graphics')


class InstalledLevelingWizard(baseline.LevelingUiTests):
    def host(self, **config):
        with patch.object(service_module,'ZortraxMenuService',service.ZortraxMenuService):
            return super().host(**config)

    def test_tested_sources_are_exactly_the_bootstrap_installed_ui(self):
        _, files, _ = bootstrap._plan(ROOT)
        for module in (service,graphics):
            path = Path(module.__file__)
            self.assertEqual(path.read_bytes(),files['klippy/extras/'+path.name][0])

    def test_loaded_oled_reports_the_actual_menu_release_to_the_installer(self):
        _, files, _ = bootstrap._plan(ROOT)
        tree=ast.parse(files['klippy/extras/inventure_oled.py'][0])
        cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='InventureOLED')
        method=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='get_status')
        namespace=dict(vars(oled))
        namespace['LEVELING_RENDERER_RELEASE']=graphics.LEVELING_RENDERER_RELEASE
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),
                     'installed/inventure_oled.py','exec'),namespace)
        _, panel=oled_fixture()
        panel.board.zortrax=self.menu
        state=namespace['get_status'](panel,0.)
        self.assertEqual(state['procedure_ui_release'],'RC8-OPERATOR-MENU-R23.4')
        self.menu.ui_release='old-menu'
        self.assertIsNone(namespace['get_status'](panel,0.)['procedure_ui_release'])

    def test_zortrax_start_confirmation_uses_the_original_three_knob_icon(self):
        self.menu.open('leveling')
        view=self.menu.view_status()
        self.assertEqual(view['icon'],'leveling:0x0802A218')
        self.assertEqual(len(graphics.menu_wire(view)),8192)

    def test_real_full_leveling_program_updates_the_panel_during_preparation_heating_and_selection(self):
        import test_orange_pi_leveling_sequence as sequence
        observed=[]
        menu=None
        def observe(board,device):
            nonlocal menu
            if menu is None:
                board.gcode=control.gcode.GCodeDispatch(board.printer)
                board.gcode.register_output_handler(lambda line:None)
                menu=service.ZortraxMenuService(board,Config())
                board.zortrax=menu
                board.gcode._handle_ready()
            menu._tick(board.reactor.monotonic())
            view=menu.view_status()
            if not view or not view.get('leveling'):
                return
            screen=view['leveling']
            self.assertNotEqual(screen['icon'],'activity:wait')
            self.assertEqual(len(graphics.menu_wire(view)),8192)
            row=(screen['phase'],screen.get('activity'),screen['icon'])
            if not observed or observed[-1]!=row:observed.append(row)
        sequence.SequenceTests().simulate('T1',door_lock=True,observer=observe)
        activities={activity for _,activity,_ in observed}
        self.assertTrue({'preparing','heating','selecting_t0'} <= activities,observed)
        self.assertTrue(any(str(activity).startswith('home_') for activity in activities),observed)
        self.assertTrue({'await_knobs','adjust','reference','measuring','finishing'} <= {phase for phase,_,_ in observed},observed)
        self.assertEqual(menu.get_status(0)['ui_release'],'RC8-OPERATOR-MENU-R23.4')

    def test_current_action_changes_the_graphics_even_while_the_public_phase_stays_heating(self):
        self.run.update(run_id='current',phase='heating')
        factory=dict(operation='PLATFORM_LEVEL',platform_level_run=self.run,phase='HOME',
                     module_stack=[],action=dict(kind='home',axis='X'))
        self.board.load_test_context=dict(factory=factory,target=200.)
        # The observation-only projection must not touch MCU/lease methods.
        detail=self.menu._platform_status()
        screen=graphics.leveling_screen(detail)
        self.assertEqual(screen['icon'],'project:homing-xy')
        factory['action']=dict(kind='level_heat')
        screen=graphics.leveling_screen(self.menu._platform_status())
        self.assertEqual(screen['icon'],'project:heat_nozzle')
        self.assertIn('Target: 200 C',screen['body'])
        factory['module_stack']=['clean']
        factory['action']=dict(kind='move')
        screen=graphics.leveling_screen(self.menu._platform_status())
        self.assertEqual(screen['icon'],'project:clean_nozzle')
        self.assertEqual(self.calls,[])

    def test_completed_prompt_or_observation_is_not_replaced_by_old_factory_activity(self):
        ui=dict(action=dict(kind='level_heat'),modules=['select_tool'])
        for phase in ('await_knobs','adjust','candidate','saved','cancelled','failed'):
            run=dict(phase=phase,awaiting_operator=True,ui=ui,instructions=[dict(knob='front',
                     action='tighten',direction='counterclockwise',turns=.25)])
            screen=graphics.leveling_screen(run)
            self.assertNotEqual(screen['icon'],'project:heat_nozzle')

    def test_external_leveling_replaces_the_stale_home_view_and_factory_owner(self):
        old_context = {'factory':{'phase':'procedure_complete'}}
        self.menu.active = dict(backend='service',object=old_context,action='external_service')
        self.menu._info('Home XYZ complete','Homing procedure finished')
        old_token = self.menu.view.token
        self.run.update(run_id='current',phase='await_knobs',prompt_id=3,awaiting_operator=True)
        self.tick()
        self.assertEqual(self.menu.active['backend'],'platform_level')
        self.assertNotEqual(self.menu.view.token,old_token)
        self.assertEqual(self.menu.view_status()['leveling']['body'],
                         graphics._LEVELING_OEM['messages']['knobs']['text'])
        self.assertEqual(self.menu.view.options[self.menu.view.selected][0],'Knobs tightened / next')
        with self.assertRaisesRegex(core.MenuError,'expired'):
            self.menu.choose(old_token,index=1)

    def test_multiple_screws_are_shown_individually_before_one_backend_confirmation(self):
        instructions = [dict(knob=knob,action='tighten',turns=.25,direction='counterclockwise',delta_steps=100)
                        for knob in ('rear_left','rear_right')]
        self.run.update(run_id='current',phase='adjust',prompt_id=5,awaiting_operator=True,instructions=instructions)
        self.tick()
        self.assertIn('Rear left side is too high',self.menu.view_status()['leveling']['body'])
        self.assertEqual(self.menu.view.options[self.menu.view.selected][0],'Next screw / view')
        old = self.menu.panel_select_command(acknowledged=True)
        self.board.gcode.run_script_from_command(old)
        self.assertEqual(self.calls,[])
        self.assertIn('Rear right side is too high',self.menu.view_status()['leveling']['body'])
        with self.assertRaisesRegex(control.gcode.CommandError,'expired'):
            self.board.gcode.run_script_from_command(old)
        self.choose('Adjusted / next')
        self.assertEqual(self.calls,[dict(ACTION='NEXT',RUN_ID='current',PROMPT_ID=5)])

    def test_original_animated_screw_pixels_reach_the_ssd1322_buffer(self):
        frames = set()
        for knob in ('front','rear_left','rear_right'):
            for action,direction in (('tighten','counterclockwise'),('loosen','clockwise')):
                self.run.update(run_id='current',phase='adjust',prompt_id=4,awaiting_operator=True,
                    instructions=[dict(knob=knob,action=action,turns=.5,direction=direction,delta_steps=200)])
                self.tick()
                view = self.menu.view_status()
                symbol = graphics._symbol(view['icon'])
                self.assertTrue(view['animated'])
                for index in range(len(symbol['frames'])):
                    raw = graphics._unpack(symbol['frames'][index],view['icon'])
                    wire = graphics.menu_wire(view,index=index)
                    self.assertEqual(len(wire),8192)
                    for y in range(50):
                        for x in range(50):
                            byte = raw[y*25+x//2]
                            actual = wire[(13+y)*128+(203+x)//2]
                            self.assertEqual(actual & 15 if (203+x) & 1 else actual >> 4,
                                             byte & 15 if x & 1 else byte >> 4)
                    frames.add(wire)
        self.assertEqual(len(frames),12)

    def test_new_prompt_needs_its_own_display_receipt_and_button_release(self):
        self.run.update(run_id='current',phase='await_knobs',prompt_id=1,awaiting_operator=True)
        self.tick()
        view = self.menu.view_status()
        wire = graphics.menu_wire(view)
        panel = SimpleNamespace(board=self.board,sleep_phase='awake',message=None,
            stream=SimpleNamespace(ready=True,last_frame_bytes=wire,generation=1),
            _zortrax_frame=(view['page'],view['generation'],wire,1))
        self.assertTrue(oled.InventureOLED._zortrax_acknowledged(panel))
        self.run.update(prompt_id=2)
        self.tick()
        # Identical pixels cannot authorize the replacement prompt.
        self.assertFalse(oled.InventureOLED._zortrax_acknowledged(panel))
        with self.assertRaisesRegex(core.MenuError,'displayed view'):
            self.menu.panel_select_command(acknowledged=False)
        self.assertEqual(self.calls,[])

    def test_candidate_and_external_save_show_the_same_five_measurements_on_oled(self):
        self.run.update(run_id='current',phase='candidate',candidate_depth_um=138408,
                        center_error_steps=8,heights_steps={0:-123,1:-87,2:-31,3:-2,4:53})
        self.tick()
        view = self.menu.view_status()
        self.assertEqual(view['leveling']['heights'],(-123,-87,-31,-2,53))
        before = graphics.menu_wire(view)
        self.run['phase']='saved'
        self.tick()
        self.assertIsNone(self.menu.active)
        self.assertEqual(self.menu.view_status()['leveling']['phase'],'saved')
        self.assertNotEqual(graphics.menu_wire(self.menu.view_status()),before)


class OriginalAssetEvidence(unittest.TestCase):
    def test_r0323_descriptor_links_match_each_knob_and_rotation(self):
        expected = {'front_tighten':'0x0802AC64','front_loosen':'0x0802B01C',
                    'rear_left_tighten':'0x0802B3D4','rear_left_loosen':'0x0802B7C8',
                    'rear_right_tighten':'0x0802BBCC','rear_right_loosen':'0x0802BFC0',
                    'knobs':'0x0802BBB0'}
        for key,sprite in expected.items():
            record = graphics._LEVELING_OEM['messages'][key]
            self.assertEqual(record['sprite'],sprite)
            for frame in graphics._LEVELING_OEM['symbols'][sprite]['frames']:
                self.assertEqual(len(graphics._unpack(frame,sprite)),1250)


if __name__ == '__main__':
    unittest.main()
