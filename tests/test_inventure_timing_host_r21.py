"""R21 timing status must be read-only and report MCU overload explicitly."""
import ast
import json
from pathlib import Path
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'restored/scheduler_r21/inventure_mcu.py'

def host_method():
    tree=ast.parse(SOURCE.read_text())
    cls,= [x for x in tree.body if isinstance(x,ast.ClassDef)
           and x.name=='InventureMCU']
    method,=[x for x in cls.body if isinstance(x,ast.FunctionDef)
             and x.name=='cmd_timing_status']
    module=ast.Module(body=[ast.Import(names=[ast.alias(name='json')]),
                            ast.Import(names=[ast.alias(name='logging')]),
                            method], type_ignores=[])
    scope={}
    exec(compile(ast.fix_missing_locations(module),str(SOURCE),'exec'),scope)
    return scope['cmd_timing_status']

def payload(**overrides):
    state=dict(clock=2048,calls=2020,native=1800,service=220,
               guards=18,skipped=0,resync=0,peak_late=120,peak_work=600,
               last_clock=1940,last_late=60,last_work=400,mode=1,fault=0)
    state.update(overrides)
    return state

def validate(row,limits):
    for key,(lo,hi) in limits.items():
        if type(row.get(key)) is not int or not lo<=row[key]<=hi:
            raise ValueError('Malformed R21 MCU timing field: '+key)

class Command:
    def __init__(self):self.messages=[]
    def error(self,description):return ValueError(description)
    def respond_info(self,s):self.messages.append(s)

class TimingStatusTests(unittest.TestCase):
    def setUp(self):
        self.method=host_method()
        self.read=[]
        self.mcu=types.SimpleNamespace(get_constants=lambda:dict(CLOCK_FREQ=120000000))
        self.reactor=types.SimpleNamespace(monotonic=lambda:7.5)
        self.board=types.SimpleNamespace(mcu=self.mcu,reactor=self.reactor,
                   commands=dict(scheduler_diag=types.SimpleNamespace(
                       send=lambda args:self.read.append(tuple(args)) or payload())),
                   _control_validate=validate)
        self.gcode=Command()

    def test_status_does_not_modify_mcu_or_other_command_queues(self):
        self.method(self.board,self.gcode)
        self.assertEqual(self.read,[()])
        message=self.gcode.messages[0]
        self.assertTrue(message.startswith('INVENTURE_TIMING {'))
        result=json.loads(message.split(' ',1)[1])
        self.assertEqual(result['peak_late_us'],1.0)
        self.assertEqual(result['peak_work_us'],5.0)
        self.assertEqual(result['warnings'],[])
        self.assertEqual(result['mode'],1)

    def test_detects_new_missed_deadline_and_overdue_samples(self):
        self.method(self.board,self.gcode)
        state=payload(resync=2,skipped=5,peak_late=24000)
        self.board.commands['scheduler_diag'].send=lambda args: state
        self.method(self.board,self.gcode)
        result=json.loads(self.gcode.messages[-1].split(' ',1)[1])
        self.assertIn('resync_increased_by_2',result['warnings'])
        self.assertIn('skipped_increased_by_5',result['warnings'])
        self.assertEqual(result['peak_late_us'],200.)

    def test_missing_r21_abi_is_reported_without_any_motion(self):
        self.board.commands.clear()
        with self.assertRaisesRegex(ValueError,'MCU R21'):
            self.method(self.board,self.gcode)
        self.assertEqual(self.read,[])

    def test_invalid_counter_is_rejected_instead_of_accepted(self):
        self.board.commands['scheduler_diag'].send=lambda args: payload(mode=999)
        with self.assertRaisesRegex(ValueError,'mode'):
            self.method(self.board,self.gcode)

    def test_identity_audit_points_one_two_are_in_installed_runtime(self):
        source=SOURCE.read_text()
        homing=(ROOT/'restored/scheduler_r21/inventure_native_z.py').read_text()
        self.assertIn("phase='service_handoff_requested'",source)
        self.assertIn("phase='service_handoff_verified'",source)
        self.assertIn("phase='klipper_xy_certified'",homing)
        self.assertIn("phase='klipper_xy_imported'",homing)
        self.assertIn("Missing verified MCU-to-Klipper handoff receipt",homing)

if __name__=='__main__': unittest.main()
