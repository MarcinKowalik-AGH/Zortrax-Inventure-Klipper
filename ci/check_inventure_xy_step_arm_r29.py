"""Replay R29's linked ARM scheduler using the F207 timer and DMA register model."""
import json

from check_inventure_xy_step_arm_r28 import Machine, GPIOA, GPIOE, TIM2, TIM3, DWT, RET, symbols

DMA1 = 0x40026000
STREAM1 = DMA1 + 0x28
TIM6, TIM13, TIM14 = 0x40001000, 0x40001c00, 0x40002000


class R29Machine(Machine):
    dma_delays = (8, 58)

    def __init__(self, clock=50000):
        self.timer_phases = dict.fromkeys((TIM3, TIM6, TIM13, TIM14), 0)
        self.requests = 0
        super().__init__(t=clock)

    def pins(self):
        mode, odr, afr = self.read(GPIOA), self.read(GPIOA+0x14), self.read(GPIOA+0x20)
        ids = self.read(GPIOA+0x10)
        for pin in (6, 7):
            function = (mode >> (pin * 2)) & 3
            alternate = (afr >> (pin * 4)) & 15
            level = ((odr >> pin) & 1) if function == 1 else 0
            if pin == 7 and function == 2 and alternate == 2:
                level = int(bool(self.read(TIM3+0x20) & 16)
                            and self.read(TIM3+0x24) < self.read(TIM3+0x38))
            elif function == 2 and alternate == 9:
                timer = TIM13 if pin == 6 else TIM14
                level = int(bool(self.read(timer+0x20) & 1)
                            and self.read(timer+0x24) >= self.read(timer+0x34))
            ids = (ids & ~(1 << pin)) | level << pin
            if self.levels[pin] != level:
                self.trace[pin].append((self.t, level))
                self.levels[pin] = level
        self.write(GPIOA+0x10, ids)

    def commit_writes(self):
        for address, value, _ in self.pending:
            if address in (GPIOA+0x18, GPIOA+0x418, GPIOE+0x18):
                self.gpio(address-0x18, value)
            elif address in (GPIOA, GPIOA+0x20, TIM3+0x20, TIM3+0x24,
                             TIM13+0x20, TIM14+0x20):
                self.pins()
            elif address-0x14 in (TIM2, *self.timer_phases) and value & 1:
                timer = address-0x14
                self.write(timer+0x24, 0)
                if timer in self.timer_phases:
                    self.timer_phases[timer] = 0
            elif address == DMA1+0x08:
                self.write(DMA1, self.read(DMA1) & ~value)
            elif address == STREAM1 and not value & 1:
                self.bus.clear()
        self.pending.clear()

    def code(self, uc, address, size, data):
        if address == RET:
            uc.emu_stop()
            return
        if address == symbols['sched_shutdown']:
            super().code(uc, address, size, data)
        self.commit_writes()
        if self.read(TIM3) & 1 or self.read(TIM6) & 1 or self.read(TIM13) & 1 or self.read(TIM14) & 1 or self.bus:
            self.advance(2)
        else:
            self.t += 2
        self.write(DWT, self.t)

    def advance(self, count):
        while count:
            events, running = [], {}
            for timer, phase in self.timer_phases.items():
                if not self.read(timer) & 1:
                    continue
                if timer == TIM3 and (not self.read(TIM2) & 1
                                      or self.read(TIM2+0x24) >= self.read(TIM2+0x34)):
                    continue
                cnt, top = self.read(timer+0x24), self.read(timer+0x2c)+1
                running[timer] = (cnt, top)
                events.append((2*(top-cnt)-phase, timer, 'update'))
                if timer != TIM6:
                    compare = self.read(timer+0x34)
                    if cnt < compare:
                        events.append((2*(compare-cnt)-phase, timer, 'compare'))
            if self.bus:
                events.append((self.bus[0]-self.t, STREAM1, 'dma'))
            elapsed = min([count] + [event[0] for event in events])
            count -= elapsed
            self.t += elapsed
            for timer, (cnt, top) in running.items():
                ticks, self.timer_phases[timer] = divmod(self.timer_phases[timer]+elapsed, 2)
                self.write(timer+0x24, (cnt+ticks) % top)
            for offset, timer, event in events:
                if offset != elapsed:
                    continue
                if event == 'dma':
                    self.bus.pop(0)
                    left = self.read(STREAM1+4)
                    control = self.read(STREAM1)
                    assert (control>>25)&7 == 7 and self.read(STREAM1+8) == TIM13
                    assert left and control & 1 and not (self.read(TIM13) & 1)
                    self.write(TIM13, self.read(self.read(STREAM1+12)))
                    self.timer_phases[TIM13] = 0
                    self.write(STREAM1+4, left-1)
                    if left == 1:
                        self.write(STREAM1, control & ~1)
                elif timer == TIM6 and self.read(TIM6+0x0c) & (1 << 8) and self.read(STREAM1) & 1:
                    # Alternate early/late arbitration while SPI2 retains its
                    # own DMA1 stream. These delays are modeled, not measured.
                    self.bus.append(self.t + self.dma_delays[self.requests % 2])
                    self.requests += 1
                elif timer == TIM3 and event == 'compare':
                    self.write(TIM2+0x24, self.read(TIM2+0x24)+1)
                elif timer in (TIM13, TIM14) and event == 'update':
                    self.write(timer, self.read(timer) & ~1)
            if any(t in running for t in (TIM3, TIM13, TIM14)):
                self.pins()

    def verify_fast(self, pin, expected):
        self.verify(pin, expected, min_width=294)
        waveform = self.trace[pin]
        for (rise, _), (fall, _) in zip(waveform[::2], waveform[1::2]):
            assert fall-rise == 294, (pin, rise, fall)

    def run_with_sampler_stalls(self):
        end_list = symbols['periodic_timer']
        next_sample = self.t + 12000
        stalls = 0
        while self.read(symbols['SchedStatus']) != end_list:
            self.run(max_events=1)
            if self.t >= next_sample:
                # A 20 us CPU-only stall approximates the guard sampler's
                # long path. Timers and DMA keep moving while CPU is busy.
                self.advance(2400)
                next_sample += 12000
                stalls += 1
        return stalls


