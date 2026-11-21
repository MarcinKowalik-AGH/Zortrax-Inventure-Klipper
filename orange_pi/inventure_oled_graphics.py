# SPDX-License-Identifier: GPL-3.0-or-later
"""Compose the Inventure status footer and OEM menu directly for the SSD1322."""
import base64
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import textwrap
import zlib

from . import inventure_oled_render as native

STRIDE = 128
FOOTER_START = 48 * STRIDE


@lru_cache(maxsize=64)
def status_footer(text):
    text = re.sub(r'~([^~]+)~', lambda m: {'degrees': '°', 'right_arrow': '→'}.get(m[1], '?'), text)
    lines = textwrap.wrap(' '.join(text.split()), width=42, max_lines=2, placeholder='...')
    frame = native.Frame()
    for index, line in enumerate(lines):
        frame.text(2, 52 if len(lines) == 1 else 48 + 8 * index, line)
    return frame.wire_bytes()[FOOTER_START:]


class KlipperStatus(native.KlipperStatus):
    """Keep the installed Inventure status layout, including its thermal icons."""
    def __init__(self, config):
        super().__init__(config)
        self._native_draw_text = self.draw_text
        self.draw_text = self._status_text
        self._source_wire = self._compact_wire = self._source_text = None

    def _status_text(self, row, col, mixed_text, eventtime):
        if row == 3 and col == 0:
            self._footer_text = str(mixed_text)[:240]
        return self._native_draw_text(row, col, mixed_text, eventtime)

    def render(self, now, board):
        self._footer_text = None
        wire = super().render(now, board)
        if self._footer_text is None:
            return wire
        if wire != self._source_wire or self._footer_text != self._source_text:
            self._compact_wire = wire[:FOOTER_START] + status_footer(self._footer_text)
            self._source_wire, self._source_text = wire, self._footer_text
        return self._compact_wire


@lru_cache(maxsize=16)
def _icon_rows(pixels):
    """Prepare original 50×50 grayscale values at the odd x origin (203)."""
    rows = []
    for start in range(0, 2500, 50):
        row = pixels[start:start + 50]
        middle = bytes((row[x] << 4) | row[x + 1] for x in range(1, 49, 2))
        rows.append(bytes([row[0]]) + middle + bytes([row[-1] << 4]))
    return tuple(rows)


@lru_cache(maxsize=24)
def _menu_text(atlas, labels, has_icon, notice):
    frame = native.Frame()
    for row, label in enumerate(labels):
        frame.oem_text(19, row * 12, label, atlas, right=193 if has_icon else 253)
    if notice:
        frame.oem_text(203, 14, 'INFO', atlas, right=255)
        frame.oem_text(203, 30, 'NO ACT', atlas, right=255)
    return frame.wire_bytes()


@lru_cache(maxsize=1)
def _sprite_catalog():
    return json.loads(Path(__file__).with_name('inventure_activity_sprites.json').read_text())


def _activity_catalog():
    return _sprite_catalog()['symbols']


def _unpack(frame, name):
    packed = zlib.decompress(base64.b64decode(frame['zlib_b64'], validate=True))
    if len(packed) != 1250 or sha256(packed).hexdigest() != frame['sha256']:
        raise ValueError('Invalid OLED activity frame: ' + name)
    return packed


@lru_cache(maxsize=1)
def sleep_wire():
    # The M300 sleep screen centers the text on the left and a 50x50 moon at
    # (202,7). Its even X origin allows copying the original packed bytes.
    message = 'Entering sleep mode'
    frame = native.Frame()
    frame.text((192 - (6 * len(message) - 1)) // 2, 24, message)
    wire = bytearray(frame.wire_bytes())
    packed = _unpack(_sprite_catalog()['sleep_moon']['frames'][0], 'sleep_moon')
    for y in range(50):
        offset = (y + 7) * STRIDE + 101
        wire[offset:offset + 25] = packed[y * 25:(y + 1) * 25]
    return bytes(wire)


@lru_cache(maxsize=32)
def _activity_rows(name, index):
    packed = _unpack(_activity_catalog()[name]['frames'][index], name)
    # Repack each row at x=203, preserving its grayscale and blank edge nibbles.
    rows = []
    for start in range(0, len(packed), 25):
        row = int.from_bytes(packed[start:start + 25], 'big') << 4
        rows.append(row.to_bytes(26, 'big'))
    return tuple(rows)


def activity_index(view, now):
    return int(now * 2) % 4 if view.get('animated') else 0


@lru_cache(maxsize=24)
def _background(atlas, labels, icon, notice, index):
    wire = _menu_text(atlas, labels, bool(icon), notice)
    if not icon or notice:
        return wire
    # The OEM layout reserves this black strip exclusively for the sprite.
    # Unused outer nibbles and transparent pixels therefore remain black.
    result = bytearray(wire)
    rows = (_activity_rows(icon[9:], index) if icon.startswith('activity:')
            else _icon_rows(atlas.icons[icon]))
    for y, row in enumerate(rows, 13):
        start = y * STRIDE + 101
        result[start:start + len(row)] = row
    return bytes(result)


def menu_wire(view, atlas=None, index=0):
    atlas = atlas or native.get_oem_assets()
    labels = view['labels']
    if not labels:
        return bytes(8192)
    icon = view.get('icon')
    if icon and not icon.startswith('activity:'):
        pixels = atlas.icons.get(icon)
        if not isinstance(pixels, bytes) or len(pixels) != 2500:
            return native.Frame().oem_menu(view, atlas).wire_bytes()
    pinned = labels[0] in ('Back', 'Powrót')
    selected = min(max(0, view['selected']), len(labels) - 1)
    indices = list(range(min(5, len(labels))))
    if len(labels) > 5:
        first = min(max(int(pinned), int(view.get('window', int(pinned)))), len(labels) - 5 + int(pinned))
        indices = ([0] if pinned else []) + list(range(first, first + 5 - int(pinned)))
    background = _background(atlas, tuple(labels[i] for i in indices), icon, bool(view.get('notice')), index)
    if selected not in indices:
        return background
    result = bytearray(background)
    center = indices.index(selected) * 12 + 5
    back = pinned and selected == 0
    for dy in range(-4, 5):
        x = 8 + abs(dy) if back else 12 - abs(dy)
        result[(center + dy) * STRIDE + x // 2] |= 15 if x & 1 else 240
    return bytes(result)
