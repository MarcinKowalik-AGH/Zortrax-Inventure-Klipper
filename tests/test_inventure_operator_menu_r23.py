"""The complete OEM tree and shared panel/Fluidd operator navigation."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import test_inventure_leveling_oled_r23 as installed
from test_orange_pi_zortrax_menu import Config
from test_orange_pi_oled_fast import Printer as PanelPrinter, Config as PanelConfig, ready_stream

ROOT=Path(__file__).resolve().parents[1]
menu_module=importlib.import_module(installed.package.__name__+'.inventure_oem_menu')
core=importlib.import_module(installed.package.__name__+'.inventure_menu_core')
oled_module=importlib.import_module(installed.package.__name__+'.inventure_oled')


class OriginalMenuCoverage(unittest.TestCase):
    def test_every_original_record_and_child_is_mapped_in_original_relative_order(self):
        records=json.loads((ROOT/'docs/validation/INVENTURE_OEM_MENU_R0323.json').read_text())
        bindings=menu_module.OEM_RECORD_BINDINGS
        self.assertEqual(set(bindings),{r['address'] for r in records})
        self.assertEqual(len(bindings),66)
        for record in records:
            binding=bindings[record['address']]
            self.assertEqual(binding['source_label'],record['label_en'])
            if binding['kind']=='action':self.assertIn(binding['target'],core.ALL_ACTIONS)
            if binding['kind']!='page':continue
            self.assertIn(binding['target'],menu_module.MENU)
            items=menu_module.MENU[binding['target']][2]
            keys=[('page',n.child) if n.child else ('action',n.action) if n.action else ('caption',n.label) for n in items]
            position=0
            for child in record['children']:
                item=bindings[child]
                if item['kind']=='spacer':continue
                key=(item['kind'],item['target'])
                self.assertIn(key,keys[position:],(record['label_en'],key,keys))
                position=keys.index(key,position)+1

    def test_launcher_has_only_klipper_and_zortrax_inventure_and_all_actions_have_owners(self):
        nav=menu_module.OEMMenu(root_page='launcher',root_back_to_status=False,operational=True)
        self.assertEqual([n.label for n in nav.items()],['Klipper','Zortrax Inventure'])
        self.assertEqual(menu_module.MENU['root'][0],'Zortrax Inventure')
        visited=set();pending=['launcher']
        while pending:
            page=pending.pop()
            if page in visited:continue
            visited.add(page);nav.open_page(page)
            for node in nav.items():
                if node.child:pending.append(node.child)
                if node.action:self.assertIn(node.action,core.ALL_ACTIONS,(page,node.label))
            for index in range(len(nav.items())):
                nav.selected=index
                self.assertEqual(len(installed.graphics.menu_wire(nav.view())),8192)
        self.assertTrue({'root','maintenance','heads','material','model_material','support_material',
                         'load_model','load_support','spool_model','spool_support','settings',
                         'language','sorting','doorlock','filecheck','information'} <= visited)

    def test_shared_page_entries_preserve_the_same_back_stack_as_physical_navigation(self):
        nav=menu_module.OEMMenu(root_page='launcher',root_back_to_status=False,operational=True)
        for page,parent in (('settings','root'),('heads','maintenance'),('material','root'),
                            ('load_support','support_material'),('klipper_control','klipper_main')):
            nav.open_page(page)
            self.assertTrue(nav.back())
            self.assertEqual(nav.page,parent)
        nav.open_page('root');self.assertTrue(nav.back());self.assertEqual(nav.page,'launcher')


class SharedOperatorNavigation(unittest.TestCase):
    def setUp(self):
        self.fixture=installed.InstalledLevelingWizard()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.board,self.menu,self.calls=self.fixture.board,self.fixture.menu,self.fixture.calls
        printer=PanelPrinter()
        self.panel=oled_module.InventureOLED(PanelConfig(printer));self.panel._build_config()
        self.panel.connected=True;self.panel.stream=ready_stream()
        self.panel.board=self.board
        self.panel.oem_menu=menu_module.OEMMenu(root_page='launcher',root_back_to_status=False,operational=True)
        self.panel.oem_display_mode='status'
        self.panel._script=self.board.gcode.run_script_from_command
        self.board.printer.objects['inventure_oled']=self.panel

    def select(self,label):
        view=self.menu.view
        view.selected=next(i for i,(name,_) in enumerate(view.options) if name==label)
        view.released=True
        command=self.menu.panel_select_command(acknowledged=True)
        self.board.gcode.run_script_from_command(command)

    def test_status_ok_opens_exactly_two_roots_and_zortrax_enters_all_five_sections(self):
        self.panel._select(0.)
        self.assertEqual(self.panel.oem_display_mode,'menu')
        self.assertEqual(self.panel.oem_menu.view()['labels'],['Klipper','Zortrax Inventure'])
        self.panel.oem_menu.selected=1;self.panel._select(0.)
        self.assertEqual(self.panel.oem_menu.page,'root')
        self.assertEqual(self.panel.oem_menu.view()['labels'],
                         ['Back','Models','Maintenance','Material','Settings','Information'])

    def test_shared_menu_rejects_an_old_button_when_the_navigation_page_changed(self):
        self.panel.oem_menu.open_page('material');self.menu.open_operator_page()
        token=self.menu.view.token
        self.panel.oem_menu.open_page('maintenance')
        with self.assertRaisesRegex(core.MenuError,'expired'):
            self.menu.choose(token,index=1)
        self.assertEqual(self.calls,[])

    def test_power_back_moves_one_parent_and_restores_the_status_screen(self):
        self.panel.oem_menu.open_page('settings');self.menu.open_operator_page()
        self.menu.panel_back();self.assertEqual(self.panel.oem_menu.page,'root')
        self.menu.panel_back();self.assertEqual(self.panel.oem_menu.page,'launcher')
        self.menu.panel_back();self.assertEqual(self.panel.oem_display_mode,'status')

    def test_middle_button_on_an_oem_warning_caption_does_not_send_a_command(self):
        self.panel.oem_menu.open_page('pause');self.panel.oem_display_mode='menu'
        self.panel.oem_menu.selected=1
        self.panel._select(0.)
        self.assertEqual(self.panel.oem_menu.page,'pause')
        self.assertEqual(self.calls,[])

    def test_material_and_maintenance_paths_open_existing_procedure_owners(self):
        self.panel.oem_menu.open_page('maintenance');self.menu.open_operator_page()
        self.select('Platform leveling')
        self.assertEqual(self.menu.view.kind,'confirm')
        self.assertEqual(self.calls,[])
        self.menu.choose(self.menu.view.token,confirm=True)
        self.assertEqual(self.calls,[dict(ACTION='START')])


if __name__=='__main__':unittest.main()