def check():
    scenarios = [
        # Sustained OEM 128 kHz per motor, at 200 mm/s on one Cartesian axis.
        ([(938, 0, 2048, 0), (937, 0, 151, 1)], [(937, 0, 2200, 0)]),
        # Worst electronic rate and half-tick rounding. This is an MCU
        # waveform test, above the OEM CoreXY motor feed limit.
        ([(661, 0, 191, 0)], [(660, 0, 503, 0)]),
        # Variable periods for independent ramps and very short blocks.
        ([(2400, -3, 450, 0), (1049, 0, 19, 0), (1049, 4, 382, 0)],
         [(2600, -4, 414, 0), (941, 0, 28, 0), (941, 4, 510, 0)]),
        ([(1639, -1, 900, 0), (738, 0, 686, 0), (738, 3, 500, 0)], []),
    ]
    pulse_counts = []
    for index, (a_blocks, b_blocks) in enumerate(scenarios):
        m = R29Machine(0xffffc000 if index == 1 else 50000)
        first = (m.t+16000) & 0xffffffff
        a = m.stepper(0x20008000, 1, first, a_blocks)
        steppers = [a]
        if b_blocks:
            steppers.append(m.stepper(0x20009000, 2, (first+212) & 0xffffffff, b_blocks))
        m.schedule(*steppers)
        m.run()
        total_a = sum(move[2] for move in a_blocks)
        total_b = sum(move[2] for move in b_blocks)
        m.verify_fast(7, total_a)
        m.verify_fast(6, total_b)
        pulse_counts.append((total_a, total_b))
        assert not m.read(symbols['xy_step_active'], 1)
        assert not m.read(symbols['xy_one_active'], 1)
        assert not m.read(TIM13) and not m.read(TIM14)
    m = R29Machine()
    a_blocks, b_blocks = scenarios[2]
    a = m.stepper(0x20008000, 1, m.t+12000, a_blocks)
    b = m.stepper(0x20009000, 2, m.t+12333, b_blocks)
    m.schedule(a,b)
    stalls = m.run_with_sampler_stalls()
    m.verify_fast(7,sum(move[2] for move in a_blocks))
    m.verify_fast(6,sum(move[2] for move in b_blocks))
    m = R29Machine()
    a = m.stepper(0x20008000, 1, m.t+12000, [(37500,0,44,0)])
    b = m.stepper(0x20009000, 2, m.t+12500, [(4500,0,51,0)], trigger=True)
    m.schedule(a,b)
    m.run()
    m.verify(7,44)
    m.verify(6,51)
    assert not m.read(symbols['xy_trains']) and not m.read(symbols['xy_one_active'],1)
    m = R29Machine()
    a = m.stepper(0x20008000, 1, m.t+12000, [(1000,-1,75,0)])
    b = m.stepper(0x20009000, 2, m.t+13030, [(1000,0,90,0)])
    m.schedule(a,b)
    m.run(max_events=5)
    assert m.read(symbols['xy_one_active'],1) == 1 and m.read(symbols['xy_step_active'],1) == 2
    try:
        m.call("inventure_filter_gpio", GPIOE, 1 << 10, 0)
    except RuntimeError as error:
        assert "Real linked firmware shutdown" in str(error)
    else:
        raise AssertionError("An enabled XY motor survived an interlock")
    assert not m.read(GPIOE+0x14) & (1 << 10)
    for stepper, pin in ((a,7),(b,6)):
        emitted = sum(level for _, level in m.trace[pin])
        m.call("stepper_stop", stepper+60, 0)
        assert m.read(stepper+48) == 0xc0000000+emitted
    before = {pin:list(trace) for pin,trace in m.trace.items()}
    m.advance(300_000)
    assert before == m.trace
    return dict(actual_arm=True, timer_dma_model=True, fast_high_ticks=294,
                fast_low_min_ticks=294, cases=len(scenarios), pulses=pulse_counts,
                simulated_sampler_stalls=stalls,
                preserves_homing_pulse=True, aborts_both_motion_modes=True,
                physical_printer=False)


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))
