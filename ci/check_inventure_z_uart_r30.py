"""Compare linked R30 Z timers with and without the priority-0 UART window."""
import argparse
from functools import partial
import json
from pathlib import Path
import struct

from check_inventure_uart_window_r27 import Image, NOW, inspect


def seed_z(uc, symbols, put, word, *, edge, doors):
    # ARM layout of struct zh_state as emitted by the pinned GNU compiler.
    address, size = symbols['zh']
    assert size == 80
    state = bytearray(size)
    struct.pack_into('<IIII', state, 8, 21, 22, NOW-100, 480000)
    struct.pack_into('<5H2h', state, 48, 8020, 4000, 802, 0, 250, 190, 208)
    state[62] = 101
    state[64] = 1  # ZH_SEEK
    state[66] = state[68] = state[72] = 1
    state[67] = edge == 'fall'
    uc.mem_write(address, bytes(state))
    put('native_z_guard_active', 1)
    put('native_z_guard_owner', 21)
    put('native_z_refine_held', 1)
    put('door_policy_flags', 0 if doors else 0x08)
    uc.mem_write(symbols['latest_adc'][0], struct.pack('<HH', 1241, 185))
    word(0x40021014, 1 << 10)  # PE10 ODR, motors on
    word(0x40020810, 1 << 14)  # PC14 IDR, contact open
    word(0x40021010, 1 << 15)  # PE15 IDR, door closed
    word(0x40020410, 1 << 10)  # PB10 IDR, lid closed
    return 'zh_event'


def compare(baseline, candidate, nm, instruction_cycles):
    before, after = Image(baseline, nm), Image(candidate, nm)
    rows = []
    for edge in ('rise', 'fall'):
        for doors in (False, True):
            fixture = partial(seed_z, edge=edge, doors=doors)
            for ceiling in (0, 32):
                for phase in range(0, 1600, 32):
                    options = dict(fixture=fixture, instruction_cycles=instruction_cycles)
                    old = inspect(before, False, False, phase, ceiling, **options)
                    new = inspect(after, False, False, phase, ceiling, **options)
                    assert old['logic'] == new['logic'], (old, new)
                    assert new['received'] == [65, 66], new
                    assert new['preemptible_instructions'] > 0, new
                    assert not new['unprotected_timer_instructions'], new
                    rows.append(dict(edge=edge, doors=doors, before=old, after=new))
    def counts(key):
        return dict(overruns=sum(r[key]['overruns'] for r in rows),
                    bytes_delivered=sum(len(r[key]['received']) for r in rows),
                    worst_callback_model_cycles=max(r[key]['guard_cycles'] for r in rows),
                    max_uart_blocked_instructions=max(r[key]['max_uart_blocked_instructions'] for r in rows),
                    shortest_preemptible_window=min(r[key]['preemptible_instructions'] for r in rows))
    return dict(cases=len(rows), before=counts('before'), after=counts('after'),
                cycles_per_useful_instruction=instruction_cycles,
                timing_model='uniform instruction cost, no physical latency claim',
                unchanged_z_state_and_deadlines=True, hardware_tested=False, rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--nm', default='arm-none-eabi-nm')
    parser.add_argument('--instruction-cycles', type=int, choices=(1, 2), default=1)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = compare(args.baseline, args.candidate, args.nm, args.instruction_cycles)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, indent=2))
