"""Wire agreement with the dictionary from the delivered ARM application."""
import json
from pathlib import Path
import unittest
from test_orange_pi_door_policy import adapter


class FactoryProtocolTests(unittest.TestCase):
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
