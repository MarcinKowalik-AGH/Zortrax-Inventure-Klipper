# Inventure R28 RC1 | CoreXY hardware STEP

R28 ports the finite STEP trains from M300 Classic source
`d1616a9e7007b44dda2a737cc0297b7f66a77b63`. The starting point is Inventure
R27 RC1 `ea03a966b800623cd15ea22274dd85e30e7533a5`. The queue, direction,
trigger handling and intermediate position remain under Klipper control.

| Inventure resource | Use |
| --- | --- |
| PA7 / PB2, motor A | TIM3_CH2 PWM with TIM2 counting each fall and closing the gate |
| PA6 / PB1, motor B | TIM1 update and compare drive DMA2 streams 5 and 6 to GPIOA BSRR |
| SPI2 / DMA1 stream 4 | Existing interruptible OLED transport |
| TIM4, TIM5, TIM8, TIM9 | Existing transfer fan, rear fan and heater owners |
| SysTick, DWT, USART6 | Existing Klipper clock, scheduler and P4 UART |

The startup checks verify the timer gate and DMA routes with STEP disconnected
from the test signals and PE10 disabled. Both checks must pass before the
paired host can configure hardware STEP. The ordinary stepper scheduler runs
slow moves, small blocks, varying periods and trigger-armed moves. A finite
train uses at least 32 steps, a period of 10–80 µs and the verified PA6/PA7
wiring with a 4 µs configured step pulse. TIM3 runs at 60 MHz. An odd
120 MHz period on PA7 is re-anchored at least every 64 pulses, limiting
rounding within a block to 63 clock ticks. TIM1 uses 120 MHz with 0.5 µs
additional high time reserved for DMA bus latency.

The tested Home procedures, held-drive handoff, T0 preparation, PC14
sampling and OLED/Fluidd commands use the R27 runtime. R28 adds one
authenticated change to CoreXY kinematics on the host and explicitly
enables the matching MCU capability. The installer verifies all effective
configuration settings against the existing setup. R28 preserves the
current configured 30 mm/s and 200 mm/s². The new CoreXY planner check also
applies after runtime velocity overrides.

At 640 steps/mm, the motors see `abs(vx+vy)*640` and
`abs(vx-vy)*640` steps/s. A 300 mm/s 45° move needs about 271,529 steps/s
on one motor, or one STEP every 3.68 µs. The current pulse needs at least
4 µs high and 4 µs low. The new firmware's planner ceiling is 100,000
steps/s per motor, giving 110.49 mm/s on either 45° diagonal and
156.25 mm/s along X or Y. These are timing bounds for planning, with
physical safe speed and acceleration still subject to printer acceptance.

## Install the test candidate

1. Flash the R28 application `INVENTUR.IVB` through the existing
   InventureBoot 1.7 FAT32 SD update procedure. The file SHA-256 is
   `0e7bbad705d4f0c5974c7e9f235047c84a8718fb580f65f5324d9d338e13420b`.
2. With the printer idle and heaters off, use `FIRMWARE_RESTART` in Fluidd
   and wait for Ready. Install the paired host before starting Home or heat.

   ```bash
   cd ~/inventure-src &&
   git fetch origin codex/inventure-xy-step-timers &&
   git switch --detach FETCH_HEAD &&
   python3 install-new-host.py --repair-home-procedures --with-xy-step-timers \
     --serial /dev/ttyS5 --baud 750000 --apply
   ```

3. With Ready, run `INVENTURE_VERSION` and `INVENTURE_XY_STEP_STATUS`.
   Expect MCU `RC8-CONTROL-R7-HOME-R28-XY-TIMERS` and
   `verified=3 enabled=1 active=0`. `blocks_a/blocks_b` and
   `steps_a/steps_b` count completed hardware trains on PA7 and PA6.
   `peak_late` reports the largest launch estimate in nominal 120 MHz
   ticks. Inspect `INVENTURE_TRANSPORT_STATUS` for UART and OLED counters.
4. Start Home XYZ twice, first from the OLED and then from Fluidd. Check
   T0 selection, Z contact, retreat and the display response. At the
   existing velocity limit and with Z at the safe post-Home clearance,
   check axis-aligned and both diagonal moves inside the calibrated XY
   area. Read STEP and transport counters before and after each move.
   Axis-aligned moves can increment both counters. Each 45° diagonal
   uses a different running motor. A short block can use software even
   when its peak rate is in the hardware range.

To revert the MCU, flash the preceding R27 RC1 application. With the printer
reset and Ready, invoke the same branch's `--repair-home-procedures` without
`--with-xy-step-timers` to stage the R27 host variant. The installer
authenticates the prior CoreXY source and retains transactional host rollback.

## Build and verify

Use the same verified RC8 source kit and GNU Arm 14.2.1 toolchain as R27.

```bash
python3 ci/build_inventure_xy_step_r28.py /path/to/kit /path/to/r28-build
python3 ci/check_inventure_xy_step_arm_r28.py /path/to/r28-build /path/to/toolchain/bin
python3 ci/check_inventure_home_r24.py --transport-r26 --xy-step-timers
PYTHONPATH=tests:ci python3 -m unittest test_inventure_xy_step_r28
```

Two GNU Arm builds produced identical IVB bytes. The ARM replay executes
the linked firmware with a timer/GPIO/DMA model and covers both motors
running independently, direction reversal, odd periods, clock rollover,
software fallback and a mid-train interlock. Its modeled bus delays do
not measure real board timing. The existing UART replay delivered both
bytes in all 312 scenarios on the R28 binary. The authenticated installed
host passed 800 Home runs and 184 refusal cases with the existing OLED and
Fluidd entry points. Physical acceptance on the Inventure is pending.
