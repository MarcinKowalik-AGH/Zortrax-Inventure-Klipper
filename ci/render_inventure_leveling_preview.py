"""Render the authenticated physical OLED wizard without accessing a printer."""
import argparse
from hashlib import sha256
import importlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inventure_bootstrap as bootstrap


def png(wire, scale=3, width=256, height=64):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    rows=[]
    stride=width//2
    for y in range(height):
        row=bytes(level*17 for byte in wire[y*stride:(y+1)*stride] for level in (byte>>4,byte&15))
        scaled=b'\0'+bytes(level for level in row for _ in range(scale))
        rows.extend([scaled]*scale)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',width*scale,height*scale,8,0,0,0,0))
            +chunk(b'IDAT',zlib.compress(b''.join(rows),9))+chunk(b'IEND',b''))


def render(destination):
    _, files, metadata=bootstrap._plan(ROOT)
    captures={}
    with tempfile.TemporaryDirectory(prefix='inventure-oled-level-preview-') as temp:
        extras=Path(temp)/'extras'
        for name,(data,_) in files.items():
            if name.startswith('klippy/extras/'):
                path=Path(temp)/name.removeprefix('klippy/')
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        package=types.ModuleType('level_preview');package.__path__=[str(extras)]
        sys.modules[package.__name__]=package
        graphics=importlib.import_module(package.__name__+'.inventure_oled_graphics')
        samples=[('preparing',dict(phase='preparing'),'Stop / cancel'),
                 ('homing_z',dict(phase='preparing',ui=dict(action=dict(kind='home',axis='Z'))),'Stop / cancel'),
                 ('homing_xy',dict(phase='heating',ui=dict(action=dict(kind='home',axis='X'))),'Stop / cancel'),
                 ('heating',dict(phase='heating',ui=dict(action=dict(kind='level_heat'),target_c=200.)),'Stop / cancel'),
                 ('selecting_t0',dict(phase='heating',ui=dict(action=dict(kind='move'),modules=['select_tool'])),'Stop / cancel'),
                 ('cleaning',dict(phase='heating',ui=dict(action=dict(kind='move'),modules=['clean'])),'Stop / cancel'),
                 ('tighten_all',dict(phase='await_knobs',awaiting_operator=True),'Knobs tightened / next')]
        for knob in ('front','rear_left','rear_right'):
            for action,direction in (('tighten','counterclockwise'),('loosen','clockwise')):
                instruction=dict(knob=knob,action=action,direction=direction,turns=.25,delta_steps=123)
                samples.append((knob+'_'+action,dict(phase='adjust',awaiting_operator=True,
                                                    instructions=[instruction]),'Adjusted / next'))
        samples.extend((name,dict(phase='measuring',next_point=point),'Stop / cancel')
                       for name,point in (('checking_front',0),('checking_back',4),('checking_center',2)))
        for phase in ('candidate','saved'):
            samples.append((phase,dict(phase=phase,candidate_depth_um=138408,center_error_steps=8,
                heights_steps={0:-123,1:-87,2:-31,3:-2,4:53}),
                'Back' if phase=='saved' else 'Review / save result'))
        destination.mkdir(parents=True,exist_ok=True)
        first_frames={}
        for name,run,label in samples:
            screen=graphics.leveling_screen(run)
            frames=len((graphics._symbol(screen['icon']) or {}).get('frames',())) or 1
            for index in range(frames):
                wire=graphics.leveling_wire(screen,[label],0,index)
                if index==0:first_frames[name]=wire
                filename='%s_%d.png' % (name,index)
                (destination/filename).write_bytes(png(wire))
                captures[filename]=dict(wire_bytes=len(wire),wire_sha256=sha256(wire).hexdigest())
        grid=['homing_z','heating','selecting_t0','tighten_all','checking_front','candidate']
        rows=[]
        for pair in range(0,len(grid),2):
            for y in range(64):
                rows.append(b''.join(first_frames[name][y*128:(y+1)*128] for name in grid[pair:pair+2]))
        gallery='OLED_LEVELING_STAGES.png'
        (destination/gallery).write_bytes(png(b''.join(rows),scale=2,width=512,height=192))
        proof=dict(status='PASS_INSTALLED_LEVELING_OLED_RENDER',hardware_tested=False,
                   ui_release=graphics.LEVELING_RENDERER_RELEASE,gallery=gallery,
                   restoration=metadata['runtime_restorations']['operator_menu_r23'],
                   oem_catalog_sha256=sha256(json.dumps(graphics._LEVELING_OEM,sort_keys=True).encode()).hexdigest(),
                   frames=captures)
        (destination/'INVENTURE_R23_LEVELING_PREVIEW.json').write_text(json.dumps(proof,indent=2)+'\n')
        return proof


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination',type=Path)
    proof=render(parser.parse_args().destination)
    print(proof['status'],len(proof['frames']),'frames; physical hardware not accessed')
