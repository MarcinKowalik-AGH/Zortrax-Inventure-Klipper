"""Build and check the R31 cassette phase protocol and its R30 STEP ABI."""
import argparse
import json
from pathlib import Path

import build_inventure_xy_step_r30 as predecessor
from apply_inventure_feeder_windows_r31 import RELEASE, overlay


def build(kit, destination):
    previous_release, previous_overlay = predecessor.RELEASE, predecessor.overlay
    try:
        predecessor.RELEASE, predecessor.overlay = RELEASE, overlay
        predecessor.build(kit, destination)
    finally:
        predecessor.RELEASE, predecessor.overlay = previous_release, previous_overlay
    dictionary = json.loads((destination/'klipper.dict').read_text())
    if dictionary['config'].get('INVENTURE_FACTORY_FEED_WINDOW_ABI') != 1:
        raise ValueError('Missing R31 cassette feed phase ABI')
    commands = {
        'inventure_factory_feed_start owner=%u run=%u condition=%c timeout_ms=%hu',
        'inventure_factory_feed_keepalive owner=%u run=%u',
    }
    if not commands <= dictionary['commands'].keys():
        raise ValueError('Missing cassette phase commands')
    response = 'inventure_factory_feed_state owner=%u run=%u state=%c a=%c b=%c'
    if response not in dictionary['responses']:
        raise ValueError('Compiled cassette status does not match the host')
    (destination/'R31_CASSETTE_WINDOWS_BUILD.json').write_text(json.dumps(dict(
        mcu_release=RELEASE, cassette_feed_abi=1, phases=['L2', 'U0', 'U3'],
        paired_step_abi=3, host_activity_lease_ms=1500, hardware_tested=False), indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    parser.add_argument('destination', type=Path)
    arguments = parser.parse_args()
    build(arguments.kit.resolve(), arguments.destination.resolve())
