"""Recover the original r0323 leveling messages and animation frames.

The descriptor-to-text-to-sprite links are read from the actual application,
not inferred from M300 artwork. No motion/control code is extracted here.
"""
import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_inventure_oled_assets import APPLICATION_SHA256, BASE, unpack_frame

SCREENS = {
    'back': 0x080368C0, 'center': 0x080368CC, 'deformed': 0x080368D8,
    'done': 0x080368E4, 'failed': 0x080368F0, 'front': 0x080368FC,
    'front_tighten': 0x08036908, 'front_loosen': 0x08036914,
    'rear_left_tighten': 0x08036920, 'rear_left_loosen': 0x0803692C,
    'warning': 0x08036938, 'rear_right_tighten': 0x08036944,
    'rear_right_loosen': 0x08036950, 'knobs': 0x0803695C,
}


def extract(application):
    if sha256(application).hexdigest() != APPLICATION_SHA256:
        raise ValueError('Expected the audited Inventure 1.6.1 r0323 application')
    messages, symbols = {}, {}
    for name, address in SCREENS.items():
        text_record, flags, sprite = struct.unpack_from('<3I', application, address-BASE)
        text_pointer, = struct.unpack_from('<I', application, text_record-BASE)
        offset = text_pointer-BASE
        text = application[offset:application.index(b'\0', offset)].decode('utf-8')
        key = '0x%08X' % sprite if sprite else None
        messages[name] = dict(descriptor='0x%08X' % address, text=text, sprite=key,
                              text_pointer='0x%08X' % text_pointer, flags=flags)
        if key is None or key in symbols:
            continue
        width, height, count, image_flags, delay, table, transform = struct.unpack_from(
            '<4B3I', application, sprite-BASE)
        if (width,height,image_flags,transform) != (50,50,0,0) or not 1 <= count <= 9:
            raise ValueError('Unexpected leveling sprite descriptor: '+key)
        frames = []
        for pointer, in struct.iter_unpack('<I', application[table-BASE:table-BASE+4*count]):
            length, = struct.unpack_from('<I',application,pointer-BASE)
            pixels = unpack_frame(application[pointer-BASE+4:pointer-BASE+4+length],1250)
            frames.append(dict(sha256=sha256(pixels).hexdigest(),
                               zlib_b64=base64.b64encode(zlib.compress(pixels,9)).decode()))
        symbols[key] = dict(width=width,height=height,frame_ms=delay,frames=frames)
    return dict(source='Zortrax Inventure 1.6.1 r0323', application_sha256=APPLICATION_SHA256,
                messages=messages, symbols=symbols)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.output.write_text(json.dumps(extract(args.application.read_bytes()),indent=2)+'\n')
