"""Reproduce the feeder ownership error through the real legacy dispatcher."""
import ast
import json
from pathlib import Path
import types
import unittest
import test_orange_pi_control as control
from test_orange_pi_door_policy import Command

ROOT=Path(__file__).resolve().parents[1]

def legacy_dispatch():
    # Compile the unmodified real method. Greenlet identity is a fixed test
    # context; no dependency or full procedure module is replaced on the host.
    tree=ast.parse((ROOT/'tests/fixtures/inventure_operations_legacy.py').read_text())
    env={'getcurrent':lambda:'test_context'}
    for node in tree.body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in ('MOTION_COMMANDS','FORBIDDEN_REFERENCE_COMMANDS','CAL_EDIT'):
                    env[target.id]=ast.literal_eval(node.value)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='InventureOperations')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_dispatch')
    exec(compile(ast.Module(body=[method],type_ignores=[]),'real_legacy_operations_dispatch','exec'),env)
    return env['_dispatch']

class ServicePrinter(control.Printer):
    def __init__(self,objects):self.objects=objects
    def lookup_object(self,name,default=None):return self.objects.get(name,default)

class FeederServiceTests(unittest.TestCase):
    def host(self):
        h=control.ControlHostTests().host()
        state=types.SimpleNamespace(owner=None,mode='idle',fatal=None)
        ops=types.SimpleNamespace(state=state,contexts={},cancel_in_progress=False,current_lease=lambda:None)
        filament=types.SimpleNamespace(enabled=True,running=False,assisting=False,configuring=False,jog_until=0.)
        h.printer=ServicePrinter({'inventure_operations':ops,'inventure_filament':filament})
        return h,ops,filament

    def dispatch(self,h,ops):
        dispatch=control.gcode.GCodeDispatch(h.printer)
        dispatch.register_output_handler(lambda line:None)
        dispatch.register_command('INVENTURE_FEEDER',h.cmd_feeder,True)
        tree=ast.parse((ROOT/'orange_pi/inventure_mcu.py').read_text())
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Attribute) and n.func.attr=='register_command'
            and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value=='INVENTURE_FEEDER_TEST')
        dispatch.register_command(call.args[0].value,getattr(h,call.args[1].attr),True)
        original_dispatch=legacy_dispatch()
        # Same wrapping point and extended parser as InventureOperations._connect.
        for name,handler in list(dispatch.ready_gcode_handlers.items()):
            if name not in ('INVENTURE_FEEDER','INVENTURE_FEEDER_TEST'):continue
            def guarded(gcmd,name=name,handler=handler):
                parsed=dispatch._get_extended_params(gcmd)
                return original_dispatch(ops,name,handler,gcmd,parsed)
            dispatch.ready_gcode_handlers[name]=guarded
            if dispatch.base_gcode_handlers.get(name) is handler:dispatch.base_gcode_handlers[name]=guarded
        dispatch._handle_ready()
        return dispatch

    def test_exact_reported_legacy_error_precedes_the_mcu_and_service_entrypoint_works(self):
        h,ops,filament=self.host();dispatch=self.dispatch(h,ops)
        with self.assertRaisesRegex(control.gcode.CommandError,'Inventure feeder requires owned load/unload/calibration'):
            dispatch.run_script_from_command('INVENTURE_FEEDER CHANNEL=0 DIRECTION=0 DURATION_MS=100')
        self.assertEqual(h.commands['feeder'].actions,[])
        dispatch.run_script_from_command('INVENTURE_FEEDER_TEST CHANNEL=0 DIRECTION=0 DURATION_MS=100')
        self.assertEqual(h.commands['feeder'].actions,[(0,0,100)])
        self.assertEqual(h.commands['motion'].actions,[])
        self.assertEqual(h.commands['output'].actions,[])

    def test_two_channels_and_both_directions_run_only_a_bounded_request(self):
        for channel in (0,1):
            for direction in (0,1):
                h,ops,filament=self.host();g=Command(CHANNEL=channel,DIRECTION=direction,DURATION_MS=100)
                h.cmd_feeder_test(g)
                self.assertEqual(h.commands['feeder'].actions,[(channel,direction,100)])
                report=json.loads(g.lines[-1].split(' ',1)[1])
                self.assertEqual(report['phase'],'requested')
                self.assertFalse(report['physical_motion_confirmed'])

    def test_off_native_fault_latch_busy_and_disconnect_refuse_without_actuation(self):
        for changes in ({'mode':0},{'mode':1,'armed':1},{'fault':6},{'latched':1},{'active':1},{'disconnect':True}):
            h,ops,filament=self.host();changes=dict(changes)
            if changes.pop('disconnect',False):h.connected=False
            h.commands['control'].reply.update(changes)
            with self.assertRaises(RuntimeError):h.cmd_feeder_test(Command(CHANNEL=0))
            self.assertEqual(h.commands['feeder'].actions,[])

    def test_global_owner_or_cancellation_cannot_be_bypassed_through_real_wrapper(self):
        for variant in ('print','load','unload','calibration','fatal','draining','cancelling'):
            h,ops,filament=self.host();dispatch=self.dispatch(h,ops)
            if variant=='fatal':ops.state.fatal='fault'
            elif variant=='cancelling':ops.cancel_in_progress=True
            elif variant=='draining':ops.state.mode='draining'
            else:ops.state.owner=types.SimpleNamespace(kind=variant);ops.state.mode='running'
            with self.assertRaisesRegex(control.gcode.CommandError,'idle operations'):
                dispatch.run_script_from_command('INVENTURE_FEEDER_TEST CHANNEL=0 DURATION_MS=100')
            self.assertEqual(h.commands['feeder'].actions,[])

    def test_enabled_idle_p13_is_compatible_but_active_filament_work_is_not(self):
        for flag in ('running','assisting','configuring','jog_until'):
            h,ops,filament=self.host();setattr(filament,flag,101. if flag=='jog_until' else True)
            with self.assertRaisesRegex(RuntimeError,'active filament procedure'):
                h.cmd_feeder_test(Command(CHANNEL=0))
            self.assertEqual(h.commands['feeder'].actions,[])
        h,ops,filament=self.host();h.cmd_feeder_test(Command(CHANNEL=1))
        self.assertEqual(h.commands['feeder'].actions,[(1,0,100)])

    def test_stop_requests_zero_duration_without_service_mode_permission_or_ownership(self):
        h,ops,filament=self.host();h.connected=False;ops.state.owner=types.SimpleNamespace(kind='print')
        filament.running=True
        h.cmd_feeder_test(Command(CHANNEL=1,ACTION='STOP',DIRECTION=999,DURATION_MS=999999))
        self.assertEqual(h.commands['feeder'].actions,[(1,0,0)])
        self.assertEqual(h.commands['control'].actions,[])

    def test_invalid_channel_direction_time_and_action_never_send_a_feeder_request(self):
        for changes in ({'CHANNEL':2},{'CHANNEL':-1},{'DIRECTION':2},
                {'DURATION_MS':1501},{'DURATION_MS':-1},{'ACTION':'LOAD'}):
            h,ops,filament=self.host();values={'CHANNEL':0};values.update(changes)
            with self.assertRaises(RuntimeError):h.cmd_feeder_test(Command(**values))
            self.assertEqual(h.commands['feeder'].actions,[])

if __name__=='__main__':unittest.main(verbosity=2)
