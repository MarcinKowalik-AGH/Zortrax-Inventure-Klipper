"""Execute the linked R28 IRQ and STEP code against a finite register model."""
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP, UC_ARM_REG_LR,
)

build = Path(sys.argv[1])
tools = Path(sys.argv[2])
elf = build / "klipper.elf"
def arm_tool(name, *args):
    return subprocess.check_output([tools / ("arm-none-eabi-" + name), *args, elf], text=True)

symbols = {
    name.split(".lto_priv.")[0]: int(address, 16)
    for row in arm_tool("nm", "--defined-only").splitlines()
    if (items := row.split()) and len(items) == 3
    for address, _, name in [items]
}
data_section = re.search(r"^\s*\d+\s+\.data\s+([0-9a-f]+)\s+([0-9a-f]+)\s+[0-9a-f]+\s+([0-9a-f]+)",
                         arm_tool("objdump", "-h"), re.M)
data_size, data_start, data_offset = [int(n, 16) for n in data_section.groups()]
initial_data = elf.read_bytes()[data_offset:data_offset+data_size]
GPIOA, GPIOB, GPIOE = 0x40020000, 0x40020400, 0x40021000
TIM1, TIM2, TIM3 = 0x40010000, 0x40000000, 0x40000400
DMA = 0x40026400
S5, S6 = DMA + 0x88, DMA + 0xa0
DWT = 0xe0001004
RET = 0x2001e000


