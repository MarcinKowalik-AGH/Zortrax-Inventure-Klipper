"""New MCU capabilities stay optional and cannot restart an actuator session."""
import json
import tempfile
import unittest
from test_host_adapters import Printer,Cmd

class LocalStatus(unittest.TestCase):
    def host(self,capable=True):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=Printer(temp.name)
        if capable:
            m=p.objects['mcu'];old=m.get_constants
            m.get_constants=lambda:dict(old(),INVENTURE_LOCAL_COOLING_ABI=1,
                INVENTURE_LOCAL_OLED_RECOVERY_ABI=1,INVENTURE_MCU_RELEASE='P01-P17-RC8.0-LOCAL')
        o=p.load_object(None,'inventure_oled');p.board_ready();p.configure()
        p.send_event('klippy:ready')
        return p,p.objects['inventure_mcu'],o
    def sample(self,**changes):
        return dict(clock=100,mode=2,reason=0,valid=1,mask=61,max_temp=60,
                    native_owner=1,adc_failures=0,gpio_updates=3,**changes)
    def test_legacy_mcu_reports_unsupported_without_sending_unknown_command(self):
        _,board,_=self.host(False);cmd=Cmd();board.cmd_cooling_status(cmd)
        self.assertFalse(json.loads(cmd.messages[-1].split(' ',1)[1])['supported'])
        self.assertNotIn('cooling_query',board.commands)
    def test_capability_query_and_valid_reactor_delivery(self):
        p,board,_=self.host();cmd=Cmd();board.cmd_cooling_status(cmd)
        self.assertEqual(board.commands['cooling_query'].calls,[[]])
        board._cooling_response(self.sample())
        self.assertEqual(board.get_status(0.)['local_cooling']['max_temp'],60)
        self.assertFalse(p.objects['toolhead'].moves)
    def test_invalid_and_out_of_order_telemetry_cannot_refresh_state(self):
        _,board,_=self.host();board._deliver_cooling(self.sample(),1.)
        for change in ({'mask':2},{'mode':4},{'native_owner':2},{'max_temp':40000},{'clock':99}):
            value=self.sample();value.update(change);board._deliver_cooling(value,2.)
            self.assertEqual(board.local_cooling_received,1.)
        value=self.sample();value['clock']=101;board._deliver_cooling(value,2.)
        self.assertEqual(board.local_cooling_received,2.)
    def test_shutdown_requests_local_status_and_keeps_host_stream_stopped(self):
        p,_,o=self.host();p.send_event('klippy:shutdown')
        s=o.get_status(0.)
        self.assertEqual(s['shutdown_policy'],'MCU_LOCAL_STATUS_NO_ACTUATOR_RESTART')
        self.assertFalse(o.stream.enabled);self.assertTrue(o.shutdown_latched)
        self.assertIn('local status',o.stream.failure)
        self.assertFalse(p.objects['toolhead'].moves)

if __name__=='__main__':unittest.main()
