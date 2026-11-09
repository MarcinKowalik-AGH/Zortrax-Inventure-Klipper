"""Fresh sensor observations and fixed read-only GPIO register comparisons."""
import ast
import json
from pathlib import Path
import types
import unittest
import test_orange_pi_control as control
from test_orange_pi_door_policy import adapter as mod, Command, Wire

ROOT=Path(__file__).resolve().parents[1]

class Reactor(control.Reactor):
    def register_async_callback(self,callback):callback(self.monotonic())

class Subscriptions:
    def __init__(self):self.callbacks={}
    def register_serial_response(self,callback,response):
        self.callbacks[response.split()[0]]=callback

class Readback(Wire):
    def __init__(self,mcu,response,reply):
        super().__init__(reply);self.mcu=mcu;self.response=response;self.failure=None
    def send_with_preface(self,heartbeat,preface,args,**kw):
        heartbeat.send(preface)
        # SerialRetryCommand owns this response name until it completes.
        self.mcu.callbacks.pop(self.response.split()[0],None)
        self.calls.append(tuple(args))
        if self.failure:raise self.failure
        return dict(self.reply(args) if callable(self.reply) else self.reply)

class GPIOReadback(Readback):
    def __init__(self,mcu):
        super().__init__(mcu,mod.DEBUG_RESPONSE,{})
        self.words={mod.GPIO_BASE+i*0x400+offset:0
                    for i in range(7) for offset in (0,0x10)}
    def send_with_preface(self,heartbeat,preface,args,**kw):
        self.calls.append(tuple(args));order,address=args
        if order!=2 or address not in self.words:raise AssertionError('Unexpected register access')
        return dict(val=self.words[address])

