#!/usr/bin/env python3
"""Inventure V9 OLED reference renderer / Klipper UI porting aid (offline).

Pure renderer adapted for inventure_oled.py; it does not access hardware itself.
It reproduces the 5x7 glyph bitmap and packed SSD1322 framebuffer rules from
Inventure boot v1.3, with the left-line + triangle menu composition derived
from the v1.3 OLED UI patch preview (last patch C sources not in release).

Does not access printers, SD or microcontroller peripherals.
"""
import argparse
import json
import base64
import zlib
from pathlib import Path

WIDTH, HEIGHT = 256, 64
RAW_LEN = WIDTH * HEIGHT // 2  # 8192 bytes
# Glyph masks supplied with the user project; no system font dependency.
FONT = {'A': [14, 17, 17, 31, 17, 17, 17], 'B': [30, 17, 17, 30, 17, 17, 30], 'C': [15, 16, 16, 16, 16, 16, 15], 'D': [30, 17, 17, 17, 17, 17, 30], 'E': [31, 16, 16, 30, 16, 16, 31], 'F': [31, 16, 16, 30, 16, 16, 16], 'G': [15, 16, 16, 23, 17, 17, 15], 'H': [17, 17, 17, 31, 17, 17, 17], 'I': [31, 4, 4, 4, 4, 4, 31], 'J': [7, 2, 2, 2, 18, 18, 12], 'K': [17, 18, 20, 24, 20, 18, 17], 'L': [16, 16, 16, 16, 16, 16, 31], 'M': [17, 27, 21, 21, 17, 17, 17], 'N': [17, 25, 21, 19, 17, 17, 17], 'O': [14, 17, 17, 17, 17, 17, 14], 'P': [30, 17, 17, 30, 16, 16, 16], 'Q': [14, 17, 17, 17, 21, 18, 13], 'R': [30, 17, 17, 30, 20, 18, 17], 'S': [15, 16, 16, 14, 1, 1, 30], 'T': [31, 4, 4, 4, 4, 4, 4], 'U': [17, 17, 17, 17, 17, 17, 14], 'V': [17, 17, 17, 17, 17, 10, 4], 'W': [17, 17, 17, 21, 21, 21, 10], 'X': [17, 17, 10, 4, 10, 17, 17], 'Y': [17, 17, 10, 4, 4, 4, 4], 'Z': [31, 1, 2, 4, 8, 16, 31], '0': [14, 17, 19, 21, 25, 17, 14], '1': [4, 12, 4, 4, 4, 4, 14], '2': [14, 17, 1, 2, 4, 8, 31], '3': [30, 1, 1, 14, 1, 1, 30], '4': [2, 6, 10, 18, 31, 2, 2], '5': [31, 16, 16, 30, 1, 1, 30], '6': [14, 16, 16, 30, 17, 17, 14], '7': [31, 1, 2, 4, 8, 8, 8], '8': [14, 17, 17, 14, 17, 17, 14], '9': [14, 17, 17, 15, 1, 1, 14], 'a': [0, 0, 14, 1, 15, 17, 15], 'b': [16, 16, 22, 25, 17, 17, 30], 'c': [0, 0, 15, 16, 16, 16, 15], 'd': [1, 1, 13, 19, 17, 17, 15], 'e': [0, 0, 14, 17, 31, 16, 14], 'f': [6, 9, 8, 28, 8, 8, 8], 'g': [0, 0, 15, 17, 15, 1, 14], 'h': [16, 16, 22, 25, 17, 17, 17], 'i': [4, 0, 12, 4, 4, 4, 14], 'j': [2, 0, 6, 2, 2, 18, 12], 'k': [16, 16, 18, 20, 24, 20, 18], 'l': [12, 4, 4, 4, 4, 4, 14], 'm': [0, 0, 26, 21, 21, 21, 21], 'n': [0, 0, 22, 25, 17, 17, 17], 'o': [0, 0, 14, 17, 17, 17, 14], 'p': [0, 0, 30, 17, 30, 16, 16], 'q': [0, 0, 15, 17, 15, 1, 1], 'r': [0, 0, 22, 25, 16, 16, 16], 's': [0, 0, 15, 16, 14, 1, 30], 't': [8, 8, 28, 8, 8, 9, 6], 'u': [0, 0, 17, 17, 17, 19, 13], 'v': [0, 0, 17, 17, 17, 10, 4], 'w': [0, 0, 17, 17, 21, 21, 10], 'x': [0, 0, 17, 10, 4, 10, 17], 'y': [0, 0, 17, 17, 15, 1, 14], 'z': [0, 0, 31, 2, 4, 8, 31], ' ': [0, 0, 0, 0, 0, 0, 0], '.': [0, 0, 0, 0, 0, 6, 6], ',': [0, 0, 0, 0, 0, 4, 8], ':': [0, 0, 4, 0, 0, 4, 0], ';': [0, 0, 4, 0, 0, 4, 8], '-': [0, 0, 0, 31, 0, 0, 0], '_': [0, 0, 0, 0, 0, 0, 31], '+': [0, 4, 4, 31, 4, 4, 0], '=': [0, 0, 31, 0, 31, 0, 0], '(': [2, 4, 8, 8, 8, 4, 2], ')': [8, 4, 2, 2, 2, 4, 8], '[': [14, 8, 8, 8, 8, 8, 14], ']': [14, 2, 2, 2, 2, 2, 14], '{': [3, 4, 4, 8, 4, 4, 3], '}': [24, 4, 4, 2, 4, 4, 24], '<': [1, 2, 4, 8, 4, 2, 1], '>': [16, 8, 4, 2, 4, 8, 16], '?': [14, 17, 1, 2, 4, 0, 4], '!': [4, 4, 4, 4, 4, 0, 4], "'": [4, 4, 8, 0, 0, 0, 0], '"': [10, 10, 0, 0, 0, 0, 0], '#': [10, 10, 31, 10, 31, 10, 10], '$': [4, 15, 20, 14, 5, 30, 4], '&': [12, 18, 20, 8, 21, 18, 13], '*': [0, 21, 14, 31, 14, 21, 0], '@': [14, 17, 23, 21, 23, 16, 14], '^': [4, 10, 17, 0, 0, 0, 0], '|': [4, 4, 4, 4, 4, 4, 4], '~': [0, 0, 9, 22, 0, 0, 0], '`': [8, 4, 0, 0, 0, 0, 0], '%': [25, 26, 2, 4, 8, 11, 19], '©': [28, 34, 93, 81, 93, 34, 28], '®': [14, 17, 29, 27, 21, 17, 14], '°': [6, 9, 9, 6, 0, 0, 0], '•': [0, 0, 14, 14, 14, 0, 0], '←': [0, 4, 8, 31, 8, 4, 0], '→': [0, 4, 2, 31, 2, 4, 0], '✓': [0, 0, 1, 2, 20, 8, 0], '×': [0, 17, 10, 4, 10, 17, 0], '/': [1, 1, 2, 4, 4, 8, 16], '\\': [16, 16, 8, 4, 4, 2, 1], 'ą': [0, 0, 14, 1, 15, 17, 15], 'Ą': [14, 17, 17, 31, 17, 17, 17], 'ć': [2, 0, 15, 16, 16, 16, 15], 'Ć': [15, 16, 16, 16, 16, 16, 15], 'ę': [0, 0, 14, 17, 31, 16, 15], 'Ę': [31, 16, 16, 30, 16, 16, 31], 'ł': [12, 4, 4, 7, 4, 4, 14], 'Ł': [16, 16, 16, 23, 16, 16, 31], 'ń': [2, 0, 22, 25, 17, 17, 17], 'Ń': [19, 25, 21, 19, 17, 17, 17], 'ó': [2, 0, 14, 17, 17, 17, 14], 'Ó': [14, 17, 17, 17, 17, 17, 14], 'ś': [2, 0, 15, 16, 14, 1, 30], 'Ś': [15, 16, 16, 14, 1, 1, 30], 'ź': [2, 0, 31, 2, 4, 8, 31], 'Ź': [31, 1, 2, 4, 8, 16, 31], 'ż': [4, 0, 31, 2, 4, 8, 31], 'Ż': [31, 1, 2, 4, 8, 16, 31]}
TITLE = "Inventure Klipper"

