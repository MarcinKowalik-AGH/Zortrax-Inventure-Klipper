import unittest, sys
from pathlib import Path
pkg=Path(__file__).resolve().parents[2]/'host'
sys.path.insert(0,str(pkg))
from inventure_oem_menu import OEMMenu, MENU
from inventure_oled_render import Frame, get_oem_assets

class OEMMenuTests(unittest.TestCase):
    def test_five_stock_root_branches(self):
        m=OEMMenu()
        self.assertEqual(m.view()['labels'],['Powrót','Modele','Konserwacja','Materiał','Ustawienia','Informacje'])
        self.assertEqual(len(MENU['root'][3]),5)
    def test_no_wrap_pinned_back_and_restored_window(self):
        m=OEMMenu()
        self.assertEqual(m.move(-1),0)
        for _ in range(9):m.move(1)
        self.assertEqual(m.selected,5)
        self.assertEqual(m.move(1),5)
        self.assertEqual(m.select(),'submenu')
        self.assertEqual(m.view()['labels'][0],'Powrót')
        self.assertEqual(m.move(-1),0)
        self.assertEqual(m.select(),'back')
        self.assertEqual(m.selected,5)
    def test_inert_heaters_motion_nvm_print(self):
        m=OEMMenu()
        m.move(1);m.move(1);m.select();[(m.move(1)) for _ in range(4)]
        self.assertEqual(m.select(),'inert')
        self.assertFalse(m.status()['actuators_enabled_by_menu'])
        self.assertEqual(m.last_action,'heat')
    def test_dialog_and_language_no_hw_side_effect(self):
        m=OEMMenu();[m.move(1) for _ in range(4)];m.select();m.move(1);m.select();m.move(1);m.move(1)
        self.assertEqual(m.select(),'language')
        self.assertEqual(m.language,'pl')
        m.move(-1);self.assertEqual(m.select(),'language');self.assertEqual(m.language,'en')
        self.assertTrue(m.status()['ui_only'])
    def test_font_and_icons_from_r0323(self):
        a=get_oem_assets()
        self.assertEqual(len(a.glyphs),403)
        self.assertEqual(len(a.icons),10)
        self.assertEqual(a.sha256,'9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268')
        self.assertIn(ord('ł'),a.glyphs)
    def test_oem_renderer_pixel_buffer_and_glyph(self):
        a=get_oem_assets()
        f=Frame().oem_menu(OEMMenu().view(),a)
        self.assertEqual(len(f.wire_bytes()),8192)
        self.assertNotEqual(f.wire_bytes(),bytes(8192))
        self.assertNotEqual(f.raw,bytearray(8192))
        self.assertGreater(sum(f.raw),0)
        self.assertEqual(f.get_pixel(203,63),f.get_pixel(203,63))
    def test_oem_root_viewport_keeps_current_selection_visible(self):
        m=OEMMenu();self.assertEqual(m.window,1)
        self.assertEqual(m.view()['selected'],0)
        self.assertEqual(m.view()['labels'][0],'Powrót')
        for _ in range(5):m.move(1)
        self.assertEqual(m.window,2)
        self.assertEqual(m.selected,5)
        for _ in range(5):m.move(-1)
        self.assertEqual(m.selected,0)
        self.assertEqual(m.window,1)

    def test_oem_icons_keep_original_4bpp_dynamic_range(self):
        atlas=get_oem_assets()
        self.assertTrue(any(v>1 for v in atlas.icons['models']))
        m=OEMMenu();m.move(1);m.select()
        view=m.view()
        frame=Frame().oem_menu(view,atlas)
        self.assertTrue(any(frame.get_pixel(x,y)>1 for y in range(13,63)
                            for x in range(203,253)))

    def test_all_items_have_label_and_unique_root_paths(self):
        self.assertGreaterEqual(len(MENU),20)
        for key,(pl,en,icon,items) in MENU.items():
            self.assertTrue(pl and en)
            for item in items:
                self.assertTrue(item.label_pl and item.label_en)
                if item.child:self.assertIn(item.child,MENU)
    def test_noninteractive_pause_exit(self):
        m=OEMMenu();m.page='pause';m.selected=3
        self.assertEqual(m.select(),'submenu')
        self.assertIn(m.page, ('stop_confirm','pause_material'))
        self.assertFalse(m.status()['actuators_enabled_by_menu'])

if __name__=='__main__':unittest.main()
