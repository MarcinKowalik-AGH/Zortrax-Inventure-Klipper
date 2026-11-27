"""Build the transport-only R26 MCU and retain the verified R25 Info checks."""
import argparse
import json
from pathlib import Path
import build_inventure_information_r25 as info
from apply_inventure_transport_r26 import RELEASE, overlay

def build(kit,destination):
    info.RELEASE,info.overlay=RELEASE,overlay
    info.build(kit,destination)
    d=json.loads((destination/'klipper.dict').read_text())
    if d['config'].get('INVENTURE_OLED_COEXISTENCE_ABI')!=1:
        raise ValueError('Missing interruptible OLED transport capability')
    if 'inventure_oled_coexist_query' not in d['commands']:
        raise ValueError('Missing read-only transport diagnostics')
    (destination/'R26_TRANSPORT_BUILD.json').write_text(json.dumps(dict(
        status='PASS_R26_TRANSPORT_ARM_BUILD',mcu_release=RELEASE,
        m300_source='d1616a9e7007b44dda2a737cc0297b7f66a77b63',
        uart_unchanged='InventureBoot P4 setting',oled_dma='DMA1 Stream4 SPI2',
        motion_sources_unchanged=True,hardware_tested=False),indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('kit',type=Path);p.add_argument('destination',type=Path)
    a=p.parse_args();build(a.kit.resolve(),a.destination.resolve())
