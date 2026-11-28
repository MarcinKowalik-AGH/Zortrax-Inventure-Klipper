"""Run R30's linked Thumb-2 STEP path with a modeled 60-tick DMA variation."""
import json
from itertools import pairwise

from check_inventure_xy_step_arm_r28 import arm_tool
from check_inventure_xy_step_arm_r29 import R29Machine, GPIOE, TIM13, TIM14, symbols


class R30Machine(R29Machine):
    dma_delays = (6, 66)

    def verify_fast(self, pin, expected):
        self.verify(pin, expected, min_width=90)
        waveform = self.trace[pin]
        for (rise, _), (fall, _) in zip(waveform[::2], waveform[1::2]):
            assert fall - rise == 294, (pin, rise, fall)


def replay(a_blocks, b_blocks, *, wrap=False, sampler=False):
    machine = R30Machine(0xffffc000 if wrap else 50000)
    first = machine.t + 16000
    motors = []
    for axis, blocks in enumerate((a_blocks, b_blocks), 1):
        if blocks:
            clock = first + (axis - 1) * 212
            address = 0x20008000 + (axis - 1) * 0x1000
            motors.append((machine.stepper(address, axis, clock & 0xffffffff, blocks),
                           8 - axis, clock, blocks))
    machine.schedule(*(motor[0] for motor in motors))
    stalls = machine.run_with_sampler_stalls() if sampler else 0
    if not sampler:
        machine.run()
    results = {}
    for axis, pin in enumerate((7, 6)):
        output = next((motor for motor in motors if motor[1] == pin), None)
        if output is None:
            machine.verify_fast(pin, 0)
            continue
        _, _, clock, blocks = output
        pulses = sum(block[2] for block in blocks)
        machine.verify_fast(pin, pulses)
        edges = machine.trace[pin]
        rises = [time for time, _ in edges[::2]]
        lows = [rise - fall for (fall, _), (rise, _) in zip(edges[1::2], edges[2::2])]
        planned = clock - blocks[0][0]
        lags = []
        for period, add, count, _ in blocks:
            for step in range(count):
                planned += period + step * add
                lags.append(rises[len(lags)] - planned)
        assert min(lags) >= 0 and max(lags) < (6000 if sampler else 2400), (pin, min(lags), max(lags))
        counters = symbols['xy_trains'] + axis * 16
        hw_steps = machine.read(counters + 4)
        asm_steps = machine.read(counters + 8)
        assert hw_steps + asm_steps == pulses
        results[f'PA{pin}'] = dict(pulses=pulses, hardware_steps=hw_steps, thumb2_steps=asm_steps,
                                   minimum_low_ticks=min(lows), peak_lag_ticks=max(lags))
    assert not machine.read(symbols['xy_step_active'], 1)
    assert not machine.read(symbols['xy_one_active'], 1)
    assert not machine.read(TIM13) and not machine.read(TIM14)
    return machine, results, stalls


def check_homing_and_abort():
    machine = R30Machine()
    a = machine.stepper(0x20008000, 1, machine.t+12000, [(37500, 0, 44, 0)])
    b = machine.stepper(0x20009000, 2, machine.t+12500, [(4500, 0, 51, 0)], trigger=True)
    machine.schedule(a, b)
    machine.run()
    machine.verify(7, 44)
    machine.verify(6, 51)
    assert not machine.read(symbols['xy_trains']) and not machine.read(symbols['xy_one_active'], 1)

    machine = R30Machine()
    a = machine.stepper(0x20008000, 1, machine.t+12000, [(1000, -1, 75, 0)])
    b = machine.stepper(0x20009000, 2, machine.t+13030, [(1000, 0, 90, 0)])
    machine.schedule(a, b)
    machine.run(max_events=5)
    assert machine.read(symbols['xy_one_active'], 1) == 1
    assert machine.read(symbols['xy_step_active'], 1) == 2
    try:
        machine.call('inventure_filter_gpio', GPIOE, 1 << 10, 0)
    except RuntimeError as error:
        assert 'Real linked firmware shutdown' in str(error)
    else:
        raise AssertionError('An enabled XY motor survived the interlock')
    assert not machine.read(GPIOE+0x14) & (1 << 10)
    for stepper, pin in ((a, 7), (b, 6)):
        emitted = sum(level for _, level in machine.trace[pin])
        machine.call('stepper_stop', stepper+60, 0)
        assert machine.read(stepper+48) == 0xc0000000 + emitted
    waveform = {pin: list(trace) for pin, trace in machine.trace.items()}
    machine.advance(300000)
    assert waveform == machine.trace


def check():
    disassembly = arm_tool('objdump', '-d', '--disassemble=stepper_xy_one_step.lto_priv.0')
    for instruction in ('bmi', 'subs', 'bgt', 'tst', 'bne', 'str', 'ldr'):
        assert instruction in disassembly, (instruction, disassembly)
    cases = {}
    for pin in (7, 6):
        plateau = [(448, 0, 32, 0)] * 200
        machine, metrics, _ = replay(plateau if pin == 7 else (), plateau if pin == 6 else (),
                                     wrap=True)
        rises = [clock for clock, _ in machine.trace[pin][::2]]
        # The queue can rearm between trains. Check the first united 160-pulse block.
        steps = {right - left for left, right in pairwise(rises[:160])}
        assert steps == ({448} if pin == 7 else {388, 508}), (pin, steps)
        metrics[f'PA{pin}']['sustained_low_ticks'] = min(steps) - 294
        cases[f'diagonal_pa{pin}'] = metrics
    a_changes = [(448 + (i % 2), 0, 32, 0) for i in range(192)]
    b_changes = [(448 + 2 * (i % 2), 0, 32, 0) for i in range(192)]
    cases['changing_pa7'] = replay(a_changes, ())[1]
    cases['changing_pa6'] = replay((), b_changes)[1]
    short = [(448 + (i % 2), 0, 1 + i % 4, 0) for i in range(200)]
    cases['short_fragments'] = replay(short, ())[1]
    dual = [(633, 0, 2550, 0)]
    _, cases['both_motors_with_sampler'], stalls = replay(dual, dual, sampler=True)
    plateau = [(634, 0, 2200, 0)]
    cases['both_motors_hardware'] = replay(plateau, plateau)[1]
    ramps = [(2300, -4, 463, 0), (450, 0, 19, 0), (450, 4, 350, 1)]
    cases['ramp_and_direction_change'] = replay(ramps, [(3000, 0, 850, 0)])[1]
    assert cases['short_fragments']['PA7']['thumb2_steps'] > 0
    assert cases['changing_pa6']['PA6']['thumb2_steps'] > 0
    assert stalls > 0
    check_homing_and_abort()
    return dict(actual_arm=True, fast_high_ticks=294, required_low_floor_ticks=90,
                modeled_dma_variation_ticks=60, cases=cases, simulated_sampler_stalls=stalls,
                preserves_homing_pulse=True, aborts_both_motion_modes=True, physical_printer=False)


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))
