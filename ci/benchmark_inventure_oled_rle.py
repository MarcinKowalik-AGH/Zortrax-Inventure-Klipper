#!/usr/bin/env python3
# Marcin Kowalik <mkowalik@agh.edu.pl>
# benchmark_oled_rle.py - Porównuje rozmiar danych i koszt kodowania ramek OLED metodą RLE.
"""Check Thumb-2 RLE fill equivalence and count emulated instructions."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0,
                               UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP)


CODE = 0x8000
STOP = 0x9000
RAM = 0x20000000
BUFFER = RAM + 4
SENTINEL = b'\x31' * 144
HEADER_DIR = (Path(__file__).resolve().parents[1]
              / 'service_mcu/control_r6/SOURCE_MCU')


def run():
    with tempfile.TemporaryDirectory(prefix='m300-oled-rle-') as temp:
        work = Path(temp)
        (work / 'fill.c').write_text('''
#include "inventure_oled_rle.h"
void reference(uint8_t *dest, uint8_t value, unsigned length)
{
    memset(dest, value, length);
}
void candidate(uint8_t *dest, uint8_t value, unsigned length)
{
    oled_fill_run(dest, value, length);
}
''')
        subprocess.run(['arm-none-eabi-gcc', '-mcpu=cortex-m3', '-mthumb', '-Os',
                        '-nostdlib', f'-I{HEADER_DIR}', '-Wl,-Ttext=0x8000,-e,reference',
                        'fill.c', '-lc_nano', '-o', 'fill.elf'], cwd=work, check=True)
        symbols = dict((name, int(address, 16)) for address, _, name in
                       (line.split() for line in subprocess.check_output(
                           ['arm-none-eabi-nm', 'fill.elf'], cwd=work, text=True).splitlines())
                       if name in ('reference', 'candidate'))
        subprocess.run(['arm-none-eabi-objcopy', '-j', '.text', '-O', 'binary',
                        'fill.elf', 'fill.bin'], cwd=work, check=True)
        image = (work / 'fill.bin').read_bytes()

    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(CODE, 0x2000)
    cpu.mem_write(CODE, image)
    cpu.mem_map(RAM, 0x1000)
    instructions = 0

    def count_instruction(cpu, address, size, user_data):
        nonlocal instructions
        instructions += 1

    cpu.hook_add(UC_HOOK_CODE, count_instruction)

    def fill(name, length, offset, value):
        nonlocal instructions
        instructions = 0
        cpu.mem_write(RAM, SENTINEL)
        for register, data in ((UC_ARM_REG_R0, BUFFER + offset), (UC_ARM_REG_R1, value),
                               (UC_ARM_REG_R2, length), (UC_ARM_REG_SP, RAM + 0xff0),
                               (UC_ARM_REG_LR, STOP | 1)):
            cpu.reg_write(register, data)
        cpu.emu_start(symbols[name] | 1, STOP, count=1024)
        index = BUFFER - RAM + offset
        expected = SENTINEL[:index] + bytes([value]) * length + SENTINEL[index + length:]
        if cpu.reg_read(UC_ARM_REG_PC) != STOP or cpu.mem_read(RAM, len(expected)) != expected:
            raise AssertionError(f'{name} failed at length {length}, offset {offset}, byte {value}')
        return instructions

    checked = 0
    for length in range(1, 129):
        for offset in range(4):
            for value in (0x00, 0x55, 0x80, 0xff):
                fill('candidate', length, offset, value)
                checked += 1
    rows = []
    for length in (4, 8, 12, 16, 32, 64, 96, 128):
        for offset in range(4):
            reference = fill('reference', length, offset, 0x55)
            candidate = fill('candidate', length, offset, 0x55)
            rows.append({'length': length, 'offset': offset, 'c_instructions': reference,
                         'asm_instructions': candidate})
    return {'status': 'PASS', 'checked_fills': checked, 'hardware_cycles_measured': False,
            'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = run()
    regressions = [row for row in result['rows']
                   if row['length'] >= 32 and row['asm_instructions'] >= row['c_instructions']]
    result['performance_passed'] = not regressions
    report = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(report + '\n')
    print(report)
    if regressions:
        raise AssertionError(f'No instruction reduction: {regressions}')


if __name__ == '__main__':
    main()
