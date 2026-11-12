"""Public host regression for the OEM door setting and R1/R2 compatibility.

MCU native register simulation and the compiled dictionary checks are shipped
with the separate offline MCU source/validation package.
"""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
package=types.ModuleType('door_test_host');package.__path__=[str(ROOT/'orange_pi'),str(ROOT/'host'),str(ROOT/'tests/fixtures/host_dependencies')]
sys.modules[package.__name__]=package
spec=importlib.util.spec_from_file_location('door_test_host.inventure_mcu',ROOT/'orange_pi/inventure_mcu.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)

class Wire:
    def __init__(self,reply):self.reply=reply;self.calls=[]
    def send(self,data,**kw):self.calls.append(tuple(data));return dict(self.reply)

class Command:
    def __init__(self,**values):self.values=values;self.lines=[]
    def get(self,key,default=None):return self.values.get(key,default)
    def get_int(self,key,default=0,minval=None,maxval=None):
        value=int(self.get(key,default))
        if (minval is not None and value<minval) or (maxval is not None and value>maxval):
            raise self.error('Invalid '+key)
        return value
    def error(self,message):return RuntimeError(message)
    def respond_info(self,message):self.lines.append(message)

def policy(**changes):
    data=dict(enabled=1,disable_bit=0,closed_mask=3,lock_command_mask=0,motor_busy=0,reject=0)
    data.update(changes);return data

class DoorPolicyTests(unittest.TestCase):
    def host(self,reply):
        h=adapter.InventureMCU.__new__(adapter.InventureMCU)
        h.commands={'door_policy':Wire(reply)};h.connected=True;h.door_policy_last={}
        return h,h.commands['door_policy']

    def global_host(self,reply):
        h,w=self.host(reply)
        h.door_policy_global=True
        h.door_policy_default_enabled=0
        return h,w

    def test_status_is_read_only_and_explicit_disable_keeps_actual_inputs(self):
        h,w=self.host(policy());h.cmd_door_policy(Command())
        self.assertEqual(w.calls,[(0,1)]);self.assertTrue(h.door_policy_last['enabled'])
        w.reply=policy(enabled=0,disable_bit=1,closed_mask=0)
        h.cmd_door_policy(Command(ENABLE=0))
        self.assertEqual(w.calls[-1],(1,0));self.assertFalse(h.door_policy_last['enabled'])
        self.assertFalse(h.door_policy_last['front_door_closed'])
        self.assertFalse(h.door_policy_last['lid_closed'])
        self.assertEqual(h.door_policy_last['oem_disable_bit_index'],3)

    def test_busy_change_reports_rejection_without_a_motion_packet(self):
        h,w=self.host(policy(reject=2,motor_busy=1))
        with self.assertRaisesRegex(RuntimeError,'finish_or_stop'):h.cmd_door_policy(Command(ENABLE=0))
        self.assertTrue(h.door_policy_last['enabled']);self.assertEqual(w.calls,[(1,0)])

    def test_malformed_disable_response_requests_default_restoration(self):
        h,w=self.host(policy(enabled=0,disable_bit=0))
        with self.assertRaisesRegex(RuntimeError,'Invalid MCU'):h.cmd_door_policy(Command(ENABLE=0))
        self.assertEqual(w.calls,[(1,0),(1,1)]);self.assertEqual(h.door_policy_last,{})

    def test_global_status_reports_startup_policy_and_raw_open_inputs(self):
        h,w=self.global_host(policy(enabled=0,disable_bit=1,closed_mask=0))
        command=Command()
        h.cmd_door_policy(command)
        self.assertEqual(w.calls,[(0,1)])
        prefix,reply=command.lines[0].split(' ',1)
        self.assertEqual(prefix,'V9_DOOR_POLICY')
        state=json.loads(reply)
        self.assertEqual(state['scope'],'global')
        self.assertIs(state['default_enabled'],False)
        self.assertIs(state['enabled'],False)
        self.assertIs(state['front_door_closed'],False)
        self.assertIs(state['lid_closed'],False)
        self.assertEqual(state['lock_command_mask'],0)

        w.reply=policy(closed_mask=1)
        h.cmd_door_policy(Command(ENABLE=1))
        self.assertEqual(w.calls[-1],(1,1))
        state=h.door_policy_last
        self.assertIs(state['enabled'],True)
        self.assertIs(state['default_enabled'],False)
        self.assertIs(state['front_door_closed'],False)
        self.assertIs(state['lid_closed'],True)

    def test_global_disable_rejects_malformed_response_without_reenabling(self):
        h,w=self.global_host(policy(enabled=0,disable_bit=0))
        with self.assertRaisesRegex(RuntimeError,'Invalid MCU'):
            h.cmd_door_policy(Command(ENABLE=0))
        self.assertEqual(w.calls,[(1,0)])
        self.assertEqual(h.door_policy_last,{})

    def test_global_disable_without_ack_discards_cached_policy(self):
        h,w=self.global_host(policy())
        h.cmd_door_policy(Command())
        self.assertIs(h.door_policy_last['enabled'],True)

        def lost_ack(data,**kw):
            w.calls.append(tuple(data))
            raise TimeoutError('Lost door policy reply')

        w.send=lost_ack
        with self.assertRaisesRegex(RuntimeError,'Lost door policy reply'):
            h.cmd_door_policy(Command(ENABLE=0))
        self.assertEqual(w.calls,[(0,1),(1,0)])
        self.assertEqual(h.door_policy_last,{})

    def test_global_off_readback_requires_both_lock_outputs_released(self):
        for values,request in (({},(0,1)),({'ENABLE':0},(1,0))):
            with self.subTest(values=values):
                h,w=self.global_host(policy(enabled=0,disable_bit=1,lock_command_mask=2))
                with self.assertRaisesRegex(RuntimeError,'Invalid MCU'):
                    h.cmd_door_policy(Command(**values))
                self.assertEqual(w.calls,[request])
                self.assertEqual(h.door_policy_last,{})

    def test_global_off_refuses_lock_engagement_and_allows_release(self):
        h,w=self.global_host(policy(enabled=0,disable_bit=1,closed_mask=0))
        h.allow_actuators=True
        h.printer=types.SimpleNamespace(lookup_object=lambda *args:None)
        lock=Wire({})
        heartbeat=Wire({})
        h.commands.update(lock=lock,heartbeat=heartbeat)

        def send_with_preface(preface,preface_args,data,**kw):
            preface.send(preface_args)
            return w.send(data,**kw)

        w.send_with_preface=send_with_preface
        for channel in (0,1):
            with self.assertRaisesRegex(RuntimeError,'(?i)door|lock'):
                h.cmd_lock(Command(CHANNEL=channel,VALUE=1))
            self.assertIs(h.door_policy_last['enabled'],False)
        self.assertEqual(w.calls,[(0,1),(0,1)])
        self.assertEqual(lock.calls,[])

        for channel in (0,1):
            h.cmd_lock(Command(CHANNEL=channel,VALUE=0))
        self.assertEqual(lock.calls,[(0,0),(1,0)])
        self.assertEqual(w.calls,[(0,1),(0,1)])

        w.reply=policy()
        h.cmd_lock(Command(CHANNEL=1,VALUE=1))
        self.assertEqual(lock.calls[-1],(1,1))
        self.assertEqual(w.calls[-1],(0,1))

    def test_old_mcu_does_not_silently_accept_the_setting(self):
        h,w=self.host(policy());h.commands={}
        with self.assertRaisesRegex(RuntimeError,'RC8-MOTOR-R2'):h.cmd_door_policy(Command(ENABLE=0))
        self.assertEqual(w.calls,[])

    def test_motor_reports_support_both_old_and_new_mcu_protocols(self):
        for abi in (1,2):
            p=dict(run=1,token=11,motor=0,direction=1,phase=4,reason=1,reject=0,
                   forward=640,returned=640,planned=640,enabled=0)
            if abi==2:p.update(door_lock_enabled=0,door_closed_mask=0)
            h,w=self.host(policy());h.motor_test_abi=abi
            h.commands={'motor_test':Wire(p)}
            state=h._motor_test_query()
            self.assertEqual(state['phase'],'done');self.assertFalse(state['establishes_position'])
            if abi==2:
                self.assertFalse(state['door_lock_enabled']);self.assertFalse(state['front_door_closed'])
            else:self.assertNotIn('door_lock_enabled',state)

    def test_oem_menu_items_dispatch_policy_through_the_matching_host(self):
        from door_test_host.inventure_oem_menu import OEMMenu
        source=(ROOT/'orange_pi/inventure_oled.py').read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='InventureOLED')
        scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_oled_class','exec'),scope)
        cls=scope['InventureOLED'];o=cls.__new__(cls);o.oem_menu=OEMMenu('en')
        o.oem_display_mode='menu';o.message=None;commands=[];o._script=commands.append
        for index,value in ((1,0),(2,1)):
            o.message=None;o.oem_menu.page='doorlock';o.oem_menu.selected=index
            o._select(100.);self.assertEqual(commands[-1],'INVENTURE_DOOR_POLICY ENABLE=%d'%value)
            self.assertIsNotNone(o.message)

if __name__=='__main__':unittest.main(verbosity=2)