class Machine:
    def __init__(self, t=50000):
        self.t = t
        self.trace = {6: [], 7: []}
        self.direction = []
        self.levels = {6: 0, 7: 0}
        self.phase = 0
        self.pending = []
        self.bus = []
        self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        for origin, size in [(0x08000000, 0x80000), (0x20000000, 0x20000),
                             (0x40000000, 0x30000), (0xe0000000, 0x100000)]:
            self.u.mem_map(origin, size)
        self.u.mem_write(0x08010000, (build/'klipper.bin').read_bytes())
        self.u.mem_write(data_start, initial_data)
        self.u.hook_add(UC_HOOK_MEM_WRITE, self.write_hook, begin=0x40000000, end=0x4002ffff)
        self.u.hook_add(UC_HOOK_CODE, self.code)
        self.write(GPIOA, (1 << 12) | (1 << 14))
        self.call("inventure_xy_step_init")
        assert self.read(symbols['xy_step_verified'], 1) == 3, "Boot peripheral self-test"
        assert not any(self.trace.values()), ("Hardware self-test drove a STEP pin", self.trace)
        self.call("command_inventure_xy_step_config")
        self.write(symbols['control_mode'], 1, 1)
        self.gpio(GPIOE, 1 << 10)

    def read(self, address, size=4):
        return int.from_bytes(self.u.mem_read(address, size), 'little')

    def write(self, address, value, size=4):
        self.u.mem_write(address, (value % (1 << (8 * size))).to_bytes(size, 'little'))

    def gpio(self, port, bsrr):
        old = self.read(port+0x14)
        odr = (old | (bsrr & 0xffff)) & ~(bsrr >> 16)
        self.write(port+0x14, odr)
        self.write(port+0x10, odr)
        if port == GPIOA:
            self.pins()
        if port == GPIOB and (old ^ odr) & 6:
            self.direction.append((self.t, odr & 6))

    def pins(self):
        mode, odr = self.read(GPIOA), self.read(GPIOA+0x14)
        ids = self.read(GPIOA+0x10)
        for pin in (6, 7):
            function = (mode >> (pin * 2)) & 3
            level = ((odr >> pin) & 1) if function == 1 else 0
            if pin == 7 and function == 2:
                level = int(bool(self.read(TIM3+0x20) & 16)
                            and self.read(TIM3+0x24) < self.read(TIM3+0x38))
            ids = (ids & ~(1 << pin)) | level << pin
            if self.levels[pin] != level:
                self.trace[pin].append((self.t, level))
                self.levels[pin] = level
        self.write(GPIOA+0x10, ids)

    def write_hook(self, uc, access, address, size, value, data):
        self.pending.append((address, value, size))

    def commit_writes(self):
        for address, value, size in self.pending:
            if address in (GPIOA+0x18, GPIOB+0x18, GPIOE+0x18):
                self.gpio(address-0x18, value)
            elif address in (GPIOA, TIM3+0x20, TIM3+0x24):
                self.pins()
            elif address in (TIM1+0x14, TIM2+0x14, TIM3+0x14) and value & 1:
                self.write(address-0x14+0x24, 0)
                if address == TIM3+0x14:
                    self.phase = 0
            elif address == DMA+0x0c:
                self.write(DMA+0x04, self.read(DMA+0x04) & ~value)
            elif address in (S5, S6) and not value & 1:
                self.bus = [event for event in self.bus if event[1] != address]
        self.pending.clear()

    def code(self, uc, address, size, data):
        if address == RET:
            uc.emu_stop()
            return
        if address == symbols['sched_shutdown']:
            raise RuntimeError("Real linked firmware shutdown %d at clock %d"
                               % (uc.reg_read(UC_ARM_REG_R0), self.t))
        self.commit_writes()
        if not (self.read(TIM1) & 1 or self.read(TIM3) & 1 or self.bus):
            self.t += 2
        else:
            self.advance(2)
        self.write(DWT, self.t)

    def do_dma(self, stream):
        count, control = self.read(stream+4), self.read(stream)
        if count and control & 1:
            assert (control >> 25) & 7 == 6
            assert self.read(stream+8) == GPIOA+0x18
            self.gpio(GPIOA, self.read(self.read(stream+12)))
            self.write(stream+4, count-1)
            if count == 1:
                self.write(stream, control & ~1)

    def advance(self, count):
        while count:
            tim1 = bool(self.read(TIM1) & 1)
            tim3 = bool(self.read(TIM3) & 1 and self.read(TIM2) & 1
                        and self.read(TIM2+0x24) < self.read(TIM2+0x34))
            events = []
            cnt1, cnt3 = self.read(TIM1+0x24), self.read(TIM3+0x24)
            if tim1:
                arr1 = self.read(TIM1+0x2c)+1
                events.append((arr1-cnt1, 'tim1-update'))
                compare = self.read(TIM1+0x3c)
                if cnt1 < compare:
                    events.append((compare-cnt1, 'tim1-compare'))
            if tim3:
                arr3 = self.read(TIM3+0x2c)+1
                events.append((2*(arr3-cnt3)-self.phase, 'tim3-update'))
                compare = self.read(TIM3+0x34)
                if cnt3 < compare:
                    events.append((2*(compare-cnt3)-self.phase, 'tim3-compare'))
            if self.bus:
                events.append((min(self.bus)[0]-self.t, 'dma'))
            elapsed = min([count] + [event[0] for event in events])
            self.t += elapsed
            count -= elapsed
            if tim1:
                self.write(TIM1+0x24, (cnt1+elapsed) % arr1)
            if tim3:
                ticks, self.phase = divmod(self.phase+elapsed, 2)
                self.write(TIM3+0x24, (cnt3+ticks) % arr3)
                self.pins()
            for offset, event in events:
                if offset != elapsed:
                    continue
                if event == 'tim1-update' and self.read(TIM1+0x0c) & (1 << 8):
                    self.bus.append((self.t + 40, S5))
                elif event == 'tim1-compare' and self.read(TIM1+0x0c) & (1 << 11):
                    self.bus.append((self.t + 4, S6))
                elif event == 'tim3-compare':
                    assert self.read(TIM2+0x08) == 0x27 and self.read(TIM3+0x08) == 0x15
                    self.write(TIM2+0x24, self.read(TIM2+0x24)+1)
                elif event == 'dma':
                    ready = [item for item in self.bus if item[0] == self.t]
                    self.bus = [item for item in self.bus if item[0] != self.t]
                    for _, stream in ready:
                        self.do_dma(stream)

    def call(self, symbol, arg0=0, arg1=0, arg2=0):
        self.u.reg_write(UC_ARM_REG_R0, arg0)
        self.u.reg_write(UC_ARM_REG_R1, arg1)
        self.u.reg_write(UC_ARM_REG_R2, arg2)
        self.u.reg_write(UC_ARM_REG_SP, 0x2001df00)
        self.u.reg_write(UC_ARM_REG_LR, RET|1)
        self.u.emu_start(symbols[symbol]|1, RET, count=500000)
        self.commit_writes()
        return self.u.reg_read(UC_ARM_REG_R0)

    def stepper(self, s, axis, first, blocks, trigger=False):
        pin, direction = (7, 2) if axis == 1 else (6, 1)
        # Native structs for the pinned ARM build (src/stepper.c DWARF).
        stepper = bytearray(80)
        struct.pack_into('<IIIIIII', stepper, 24, first-blocks[0][0], 480,
                         GPIOA, 1 << pin, GPIOB, 1 << direction, 0xc0000000)
        stepper[69] = axis
        if trigger:
            struct.pack_into('<I', stepper, 64, 1)
        for i, (period, add, count, flags) in enumerate(blocks):
            node = s+0x100+i*16
            self.u.mem_write(node, struct.pack('<IIhHBxxx', node+16 if i+1<len(blocks) else 0,
                                               period, add, count, flags))
        struct.pack_into('<II', stepper, 52, s+0x100, s+0x100+(len(blocks)-1)*16)
        self.u.mem_write(s, bytes(stepper))
        self.call("stepper_load_next", s)
        return s

    def schedule(self, *steppers):
        periodic = symbols['periodic_timer']
        self.write(periodic+8, self.t + 13_000_000)
        self.write(symbols['sentinel_timer']+8, self.t + 13_000_000 + 0x80000000)
        ordered = sorted(steppers, key=lambda s: (self.read(s+8)-self.t) & 0xffffffff)
        for before, after in zip(ordered, ordered[1:] + [periodic]):
            self.write(before, after)
        sched = symbols['SchedStatus']
        self.write(sched, ordered[0])
        self.write(sched+4, periodic)

    def run(self, max_events=None):
        scheduler = symbols['SchedStatus']
        events = 0
        while self.read(scheduler) != symbols['periodic_timer']:
            wake = self.read(self.read(scheduler)+8)
            delta = ((wake - self.t + 0x80000000) & 0xffffffff) - 0x80000000
            if delta > 0:
                self.advance(delta)
            self.call("SysTick_Handler")
            events += 1
            assert events < 20000
            if events == max_events:
                return events
        return events

    def verify(self, pin, expected, min_width=480):
        trace = self.trace[pin]
        assert len(trace) == 2*expected, (pin, expected, len(trace), trace[-8:])
        for (time_a, first), (time_b, second) in zip(trace, trace[1:]):
            assert first != second and time_b-time_a >= min_width, (pin, time_a, time_b)
        assert not self.levels[pin]


