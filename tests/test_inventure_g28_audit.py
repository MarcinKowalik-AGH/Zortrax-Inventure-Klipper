"""The live G28 audit must never execute printer source or send G-code."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('inventure_readonly_g28_audit',ROOT/'tools/inventure_g28_audit.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ReadOnlyG28AuditTests(unittest.TestCase):
    def test_uses_only_read_endpoints_and_parses_source_without_importing_it(self):
        calls=[]
        settings={'gcode_macro INVENTURE_HOME_X':{'gcode':'INVENTURE_HOME_REQUEST AXES=X'},
                  'stepper_x':{'endstop_pin':'^PB11','enable_pin':'PE10'}}
        def request(endpoint):
            calls.append(endpoint)
            if endpoint=='/printer/info':return {'result':{'state':'ready','klipper_path':'/runtime','process_id':42}}
            if endpoint.startswith('/printer/objects/query?'):
                return {'result':{'status':{'configfile':{'settings':settings},'inventure_native_z':{
                    'host_module':'/runtime/klippy/extras/inventure_native_z.py',
                    'home_procedure_profile':'oem-home-r38.6'}}}}
            if endpoint=='/printer/gcode/help':return {'result':{'G28':'Home axes'}}
            self.fail('Unexpected endpoint: '+endpoint)
        def read_file(path):
            if Path(path).name=='homing.py':
                return b"raise RuntimeError('must never execute')\nclass PrinterHoming:\n    def cmd_G28(self, cmd):\n        pass\n"
            return b"raise RuntimeError('must never execute')\nclass InventureNativeZ:\n    def _home_limit(self, axis):\n        pass\n    def _prepare_t0(self):\n        self.enable.motor_off()\n"
        result=module.audit(request,read_file)
        self.assertEqual(calls,['/printer/info','/printer/objects/query?configfile&inventure_native_z&inventure_mcu',
                                '/printer/gcode/help'])
        self.assertEqual(result['status'],'READ_ONLY_G28_AUDIT')
        self.assertEqual(result['configured_overrides'],{})
        self.assertEqual(result['home_macros']['gcode_macro INVENTURE_HOME_X'],'INVENTURE_HOME_REQUEST AXES=X')
        self.assertEqual(result['source_on_disk']['native']['motor_off_calls'],[{'method':'_prepare_t0','line':6}])
        self.assertFalse(result['gcode_sent'])
        self.assertFalse(result['configuration_written'])
        self.assertFalse(result['service_changed'])

    def test_reports_an_existing_override_and_missing_source_without_changing_either(self):
        def request(endpoint):
            if endpoint=='/printer/info':return {'result':{'state':'shutdown','klipper_path':'/runtime'}}
            if endpoint=='/printer/gcode/help':return {'error':{'message':'Printer is shutdown'}}
            return {'result':{'status':{'configfile':{'config':{
                'homing_override':{'gcode':'USER_HOME {rawparams}'}}}}}}
        def missing(path):raise FileNotFoundError(str(path))
        result=module.audit(request,missing)
        self.assertIn('homing_override',result['configured_overrides'])
        self.assertEqual(result['g28_help_error'],{'message':'Printer is shutdown'})
        self.assertIn('error',result['source_on_disk']['core'])
        self.assertFalse(result['gcode_sent'])


if __name__=='__main__':unittest.main()