class OEMAssets:
    """Read-only, self-contained atlas extracted from stock r0323 application."""
    def __init__(self, path=None):
        source=Path(path) if path else Path(__file__).with_name('inventure_oem_assets.json')
        d=json.loads(source.read_text(encoding='utf8'))
        if d.get('font_count')!=403 or d.get('icon_count')!=10:
            raise ValueError('Inventure OEM asset catalogue incomplete')
        self.glyphs={int(k):(int(v[0]),int(v[1]),base64.b64decode(v[2]))
                     for k,v in d['glyphs'].items()}
        self.icons={}
        for k,v in d['icons'].items():
            data=zlib.decompress(base64.b64decode(v['payload_zlib_b64']))
            if len(data)!=2500 or v['width']!=50 or v['height']!=50:
                raise ValueError('Bad OEM icon size: '+k)
            self.icons[k]=data
        self.sha256=d['application_sha256']

_OEM_ASSETS=None

def get_oem_assets(path=None):
    global _OEM_ASSETS
    if path is not None:
        return OEMAssets(path)
    if _OEM_ASSETS is None: _OEM_ASSETS=OEMAssets()
    return _OEM_ASSETS


class Frame:
    def __init__(self):
        # identical column-major memory representation as v1.3
        self.raw = bytearray(RAW_LEN)

    def pixel(self, x: int, y: int, level: int = 15):
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            return
        i = (x // 2) * HEIGHT + y
        shift = 4 if (x & 1) == 0 else 0
        self.raw[i] = (self.raw[i] & ~(0xF << shift)) | ((level & 0xF) << shift)

    def get_pixel(self, x, y):
        assert 0 <= x < WIDTH and 0 <= y < HEIGHT
        i = (x // 2) * HEIGHT + y
        return (self.raw[i] >> (4 if not x % 2 else 0)) & 0xF

    def text(self, x, y, chars, level=15):
        # 1x font size, 5x7 (© exactly 7x7). 1 blank pixel between glyphs.
        for c in chars:
            glyph = FONT.get(c, FONT["?"])
            width = 7 if c == "©" else 5
            if x + width > WIDTH:
                break
            for dy, bits in enumerate(glyph):
                for dx in range(width):
                    if bits & (1 << (width-1-dx)):
                        self.pixel(x + dx, y + dy, level)
            x += width + 1

    def oem_text(self,x,y,chars,assets=None,level=15,right=194):
        """Stock r0323 original 13-row proportional glyphs (bit7 = x0)."""
        atlas=assets or get_oem_assets()
        for c in str(chars):
            glyph=atlas.glyphs.get(ord(c),atlas.glyphs[ord('?')])
            width,span,bits=glyph
            if x+width>right:break
            for dy in range(span):
                if dy>=len(bits):break
                mask=bits[dy]
                for dx in range(width):
                    if mask & (1<<(7-dx)):
                        self.pixel(x+dx,y+dy,level)
            x+=width+1
        return x

    def oem_icon(self,name,assets=None):
        atlas=assets or get_oem_assets()
        data=atlas.icons.get(name)
        if data is None:return False
        for y in range(50):
            for x in range(50):
                v=data[y*50+x]
                if v:
                    self.pixel(203+x,13+y,v) # already quantized 0..15 in OEMAssets
        return True

    def oem_menu(self,view,assets=None):
        """Native r0323 five-row layout (y=0,12,24,36,48), entire display.

        Like the M300 Klipper UI: no fixed Zortrax title or underline. Back is
        a normal selectable row, pinned during scrolling. A static category
        icon is rendered on submenu pages; the root doesn't redraw a 50x50
        sprite on every navigation detent. Action dispatch stays host-owned.
        """
        atlas=assets or get_oem_assets()
        self.raw[:]=b'\x00'*RAW_LEN
        labels=list(view['labels'])
        if not labels:return self
        sel=max(0,min(view['selected'],len(labels)-1))
        has_back=labels[0] in ('Back','Powrót')
        visible_count=4 if has_back else 5
        if len(labels)<=5:
            visible=list(range(len(labels)))
        elif has_back:
            first=max(1,min(int(view.get('window',1)),max(1,len(labels)-visible_count)))
            visible=[0]+list(range(first,min(first+visible_count,len(labels))))
        else:
            first=max(0,min(int(view.get('window',0)),max(0,len(labels)-visible_count)))
            visible=list(range(first,min(first+visible_count,len(labels))))
        right=193 if view.get('icon') else 253
        for row,index in enumerate(visible):
            y=row*12
            self.oem_text(19,y,labels[index],atlas,right=right)
            if index==sel:
                # Nine-pixel stock marker; Back points left, all other rows
                # point right. Marker never overlaps text or the next row.
                for dy in range(-4,5):
                    yy=y+5+dy
                    xx=(8+abs(dy)) if index==0 and has_back else (12-abs(dy))
                    self.pixel(xx,yy,15)
        if view.get('notice'):
            # Preserve all five selectable rows: notice is in icon column.
            self.oem_text(203,14,'INFO',atlas,right=255)
            self.oem_text(203,30,'NO ACT',atlas,right=255)
        elif view.get('icon'):
            self.oem_icon(view['icon'],atlas)
        return self

    def klipper_status(self, status, assets=None):
        """M300-like Klipper home (EN); OK opens the full-height OEM menu.

        Do not mislabel unqualified native board ADC samples as actual
        extruder/bed temperatures; the test-only printer uses kinematics none.
        """
        atlas=assets or get_oem_assets()
        self.raw[:]=b'\x00'*RAW_LEN
        self.oem_text(4,0,'KLIPPER',atlas,right=252)
        healthy=bool(status.get('connected') and not status.get('fault'))
        self.oem_text(4,12,'MCU: Ready' if healthy else 'MCU: Check status',atlas,right=252)
        rel=str(status.get('mcu_release') or 'unknown')
        self.oem_text(4,24,rel.replace('P01-P17-',''),atlas,right=252)
        baud=status.get('p4_boot',{}).get('baud')
        self.oem_text(4,36,'P4: '+str(baud if baud is not None else '---'),atlas,right=252)
        self.oem_text(4,48,'OK: Open menu',atlas,right=252)
        return self

    def header(self, title=TITLE):
        self.raw[:] = b"\x00"*RAW_LEN
        self.text(4, 2, title)
        for x in range(4, 252):
            self.pixel(x, 11, 5)

    def status(self, upper, lower, header=TITLE):
        self.header(header)
        self.text(4, 23, upper)
        self.text(4, 41, lower)
        return self

    def menu(self, title, items, selected=0, keep_back_visible=True, header=TITLE):
        self.header(header)
        self.text(4, 14, title)
        if not items:
            return self
        selected = max(0, min(int(selected), len(items)-1))
        # In the UI patch Back has index 0 and stays in the first row during scroll.
        if len(items) <= 4:
            visible = list(range(len(items)))
        elif keep_back_visible and items[0] in ("Back", "Powrót"):
            if selected <= 3:
                visible = [0,1,2,3]
            else:
                visible = [0] + list(range(max(1, selected-2), selected+1))
        else:
            first = max(0, selected-3)
            visible = list(range(first, min(first+4,len(items))))
        # Stock-style left line and moving triangle, no highlight rectangle.
        for y in range(24, 63):
            self.pixel(9,y,7)
        for row, idx in enumerate(visible[:4]):
            y = 23 + row * 10
            self.text(20, y+1, items[idx],15)
            if idx == selected:
                # Exact 9-pixel stock-style arrow outline from exported UI patch.
                for dy in range(9):
                    ay = y + 1 + dy
                    x = 9 + min(dy, 8-dy)
                    if 0 < dy < 8: self.pixel(9, ay, 0)
                    self.pixel(x, ay, 15)
        return self

    def progress(self, phase, description, pct, header=TITLE):
        pct = min(100,max(0,int(pct)))
        self.header(header)
        self.text(4, 17, phase)
        self.text(4, 30, description)
        for y in range(45,58):
            for x in range(4,252):
                border = y in (45,57) or x in (4,251)
                if border or x < 5 + 246*pct//100:
                    self.pixel(x,y,8 if border else 15)
        return self

    def wire_bytes(self):
        # SSD1322 0x5C stream: 64 rows x 128 bytes per row.
        # WARNING: writing self.raw directly produces distorted image!
        return bytes(self.raw[col_byte * HEIGHT + y]
                     for y in range(HEIGHT) for col_byte in range(WIDTH//2))

    def to_image(self, scale=4):
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError('Pillow required for PNG preview; wire_bytes needs no third-party packages') from exc
        palette=[(2,6,16),(3,11,21),(3,15,26),(4,20,35),
                 (5,27,45),(8,57,95),(10,67,108),(12,88,142),
                 (13,97,154),(14,107,168),(15,117,183),(16,129,203),
                 (18,140,220),(20,151,235),(21,157,245),(22,161,254)]
        img=Image.new('RGB',(WIDTH,HEIGHT),(2,6,16))
        px=img.load()
        for y in range(HEIGHT):
            for x in range(WIDTH):
                px[x,y]=palette[self.get_pixel(x,y)]
        return img.resize((WIDTH*scale,HEIGHT*scale), Image.Resampling.NEAREST)