class CassetteInputTests(unittest.TestCase):
    def host(self):
        h=control.ControlHostTests().host();h.reactor=Reactor();h.printer=control.Printer()
        h.mcu=Subscriptions();h.adc_accepting=True;h.adc_generation=0
        h.cassette_gpio_baseline=None;h.rfid_overruns=[0,0];h.uart_status=[{},{}]
        h.inputs_received=10.;h.inputs={'raw':{'t0_a':0},'buttons':0,'sequence':0}
        h.status_received=10.;h.status=dict(armed=0,fault=0,seen=255,cable=0,
                                         p0=0,p1=0,p2=0,p3=0,p4=2,p5=1)
        h.commands['sensor_board']=Readback(h.mcu,mod.BOARD_RESPONSE,lambda args:h.board_reply)
        h.board_reply=dict(h.status)
        h.commands['sensor_inputs']=Readback(h.mcu,mod.INPUT_RESPONSE,
            dict(clock=12000000,raw=0,buttons=0,sequence=74))
        h.commands['sensor_uart']=Readback(h.mcu,mod.UART_RESPONSE,
            lambda args:dict(channel=args[0],errors=0,received=10+args[0],sent=20+args[0]))
        h.commands['gpio_read']=GPIOReadback(h.mcu)
        h.cmd_panel_io=lambda g:g.respond_info('PANEL '+json.dumps(h.inputs))
        return h

    def result(self,command,prefix='V9_CASSETTE_DIAG '):
        return json.loads(next(s[len(prefix):] for s in command.lines if s.startswith(prefix)))

    def test_sensors_wait_for_new_values_instead_of_reporting_the_old_cache(self):
        h=self.host();h.board_reply['p4']=9
        h.commands['sensor_inputs'].reply['raw']=1<<9
        cmd=Command();h.cmd_sensors(cmd);result=self.result(cmd,'V9_SENSORS ')
        self.assertEqual(h.inputs['raw']['t0_b'],1)
        self.assertEqual(result['counters']['4'],9)
        self.assertTrue(result['snapshot_fresh']);self.assertEqual(result['inputs_age_s'],0.)
        self.assertEqual([r['channel'] for r in result['rfid_uart']],[0,1])
        self.assertEqual(result['rfid_uart'][1]['received'],11)
        self.assertEqual(h.commands['sensor_uart'].calls,[(0,),(1,)])
        self.assertEqual(h.mcu.callbacks['inventure_status'],h._status)
        self.assertEqual(h.mcu.callbacks['inventure_inputs'],h._inputs)
        self.assertEqual(h.mcu.callbacks['inventure_uart_status'],h._uart_status)

    def test_input_command_waits_for_response_and_restores_subscription(self):
        h=self.host();h.commands['sensor_inputs'].reply['raw']=1<<8
        cmd=Command();h.cmd_inputs(cmd);reply=json.loads(cmd.lines[0])
        self.assertEqual(reply['raw']['t0_a'],1);self.assertTrue(reply['fresh'])
        self.assertEqual(h.mcu.callbacks['inventure_inputs'],h._inputs)

    def test_timeout_restores_subscription_and_does_not_print_a_false_fresh_sample(self):
        h=self.host();h.commands['sensor_inputs'].failure=RuntimeError('read timeout')
        cmd=Command()
        with self.assertRaisesRegex(RuntimeError,'read timeout'):h.cmd_sensors(cmd)
        self.assertEqual(cmd.lines,[])
        self.assertEqual(h.mcu.callbacks['inventure_inputs'],h._inputs)

    def test_invalid_response_or_disconnection_cannot_reuse_cached_success(self):
        h=self.host();h.commands['sensor_inputs'].reply['raw']=True
        with self.assertRaisesRegex(ValueError,'Invalid'):h._fresh_inputs()
        h=self.host();h.connected=False
        with self.assertRaisesRegex(RuntimeError,'disconnected'):h.cmd_sensors(Command())
        self.assertEqual(h.commands['sensor_board'].calls,[])

    def test_gpio_comparison_finds_known_and_previously_unreported_inputs(self):
        h=self.host();wire=h.commands['gpio_read']
        # PA9 is peripheral AF: UART traffic must not be called a button.
        wire.words[mod.GPIO_BASE]=2<<(2*9)
        h.cmd_cassette_diag(Command(ACTION='BASELINE'))
        wire.words[mod.GPIO_BASE+0x10]=(1<<9)|(1<<12)
        wire.words[mod.GPIO_BASE+5*0x400+0x10]=2
        h.board_reply['p4']=5
        cmd=Command(ACTION='COMPARE');h.cmd_cassette_diag(cmd);r=self.result(cmd)
        self.assertEqual({c['pin'] for c in r['changes']},{'PA12','PF1'})
        named={c['pin']:c for c in r['changes']}
        self.assertEqual(named['PF1']['name'],'t0_b')
        self.assertEqual(named['PA12']['name'],'unmapped_input')
        self.assertEqual(named['PF1']['before'],0);self.assertEqual(named['PF1']['after'],1)
        self.assertEqual(r['pulse_delta'],dict(t0_pb12=3,t1_pd14=0))
        self.assertFalse(r['physical_function_confirmed'])
        expected={(2,mod.GPIO_BASE+i*0x400+off) for i in range(7) for off in (0,0x10)}
        self.assertEqual(set(wire.calls),expected);self.assertEqual(len(wire.calls),28)
        self.assertEqual(h.commands['feeder'].calls,[])
        self.assertEqual(h.commands['output'].calls,[])

    def test_levels_include_both_transport_pins_without_claiming_motion(self):
        h=self.host();wire=h.commands['gpio_read']
        wire.words[mod.GPIO_BASE+0x400+0x10]=1<<12
        wire.words[mod.GPIO_BASE+3*0x400+0x10]=1<<14
        cmd=Command(ACTION='STATUS');h.cmd_cassette_diag(cmd);r=self.result(cmd)
        self.assertEqual(r['input_levels']['PB12'],1)
        self.assertEqual(r['input_levels']['PD14'],1)
        self.assertEqual(r['filament']['t0_pb12'],2)
        self.assertFalse(r['baseline_available']);self.assertIsNone(r['pulse_delta'])

    def test_missing_baseline_and_native_or_busy_mode_reject_before_gpio_reads(self):
        h=self.host()
        with self.assertRaisesRegex(RuntimeError,'BASELINE first'):
            h.cmd_cassette_diag(Command(ACTION='COMPARE'))
        for changes in (dict(mode=1,armed=1),dict(active=1),dict(latched=1)):
            h=self.host();h.commands['control'].reply.update(changes)
            with self.assertRaisesRegex(RuntimeError,'idle SERVICE'):
                h.cmd_cassette_diag(Command(ACTION='BASELINE'))
            self.assertEqual(h.commands['gpio_read'].calls,[])

    def test_gpio_read_allowlist_rejects_output_registers_and_foreign_ports(self):
        h=self.host()
        for port,offset in (('A',0x14),('A',0x18),('H',0),('AB',0)):
            with self.assertRaisesRegex(ValueError,'allowlist'):h._gpio_read_word(port,offset)
        self.assertEqual(h.commands['gpio_read'].calls,[])

    def test_existing_r5_dictionary_encodes_every_new_diagnostic_read(self):
        parser=control.msgproto.MessageParser()
        dictionary=(ROOT/'tests/fixtures/control_r5.dict').read_bytes()
        parser.process_identify(dictionary,decompress=False)
        for command,args,response in (
            ('debug_read order=%c addr=%u',[2,mod.GPIO_BASE+6*0x400+0x10],mod.DEBUG_RESPONSE),
            ('inventure_inputs_query',[],mod.INPUT_RESPONSE),
            ('inventure_query',[],mod.BOARD_RESPONSE),
            ('inventure_uart_query channel=%c',[1],mod.UART_RESPONSE)):
            encoded=parser.lookup_command(command).encode(args)
            self.assertLessEqual(len(encoded),control.msgproto.MESSAGE_PAYLOAD_MAX)
            self.assertIsNotNone(parser.lookup_command(response))
        tree=ast.parse((ROOT/'orange_pi/inventure_mcu.py').read_text())
        strings=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
        self.assertIn('INVENTURE_CASSETTE_DIAG',strings)
        self.assertFalse(any(s.startswith('debug_write ') for s in strings))

    def test_real_pinned_retry_wrapper_does_not_remove_periodic_sensor_subscriptions(self):
        serial_tree=ast.parse((ROOT/'orange_pi/serialhdl.py').read_text())
        retry=next(n for n in serial_tree.body if isinstance(n,ast.ClassDef) and n.name=='SerialRetryCommand')
        serial_env={'error':RuntimeError}
        exec(compile(ast.Module(body=[retry],type_ignores=[]),'published_serial_retry','exec'),serial_env)
        env={'serialhdl':types.SimpleNamespace(SerialRetryCommand=serial_env['SerialRetryCommand'],error=RuntimeError)}
        exec(compile((ROOT/'tests/fixtures/pinned_mcu_sensor_queries.py').read_text(),'pinned_mcu_queries','exec'),env)
        h=self.host()
        class Codec:
            def __init__(self,fmt):self.fmt=fmt
            def encode(self,args):return self.fmt,tuple(args)
        class Serial:
            def __init__(self):self.callbacks={};self.failure=False;self.reactor=h.reactor
            def get_msgparser(self):return self
            def lookup_command(self,fmt):return Codec(fmt)
            def get_default_command_queue(self):return object()
            def register_response(self,callback,name,oid=None):
                if callback is None:self.callbacks.pop(name,None)
                else:self.callbacks[name]=callback
            def raw_send(self,*args):pass
            def raw_send_wait_ack(self,data,*args):
                if self.failure:raise RuntimeError('wire failed')
                fmt,values=data
                if fmt=='inventure_query':name='inventure_status';reply=h.board_reply
                elif fmt=='inventure_inputs_query':
                    name='inventure_inputs';reply=dict(clock=9,raw=1<<9,buttons=0,sequence=75)
                else:
                    name='inventure_uart_status';reply=dict(channel=values[0],errors=0,received=0,sent=0)
                self.callbacks[name](dict(reply))
        serial=Serial();connection=types.SimpleNamespace(get_serial=lambda:serial)
        config=types.SimpleNamespace(is_config_finalized=lambda:True)
        class MCU:
            def is_fileoutput(self):return False
            def get_printer(self):return types.SimpleNamespace(command_error=RuntimeError)
            def register_serial_response(self,callback,response):
                return env['AsyncResponseWrapper'](connection,config,callback,response)
        h.mcu=MCU();connection.get_mcu=lambda:h.mcu
        h.commands['heartbeat']=types.SimpleNamespace(_cmd=Codec('inventure_heartbeat'))
        for key,command,response in (
                ('sensor_board','inventure_query',mod.BOARD_RESPONSE),
                ('sensor_inputs','inventure_inputs_query',mod.INPUT_RESPONSE),
                ('sensor_uart','inventure_uart_query channel=%c',mod.UART_RESPONSE)):
            h.commands[key]=env['CommandQueryWrapper'](connection,command,response)
        h._fresh_sensor_snapshot()
        self.assertEqual(h.inputs['raw']['t0_b'],1)
        self.assertEqual(serial.callbacks['inventure_status'],h._status)
        self.assertEqual(serial.callbacks['inventure_inputs'],h._inputs)
        self.assertEqual(serial.callbacks['inventure_uart_status'],h._uart_status)
        serial.callbacks['inventure_inputs'](dict(clock=10,raw=0,buttons=0,sequence=75))
        self.assertEqual(h.inputs['raw']['t0_b'],0)
        serial.failure=True
        with self.assertRaisesRegex(RuntimeError,'wire failed'):h._fresh_inputs()
        self.assertEqual(serial.callbacks['inventure_inputs'],h._inputs)

if __name__=='__main__':unittest.main()
