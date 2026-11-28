"""Build the paired 224-tick CoreXY firmware with GNU Arm 14.2.1."""
import argparse
from functools import partial
import json
from pathlib import Path

import build_inventure_uart_r27 as parent
from apply_inventure_xy_step_r30 import RELEASE, overlay


STEP_CONSTANTS = {
    'CLOCK_FREQ': 120000000,
    'INVENTURE_XY_STEP_TIMER_ABI': 3,
    'INVENTURE_XY_STEP_MIN_TICKS': 448,
    'INVENTURE_XY_STEP_HIGH_TICKS': 294,
    'INVENTURE_XY_STEP_LOW_FLOOR_TICKS': 90,
}


def build(kit, destination, *, native_z_uart=False):
    parent.RELEASE = RELEASE
    parent.overlay = partial(overlay, native_z_uart=native_z_uart)
    parent.build(kit, destination)
    dictionary = json.loads((destination/'klipper.dict').read_text())
    for key, expected in STEP_CONSTANTS.items():
        if dictionary['config'].get(key) != expected:
            raise RuntimeError('Unexpected R30 firmware constant ' + key)
    assert {'inventure_xy_step_query', 'inventure_xy_step_config'} <= dictionary['commands'].keys()
    uart_window = dictionary['config'].get('INVENTURE_NATIVE_Z_UART_WINDOW_ABI', 0)
    if uart_window != int(native_z_uart):
        raise RuntimeError('Unexpected native Z UART window ABI')
    transport = destination/'R26_TRANSPORT_BUILD.json'
    report = json.loads(transport.read_text())
    report['motion_sources_unchanged'] = False
    transport.write_text(json.dumps(report, indent=2)+'\n')
    (destination/'R30_XY_STEP_BUILD.json').write_text(json.dumps(dict(
        mcu_release=RELEASE, step_timer_abi=3, timer_hz=60_000_000,
        timer_period_ticks=224, high_ticks=147, nominal_low_ticks=77,
        low_floor_us=.75, dma_latency_allowance_us=.50, ramp_step_implementation='Thumb-2',
        native_z_uart_window_abi=uart_window,
        pa7='TIM3/TIM2 constant, TIM14 one-shot', pa6='TIM6/DMA1 Stream1/TIM13',
        oled='SPI2/DMA1 Stream4', hardware_tested=False), indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--native-z-uart', action='store_true')
    args = parser.parse_args()
    build(args.kit.resolve(), args.destination.resolve(), native_z_uart=args.native_z_uart)
