"""Build the F207 STEP candidate with the same pinned toolchain as R27."""
import argparse
import json
from pathlib import Path

import build_inventure_uart_r27 as parent
from apply_inventure_xy_step_r28 import RELEASE, overlay


def build(kit, destination):
    parent.RELEASE, parent.overlay = RELEASE, overlay
    parent.build(kit, destination)
    dictionary = json.loads((destination/'klipper.dict').read_text())
    constants = dictionary['config']
    assert constants['INVENTURE_XY_STEP_TIMER_ABI'] == 1
    assert constants['INVENTURE_XY_STEP_MIN_TICKS'] == 1200
    assert {'inventure_xy_step_query', 'inventure_xy_step_config'} <= dictionary['commands'].keys()
    response = ("inventure_xy_step_state verified=%c enabled=%c active=%c blocks_a=%u"
                " blocks_b=%u steps_a=%u steps_b=%u aborted=%u errors=%u peak_late=%u")
    assert response in dictionary['responses']
    transport = destination/'R26_TRANSPORT_BUILD.json'
    report = json.loads(transport.read_text())
    report['motion_sources_unchanged'] = False
    transport.write_text(json.dumps(report, indent=2)+'\n')
    (destination/'R28_XY_STEP_BUILD.json').write_text(json.dumps(dict(
        mcu_release=RELEASE, step_timer_abi=1, pulse_ticks=480, minimum_period_ticks=1200,
        m300_source='d1616a9e7007b44dda2a737cc0297b7f66a77b63',
        pa7='TIM3_CH2, TIM2 finite gate', pa6='TIM1, DMA2 Streams 5/6',
        oled='SPI2, DMA1 Stream4', hardware_tested=False), indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    build(args.kit.resolve(), args.destination.resolve())
