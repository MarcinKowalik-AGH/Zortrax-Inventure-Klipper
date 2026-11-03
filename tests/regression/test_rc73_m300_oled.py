# SPDX-License-Identifier: GPL-3.0-or-later
"""RC7.3 M300-style OLED navigation: genuine host code, modeled I/O only."""
import importlib, sys, unittest
from types import SimpleNamespace
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'host'))
from inventure_oem_menu import OEMMenu
from inventure_oled_render import Frame, KlipperStatus, get_oem_assets
from inventure_oled_transport import diff_regions, frame_operations

class FiveRows(unittest.TestCase):
    def test_native_status_packs_rows_and_reuses_unchanged_wire(self):
        status=KlipperStatus.__new__(KlipperStatus)
        pages=[bytearray(128) for _ in range(8)]
        pages[0][0]=1
        pages[1][2]=1<<5
        pages[7][127]=1<<7
        status.lcd_chip=SimpleNamespace(clear=lambda:None,vram=pages)
        status.display_templates={}
        status.group=SimpleNamespace(show=lambda *args:None)
        status._draw_unconfigured_fields=lambda *args:None
        status._previous=status._wire=None
        frame=status.render(0.,None)
        self.assertEqual(len(frame),8192)
        self.assertEqual([i for i,value in enumerate(frame) if value], [0,13*128+2,8191])
        self.assertTrue(all(value in (0,255) for value in frame))
        self.assertIs(status.render(.2,None),frame)

    def test_root_full_height_pinned_back_scroll_and_return(self):
        m=OEMMenu(language='pl')
        self.assertEqual(m.view()['labels'],['Powrót','Modele','Konserwacja','Materiał','Ustawienia','Informacje'])
        f=Frame().oem_menu(m.view())
        # The new five rows fill y=0..60; no legacy separator y=12, x4..197.
        self.assertEqual(f.get_pixel(180,12),0)
        self.assertGreater(sum(f.get_pixel(x,y) for y in range(48,61) for x in range(19,150)),0)
        self.assertEqual(m.select(),'exit_to_status')
        self.assertFalse(m.stack)
        for _ in range(5):m.move(1)
        self.assertEqual(m.selected,5)
        self.assertEqual(m.window,2)  # back and four sliding rows
        self.assertEqual(m.move(1),5)  # nonwrapping
        self.assertEqual(m.select(),'submenu')
        self.assertEqual(m.page,'information')
        self.assertEqual(m.view()['labels'][0],'Powrót')
        self.assertEqual(m.select(),'back')
        self.assertEqual(m.selected,5)
        self.assertEqual(m.window,2)
    def test_original_atlas_and_submenu_icon(self):
        atlas=get_oem_assets()
        self.assertEqual(len(atlas.glyphs),403)
        self.assertEqual(len(atlas.icons),10)
        root=OEMMenu();root.move(1);root.select()
        f=Frame().oem_menu(root.view(),atlas)
        self.assertTrue(any(f.get_pixel(x,y) for x in range(203,253) for y in range(13,63)))
    def test_root_cursor_diff_small_without_retransmitting_icon(self):
        m=OEMMenu()
        start=Frame().oem_menu(m.view()).wire_bytes()
        m.move(1)
        changed=Frame().oem_menu(m.view()).wire_bytes()
        parts=diff_regions(start,changed)
        total=sum((y1-y0+1)*(c1-c0+1)*2 for y0,y1,c0,c1 in parts)
        # All root strings and icons are unchanged; only cursor moves.
        self.assertLessEqual(total,256)
        self.assertLessEqual(sum(len(list(frame_operations(changed,*r))) for r in parts),20)
        self.assertEqual(len(changed),8192)
    def test_unqualified_actions_remain_inert(self):
        m=OEMMenu();m.move(1);m.move(1);m.select()  # maintenance
        for _ in range(4):m.move(1)  # heater item
        self.assertEqual(m.select(),'inert')
        self.assertEqual(m.last_action,'heat')
        self.assertFalse(m.status()['actuators_enabled_by_menu'])

class HostLifecycle(unittest.TestCase):
    def setUp(self):
        import tempfile
        from test_host_adapters import Printer,Cmd
        self.Cmd=Cmd
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=Printer(self.tmp.name)
        self.p.settings['inventure_oled'].update(oem_menu=True,oem_language='pl')
        self.o=self.p.load_object(None,'inventure_oled')
        self.p.board_ready();self.p.configure()
        self.p.send_event('klippy:ready')
    def test_default_m300_status_then_ok_menu_then_back(self):
        self.assertEqual(self.o.oem_display_mode,'status')
        self.assertTrue(self.o.stream.latest)
        self.assertEqual(self.o._rendered_screen,'klipper_status')
        self.assertEqual(self.o.oem_menu.page,'launcher')
        first=self.o.stream.target
        self.o._select(0.)
        self.assertEqual(self.o.oem_display_mode,'menu')
        self.o._render(.001)
        self.assertNotEqual(first,self.o.stream.latest)
        self.assertIs(first,self.o.stream.target)  # finish the first status frame
        self.assertEqual(self.o.oem_menu.view()['labels'][1:],
                         ['Klipper','Zortrax Inventure','Service / Tests'])
        self.o.oem_menu.move(1)
        self.o.oem_menu.move(1)
        self.o._select(.002)
        self.assertEqual(self.o.oem_menu.page,'root')
        self.assertEqual(self.o.oem_menu.view()['labels'][1],'Models')
        self.o._select(.003)
        self.assertEqual(self.o.oem_menu.page,'launcher')
        self.o.oem_menu.home()
        self.o._select(.004)
        self.assertEqual(self.o.oem_display_mode,'status')
    def test_immediate_menu_mode_is_opt_in_and_safe(self):
        import tempfile
        from test_host_adapters import Printer
        with tempfile.TemporaryDirectory() as tmp:
            p=Printer(tmp)
            p.settings['inventure_oled'].update(oem_menu=True,oem_start_screen='menu')
            o=p.load_object(None,'inventure_oled');p.board_ready();p.configure()
            p.send_event('klippy:ready')
            self.assertEqual(o.oem_display_mode,'menu')
            self.assertEqual(o.oem_menu.view()['labels'][0],'Back')
            self.assertFalse(p.objects['toolhead'].moves)
    def test_repeated_static_render_reuses_cached_frame(self):
        first=self.o.stream.latest
        self.o._render(0.1)
        self.assertIs(first,self.o.stream.latest)
        self.o._select(.2);self.o._render(.2)
        changed=self.o.stream.latest
        self.assertIsNot(first,changed)
        self.o._render(.3)
        self.assertIs(changed,self.o.stream.latest)
    def test_shutdown_and_menu_do_not_create_motion_or_heater_commands(self):
        self.o._select(0.0)
        self.o._select(0.1)  # exit to status with Back selected
        self.assertFalse(self.p.objects['toolhead'].moves)
        self.assertFalse(self.p.objects['gcode'].scripts)
        self.p.send_event('klippy:shutdown')
        self.assertTrue(self.o.shutdown_latched)
        self.assertFalse(self.o.stream.enabled)

if __name__=='__main__':unittest.main()
