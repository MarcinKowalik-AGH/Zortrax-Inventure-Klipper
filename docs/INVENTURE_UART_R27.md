# Inventure R27 RC1 — UART reception during the existing board sampler

R26 RC1 failed physical Home X acceptance. The supplied log identifies the R26
MCU, an MCU `Timer too close` shutdown and a subsequent missing `trsync_state`
response. Its preceding telemetry reports 69 USART overruns, 5 noise flags,
10 framing flags, zero software RX overflows and zero OLED DMA errors. The
measured OLED critical-section peak was 495 ticks (4.125 us at 120 MHz).
The raw printer log and printer identity are not published.

## Reproduced defect

The native Home/endstop path in both ports uses pinned Klipper homing and
trsync. The M300 reference is source commit
`d1616a9e7007b44dda2a737cc0297b7f66a77b63`, including
`host/klippy/extras/m300_native_motion.py`, its native G28 adapter,
`mcu/SOURCE_MCU/overlay/src/stm32/m300_board.c` and the pinned Cortex-M timer and
UART implementations. USART has priority 0; SysTick has priority 2.

Inventure additionally polls factory/filament inputs at 20 kHz (10 kHz when
eligible in native mode), with independent guards every 10 ms and eight sensor
channels. The Cortex-M scheduler enters callbacks with PRIMASK set. This
masked even priority-0 UART throughout the complete sampler callback, including
the native sensor/cooling guard evaluation. R26 only shortened the OLED worker
and UART-frame publication locks; it did not fix this separate callback.

The actual R26 application was rebuilt locally and matched the published IVB
SHA256 `5c9781730189514f7dbe50daae7c3c7d32f6a75cf6f9259ccc54c93349574071`.
Its linked sampler and USART ISR were then executed in a Cortex-M3 model.
Even a favourable lower-bound model (one useful instruction/cycle; IT/hints
cost zero) needs at least 1920 cycles for the tested native callback. One
750000-baud, 10-bit UART byte takes 1600 cycles at the nominal 120 MHz clock.
Thus an unread receiver can lose its next byte while that callback masks IRQs.

The comparison covers 312 cases: OFF/NATIVE/SERVICE, cold/hot inputs, fan-edge
activity, arrival phase and an existing priority ceiling. The preceding R26
image loses data in 24 cases. The R27 image delivers both bytes in every case,
through the actual firmware ISR and receive buffer, with zero overruns. Timer
rescheduling results, guard state and caller masks are compared. The test also
checks priority-ceiling cleanup after shutdown. These are executable software
tests, not a physical measurement or physical printer acceptance.

## Focused adaptation

R27 wraps the unchanged Inventure sampler body in a Cortex-M BASEPRI priority
ceiling. Priorities >=1 remain masked, including SysTick/motion and OLED DMA.
Only the existing priority-0 USART handler may preempt this work. That handler
receives/transmits serial bytes and wakes tasks; it does not dispatch received
commands, edit the timer list, change ownership or write motor pins. PRIMASK
and BASEPRI are restored to their caller values. A shutdown hook clears the
ceiling if a guard exits the callback through the scheduler's longjmp path.

All accepted X/Y/Z Python executors, the native motion/PC14 state machines,
coordinates, speeds, opening-edge reference, dynamic zero, 5 mm XY parking,
held motors, valid-XY reuse, T0 preparation, tray/door/thermal/heartbeat guards,
sampling rates and deadlines are preserved. No timeout is enlarged and no
safety check is bypassed. Native STEP callbacks are not made reentrant.

The MCU remains on the existing P4 port/rate, `/dev/ttyS5` and 750000 baud for
this printer. OLED remains SPI2/DMA1 Stream4. UART remains interrupt driven.
The new capability is `INVENTURE_UART_TIMER_WINDOW_ABI=1`.

The host only enables concurrent Home pixels on an MCU advertising both the
OLED and UART-window capabilities. It retains the preceding quiet fallback on
older MCU. Factory Info/Flash sync still waits for idle. New window counters
are cached alongside the existing UART/OLED counters, and a cached transport
snapshot is automatically written to klippy.log on shutdown.

From R26, the host upgrade changes only the communication adapter and its
integrity manifest. The installer authenticates the exact preceding R26
manifest, preserves printer settings and retains changed-file rollback.

## Installation

Use release `inventure-native-uart-r27-rc1`. At idle, with heater targets off,
run `FIRMWARE_RESTART` in Fluidd and wait for Ready. Install the host first:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-native-uart-r27-rc1 &&
python3 install-new-host.py --repair-transport --serial /dev/ttyS5 --baud 750000 --apply
```

Next copy the release's `INVENTUR.IVB` to the root of the printer's FAT32 SD
card and update the application through the existing InventureBoot 1.7
procedure. Bootloader, P4 selection and factory identity/counter banks are
retained. Complete both updates before trying Home.

After Ready, use these read-only commands:

```gcode
INVENTURE_VERSION
INVENTURE_OLED_STATUS
INVENTURE_TRANSPORT_STATUS
INVENTURE_HOME_Z_STATUS
```

Expected MCU: `RC8-CONTROL-R7-HOME-R27-UART-OLED`; transport component:
`RC8-COMMUNICATION-R27.1`; mode: `cooperative_dma`.
`uart_priority_window.windows` counts sampler windows and `rx` counts USART
receptions while those windows run. `peak_ticks` measures whole preemptible
work duration, not globally blocked IRQ time. Divide by 120 for nominal us.
UART error counters remain cumulative; faults are not cleared by reading them.

CI gates publication on all host/MCU regressions, installed Home/OLED/menu/Info
checks, actual linked ARM replay, and two matching GNU Arm 14.2.1 builds.
Physical R27 acceptance remains pending. The reproduced RX defect explains a
concrete way to lose control traffic; it is not proof that every recorded
shutdown or physical framing/noise error has the same cause.
