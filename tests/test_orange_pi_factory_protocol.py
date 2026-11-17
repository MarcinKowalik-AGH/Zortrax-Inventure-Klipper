"""Wire agreement with the dictionary from the delivered ARM application."""
import json
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from test_orange_pi_door_policy import adapter


class FactoryProtocolTests(unittest.TestCase):
    def test_current_r10_dictionary_accepts_the_complete_host_configuration(self):
        path = Path(__file__).with_name('fixtures')/'control_r10.dict'
        dictionary = json.loads(path.read_bytes())
        self.assertEqual(dictionary['config']['INVENTURE_MCU_RELEASE'],
                         'RC8-CONTROL-R7-PAD-THERMAL-R10')
        commands = []
        responses = []
        def command(fmt, **kwargs):
            self.assertIn(fmt, dictionary['commands'])
            commands.append(fmt)
            return Mock()
        def query(fmt, response, **kwargs):
            self.assertIn(response, dictionary['responses'])
            responses.append(response)
            return command(fmt)
        def register(callback, fmt, **kwargs):
            self.assertIn(fmt, dictionary['responses'])
            responses.append(fmt)
        host = adapter.InventureMCU.__new__(adapter.InventureMCU)
        host.mcu = Mock()
        host.mcu.get_constants.return_value = dictionary['config']
        host.mcu.get_constant_float.side_effect = lambda name: float(dictionary['config'][name])
        host.mcu.lookup_command.side_effect = command
        host.mcu.lookup_query_command.side_effect = query
        host.mcu.check_valid_response.side_effect = lambda fmt: fmt in dictionary['responses']
        host.mcu.register_serial_response.side_effect = register
        host.printer = Mock()
        host.printer.config_error = ValueError
        host.gcode = Mock()
        host.commands = {}
        host._build_config()
        with patch.dict(sys.modules, {adapter.__package__+'.homing': Mock()}):
            native = importlib.import_module(adapter.__package__+'.inventure_native_z')
        native_z = native.InventureNativeZ.__new__(native.InventureNativeZ)
        native_z.board, native_z.printer = host, host.printer
        native_z._build_config()
        self.assertGreater(len(commands), 30)
        self.assertGreater(len(responses), 25)
        self.assertEqual(host.heartbeat_timeout_s, 5)
        self.assertEqual(host.nozzle_service_target_max, 310)
        self.assertEqual(host.nozzle_pad_measurement_band_c, (95, 105))
        self.assertEqual(host.factory_motion_tools, 3)
        self.assertIn(native.GUARD_COMMAND, commands)
        self.assertIn(native.REFINE_COMMAND, commands)
        self.assertTrue({'factory_session','z_edge','nozzle_z_test','load_stream'} <= host.commands.keys())

    def test_compiled_R7_dictionary_preserves_stream_and_exposes_factory_session(self):
        d=json.loads((Path(__file__).with_name('fixtures')/'control_r7.dict').read_bytes())
        self.assertEqual(d['config']['INVENTURE_MCU_RELEASE'],'RC8-CONTROL-R7-FACTORY-FEED')
        self.assertEqual(d['config']['INVENTURE_FACTORY_FEED_CLOCK'],1)
        self.assertEqual(d['config']['INVENTURE_FACTORY_ABI'],1)
        self.assertEqual(d['config']['INVENTURE_LOAD_STREAM_ABI'],1)
        self.assertIn(adapter.FACTORY_COMMAND,d['commands'])
        self.assertIn(adapter.FACTORY_RESPONSE,d['responses'])
        self.assertIn(adapter.LOAD_STREAM_COMMAND,d['commands'])
        self.assertIn(adapter.LOAD_STREAM_RESPONSE,d['responses'])


if __name__=='__main__':unittest.main()
