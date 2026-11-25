"""R30 migration, Moonraker settings, printer-backed dashboard routes and control ownership."""
import configparser
import copy
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orange_pi import inventure_fluidd as routes
from orange_pi.inventure_menu_core import SettingsStore
from orange_pi.inventure_oled_uifast_transport import FastOledStream, OledStream, mode_operations

spec = importlib.util.spec_from_file_location('fluidd_setup', ROOT / 'setup-fluidd.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class FluiddSettingsTests(unittest.TestCase):
    def test_only_the_primary_mcu_configuration_latch_can_use_installed_macros(self):
        message = "Can not update MCU 'mcu' config as it is shutdown"
        for state, reason, permitted in (
                ('error', message+'\nOnce the underlying issue is corrected', True),
                ('shutdown', message, True),
                ('shutdown', "MCU 'mcu' shutdown: ADC out of range", False),
                ('error', "Can not update MCU 'aux' config as it is shutdown", False),
                ('error', "Unable to parse option 'max_temp'", False),
                ('error', "Invalid command\n"+message, False)):
            with self.subTest(state=state, reason=reason), patch.object(setup, 'request', return_value={
                    'status':{'webhooks':{'state':state,'state_message':reason}}}), \
                    patch.object(setup, 'read_groups', return_value={}) as groups:
                installed = MagicMock(return_value={'INVENTURE_DIAGNOSTICS','INVENTURE_HOME_ALL'})
                if permitted:
                    result = setup.setup('printer', Path('.'), installed_names=installed)
                    self.assertTrue(result['firmware_restart_required'])
                    self.assertEqual(result['klipper_message'], reason)
                    with self.assertRaisesRegex(ValueError, 'missing INVENTURE_DIAGNOSTICS'):
                        setup.setup('printer', Path('.'), installed_names=lambda: set())
                    with self.assertRaises(setup.MCUConfigShutdown):
                        setup.live_names('printer', 0)
                else:
                    with self.assertRaises(ValueError):
                        setup.setup('printer', Path('.'), installed_names=installed)
                    installed.assert_not_called()
                    groups.assert_not_called()

    def test_ready_with_stale_runtime_or_missing_macros_cannot_use_disk_fallback(self):
        with patch.object(setup, 'request', return_value={'process_id':419}) as request:
            with self.assertRaisesRegex(ValueError, 'Waiting for Klipper PID 421'):
                setup.live_names('printer', 0, expected_pid=421)
            self.assertEqual(request.call_count, 1)
        for cfg, expected in (({}, 'missing INVENTURE_DIAGNOSTICS'),
                              ({'gcode_macro INVENTURE_DIAGNOSTICS':{}}, 'running host revision old')):
            answers = [{'status':{'webhooks':{'state':'ready'}, 'configfile':{'config':cfg}}},
                       {'status':{'inventure_mcu':{'host_revision':'old'}}}]
            with self.subTest(cfg=cfg), patch.object(setup, 'request', side_effect=answers), \
                    patch.object(setup, 'read_groups') as groups:
                installed = MagicMock()
                with self.assertRaisesRegex(ValueError, expected):
                    setup.setup('printer', Path('.'), installed_names=installed, expected_revision='new')
                installed.assert_not_called()
                groups.assert_not_called()

    def test_install_english_writes_only_locale_and_is_idempotent(self):
        endpoint = '/server/database/item?namespace=fluidd&key=uiSettings.general.locale'
        with patch.object(setup, 'request', side_effect=[{'value':'pl'}, {}, {'value':'en'}]) as request:
            setup.install_english('printer')
            self.assertEqual(request.call_args_list[0].args, ('printer', endpoint))
            self.assertEqual(request.call_args_list[1].args[2], dict(namespace='fluidd', key='uiSettings.general.locale', value='en'))
        with patch.object(setup, 'request', return_value={'value':'en'}) as request:
            setup.install_english('printer')
            request.assert_called_once_with('printer', endpoint)

    def test_names_only_from_loaded_gcode_macro_sections(self):
        result = setup.configured_names({'settings': {'extruder': {}, 'gcode_macro PID_STATUS': {}},
                                        'config': {'include *.cfg': {}, 'gcode_macro inventure_load': {}}})
        self.assertEqual(result, {'INVENTURE_LOAD', 'PID_STATUS'})

    def test_existing_categories_visibility_custom_layout_and_aliases(self):
        before = dict(categories=[{'id':'k','name':'Klipper'}, {'id':'u','name':'My tests'},
                                  {'id':'z','name':'ZORTRAX INVENTURE'}],
                      stored=[dict(name='ZORTRAX_LOAD', visible=False, custom=23),
                              dict(name='MY_TEST', categoryId='u', visible=True)], layout='compact')
        snapshot = copy.deepcopy(before)
        names = {'INVENTURE_LOAD', 'MY_TEST', 'INVENTURE_DIAGNOSTICS', 'PID_STATUS', 'INVENTURE_TEST_FAN_TEST'}
        after, assigned = setup.merge_groups(before, names)
        self.assertEqual(before, snapshot)
        self.assertEqual([c['id'] for c in after['categories']], ['z','u','k'])
        self.assertEqual(after['layout'], 'compact')
        rows = {r['name']: r for r in after['stored']}
        self.assertEqual(rows['INVENTURE_LOAD'], dict(name='INVENTURE_LOAD', visible=False, custom=23, categoryId='z'))
        self.assertEqual(rows['MY_TEST'], before['stored'][1])
        self.assertFalse(rows['INVENTURE_TEST_FAN_TEST']['visible'])
        self.assertEqual(assigned['PID_STATUS'], 'Klipper')
        self.assertEqual(setup.merge_groups(after, names)[0], after)

    def test_duplicate_settings_rejected_and_local_legacy_button_hidden(self):
        names = {'INVENTURE_LEVEL', 'ZORTRAX_LEVEL'}
        old = dict(stored=[dict(name='ZORTRAX_LEVEL', visible=True)])
        after, _ = setup.merge_groups(old, names)
        self.assertFalse(after['stored'][0]['visible'])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            setup.merge_groups(dict(stored=[dict(name='A'), dict(name='a')]), names)

    def test_menu_entry_macros_are_visible_in_zortrax_group_and_keep_user_choices(self):
        names = {'INVENTURE_MAIN_MENU', 'INVENTURE_KLIPPER_MENU',
                 'INVENTURE_MAINTENANCE', 'INVENTURE_MATERIAL_MENU'}
        after, assigned = setup.merge_groups({}, names)
        self.assertEqual(set(assigned.values()), {'ZORTRAX INVENTURE'})
        self.assertTrue(all(row['visible'] for row in after['stored']))
        after['stored'][0]['visible'] = False
        self.assertEqual(setup.merge_groups(after, names)[0], after)

    def test_http_apply_readback_noop_and_restore(self):
        source = {'categories':[dict(name='My group', id='local')], 'stored':[]}
        state = dict(macros=copy.deepcopy(source), writes=0)
        class Server(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def answer(self, payload):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(dict(result=payload)).encode())
            def do_GET(self):
                if self.path.startswith('/printer/objects/query'):
                    self.answer(dict(status=dict(webhooks=dict(state='ready'),
                        configfile=dict(config={'gcode_macro INVENTURE_DIAGNOSTICS':{}, 'gcode_macro PID_STATUS':{}}))))
                else:
                    self.answer(dict(value=state['macros']))
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if (body['namespace'],body['key']) != ('fluidd','macros'):
                    self.send_error(400)
                    return
                state.update(macros=body['value'], writes=state['writes']+1)
                self.answer(dict(value=body['value']))
        with ThreadingHTTPServer(('127.0.0.1',0), Server) as server, tempfile.TemporaryDirectory() as tmp:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = 'http://127.0.0.1:%d' % server.server_port
                directory = Path(tmp)
                self.assertEqual(setup.setup(base,directory)['status'],'GROUPS_PLANNED')
                self.assertEqual(state['writes'],0)
                result = setup.setup(base,directory,apply=True)
                backup = Path(result['backup'])
                self.assertEqual(backup.stat().st_mode & 0o777,0o600)
                self.assertEqual(setup.setup(base,directory,apply=True)['status'],'ALREADY_GROUPED')
                state['macros']['locally_edited'] = True
                with self.assertRaisesRegex(ValueError,'does not match'):
                    setup.setup(base,directory,apply=True,rollback=backup)
                del state['macros']['locally_edited']
                setup.setup(base,directory,apply=True,rollback=backup)
                self.assertEqual(state, dict(macros=source,writes=2))
            finally:
                server.shutdown()
                thread.join()

    def test_concurrent_edit_detected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(setup, 'live_names', return_value={'PID_STATUS'}), \
                patch.object(setup, 'read_groups', side_effect=[{}, {'operator':'edited'}]), patch.object(setup, 'request') as post:
            with self.assertRaisesRegex(ValueError, 'changed'):
                setup.setup('http://localhost',Path(tmp),apply=True)
            post.assert_not_called()


class DashboardRouteTests(unittest.TestCase):
    def test_operator_menu_and_oled_settings_use_current_one_shot_view(self):
        from test_orange_pi_zortrax_menu import MenuFixture, menu_module, service_module
        from test_orange_pi_oled_fast import oled
        _, panel = oled()
        board, menu = MenuFixture().host()
        board.printer.objects['inventure_oled'] = panel
        panel.board.zortrax = menu
        panel.oem_menu = menu_module.OEMMenu('en')
        panel.oem_menu.operational = True
        panel.oem_menu.page = 'root'
        lines = []
        board.gcode.register_output_handler(lines.append)
        menu.open_operator_page()
        menu.view.move(1)
        self.assertEqual(menu.view.render()['icon'], 'models')
        token = menu.view.token
        labels = menu.view.render()['labels']
        self.assertIn('Material', labels)
        menu.choose(token, index=labels.index('Settings'))
        with self.assertRaises(ValueError):
            menu.choose(token, index=0)
        menu.choose(menu.view.token, index=menu.view.render()['labels'].index('OLED brightness'))
        token = menu.view.token
        self.assertEqual(menu.view.render()['icon'], 'activity:oled_brightness')
        self.assertIn('// action:prompt_text INVENTURE_OLED_EDITOR LEVEL 16 '+token, lines)
        cmd = service_module.Command(menu, TOKEN=token, VALUE=7)
        menu.save_display_value(cmd)
        self.assertEqual(panel.stream.awake_level, 6)
        self.assertEqual(menu.settings.value['oled_level'], 7)
        with self.assertRaises(ValueError):
            menu.save_display_value(cmd)
        self.assertEqual(board.device.packets, [])

    def test_minute_timers_work_when_busy_and_preserve_legacy_seconds(self):
        from test_orange_pi_zortrax_menu import MenuFixture, service_module
        from test_orange_pi_oled_fast import oled
        _, panel = oled()
        _, menu = MenuFixture().host()
        panel.board.zortrax = menu
        def set_values(**values):
            panel.cmd_display_control(service_module.Command(menu, **values))
        set_values(DIM=15, SLEEP=5)
        self.assertEqual(panel.dim_timeout, 300)
        set_values(SLEEP=20)
        self.assertEqual(panel.dim_timeout, 900)
        panel.last_activity = 0.
        panel._display_working = MagicMock(return_value=True)
        panel._service_display(901.)
        self.assertEqual(panel.sleep_phase, 'dim')
        panel._service_display(1190.)
        self.assertEqual((panel.sleep_phase, panel.notice_level), ('notice', 16))
        panel._render(1190.)
        bright = panel._render_wire
        panel.stream.previous = bright
        panel.stream.display_mode = 'dim'
        panel._service_display(1190.)
        panel._service_display(1199.5)
        self.assertEqual((panel.sleep_phase, panel.notice_level), ('notice', 1))
        panel._render(1199.)
        self.assertEqual(panel._render_wire, bright)
        self.assertEqual(panel.stream.fade_step, 1)
        panel._service_display(1201.)
        self.assertEqual(panel.sleep_phase, 'off')
        panel._display_activity(1202.)
        self.assertTrue(panel.wake_wait)
        self.assertEqual(panel.sleep_phase, 'awake')
        with self.assertRaises(ValueError):
            set_values(DIM=2, NOTICE=6)
        set_values(IDLE=123, NOTICE=6)
        self.assertFalse(panel.minute_timers)
        self.assertEqual((panel.sleep_timeout, panel.sleep_notice), (123, 6))

    def test_fluidd_and_oled_consume_the_same_prompt_and_close_it_once(self):
        from test_orange_pi_zortrax_menu import MenuFixture
        board, menu = MenuFixture().host()
        lines = []
        board.gcode.register_output_handler(lines.append)
        board.gcode.run_script_from_command('INVENTURE_ZORTRAX OP=OPEN ACTION=select_t1 PROMPT=1')
        token = menu.view.token
        self.assertTrue(any('TOKEN='+token+' INDEX=1' in line for line in lines))
        menu.choose(token,index=0)  # OLED Back, no physical tool change
        self.assertEqual(lines.count('// action:prompt_end'),1)
        self.assertIsNone(menu.menu_dialog_token)
        with self.assertRaises(ValueError):
            menu.choose(token,confirm=True)
        self.assertEqual(board.device.packets,[])

    def test_pid_uses_physical_channel_for_both_nozzles(self):
        board = MagicMock()
        heaters = MagicMock()
        heaters.get_all_heaters.return_value = ['extruder', 'heater_generic right_nozzle', 'heater_generic chamber']
        heaters.lookup_heater.side_effect = {
            name: SimpleNamespace(sensor=SimpleNamespace(channel=i),mcu_pwm=SimpleNamespace(name=pin))
            for i,(name,pin) in enumerate((('extruder','HEATER_T0'),('right_nozzle','HEATER_T1'),('chamber','CHAMBER')))
        }.__getitem__
        board.printer.lookup_object.side_effect = lambda name,*args: heaters if name=='heaters' else None
        board.nozzle_z_context=board.feeder_run_context=board.load_test_context=board.filament_jam=None
        board._control_query.return_value=dict(mode='NATIVE',armed=1,fault=0,latched=0)
        cmd = MagicMock()
        cmd.get_float.return_value = 200.
        for i, name in enumerate(('extruder','right_nozzle')):
            cmd.get.return_value = 'T%d'%i
            routes.calibrate_nozzle_pid(board,cmd)
            board.gcode.run_script_from_command.assert_called_with('PID_CALIBRATE HEATER=%s TARGET=200'%name)
        board._control_query.return_value['armed']=0
        with self.assertRaisesRegex(ValueError,'NATIVE'):
            routes.calibrate_nozzle_pid(board,cmd)
        board.load_test_context = {}
        with self.assertRaisesRegex(ValueError,'service procedure'):
            routes.calibrate_nozzle_pid(board,cmd)
        self.assertEqual(board.gcode.run_script_from_command.call_count,2)

    def test_platform_sign_single_axis_limits_reference_and_guard(self):
        board = MagicMock()
        board.nozzle_z_context=board.feeder_run_context=board.load_test_context=board.filament_jam=None
        head, native, gm = MagicMock(), MagicMock(), MagicMock()
        devices=dict(toolhead=head,inventure_native_z=native,gcode_move=gm)
        board.printer.lookup_object.side_effect=lambda name,*args: devices.get(name)
        head.get_status.return_value={'homed_axes':'xyz','axis_minimum':[0,0,0],'axis_maximum':[150,150,140]}
        head.get_position.return_value=[7.,8.,3.,9.]
        native.running=native.sequence_running=False
        native.last_result={'reference_established':True}
        board._control_next_token.return_value=5
        for direction,target in (('up',2.),('down',4.)):
            routes.move_platform(board,direction,1.,2.)
            head.move.assert_called_with([7.,8.,target,9.],2.)
            native._verify_guard.assert_called_with(2,5,False)
        self.assertEqual(gm.reset_last_position.call_count,2)
        for direction,distance in (('up',5.),('down',float('nan')),('up',10.)):
            with self.assertRaises(ValueError):
                routes.move_platform(board,direction,distance,1.)
        native.sequence_running=True
        with self.assertRaisesRegex(ValueError,'homing to finish'):
            routes.move_platform(board,'down',1.,2.)
        native.sequence_running=False
        native.last_result=None
        with self.assertRaisesRegex(ValueError,'reference'):
            routes.move_platform(board,'up',1.,2.)
        self.assertEqual(head.move.call_count,2)

    def test_oled_level_updates_survive_inflight_change_and_settings_reload(self):
        for cls in (OledStream, FastOledStream):
            stream=cls()
            stream.display_mode='awake'
            stream.awake_level=4
            ops=list(stream.begin_mode_update(fast=(cls is FastOledStream)))
            self.assertEqual(ops,list(mode_operations('awake','awake',cls is FastOledStream,4)))
            stream.awake_level=7
            stream.finish_mode_update()
            self.assertEqual(stream.display_level,4)
            self.assertTrue(stream.needs_mode_update())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'settings.json'
            settings=SettingsStore(path)
            settings.set(sorting='name')
            settings.set(oled_level=4,sleep_enabled=False,idle_s=55,notice_s=10)
            self.assertEqual(SettingsStore(path).value,settings.value)
            with self.assertRaises(ValueError):
                settings.set(oled_level=17)

    def test_real_macro_templates_use_pending_pid_key_spelling_and_preflight_ticket(self):
        cfg=configparser.RawConfigParser()
        cfg.read(ROOT/'config/INVENTURE_KLIPPER.cfg')
        env=Environment(variable_start_string='{',variable_end_string='}')
        def render(name,params,printer):
            def fail(message):
                raise ValueError(message)
            return env.from_string(cfg.get('gcode_macro '+name,'gcode')).render(params=params,printer=printer,action_raise_error=fail)
        native={'configfile':{'save_config_pending_items':{'extruder':{'pid_Kp':'1','pid_Ki':'2','pid_Kd':'3'}}}}
        self.assertEqual(render('PID_SAVE',{},native).split(),['INVENTURE_NATIVE_IDLE','SAVE_CONFIG'])
        native['configfile']['save_config_pending_items']={'probe':{'z_offset':1}}
        with self.assertRaisesRegex(ValueError,'PID tune'):
            render('PID_SAVE',{},native)
        with self.assertRaisesRegex(ValueError,'preflight'):
            render('START_PRINT',{},native)
        start=render('START_PRINT',dict(TOKEN='abc',ACK='START_CHECKED_JOB'),native)
        self.assertEqual(start.split(),['INVENTURE_PREFLIGHT_START','TOKEN=abc','ACK=START_CHECKED_JOB'])

    def test_repository_operator_names_and_unique_templates(self):
        paths=list((ROOT/'config').glob('*.cfg'))+list((ROOT/'fluidd').glob('*.cfg'))
        names=set()
        for path in paths:
            for line in path.read_text().splitlines():
                if line.startswith('[gcode_macro '):
                    name=line[13:-1]
                    self.assertTrue(name.startswith('INVENTURE_') or name in setup.KLIPPER,name)
                    self.assertNotIn(name,names)
                    names.add(name)
        self.assertFalse(names.intersection({'INVENTURE_LEVEL_STATUS','INVENTURE_LEVEL_CANCEL',
            'INVENTURE_SELECT_TOOL','INVENTURE_PURGE','INVENTURE_FILAMENT_STATUS','INVENTURE_FILAMENT_JOG'}))
        self.assertTrue(set(routes.LEGACY_COMMANDS.values())<=names)
