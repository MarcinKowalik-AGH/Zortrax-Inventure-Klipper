"""Independent pixels/catalog checks on the compiled R23 MCU display."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'ci')]
from prepare_kit import prepare
from render_inventure_r23_preview import render


def array(path,name):
    text=path.read_text()
    body=re.search(r'\b'+name+r'(?:\[[^]]*\])+\s*=\s*\{(.*?)\};',text,re.S).group(1)
    return [int(n,0) for n in re.findall(r'0x[0-9a-fA-F]+|\b\d+\b',body)]


def icon(header,kind,index):
    data=array(header,'local_'+kind+'_rle')
    frames=array(header,'local_'+kind+'_frames')
    result=[]
    for row in range(50):
        offset=frames[index*4*50+row]
        pixels=[]
        while len(pixels)<50:
            token=data[offset];offset+=1
            pixels.extend([token&15]*((token>>4)+1))
        if len(pixels)!=50:raise ValueError('Invalid immutable 50px row')
        result.extend(pixels)
    return bytes(result)


def extracted_icon(wire):
    return bytes((wire[y*128+x//2]>>(0 if x%2 else 4))&15
                 for y in range(7,57) for x in range(200,250))


class DisplayR23Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='inventure-r23-pixels-')
        cls.work=Path(cls.tmp.name)
        kit=prepare(cls.work/'kit')
        cls.captures=Path(os.environ.get('INVENTURE_DISPLAY_CAPTURE_DIR',str(cls.work/'captures')))
        cls.captures.mkdir(parents=True,exist_ok=True)
        binary=cls.work/'panel'
        cmd=['gcc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-pointer-to-int-cast',
             '-I',ROOT/'tests/mcu/mock','-I',ROOT/'service_mcu/control_r6/SOURCE_MCU',
             '-I',kit/'mcu','-I',kit/'regression_tests/overlay/src/stm32',
             ROOT/'tests/mcu/test_r23_panel.c','-o',binary]
        subprocess.run(list(map(str,cmd)),check=True,capture_output=True,text=True,timeout=30)
        result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=15,
                              env=dict(os.environ,INVENTURE_DISPLAY_CAPTURE_DIR=str(cls.captures)))
        cls.c_proof=result.stdout
        cls.source=ROOT/'service_mcu/control_r6/SOURCE_MCU'

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_assets_match_the_pinned_working_m300_sources(self):
        spec=json.loads((ROOT/'docs/validation/INVENTURE_R23_M300_DISPLAY_PROVENANCE.json').read_text())
        self.assertEqual(spec['reference_commit'],'20115e6b171c74a12a9578a3c95ec488c8ff6dd8')
        for name,record in spec['assets'].items():
            self.assertEqual(sha256((self.source/name).read_bytes()).hexdigest(),record['sha256'],name)

    def test_actual_spi_pixels_keep_native_m300_icons_and_grayscale(self):
        for name,index in (('disconnected',11),('timer_error',10),('cable_error',5),
                           ('door_error',10),('tray_error',6)):
            with self.subTest(screen=name):
                wire=(self.captures/(name+'.bin')).read_bytes()
                expected=icon(self.source/'inventure_oled_error_icons.inc','error',index)
                self.assertEqual(extracted_icon(wire),expected)
                self.assertGreater(len(set(expected)),2)
        self.assertEqual(extracted_icon((self.captures/'waiting.bin').read_bytes()),
                         icon(self.source/'inventure_oled_activity.inc','activity',0))

    def test_error_text_is_not_clipped_by_the_icon_column(self):
        glyph=self.source.parent/'TESTS/support/inventure_oled_stock_glyphs.inc'
        packed=array(glyph,'glyph_packed');spans=array(glyph,'glyph_span')
        widths={};offset=0
        for code in range(32,127):
            value=packed[offset];offset+=1;width=value>>4;span=value&15
            if span==15:span=packed[offset];offset+=1
            else:span=spans[span]
            widths[chr(code)]=width
            offset+=(width*(span&15)+7)//8
        catalog=(self.source/'inventure_display_errors.inc').read_text().split('display_catalog[',1)[1]
        catalog=catalog.split('};',1)[0]
        for text in re.findall(r'"([^"\n]*)"',catalog):
            pixels=sum(widths.get(c,widths['?'])+1 for c in text)
            self.assertLessEqual(pixels,188,'Text overlaps the 50px icon: '+text)

    def test_c_lifecycle_checks_and_exact_png_export(self):
        self.assertIn('1307B/frame',self.c_proof)
        self.assertIn('first shutdown cause',self.c_proof)
        preview=render(self.captures,self.captures/'preview')
        self.assertTrue(preview.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))


if __name__=='__main__':unittest.main()
