"""Build the paired r0323-rate CoreXY firmware with GNU Arm 14.2.1."""
import argparse
import json
from pathlib import Path

import build_inventure_uart_r27 as parent
from apply_inventure_xy_step_r29 import RELEASE, overlay


def build(kit, destination):
    parent.RELEASE, parent.overlay = RELEASE, overlay
    parent.build(kit, destination)
    dictionary = json.loads((destination/'klipper.dict').read_text())
    constants = dictionary['config']
    assert constants['INVENTURE_XY_STEP_TIMER_ABI'] == 2
    assert constants['INVENTURE_XY_STEP_MIN_TICKS'] == 660
    assert constants['INVENTURE_XY_STEP_HIGH_TICKS'] == 294
    assert {'inventure_xy_step_query', 'inventure_xy_step_config'} <= dictionary['commands'].keys()
    transport = destination/'R26_TRANSPORT_BUILD.json'
    report = json.loads(transport.read_text())
    report['motion_sources_unchanged'] = False
    transport.write_text(json.dumps(report, indent=2)+'\n')
    (destination/'R29_XY_STEP_BUILD.json').write_text(json.dumps(dict(
        mcu_release=RELEASE, step_timer_abi=2, fast_step_pulse_us=2.45,
        minimum_period_ticks=660, oem_motor_velocity_mm_s=200, oem_xy_accel_mm_s2=1500,
        pa7='TIM3/TIM2 constant, TIM14 one-shot', pa6='TIM6/DMA1 Stream1/TIM13',
        oled='SPI2/DMA1 Stream4', hardware_tested=False), indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    build(args.kit.resolve(), args.destination.resolve())
