# SPDX-License-Identifier: GPL-3.0-or-later
"""Moonraker view of the last complete, acknowledged SSD1322 framebuffer."""
import base64
import struct
import zlib

PALETTE = bytes.fromhex('020610 030b15 030f1a 041423 051b2d 08395f 0a436c 0c588e '
                        '0d619a 0e6ba8 0f75b7 1081cb 128cdc 1497eb 159df5 16a1fe')


def png_chunk(name, data):
    return (struct.pack('>I', len(data)) + name + data
            + struct.pack('>I', zlib.crc32(name + data) & 0xffffffff))


def encode_frame(wire):
    if len(wire) != 8192:
        raise ValueError('Expected a 256 by 64 SSD1322 framebuffer')
    header = struct.pack('>IIBBBBB', 256, 64, 4, 3, 0, 0, 0)
    rows = b''.join(b'\0' + wire[i:i+128] for i in range(0, len(wire), 128))
    png = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', header) + png_chunk(b'PLTE', PALETTE)
           + png_chunk(b'IDAT', zlib.compress(rows, 1)) + png_chunk(b'IEND', b''))
    return base64.b64encode(png).decode('ascii')


class OledPreview:
    def __init__(self, owner):
        self.owner = owner
        self.previous = None
        self.image = ''

    def get_status(self, now):
        owner = self.owner
        stream = owner.stream
        live = stream.ready and owner._base_live(now) and owner._wire_live(now)
        state = stream.display_mode if live else 'offline'
        wire = stream.previous if live and state != 'off' else None
        if wire != self.previous:
            self.previous = wire
            self.image = encode_frame(wire) if wire is not None else ''
        return {'display': state, 'image_base64': self.image}
