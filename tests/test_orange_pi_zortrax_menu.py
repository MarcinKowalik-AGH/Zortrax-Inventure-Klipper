"""Real menu adapter, one-use operator authority, existing backend contracts."""
import ast
from contextlib import contextmanager
import importlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest

import test_orange_pi_factory_host as factory
import test_orange_pi_control as control
from test_orange_pi_door_policy import adapter, Command

service_module=importlib.import_module(adapter.__package__+'.inventure_menu_service')
core=importlib.import_module(adapter.__package__+'.inventure_menu_core')
menu_module=importlib.import_module(adapter.__package__+'.inventure_oem_menu')
ROOT=Path(__file__).resolve().parents[1]


class Config:
    def __init__(self,**values):self.values=values
    def get(self,key,default=None):return self.values.get(key,default)
    def error(self,message):return RuntimeError(message)


class MenuFixture:
    def host(self,**config):
        h,ops,p13=factory.FactoryHostTests().host()
        p13.enabled=False
        h.gcode=control.gcode.GCodeDispatch(h.printer)
        h.gcode.register_output_handler(lambda line:None)
        svc=service_module.ZortraxMenuService(h,Config(**config));h.zortrax=svc
        h.gcode._handle_ready();h.procedure_profiles=None
        return h,svc

    def execute(self,svc,action,**params):
        svc.open(action,**params);token=svc.view.token
        svc.choose(token,confirm=True)


