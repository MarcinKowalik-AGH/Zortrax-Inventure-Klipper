import ast, pathlib, types, unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
HOST = ROOT/'HOST' if (ROOT/'HOST').is_dir() else ROOT/'host'
class AckPumpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse((HOST/'inventure_oled.py').read_text())
        c=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='InventureOLED')
        methods={n.name:n for n in c.body if isinstance(n,ast.FunctionDef)}
        cls.oled=types.SimpleNamespace()
        for name in ('_ack','_send_one'):
            f=ast.Module(body=[methods[name]],type_ignores=[])
            # isolate methods with no hardware, but use the actual production AST
            ns={'TimeoutError':TimeoutError};exec(compile(ast.fix_missing_locations(f),'<rc74>', 'exec'),ns)
            setattr(cls.oled,name,ns[name])
    def _mock(self,accepted=True):
        events=[]
        class Reactor:
            def monotonic(self):return 1.02
            def register_async_callback(self,cb):cb(1.03)
        class Stream:
            enabled=True;generation=1;phase='updating';failure=None
            def ack(self,token,received,generation,result,processed_at):return accepted
            def step(self,now):
                events.append(('step',now))
                return ('write',99,(1,b'abc'))
        class Cmd:
            def send(self,args):events.append(('send',args))
        x=types.SimpleNamespace(reactor=Reactor(),stream=Stream(),commands={'write':Cmd()},connected=True,
            shutdown_latched=False,_oled_last_tx={'generation':1,'token':98,'operation':'write','queued_at':1.0},
            _oled_last_ack=None,recovery_state='ready', _lose=lambda *a:events.append(('lost',a)))
        x._send_one=types.MethodType(self.oled._send_one,x)
        x._ack=types.MethodType(self.oled._ack,x)
        return x,events
    def test_accepted_ack_immediately_pumps_next_packet(self):
        x,e=self._mock();x._ack({'generation_hi':0,'generation_lo':1,'token':98,'result':0})
        self.assertEqual([a[0] for a in e],['step','send'])
        self.assertEqual(x._oled_last_ack['token'],98)
        self.assertEqual(e[-1][1][-1],b'abc')
    def test_rejected_stale_ack_does_not_advance(self):
        x,e=self._mock(False);x._ack({'generation_hi':0,'generation_lo':1,'token':98,'result':0})
        self.assertEqual(e,[])
    def test_shutdown_does_not_pump(self):
        x,e=self._mock();x.shutdown_latched=True;x._ack({'generation_hi':0,'generation_lo':1,'token':98,'result':0})
        self.assertEqual(e,[])
    def test_pending_transport_gate_preserved(self):
        s=(HOST/'inventure_oled.py').read_text()
        self.assertEqual(s.count('self.stream.step(now)'),1)
        self.assertIn('if accepted:',s)
        self.assertIn('if self.stream.enabled and not self.shutdown_latched:',s)
