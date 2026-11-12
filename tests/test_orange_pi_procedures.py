"""Shared service procedures: OEM trace regression, profiles and leased execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

from test_orange_pi_factory_core import core, Command as PlannerCommand, Executor, short_feedback, long_feedback
import test_orange_pi_factory_host as factory_host
from test_orange_pi_door_policy import Command

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('factory_r7', ROOT/'tests/fixtures/factory_r7_core.py')
previous = importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)


class ProcedureCoreTests(unittest.TestCase):
    def test_T1_composition_preserves_published_R7_hardware_trace_and_OEM_decisions(self):
        self.assertEqual(hashlib.sha256(Path(previous.__file__).read_bytes()).hexdigest(),
            '3354997b7071b009202bb680edd0c6d23d5baa97179e9b7651428fb0bdcd4af9')
        def no_final_starts(ex, action, result):
            long_feedback(ex, action, result)
            if result is not None and ex.phase=='L4':result['starts']=108
        def trace(planner,p,hook):
            ex=Executor(p,hook,planner=planner);error=None
            try:ex.run()
            except ValueError as exc:error=(str(exc),exc.details)
            actions=[]
            for raw in ex.actions:
                if raw['kind']=='module':continue
                action=dict(raw)
                action.pop('cleaning_done',None)
                action.pop('leg',None)  # selector diagnostics do not change the actuator request
                actions.append(action)
            return actions,error
        for hook in (short_feedback,long_feedback,no_final_starts):
            for final_mm in (50.,100.):
                with self.subTest(path=hook.__name__,final_mm=final_mm):
                    p=core.defaults(1);p['final_mm']=final_mm
                    self.assertEqual(trace(core,p,hook),trace(previous,p,hook))

    def test_T0_material_profile_applies_before_config_and_command_overrides(self):
        cfg=core.profiles('{}','{"MATERIAL":"Z-PETG","UNLOAD_L1":45}','{}')
        p=core.parameters(PlannerCommand(UNLOAD_L2=55),cfg)
        self.assertEqual((p['material'],p['unload_l1'],p['unload_l2']),('Z-PETG',45.,55.))
        p=core.parameters(PlannerCommand(MATERIAL='Z-PETG'))
        self.assertEqual((p['unload_l1'],p['unload_l2'],p['target']),(50.,50.,230.))
        flex=core.parameters(PlannerCommand(MATERIAL='Z-FLEX'))
        self.assertEqual((flex['feeder_ms'],flex['feeder_k']),(1500,3.))
        with self.assertRaisesRegex(core.FactoryError,'T0 cassette'):
            core.parameters(PlannerCommand(TOOL='T1',MATERIAL='Z-PETG'))

    def test_standalone_select_is_cold_and_all_modules_finish_without_E(self):
        for tool in (0,1):
            ex=Executor(core.defaults(tool),operation='SELECT').run()
            self.assertFalse(any(a['kind']=='heat' or a.get('e') for a in ex.actions))
            home=[(a['kind'],a['axis']) for a in ex.actions if a['kind'] in ('home','home_release')]
            if tool==0:
                self.assertEqual(home,[('home','Z'),('home','Y'),('home_release','Y'),
                                       ('home','X'),('home_release','X')])
            else:
                self.assertEqual(home,[('home','Z'),('home','X'),('home','Y')])
            select=[a for a in ex.actions if a['kind']=='move']
            approach,press=select[1:3]
            if tool==0:
                self.assertEqual((approach['x'],approach['y']),(139.5,154.5))
                self.assertEqual((press['x'],press['y']),(154.5,154.5))
                self.assertEqual(press['speed'],250./60.)
            else:
                self.assertEqual((press['x'],press['y']),(-4.5,152.))
            self.assertEqual(ex.done['terminal'],'SELECT')
            self.assertFalse(ex.done['cleaning_done'])

    def test_standalone_clean_uses_wipe_geometry_independent_of_selector(self):
        p=core.parameters(PlannerCommand(TOOL='T1',WIPE_Y=149,WIPE_SPAN=80,WIPE_EXIT=15))
        ex=Executor(p,operation='CLEAN').run()
        wipes=[a for a in ex.in_phase('CLEAN','move')]
        self.assertEqual([(a['x'],a['y']) for a in wipes[-3:]],[(107.5,149),(27.5,149),(52.5,149)])
        self.assertTrue(all(a['e']==0 for a in wipes))
        self.assertEqual([a for a in ex.in_phase('SWITCH','move')][2]['y'],152)
        self.assertTrue(ex.done['cleaning_done'])

    def test_configurable_purge_span_and_E_keep_full_portion_and_optional_clean(self):
        for clean in (0,1):
            p=core.parameters(PlannerCommand(PURGE_SPAN=30,PURGE_E_STEPS=1920,
                PURGE_MM=4,CLEAN_AFTER=clean))
            ex=Executor(p,operation='PURGE').run()
            moves=[a for a in ex.in_phase('PURGE','move') if a['e']]
            self.assertEqual([(a['x'],a['y']) for a in moves],
                             [(67.8,152.5),(97.8,152.5),(67.8,152.5)])
            self.assertEqual([round(a['e']*960) for a in moves],[960,1920,1920])
            self.assertEqual(ex.done['cleaning_done'],bool(clean))
            self.assertEqual(any(a['kind']=='module' and a['name']=='clean' for a in ex.actions),bool(clean))

    def test_manual_feed_has_logical_direction_and_optional_assistance(self):
        for direction,sign in (('LOAD',1),('UNLOAD',-1)):
            for assist in (0,1):
                p=core.parameters(PlannerCommand(MANUAL_MM=12,MANUAL_DIR=direction,FEEDER=assist))
                ex=Executor(p,operation='MANUAL').run()
                extrusions=[a for a in ex.actions if a.get('e')]
                self.assertEqual(len(extrusions),1);self.assertEqual(extrusions[0]['e'],sign*12)
                self.assertEqual((extrusions[0]['x'],extrusions[0]['y']),(82.8,152.5))
                modes=[a['mode'] for a in ex.actions if a['kind']=='mode']
                self.assertIn((2 if sign>0 else 3) if assist else 0,modes)
                self.assertEqual(ex.done['terminal'],'MANUAL')

    def test_standalone_unload_requires_fresh_empty_A_and_uses_same_U_modules(self):
        ex=Executor(operation='UNLOAD').run()
        self.assertEqual(ex.done['terminal'],'UNLOAD')
        self.assertEqual(ex.program.history[-5:],['U0','U1','U2','U3','DONE'])
        def still_present(ex,a,r):
            if a['kind']=='retract_until_a':r.update(a=1,ok=False)
        with self.assertRaisesRegex(core.FactoryError,'unload_sensor_still_present'):
            Executor(hook=still_present,operation='UNLOAD').run()

    def test_persistent_layers_and_command_overrides_are_isolated_between_tools(self):
        cfg=core.profiles('{"E_SPEED":2,"TARGET":220,"MAX_POWER":35}',
            '{"TARGET":225,"MANUAL_MM":15}', '{"TARGET":230,"E_SPEED":3}')
        a=core.parameters(PlannerCommand(TOOL='T0'),cfg)
        b=core.parameters(PlannerCommand(TOOL='T1',E_SPEED=4),cfg)
        self.assertEqual((a['e_speed'],a['target'],a['manual_mm'],a['max_power']),(2,225,15,35))
        self.assertEqual((b['e_speed'],b['target'],b['manual_mm'],b['max_power']),(4,230,20,35))
        b['target']=240
        self.assertEqual(core.parameters(PlannerCommand(TOOL='T1'),cfg)['target'],230)
        self.assertEqual(core.defaults(0)['target'],230)

    def test_profile_and_motion_errors_fail_before_first_action(self):
        for value in ('[]','null','{"TOOL":"T1"}','{"E_SPEDE":2}',
                '{"TARGET":300}','{"FEEDER":2}','{"MANUAL_DIR":"RIGHT"}',
                '{"e_speed":1,"E_SPEED":2}'):
            with self.subTest(value=value),self.assertRaises(core.FactoryError):core.profiles(value)
        for values in ({'PURGE_SPAN':1,'PURGE_E_STEPS':96000},
                {'WIPE_Y':999},{'WIPE_SPAN':200},{'MANUAL_MM':250,'E_SPEED':.1},
                {'TARGET':float('nan')},{'DURATION':901},{'MAX_POWER':51}):
            with self.subTest(values=values),self.assertRaises(core.FactoryError):
                core.parameters(PlannerCommand(**values))

    def test_module_stack_is_balanced_for_composed_and_standalone_paths(self):
        for operation in core.PROCEDURES:
            ex=Executor(hook=long_feedback,operation=operation).run();stack=[]
            for a in ex.actions:
                if a['kind']!='module':continue
                if a['state']=='start':stack.append(a['name'])
                else:self.assertEqual(stack.pop(),a['name'])
            self.assertEqual(stack,[])
            self.assertEqual(sum(a['kind']=='home' for a in ex.actions),3)


class ProcedureHostTests(unittest.TestCase):
    def setUp(self):self.fixture=factory_host.FactoryHostTests()

    def test_catalog_and_parameter_queries_are_read_only_on_old_MCU(self):
        h,_,_=self.fixture.host();h.commands.pop('factory_session')
        for action in ('LIST','PARAMS'):
            h.cmd_procedure(Command(ACTION=action,TOOL='T1',PROCEDURE='CLEAN'))
        self.assertIsNone(h.load_test_context);self.assertEqual(h.device.packets,[])
        for action in ('SELECT','CLEAN','PURGE','MANUAL','UNLOAD','AUTO','HOME','PARK','SWEEP','BOGUS'):
            with self.assertRaises(RuntimeError):h.cmd_procedure(Command(ACTION=action))
        self.assertEqual(h.device.packets,[])

    def test_select_completes_without_heat_or_E_for_both_tools(self):
        for tool in (0,1):
            h,_,_=self.fixture.host();h.cmd_procedure(Command(ACTION='SELECT',TOOL='T%d'%tool))
            last=self.fixture.finish(h)
            self.assertEqual(last['phase'],'done',last)
            self.assertFalse(any(p[:2]==('output',1) for p in h.device.packets))
            self.assertFalse(any(p[0]=='motion' and p[1]==1 and p[6] for p in h.device.packets))
            self.assertEqual(last['factory']['operation'],'SELECT')
            self.assertTrue(last['factory']['selection_commanded'])
            self.assertFalse(last['factory_transport_completed'])
            self.assertEqual(last['factory']['module_stack'],[])
            self.assertFalse(last['factory']['reference_valid'])

    def test_T0_park_references_Y_X_then_parks_cold_without_select_or_E(self):
        h,_,_=self.fixture.host()
        h.cmd_procedure(Command(ACTION='PARK',TOOL='T0'))
        last=self.fixture.finish(h)
        f=last['factory']
        self.assertEqual(f['history'],['HOME','PARK','DONE'])
        self.assertEqual(f['position'],[82.8,152.5,0.])
        self.assertEqual(f['e_steps_by_phase'],{})
        self.assertFalse(f['selection_commanded'])
        self.assertFalse(any(p[:2]==('output',1) for p in h.device.packets))

    def test_T0_unload_uses_material_step_caps_and_reports_each_phase(self):
        for name,steps in (('Z-PLA',(28800,67200)),('Z-PETG',(48000,48000))):
            with self.subTest(material=name):
                h,_,_=self.fixture.host()
                h.cmd_procedure(Command(ACTION='UNLOAD',TOOL='T0',MATERIAL=name))
                last=self.fixture.finish(h)
                self.assertEqual(last['phase'],'done',last)
                self.assertEqual(last['factory']['position'],[82.8,152.5,0.])
                self.assertEqual(last['factory']['e_steps_by_phase'],
                                 dict(U1=-steps[0],U2=-steps[1]))

    def test_T0_unload_on_silence_issues_only_one_step_in_U2(self):
        h,_,_=self.fixture.host('no_edges')
        h.cmd_procedure(Command(ACTION='UNLOAD',TOOL='T0'))
        last=self.fixture.finish(h)
        self.assertEqual(last['phase'],'done',last)
        self.assertEqual(last['factory']['e_steps_by_phase']['U2'],-1)

    def test_clean_and_purge_share_owner_heater_selection_and_completion_cleanup(self):
        for operation in ('CLEAN','PURGE'):
            h,_,_=self.fixture.host()
            h.cmd_procedure(Command(ACTION=operation,TOOL='T1',PURGE_MM=2))
            last=self.fixture.finish(h)
            self.assertEqual(last['phase'],'done',last)
            self.assertFalse(last['cleaning_pending'])
            self.assertFalse(last['factory_transport_completed'])
            self.assertTrue(any(p[:2]==('output',1) and p[2]==7 for p in h.device.packets))
            self.assertFalse(any(p[:2]==('output',1) and p[2]==6 for p in h.device.packets))
            E=[p for p in h.device.packets if p[0]=='motion' and p[1]==1 and p[6]]
            self.assertEqual(bool(E),operation=='PURGE')
            self.assertEqual(h.device.output(7)['duty'],0);self.assertFalse(h.device.active())
            self.assertEqual(h.device.auto_entries,0)

    def test_manual_direction_is_independent_of_tool_polarity_and_counter_proof(self):
        for tool in (0,1):
            for direction,reverse in (('LOAD',False),('UNLOAD',True)):
                h,_,_=self.fixture.host('no_edges')
                h.cmd_procedure(Command(ACTION='MANUAL',TOOL='T%d'%tool,
                    MANUAL_MM=3,MANUAL_DIR=direction,FEEDER=1))
                last=self.fixture.finish(h)
                self.assertEqual(last['phase'],'done',last)
                E=[p for p in h.device.packets if p[0]=='motion' and p[1]==1 and p[6]]
                self.assertEqual(len(E),1);self.assertEqual(E[0][6],2880)
                self.assertEqual(E[0][2]>>3 & 1,tool^int(reverse))
                self.assertIn(3 if reverse else 2,h.device.mode_history)
                self.assertEqual(last['factory']['e_sent_steps'],-2880 if reverse else 2880)
                self.assertFalse(last['factory_transport_completed']);self.assertFalse(last['load_completed'])

    def test_standalone_unload_reuses_feedback_and_does_not_report_loaded(self):
        h,_,_=self.fixture.host();h.cmd_procedure(Command(ACTION='UNLOAD',TOOL='T1'))
        last=self.fixture.finish(h)
        self.assertEqual(last['phase'],'done',last)
        self.assertEqual(last['factory']['result']['terminal'],'UNLOAD')
        self.assertFalse(last['factory']['fallback_started'])
        self.assertFalse(last['factory_transport_completed']);self.assertFalse(last['load_completed'])

    def test_profiles_set_real_thermal_context_and_are_snapshotted_for_whole_session(self):
        h,_,_=self.fixture.host()
        h.procedure_profiles=core.profiles('{"TARGET":225,"MAX_POWER":30}',
            '{"MANUAL_MM":5}','{"MANUAL_MM":8}')
        h.cmd_procedure(Command(ACTION='MANUAL',TOOL='T1',MANUAL_MM=2))
        ctx=h.load_test_context
        self.assertEqual((ctx['target'],ctx['power']),(225,30))
        h.procedure_profiles[0]['target']=230
        last=self.fixture.finish(h)
        self.assertEqual(last['factory']['parameters']['target'],225)
        self.assertEqual(last['factory']['e_sent_steps'],1920)

    def test_config_parser_reports_bad_persistent_key_at_startup(self):
        h,_,_=self.fixture.host()
        class Config:
            def get(self,key,default=None):return '{"E_SPEDE":2}' if key=='procedure_t1' else default
            def error(self,message):return RuntimeError(message)
        with self.assertRaisesRegex(RuntimeError,'unknown parameter E_SPEDE'):
            h._configure_procedures(Config())
        self.assertEqual(h.device.packets,[])

    def test_STOP_in_each_new_hot_procedure_prevents_late_START(self):
        for operation,phase in (('CLEAN','CLEAN'),('PURGE','PURGE'),('MANUAL','MANUAL'),('UNLOAD','U1')):
            h,_,_=self.fixture.host();h.cmd_procedure(Command(ACTION=operation))
            self.fixture.wait_phase(h,phase)
            h.cmd_procedure(Command(ACTION='STOP'))
            before=list(h.device.packets);sessions=list(h.device.factory_packets)
            h.reactor.pause(h.reactor.now+3.)
            self.assertEqual(h.device.packets,before);self.assertEqual(h.device.factory_packets,sessions)
            self.assertFalse(h.device.active());self.assertEqual(h.device.output(6)['duty'],0)


if __name__=='__main__':unittest.main(verbosity=2)
