# SPDX-License-Identifier: GPL-3.0-or-later
import ast, pathlib, sys, types, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
HOST=ROOT/'HOST' if (ROOT/'HOST').is_dir() else ROOT/'host'
sys.path.insert(0,str(HOST))
from inventure_oem_menu import OEMMenu

class LauncherMenuTests(unittest.TestCase):
    def test_legacy_oem_menu_remains_available(self):
        m=OEMMenu()
        self.assertEqual(m.page,'root')
        self.assertEqual(m.view()['labels'][:3],['Powrót','Modele','Konserwacja'])
        self.assertEqual(m.select(),'exit_to_status')
    def test_launcher_is_m300_style_without_oem_as_first_screen(self):
        m=OEMMenu('pl',root_page='launcher')
        self.assertEqual(m.view()['labels'],['Back','Klipper','Zortrax Inventure','Service / Tests'])
        self.assertEqual(m.select(),'exit_to_status')
        self.assertEqual(m.status()['root_page'],'launcher')
        self.assertFalse(m.status()['actuators_enabled_by_menu'])
    def test_launcher_navigation_and_zortrax_nested(self):
        m=OEMMenu(root_page='launcher')
        m.move(1);m.move(1);self.assertEqual(m.select(),'submenu')
        self.assertEqual(m.page,'root')
        self.assertEqual(m.view()['labels'][:3],['Back','Models','Maintenance'])
        self.assertEqual(m.select(),'back')
        self.assertEqual(m.page,'launcher')
        self.assertEqual(m.selected,2)
        self.assertEqual(m.select(),'submenu')
        self.assertEqual(m.select(),'back')
        self.assertEqual(m.page,'launcher')
    def test_klipper_menu_and_status_escape(self):
        m=OEMMenu(root_page='launcher')
        m.move(1);self.assertEqual(m.select(),'submenu')
        self.assertEqual(m.page,'klipper_main')
        self.assertEqual(m.view()['labels'][1:4],['Printer status','Print / Job','Temperatures'])
        m.move(1);self.assertEqual(m.select(),'exit_to_status')
        self.assertFalse(m.status()['actuators_enabled_by_menu'])
    def test_service_is_diagnostic_and_no_side_effect(self):
        m=OEMMenu(root_page='launcher');[m.move(1) for _ in range(3)];m.select()
        self.assertEqual(m.page,'service_tests')
        m.move(1);self.assertEqual(m.select(),'inert')
        self.assertFalse(m.status()['actuators_enabled_by_menu'])
    def test_navigation_no_wrap_and_pinned_back(self):
        m=OEMMenu(root_page='launcher')
        self.assertEqual(m.move(-1),0)
        for _ in range(20):m.move(1)
        self.assertEqual(m.selected,3)
        for _ in range(20):m.move(-1)
        self.assertEqual(m.selected,0)
        m.move(1);m.move(1);m.select()  # r0323 five branches
        for _ in range(20):m.move(1)
        self.assertEqual(m.selected,len(m.items())-1)
        self.assertEqual(m.window,2)
    def test_launcher_language_page_stays_english(self):
        m=OEMMenu('pl',root_page='launcher')
        m.page='language'
        self.assertEqual(m.view()['labels'],['Back','English'])
        m.move(1)
        self.assertEqual(m.select(),'language')
        self.assertEqual(m.status()['language'],'en')

    def test_root_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):OEMMenu(root_page='unknown')

class RealOledReadyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse((HOST/'inventure_oled.py').read_text())
        klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='InventureOLED')
        methods={n.name:n for n in klass.body if isinstance(n,ast.FunctionDef)}
        for name in ('_ready','cmd_menu_status'):
            ns={'logging':__import__('logging'),'json':__import__('json')}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[methods[name]],type_ignores=[])),
                         '<production-oled>', 'exec'),ns)
            setattr(cls,name,ns[name])
    def test_ready_enforces_klipper_start(self):
        class Reactor:
            NOW=0
            def monotonic(self):return 1.0
            def update_timer(self,*a):pass
        actions=[]
        x=types.SimpleNamespace(shutdown_latched=False,connected=False,
          reactor=Reactor(),oem_start_screen='klipper',oem_display_mode='menu',
          menu_profile='m300',oem_menu=OEMMenu(root_page='launcher'),message=('temp','notice'),
          timer=object(),stream=types.SimpleNamespace(refresh_interval=.2),_oem_render_cache='dirty',
          _render=lambda now:actions.append(('render',now)),
          _start=lambda now,reason:actions.append(('start',now,reason)))
        x.oem_menu.move(1);x.oem_menu.move(1);x.oem_menu.select()
        RealOledReadyTests._ready(x)
        self.assertEqual(x.oem_display_mode,'status')
        self.assertEqual(x.oem_menu.page,'launcher')
        self.assertIsNone(x.message)
        self.assertEqual(actions,[('render',1.),('start',1.,'initial_start')])
    def test_ready_menu_mode_optin(self):
        class Reactor:
            NOW=0
            def monotonic(self):return 1.0
            def update_timer(self,*a):pass
        x=types.SimpleNamespace(shutdown_latched=False,connected=False,
          reactor=Reactor(),oem_start_screen='menu',oem_display_mode='status',
          menu_profile='m300',oem_menu=OEMMenu(root_page='launcher'),message=None,
          timer=object(),stream=types.SimpleNamespace(refresh_interval=.2),_oem_render_cache=None,_render=lambda x:None,_start=lambda x,y:None)
        RealOledReadyTests._ready(x)
        self.assertEqual(x.oem_display_mode,'menu')
    def test_status_contains_root_diagnostic(self):
        m=OEMMenu(root_page='launcher')
        x=types.SimpleNamespace(oem_menu=m,oem_display_mode='status',menu_profile='m300',
          oem_start_screen='klipper',_rendered_screen='klipper_status')
        class Cmd:
            def respond_info(self,s):self.answer=s
        cmd=Cmd();RealOledReadyTests.cmd_menu_status(x,cmd)
        self.assertIn('"rendered_screen": "klipper_status"',cmd.answer)
        self.assertIn('"menu_profile": "m300"',cmd.answer)

if __name__=='__main__':unittest.main()