class MenuCoreTests(unittest.TestCase):
    def test_complete_original_and_paused_menu_vocabulary_has_a_route(self):
        actions={n.action for page in menu_module.MENU.values() for n in page[3]
            if n.action and not n.action.startswith('bench_')}
        self.assertEqual(actions-core.ALL_ACTIONS,set())
        self.assertEqual([x.label_en for x in menu_module.MENU['root'][3]],
            ['Models','Maintenance','Material','Settings','Information'])

    def test_operator_menu_preserves_OEM_leaves_and_keeps_bench_branch_separate(self):
        menu=menu_module.OEMMenu('en',root_page='launcher',bench=True,operational=True)
        menu.page='maintenance'
        self.assertIn('heat',[x.action for x in menu.items()])
        self.assertIn('fan_test',[x.action for x in menu.items()])
        menu.page='service_tests'
        self.assertIn('bench_heat',[x.child for x in menu.items()])

    def test_material_catalogue_matches_audited_order_and_tool_roles(self):
        self.assertEqual(len(core.MODEL_MATERIALS),17);self.assertEqual(len(core.SUPPORT_MATERIALS),5)
        self.assertEqual([i for i,_ in core.MODEL_MATERIALS],[0,26,12,8,27,15,2,3,16,4,5,10,11,14,1,6,128])
        self.assertEqual([i for i,_ in core.SUPPORT_MATERIALS],[7,13,17,23,127])

    def test_view_is_single_use_and_bound_to_live_identity_time_and_selection(self):
        view=core.View(100,'confirm','Run?',[('No',None),('Yes',('start',{}))],binding=('job',1))
        old=view.token;view.move(1)
        with self.assertRaises(core.MenuError):view.claim(old,101,('job',1))
        with self.assertRaises(core.MenuError):view.claim(view.token,101,('job',2))
        with self.assertRaises(core.MenuError):view.claim(view.token,221,('job',1))
        view.claim(view.token,101,('job',1))
        with self.assertRaises(core.MenuError):view.claim(view.token,101,('job',1))

    def test_host_settings_persist_atomically_with_checksum_and_avoid_noop_writes(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'menu.json';store=core.SettingsStore(path)
            self.assertFalse(path.exists());self.assertTrue(store.set(sorting='name'))
            before=path.stat().st_mtime_ns
            self.assertFalse(store.set(sorting='name'));self.assertEqual(path.stat().st_mtime_ns,before)
            self.assertEqual(core.SettingsStore(path).value['sorting'],'name')
            envelope=json.loads(path.read_text());envelope['settings']['sorting']='size';path.write_text(json.dumps(envelope))
            with self.assertRaisesRegex(core.MenuError,'checksum'):core.SettingsStore(path)

    def test_preview_rejects_outside_symlinks_and_reports_exact_current_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'part.gcode';path.write_text('G1 X1\n')
            a=core.source_identity(root,'part.gcode');self.assertEqual(a['size'],6)
            path.write_text('G1 X2\n');b=core.source_identity(root,'part.gcode')
            self.assertNotEqual(a,b)
            (Path(root)/'link.gcode').symlink_to(path)
            for name in ('../part.gcode',str(path),'link.gcode','part\ngcode'):
                with self.subTest(name=name),self.assertRaises(core.MenuError):core.source_identity(root,name)


class MenuServiceTests(unittest.TestCase,MenuFixture):
    def test_loading_menu_and_opening_confirmation_cannot_start_hardware(self):
        h,s=self.host();self.assertEqual(h.device.packets,[])
        for action in ('select_t0','select_t1','purge_t0','manual_unload_t1','heat_t0'):
            s.open(action);self.assertEqual(s.view.kind,'confirm');self.assertEqual(s.view.selected,0)
            self.assertIsNone(h.load_test_context);self.assertEqual(h.device.packets,[])
            s.choose(s.view.token,index=0)

    def test_public_gcode_and_menu_confirmation_use_the_same_actual_R7_executor(self):
        h,s=self.host()
        h.gcode.run_script_from_command('INVENTURE_ZORTRAX OP=OPEN ACTION=select_t1')
        token=s.view.token;h.gcode.run_script_from_command('INVENTURE_ZORTRAX OP=CONFIRM TOKEN='+token)
        self.assertEqual(h.load_test_context['factory']['operation'],'SELECT')
        self.assertEqual(h.load_test_context['tool'],1)
        self.assertIs(s.active['object'],h.load_test_context)
        last=factory.FactoryHostTests().finish(h);s._tick(h.reactor.now)
        self.assertEqual(last['phase'],'done');self.assertIsNone(s.active)
        self.assertFalse(h.device.active());self.assertEqual(h.device.output(7)['duty'],0)

    def test_qualified_native_selector_keeps_P04_owner_instead_of_entering_SERVICE(self):
        h,s=self.host();calls=[]
        h.printer.objects['inventure_tools']=types.SimpleNamespace(enabled=True,stopped=False,
            cmd_select=lambda c:calls.append(c.values))
        self.execute(s,'select_t1')
        self.assertEqual(calls,[{'TOOL':1}]);self.assertEqual(h.device.packets,[])
        self.assertIsNone(h.load_test_context)

    def test_queued_old_or_duplicate_confirmation_cannot_start_a_second_procedure(self):
        h,s=self.host();s.open('select_t0');old=s.view.token;s.panel_move(1)
        with self.assertRaises(core.MenuError):s.choose(old,index=1)
        self.assertEqual(h.device.packets,[])
        current=s.view.token;s.choose(current,confirm=True)
        ctx=h.load_test_context
        with self.assertRaises(core.MenuError):s.choose(current,confirm=True)
        self.assertIs(h.load_test_context,ctx)
        h.cmd_control_stop(Command())

    def test_opening_another_action_or_disconnect_revokes_old_view_authority(self):
        h,s=self.host();s.open('select_t0');old=s.view.token;s.open('select_t1')
        with self.assertRaises(core.MenuError):s.choose(old,confirm=True)
        current=s.view.token;s._shutdown()
        with self.assertRaises(core.MenuError):s.choose(current,confirm=True)
        self.assertEqual(h.device.packets,[])

    def test_native_print_started_after_preview_revokes_service_confirmation(self):
        h,s=self.host();s.open('purge_t0');token=s.view.token
        stats=types.SimpleNamespace(get_status=lambda now:{'state':'printing'})
        h.printer.objects['print_stats']=stats
        with self.assertRaises(core.MenuError):s.choose(token,confirm=True)
        self.assertEqual(h.device.packets,[])
        with self.assertRaisesRegex(RuntimeError,'idle printer'):h.cmd_procedure(Command(ACTION='SELECT'))
        self.assertEqual(h.device.packets,[])

    def test_display_ack_and_fresh_release_are_required_for_each_panel_choice(self):
        h,s=self.host();s.observe_buttons(4,1);s.open('select_t0')
        with self.assertRaises(core.MenuError):s.panel_select_command(True)
        s.observe_buttons(0,2);s.panel_move(1)
        with self.assertRaises(core.MenuError):s.panel_select_command(False)
        command=s.panel_select_command(True)
        with self.assertRaises(core.MenuError):s.panel_select_command(True)
        h.gcode.run_script(command)
        self.assertIsNotNone(h.load_test_context)
        self.assertFalse(s.observe_buttons(4,2));h.cmd_control_stop(Command())

    def test_cancel_targets_only_the_owned_service_and_never_late_starts(self):
        h,s=self.host();self.execute(s,'purge_t0')
        factory.FactoryHostTests().wait_phase(h,'SWITCH')
        s.panel_back();s.choose(s.view.token,confirm=True)
        before=list(h.device.packets);h.reactor.pause(h.reactor.now+3)
        self.assertEqual(h.device.packets,before);self.assertIsNone(h.load_test_context)
        self.assertFalse(h.device.active())

    def test_missing_native_modules_have_concrete_reasons_and_no_fabricated_results(self):
        h,s=self.host()
        for action in ('leveling','alignment','filecheck_off','ignore_filament'):
            s.open(action);self.assertEqual(s.view.kind,'info')
            self.assertIn('absent',s.view.data['reason'])
        s.open('save_print');self.assertIn('no restart checkpoint',s.view.data['reason'])
        self.assertEqual(h.device.packets,[])

    def test_material_choices_preserve_support_categories_and_do_not_start_on_selection(self):
        h,s=self.host();s.open('spool_catalog_support')
        self.assertEqual(s.view.options[-1][0],'External Support')
        s.choose(s.view.token,index=3)
        self.assertEqual(s.view.kind,'confirm')
        self.assertEqual(s.view.data['parameters'],{'tool':1,'material_id':17})
        self.assertEqual(h.device.packets,[])
        with self.assertRaisesRegex(core.MenuError,'P11/P13'):s.choose(s.view.token,confirm=True)
        self.assertIsNone(h.load_test_context)

    def test_native_P13_prompt_has_one_consumer_and_stale_continue_is_rejected(self):
        h,s=self.host();calls=[]
        obj=types.SimpleNamespace(enabled=True,stopped=False,running=True,job=3,phase='insert_spool',
            prompt={'token':'native:3:1','kind':'insert_spool','accepted':False})
        obj.get_status=lambda now:dict(running=obj.running,phase=obj.phase)
        def advance(cmd):
            self.assertEqual(cmd.get('TOKEN'),obj.prompt['token']);calls.append(cmd.get('TOKEN'));obj.prompt['accepted']=True
        obj.cmd_continue=advance;h.printer.objects['inventure_filament']=obj
        s._tick(h.reactor.now)
        self.assertEqual(s.legacy_buttons(4),0)
        self.assertEqual(s.legacy_buttons(5),1)
        old=s.view.token;obj.prompt={'token':'native:3:2','kind':'remove_spool','accepted':False}
        with self.assertRaises(core.MenuError):s.choose(old,index=1)
        self.assertEqual(calls,[])
        s.last_native_key=None;s._running_view();s.choose(s.view.token,index=1)
        self.assertEqual(calls,['native:3:2'])

    def test_input_adapter_delivers_raw_menu_event_and_suppressed_legacy_OK(self):
        h,s=self.host();events=[]
        h.printer.send_event=lambda *args:events.append(args)
        h.printer.objects['inventure_filament'].running=True
        h.printer.objects['inventure_filament'].prompt={'token':'x'}
        h._inputs({'raw':0,'buttons':4,'sequence':5})
        self.assertEqual(events[0][0],'inventure:menu_buttons');self.assertEqual(events[0][2],4)
        self.assertEqual(events[1][0],'inventure:buttons');self.assertEqual(events[1][2],0)

    def test_sorting_persists_without_starting_a_model(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'menu.json';h,s=self.host(zortrax_settings_file=str(path))
            s.open('sort_name');self.assertEqual(core.SettingsStore(path).value['sorting'],'name')
            self.assertEqual(h.device.packets,[])

    def test_saved_language_is_applied_on_ready_and_M300_launcher_stays_English(self):
        h,s=self.host();s.settings.value['language']='pl'
        menu=menu_module.OEMMenu('en',root_page='root',operational=True)
        oled=types.SimpleNamespace(oem_menu=menu,menu_profile='zortrax')
        h.printer.objects['inventure_oled']=oled;s._ready();self.assertEqual(menu.language,'pl')
        oled.menu_profile='m300';s._ready();self.assertEqual(menu.language,'en')
        self.assertEqual(h.device.packets,[])

    def test_model_source_change_between_preview_and_confirmation_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            h,s=self.host();p=Path(root)/'job.json';p.write_text('{}\n');calls=[]
            sd=types.SimpleNamespace(sdcard_dirname=root,current_file=None,is_active=lambda:False)
            pre=types.SimpleNamespace(enabled=True,stopped=False,epoch=0,ticket=None)
            pre.get_status=lambda now:{'enabled':True,'token':None}
            pre.cmd_check=lambda c:calls.append(c.values)
            h.printer.objects.update(virtual_sdcard=sd,inventure_preflight=pre)
            s._preview('job.json');s.choose(s.view.token,index=2)
            self.assertEqual(s.view.kind,'confirm');p.write_text('{"changed":true}\n')
            with self.assertRaisesRegex(core.MenuError,'changed after preview'):s.choose(s.view.token,confirm=True)
            self.assertEqual(calls,[])

    def test_model_preflight_and_start_use_current_P15_token_and_never_start_on_browse(self):
        with tempfile.TemporaryDirectory() as root:
            h,s=self.host();(Path(root)/'job.json').write_text('{}\n');calls=[]
            sd=types.SimpleNamespace(sdcard_dirname=root,current_file=None,is_active=lambda:False)
            pre=types.SimpleNamespace(enabled=True,stopped=False,epoch=0,ticket=None,busy=False)
            def check(c):pre.epoch+=1;pre.busy=True;calls.append(('check',c.values))
            pre.cmd_check=check
            pre.get_status=lambda now:dict(busy=pre.busy,status={'status':'READY' if pre.ticket else 'CHECKING'},token=None if pre.ticket is None else pre.ticket.token)
            pre.cmd_start=lambda c:calls.append(('start',c.values))
            h.printer.objects.update(virtual_sdcard=sd,inventure_preflight=pre)
            s.open('sd_browser');self.assertEqual(calls,[])
            s.choose(s.view.token,index=1);s.choose(s.view.token,index=2);s.choose(s.view.token,confirm=True)
            self.assertEqual(calls[0][0],'check');self.assertEqual(calls[0][1]['ACK'],'JOB_AND_SPOOL_SUPPLY_CHECKED')
            pre.busy=False;pre.ticket=types.SimpleNamespace(token='p15-current')
            s._running_view();s.choose(s.view.token,index=1)
            self.assertEqual(s.view.kind,'confirm');self.assertEqual(len(calls),1)
            s.choose(s.view.token,confirm=True)
            self.assertEqual(calls[-1],('start',{'TOKEN':'p15-current','ACK':'START_CHECKED_JOB'}))

    def test_native_calibration_phase_change_expires_old_advance_and_commit_is_explicit(self):
        h,s=self.host();calls=[]
        obj=types.SimpleNamespace(enabled=True,stopped=False,running=False,session=None,state='idle')
        def start(c):obj.running=True;obj.session=object();obj.state='measuring';calls.append(('start',c.values))
        obj.cmd_start=start;obj.cmd_advance=lambda c:calls.append(('advance',c.values));obj.cmd_cancel=lambda c:calls.append(('cancel',c.values))
        obj.cmd_commit=lambda c:calls.append(('commit',c.values))
        obj.get_status=lambda now:dict(state=obj.state,deformed=True,instructions=[])
        h.printer.objects['inventure_leveling']=obj
        self.execute(s,'leveling');self.assertEqual(calls[0][1]['ACK'],'KNOBS_AND_TRAY_CHECKED')
        old=s.view.token;obj.state='candidate'
        with self.assertRaises(core.MenuError):s.choose(old,index=1)
        s.last_native_key=None;s._running_view();s.choose(s.view.token,index=1)
        self.assertEqual(s.view.title,'Accept deformed tray?');self.assertEqual(len(calls),1)
        s.choose(s.view.token,confirm=True)
        self.assertEqual(calls[-1],('commit',{'ACK':'SAVE_HOST_LEVELING','DEFORMATION_ACK':'ACCEPT_DEFORMED_TRAY'}))

    def test_alignment_menu_uses_real_choose_Y_choose_X_and_stock_neutral_X(self):
        h,s=self.host();calls=[]
        obj=types.SimpleNamespace(enabled=True,stopped=False,running=True,lease=object(),state='choose_Y',x_mode='stock_reference')
        obj.get_status=lambda now:{'state':obj.state}
        obj.cmd_line=lambda c:calls.append(c.values)
        h.printer.objects['inventure_alignment']=obj
        s.active=dict(backend='align',object=obj,session=obj.lease,action='alignment')
        s._running_view();self.assertEqual(len([x for x in s.view.options if x[1] and x[1][0]=='line']),13)
        s.choose(s.view.token,index=7);self.assertEqual(calls[-1],{'AXIS':'Y','LINE':7})
        obj.state='choose_X';s.last_native_key=None;s._running_view()
        self.assertEqual([x[0] for x in s.view.options if x[1]],['Stop / cancel','Line 7 (neutral)'])

    def test_changed_native_job_cannot_consume_queued_resume(self):
        h,s=self.host();state={'state':'paused'};sd=types.SimpleNamespace(current_file=io.StringIO(''),is_active=lambda:False)
        cycle=types.SimpleNamespace(enabled=True,stopped=False,cmd_resume=lambda c:self.fail('resumed a replacement file'))
        h.printer.objects.update(virtual_sdcard=sd,print_stats=types.SimpleNamespace(get_status=lambda now:state),inventure_cycle=cycle)
        s.open('resume');token=s.view.token;sd.current_file=io.StringIO('')
        self.assertIsNotNone(s.view.job_reference)
        self.assertIsNot(s.view.job_reference,sd.current_file)
        with self.assertRaises(core.MenuError):s.choose(token,confirm=True)

    def test_lost_native_session_is_not_cancelled_as_if_it_were_the_old_owner(self):
        h,s=self.host();calls=[]
        obj=types.SimpleNamespace(job=2,running=True,cmd_cancel=lambda c:calls.append('cancel'))
        s.active=dict(backend='filament',object=obj,job=1,action='load')
        s._cancel_active();self.assertEqual(calls,[])

    def test_heat_dialog_uses_persistent_tool_temperature_and_shared_manual_jog(self):
        h,s=self.host()
        planner=importlib.import_module(adapter.__package__+'.inventure_factory_core')
        h.procedure_profiles=planner.profiles('{"TARGET":225,"MAX_POWER":30}', '{}','{"TARGET":230}')
        s.open('heat_t0');self.assertIn('225C',s.view.title);s.choose(s.view.token,confirm=True)
        self.assertEqual((h.load_test_context['target'],h.load_test_context['power']),(225,30))
        h.cmd_control_stop(Command())
        self.execute(s,'heat_t1');h.temperatures[1]=230.
        h.load_test_context['pid'].update(time=h.reactor.now,temperature=230.,derivative=0.,integral=0.)
        h.reactor.pause(h.reactor.now+3);s._running_view()
        labels=[a[0] for a in s.view.options];self.assertIn('Feed 20mm',labels)
        s.choose(s.view.token,index=labels.index('Feed 20mm'))
        self.assertEqual(h.load_test_context['jog']['direction'],1)
        self.assertEqual(h.load_test_context['jog']['steps'],19200)
        h.cmd_control_stop(Command())

    def test_spool_selection_uses_P11_pair_and_P13_ack_without_using_cartridge_AUTO(self):
        h,s=self.host();calls=[];pair=[{'material_id':10,'source':'spool'},{'material_id':17,'source':'spool'}]
        materials=types.SimpleNamespace(enabled=True,stopped=False,generation=0)
        materials.get_status=lambda now:dict(selection=pair)
        def select(cmd):
            calls.append(('select',cmd.values));materials.generation+=1
            pair[0]=dict(material_id=cmd.get('A'),source=cmd.get('A_SOURCE'))
            pair[1]=dict(material_id=cmd.get('B'),source=cmd.get('B_SOURCE'))
        materials.cmd_select=select
        filament=types.SimpleNamespace(enabled=True,stopped=False,running=False,job=0,prompt=None,phase='idle')
        filament.get_status=lambda now:dict(phase=filament.phase)
        def load(cmd):calls.append(('load',cmd.values));filament.job+=1;filament.running=True;filament.phase='insert_spool'
        filament.cmd_load=load
        h.printer.objects.update(inventure_materials=materials,inventure_filament=filament)
        s.open('spool_catalog_support');s.choose(s.view.token,index=3);s.choose(s.view.token,confirm=True)
        self.assertEqual(calls[0],('select',dict(A=10,B=17,A_SOURCE='spool',B_SOURCE='spool',ACK='MATERIALS_AND_SOURCES_CHECKED')))
        self.assertEqual(calls[1],('load',dict(TOOL=1,ACK='TRANSPORT_PATH_CHECKED')))
        self.assertEqual(h.device.packets,[])

    def test_platform_step_uses_native_owner_bounds_and_preserves_XYE(self):
        h,s=self.host();moves=[];position=[20.,30.,15.,4.];ops=h.printer.objects['inventure_operations']
        @contextmanager
        def procedure(kind):
            self.assertEqual(kind,'calibration');moves.append('begin');yield object();moves.append('end')
        ops.procedure=procedure;ops.require_motion=lambda:None;ops.require_position=lambda axes:self.assertEqual(axes,'z')
        head=types.SimpleNamespace(get_position=lambda:position,get_status=lambda now:{'homed_axes':'xyz'},
            move=lambda pos,speed:moves.append((pos,speed)),wait_moves=lambda:moves.append('wait'))
        h.printer.objects.update(inventure_motion=types.SimpleNamespace(enabled=True),toolhead=head)
        self.execute(s,'platform_up')
        self.assertEqual(moves,['begin',([20.,30.,14.,4.],1.),'wait','end'])
        self.assertEqual(position,[20.,30.,15.,4.]);self.assertEqual(h.device.packets,[])

    def test_paused_native_job_opens_contextual_menu_without_resuming(self):
        h,s=self.host();stats=types.SimpleNamespace(get_status=lambda now:dict(state='paused'))
        h.printer.objects.update(print_stats=stats,virtual_sdcard=types.SimpleNamespace(current_file=io.StringIO(''),is_active=lambda:False))
        s._tick(h.reactor.now)
        self.assertEqual(s.view.kind,'job');self.assertIn('Resume this print',[x[0] for x in s.view.options])
        self.assertEqual(h.device.packets,[])

    def test_external_R7_command_is_observed_by_the_same_menu_status(self):
        h,s=self.host();h.cmd_procedure(Command(ACTION='SELECT',TOOL='T1'))
        s._tick(h.reactor.now)
        self.assertIs(s.active['object'],h.load_test_context)
        self.assertEqual(s.view.kind,'running')
        h.cmd_control_stop(Command())

    def test_fan_menu_returns_to_controls_and_restores_only_its_own_run_on_exit(self):
        h,s=self.host();calls=[];h.commands['fan_manual']=object()
        def manual(cmd):
            calls.append(cmd.values);h.fan_manual_last=dict(run=4,channel=cmd.get('CHANNEL'),phase='on',tach_on_edges=12,tach_off_edges=0)
        h.cmd_fan_manual=manual;h.cmd_fan_manual_status=lambda cmd:None
        h.cmd_fan_manual_cancel=lambda cmd:calls.append({'ACTION':'CANCEL'})
        self.execute(s,'fan_on',channel=2)
        self.assertEqual(s.view.kind,'fan');self.assertEqual(s.fan_session,dict(run=4,channel=2))
        s.choose(s.view.token,index=0)
        self.assertEqual(calls[-1],{'ACTION':'CANCEL'});self.assertIsNone(s.fan_session)
        self.execute(s,'fan_on',channel=2);h.fan_manual_last['run']=5
        s._fan_page(2);before=len(calls);s.choose(s.view.token,index=0)
        self.assertEqual(len(calls),before)

    def test_owned_fan_test_blocks_native_calibration_requests_too(self):
        h,s=self.host();s.fan_session={'run':1,'channel':0}
        h.printer.objects['inventure_leveling']=types.SimpleNamespace(enabled=True,stopped=False)
        s.open('leveling');self.assertEqual(s.view.kind,'info')
        self.assertIn('End the fan-test',s.view.data['reason']);self.assertEqual(h.device.packets,[])


class OledMenuTests(unittest.TestCase,MenuFixture):
    @staticmethod
    def oled_class():
        tree=ast.parse((ROOT/'orange_pi/inventure_oled.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='InventureOLED')
        scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),'real_oled_class','exec'),scope)
        return scope['InventureOLED']

    def test_confirmed_frame_must_match_view_generation_wire_and_transport_session(self):
        h,s=self.host();s.open('select_t0');cls=self.oled_class();o=cls.__new__(cls)
        o.board=h;o.sleep_phase='awake';o.message=None
        o.stream=types.SimpleNamespace(ready=True,last_frame_bytes=b'frame',generation=12)
        v=s.view.render();o._zortrax_frame=(v['page'],v['generation'],b'frame',12)
        self.assertTrue(o._zortrax_acknowledged());s.panel_move(1)
        self.assertFalse(o._zortrax_acknowledged())
        v=s.view.render();o._zortrax_frame=(v['page'],v['generation'],b'frame',12)
        o.stream.generation=13;self.assertFalse(o._zortrax_acknowledged())
        o.stream.generation=12;o.stream.last_frame_bytes=b'old';self.assertFalse(o._zortrax_acknowledged())

    def test_OEM_leaf_opens_shared_procedure_confirmation_without_mcu_write(self):
        h,s=self.host();cls=self.oled_class();o=cls.__new__(cls);o.board=h;o.message=None
        o.oem_menu=menu_module.OEMMenu('en',root_page='launcher',operational=True)
        o.oem_menu.page='load_model';o.oem_menu.selected=1;o.oem_display_mode='menu'
        o._select(h.reactor.now)
        self.assertEqual(s.view.kind,'confirm');self.assertEqual(s.view.data['action'],'load_cartridge_t0')
        self.assertEqual(h.device.packets,[])


if __name__=='__main__':unittest.main(verbosity=2)
