"""Compile R27 using the same pinned GNU ARM and all prior ABI checks."""
import argparse,json
from pathlib import Path
import build_inventure_transport_r26 as parent
from apply_inventure_uart_r27 import RELEASE,overlay

def build(kit,destination):
    parent.RELEASE,parent.overlay=RELEASE,overlay
    parent.build(kit,destination)
    dictionary=json.loads((destination/'klipper.dict').read_text())
    if dictionary['config'].get('INVENTURE_UART_TIMER_WINDOW_ABI')!=1:
        raise ValueError('Missing compiled sampler/UART priority window')
    if 'inventure_uart_window_query' not in dictionary['commands']:
        raise ValueError('Missing read-only UART window telemetry')
    (destination/'R27_UART_BUILD.json').write_text(json.dumps(dict(
        status='PASS_R27_UART_ARM_BUILD',mcu_release=RELEASE,uart_priority=0,
        timer_priority=2,oled_dma_priority=4,masked_priority_floor=1,
        hardware_tested=False),indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('kit',type=Path);p.add_argument('destination',type=Path)
    a=p.parse_args();build(a.kit.resolve(),a.destination.resolve())
