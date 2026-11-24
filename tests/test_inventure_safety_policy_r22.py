"""R22 read-only policy diagnostic: SERVICE strict, NATIVE broad and separate."""
import ast
import json
from pathlib import Path
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
HOST=ROOT/'restored/safety_policy_r22/inventure_mcu.py'
CTRL=ROOT/'service_mcu/control_r6/SOURCE_MCU/inventure_control.inc'

def policy_method():
    tree=ast.parse(HOST.read_text())
    cls,=[node for node in tree.body if isinstance(node,ast.ClassDef)
          and node.name=='InventureMCU']
    method,=[node for node in cls.body if isinstance(node,ast.FunctionDef)
             and node.name=='cmd_safety_policy_status']
    constants,=[node for node in tree.body if isinstance(node,ast.Assign)
                and any(isinstance(target,ast.Name) and
                        target.id=='NATIVE_ABSOLUTE_MAX_C' for target in node.targets)]
    module=ast.Module(body=[ast.Import(names=[ast.alias(name='json')]),
                            constants,method],type_ignores=[])
    namespace={}
    exec(compile(ast.fix_missing_locations(module),str(HOST),'exec'),namespace)
    return namespace['cmd_safety_policy_status']

def response(**updates):
    row=dict(mode=1,profile=2,service_mask=127,native_hot=0,
             native_powered=0,native_required=0,fault=0,latched=0)
    row.update(updates)
    return row

class GCode:
    def __init__(self):self.messages=[]
    def error(self,message):return ValueError(message)
    def respond_info(self,message):self.messages.append(message)

def validate(row,limits):
    for k,(lo,hi) in limits.items():
        if type(row.get(k)) is not int or not lo<=row[k]<=hi:
            raise ValueError('invalid MCU policy field: '+k)

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.actions=[]
        self.method=policy_method()
        self.gcode=GCode()
        self.row=response()
        self.board=types.SimpleNamespace(
            commands={'safety_policy_diag':types.SimpleNamespace(
                send=lambda values:self.actions.append(tuple(values)) or dict(self.row))},
            ADC_PINS=('PC0','PF8','PC1','PC2','PA4','PF3','PF4','PC4'),
            _control_validate=validate)

    def report(self):
        self.method(self.board,self.gcode)
        return json.loads(self.gcode.messages[-1].split(' ',1)[1])

    def test_normal_home_does_not_inherit_service_mask_or_temperature_window(self):
        self.row.update(service_mask=255,mode=1,profile=2)
        result=self.report()
        self.assertEqual(result['effective_profile'],'NORMAL')
        self.assertEqual(result['strict_service_mask'],255)
        self.assertFalse(result['strict_service_limits_apply_to_normal_motion'])
        self.assertEqual(result['native_absolute_max_c'],
                         [310,310,150,150,150,100,100,190])
        self.assertEqual(self.actions,[()])
        self.assertTrue(result['report_is_read_only'])

    def test_service_reports_strict_mask_without_changing_runtime(self):
        self.row.update(mode=2,profile=1,service_mask=5)
        result=self.report()
        self.assertEqual(result['effective_profile'],'STRICT_SERVICE')
        self.assertEqual(result['strict_service_mask'],5)
        self.assertEqual(self.actions,[()])

    def test_native_powered_heater_requires_sensor_and_psu_board(self):
        self.row.update(native_hot=1,native_powered=1,native_required=97)
        result=self.report()
        self.assertEqual(result['native_required_sensors'],['PC0','PF3','PF4'])
        self.assertEqual(result['native_required'],97)
        self.assertEqual(self.actions,[()])

    def test_cold_unused_sensor_is_not_required_for_normal_motion(self):
        self.row.update(service_mask=255,native_hot=0,native_powered=0,
                        native_required=0)
        result=self.report()
        self.assertEqual(result['native_required_sensors'],[])

    def test_invalid_owner_or_required_mask_is_rejected(self):
        for updated,key in ((dict(mode=1,profile=1),'profile'),
                            (dict(native_powered=1,native_required=1),'requirements'),
                            (dict(mode=5),'mode')):
            with self.subTest(updated=updated):
                self.row=response(**updated)
                with self.assertRaises(ValueError) as exc:self.report()
                self.assertIn(key,str(exc.exception))

    def test_older_mcu_refuses_unsupported_query_without_writes(self):
        self.board.commands={}
        with self.assertRaisesRegex(ValueError,'MCU R22'):
            self.report()
        self.assertEqual(self.actions,[])

    def test_firmware_enforces_strict_only_in_service(self):
        text=CTRL.read_text()
        self.assertIn('static uint8_t service_sensors_valid(',text)
        self.assertIn('static uint8_t native_sensors_valid(',text)
        self.assertIn('void command_inventure_factory_native_handoff(',text)
        handoff=text.split('void command_inventure_factory_native_handoff(',1)[1]
        self.assertIn('native_sensors_valid(now)',handoff)
        self.assertNotIn('service_sensors_valid(',handoff)
        native=text.split('if(control_mode==CT_NATIVE) {',1)[1].split('return;',1)[0]
        self.assertIn('native_sensors_valid(now)',native)
        self.assertNotIn('service_sensors_valid(',native)

if __name__=='__main__':unittest.main()
