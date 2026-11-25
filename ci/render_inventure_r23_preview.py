#!/usr/bin/env python3
"""Export actual register-model SSD1322 RAM as exact grayscale PNGs.

Pixels come from compiled MCU code, not an illustrative renderer. These are
software captures, not photographs or evidence of a physical OLED test.
"""
import argparse
from pathlib import Path
import struct
import zlib

SCREENS=('startup','waiting','disconnected','timer_error','cable_error','door_error','tray_error')


def write_png(path, pixels, width, height):
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    scan=b''.join(b'\0'+pixels[y*width:(y+1)*width] for y in range(height))
    encoded=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,0,0,0,0))
             +chunk(b'IDAT',zlib.compress(scan,9))+chunk(b'IEND',b''))
    path.write_bytes(encoded)


def render(captures, output):
    output.mkdir(parents=True,exist_ok=True)
    width,height=1064,584
    canvas=bytearray(width*height)
    for index,name in enumerate(SCREENS):
        wire=(captures/(name+'.bin')).read_bytes()
        if len(wire)!=8192:
            raise ValueError('Expected exact 8192-byte SSD1322 RAM: '+name)
        pixels=bytes(level*17 for pair in wire for level in (pair>>4,pair&15))
        write_png(output/(name+'.png'),pixels,256,64)
        left,top=8+(index%2)*536,8+(index//2)*144
        for row in range(64):
            scaled=bytes(value for value in pixels[row*256:(row+1)*256] for _ in range(2))
            for dy in range(2):
                offset=(top+row*2+dy)*width+left
                canvas[offset:offset+512]=scaled
    target=output/'INVENTURE_R23_SCREENS.png'
    write_png(target,bytes(canvas),width,height)
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captures',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    print(render(args.captures,args.output))
