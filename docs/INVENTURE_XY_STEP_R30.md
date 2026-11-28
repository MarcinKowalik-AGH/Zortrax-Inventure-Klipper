# Inventure R30 RC1 | CoreXY STEP timing

R30 uses an STM32F207 timer clock of 60 MHz and a DWT clock of 120 MHz.
It retains the M300-style high pulse used in R29 and changes the
minimum programmed period on the two fast native XY STEP outputs.

| Parameter | Timer ticks at 60 MHz | Time |
| --- | ---: | ---: |
| STEP high | 147 | 2.450 µs |
| Nominal STEP low at maximum rate | 77 | 1.283333 µs |
| Minimum programmed period | 224 | 3.733333 µs |
| Required low-time floor | 45 | 0.750 µs |

At 640 STEP/mm on each CoreXY motor, the minimum period corresponds
to 267,857 STEP/s per motor. With the R30 travel limits selected, the
Klipper planner caps pure X or Y travel at 296.00 mm/s and either
45° diagonal at 295.94 mm/s. The matched host keeps 30 mm/s and
200 mm/s² at startup with the field configuration. It also uses
30 mm/s and 200 mm/s² as ceilings if the timer capability or hardware
self-test is unavailable.

| Motion | PA7 | PA6 |
| --- | --- | --- |
| Sustained even-period blocks | TIM3 PWM with TIM2 counting the pulses | TIM6 triggers DMA1 stream 1, which starts a TIM13 one-shot |
| Ramps, odd periods, short blocks | TIM14 one-shot with Thumb-2 scheduling | TIM13 one-shot with Thumb-2 scheduling |
| Slow Home and trigger-armed moves | Existing 4 µs Klipper pulse | Existing 4 µs Klipper pulse |

The Thumb-2 loop reads the DWT clock, waits for the scheduled edge and
starts the hardware one-shot. It reads back the APB register before
sampling the clock for the next low-time deadline. Compatible short
blocks are prepared during the preceding pulse. Direction changes
and timer-owner changes wait for the pulse to drain. The scheduler
keeps the absolute Klipper step clock and can recover from a late
handoff inside the low-time floor.

OLED refresh stays on SPI2 and DMA1 stream 4. A change in DMA
arbitration latency moves the PA6 rising edge while TIM13 fixes its
high time. A 0.500 µs variation leaves 0.783 µs of LOW in the modeled
fastest train. The LV8729V requires at least 0.500 µs at each STEP
logic level. [LV8729V input timing](https://www.onsemi.com/pub/Collateral/LV8729VJP-D.PDF)

## Install the paired MCU and host

1. Verify `INVENTUR.IVB` against the archive's `SHA256SUMS`. With
   InventureBoot 1.7, install the image by the existing FAT32 SD
   procedure.
2. With the printer idle and heater targets off, enter
   `FIRMWARE_RESTART` in Fluidd and wait for Ready. Update the host
   before starting a Home or heating operation.

   ```bash
   cd ~/inventure-src &&
   git fetch origin codex/inventure-native-xy-r30 &&
   git switch --detach FETCH_HEAD &&
   python3 install-new-host.py --repair-home-procedures --with-xy-step-timers \
     --serial /dev/ttyS5 --baud 750000 --apply
   ```

3. Run `INVENTURE_VERSION` and `INVENTURE_XY_STEP_STATUS`. Expect
   `RC8-CONTROL-R7-HOME-R30-XY-STEP-224` and
   `verified=3 enabled=1 active=0` while idle. Verify the existing
   Home XYZ from OLED and Fluidd with the startup limits.
4. Once homed, the independent travel test can select
   `SET_VELOCITY_LIMIT VELOCITY=296 ACCEL=1500`. Use
   `SET_VELOCITY_LIMIT VELOCITY=30 ACCEL=200` to return to the
   baseline settings immediately. A Klipper restart also reloads
   the configured limits.

For rollback, load the previous R29 image through InventureBoot and
repeat the host install. The installer selects the paired profile
from the reported MCU identity and checks its timing constants.

## Verification

The linked ARM tests cover sustained operation, short queued blocks,
ramps, DWT wrap, independent motor timing, simulated sampler stalls,
and loss of motor enable. The installed Klipper check exercises
the literal velocity-limit command and both diagonals. It also covers
Home from the menu and through the Fluidd commands. The leveling
regression exercises the separate five-point procedure.

Physical acceptance is pending. Check the shortest PA6 LOW at the
peak step rate with OLED activity against the 0.750 µs floor. The
planner speeds still need a travel test on the actual Inventure.