def main():
    counts = []
    for i, (a_period, b_period) in enumerate(((1200,1200), (1341,1440), (4419,4760), (9600,1201))):
        m = Machine(t=0xffffc000 if i == 3 else 50000)
        first = (m.t+12000) & 0xffffffff
        a = m.stepper(0x20008000, 1, first, [(a_period,0,129,0), (a_period,0,64,1)])
        b = m.stepper(0x20009000, 2, first+800, [(b_period,0,153,0)])
        m.schedule(a, b)
        callbacks = m.run()
        m.verify(7, 193)
        m.verify(6, 153)
        assert len(m.direction) == 1 and m.direction[0][1] == 4
        dir_clock = m.direction[0][0]
        assert m.trace[7][257][0] < dir_clock < m.trace[7][258][0]
        assert m.trace[7][258][0] - dir_clock >= 480
        assert not m.read(symbols['xy_step_active'], 1)
        counts.append(callbacks)
    # The two tested homing/short/variable-interval paths stay on GPIO.
    m = Machine()
    a = m.stepper(0x20008000, 1, m.t+12000,
                  [(37500,0,46,0), (4500,7,55,0), (4500,0,20,0)])
    m.schedule(a)
    m.run()
    m.verify(7, 121)
    assert m.read(symbols['xy_trains']) == 0
    m = Machine()
    a = m.stepper(0x20008000, 1, m.t+12000, [(4500,0,46,0)], trigger=True)
    m.schedule(a)
    m.run()
    m.verify(7, 46)
    assert not m.trace[6] and m.read(symbols['xy_trains']) == 0
    # Interrupt both active trains mid-block. The physical enable is cut,
    # each counter preserves its unissued steps, and no new rising edge follows.
    m = Machine()
    a = m.stepper(0x20008000, 1, m.t+12000, [(4419,0,64,0)])
    b = m.stepper(0x20009000, 2, m.t+12900, [(5000,0,90,0)])
    m.schedule(a,b)
    m.run(max_events=2)
    m.advance(42000)
    assert m.read(symbols['xy_step_active'], 1) == 3
    try:
        m.call("inventure_filter_gpio", GPIOE, 1 << 10, 0)
    except RuntimeError as error:
        assert "Real linked firmware shutdown" in str(error)
    else:
        raise AssertionError("A live STEP train continued after motor disable")
    assert not m.read(GPIOE+0x14) & (1 << 10)
    snapshot = {pin: trace.copy() for pin, trace in m.trace.items()}
    for axis, pin, total in ((1,7,64),(2,6,90)):
        emitted = sum(level for _, level in m.trace[pin])
        observed = m.call("inventure_xy_step_stop", axis)
        assert total-emitted == observed, (pin, emitted, observed)
        stepper = a if axis == 1 else b
        m.call("stepper_stop", stepper+60, 0)
        assert m.read(stepper+48) == 0xc0000000+emitted
        assert not m.read(stepper+20)
    m.advance(1_200_000)
    assert m.trace == snapshot
    print(json.dumps(dict(actual_arm=True, timer_gpio_model=True, concurrent_cases=4,
                          scheduler_irq_calls=counts, directions_and_rounding=True,
                          wraparound=True, fallback_steps=167, partial_stop='both motors',
                          physical_printer=False), indent=2))


if __name__ == '__main__':
    main()
