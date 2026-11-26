"""Build the R25 Info MCU and prove the Flash worker executes in SRAM."""
import argparse
import json
from pathlib import Path
import subprocess
from apply_inventure_information_r25 import RELEASE, overlay
import build_inventure_home_r24 as parent


def build(kit, destination):
    parent.RELEASE, parent.overlay = RELEASE, overlay
    parent.build(kit, destination)
    dictionary = json.loads((destination/'klipper.dict').read_text())
    constants = dictionary['config']
    for key in ('INVENTURE_FACTORY_INFO_ABI', 'INVENTURE_FACTORY_COUNTER_ABI', 'INVENTURE_NATIVE_HELD_SERVICE_ABI'):
        if constants.get(key) != 1:
            raise ValueError('Missing compiled ABI: '+key)
    # The ELF is an ordinary existing builder output; no symbols are invented.
    elf = next(destination.rglob('*.elf'))
    symbols = subprocess.check_output(['arm-none-eabi-nm', '-n', str(elf)], text=True)
    worker = [line for line in symbols.splitlines() if line.split()[-1].startswith('info_flash_hw')]
    if len(worker) != 1 or not 0x20000000 <= int(worker[0].split()[0],16) < 0x20020000:
        raise ValueError('Flash worker is not in executable SRAM')
    (destination/'R25_INFO_BUILD.json').write_text(json.dumps(dict(
        status='PASS_R25_INFO_ARM_BUILD', mcu_release=RELEASE, bootloader='InventureBoot 1.7',
        flash_worker=worker[0], factory_info_abi=1, factory_counter_abi=1,
        oem_banks=['0x08008000', '0x0800C000'], otp_read_only=True,
        hardware_tested=False), indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    build(args.kit.resolve(), args.destination.resolve())
