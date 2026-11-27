# Inventure R26 RC1: interruptible OLED DMA and UART publication

This paired host/MCU update retains the accepted Inventure Home procedures and
repairs two interrupt-masking sections in the display/serial transport. It uses
the short critical-section discipline of the working M300 implementation.

## Existing DMA and the actual changes

Inventure already transfers OLED pixels through **SPI2 / DMA1 Stream4**. The
Orange Pi connection is a separate interrupt-driven UART; this update does not
replace it with UART DMA. The P4 baud remains the value selected in InventureBoot
(750000 for the reported printer). The compiled 250000 fallback is already
overridden by the actual connected UART rate in the installed host serial layer.

The M300 source used for comparison is commit
`d1616a9e7007b44dda2a737cc0297b7f66a77b63`: its OLED DMA worker prepares the
transfer with interrupts enabled and protects only short hardware/queue commits.
Inventure previously masked interrupts across its entire DMA worker, including
SPI preparation, timer edits, descriptor preparation and ACK generation. Its
UART ring also copied a whole encoded frame while interrupts were masked.

R26 changes these two sections:

- SPI/DMA preparation and display ACK generation run with interrupts enabled.
  Short atomic sections commit queue/hardware state and edit the alarm timer.
  A final generation/session check prevents a watchdog or shutdown cancellation
  during preparation from enabling an abandoned DMA transfer.
- The single console producer copies an unpublished UART frame with interrupts
  enabled. Only publication of the complete frame and TX interrupt enable are
  atomic. Ring capacity, framing and retransmission rules remain unchanged.
- Read-only counters expose peak OLED critical-section ticks, completed blocks,
  DMA errors, USART overrun/noise/framing errors and software RX overflows.
- Only an MCU advertising `INVENTURE_OLED_COEXISTENCE_ABI=1` enables
  `oled_transport_mode: cooperative_dma` on the host. This mode keeps existing
  Home animation active during motion and thermal preparation. Older MCU uses
  `legacy_quiet`, retaining the prior display-pause fallback. Optional factory
  Info/Flash synchronization still waits until the printer is idle.

The MCU identity is `RC8-CONTROL-R7-HOME-R26-TRANSPORT-OLED`; the host transport
component is `RC8-COMMUNICATION-R26.1`. The base host identity remains R38.6 and
Information remains R25.2 because their accepted executors are unchanged.

## Evidence and limits

The supplied log contains a stalled UART followed by the five-second native-Z
guard, and a separate `Timer too close` shutdown. It also contains two later
completed Home XYZ runs with 102 refinement cycles, 101 non-improvements and
reference establishment. Those successes do not identify the exact installed
host patch. No software test proves that R26 resolves every recorded hardware
shutdown; physical acceptance on the printer is still required.

The native register model executes the actual F207 DMA branch for 2004 blocks,
including SPI-busy completion, DMA errors and cancellation during preparation.
The UART model exercises the actual generated ring for 2000 frames, interrupt
preemption, a full ring and counter wrap. Both models first demonstrate failure
of the preceding long interrupt-masking section, then pass the revised section.
The installed Home model also exercises 800 cases with OLED animation enabled.
These are software/register tests, not measurements of MCU interrupt latency.
CI runs all host/MCU regressions, compiles twice with hash-pinned GNU Arm 14.2.1,
compares the application/dictionary outputs, and publishes only after success.

All four accepted Home executor files retain their SHA256:

| Executor | SHA256 |
| --- | --- |
| `inventure_operator_home.py` | `79dc8b712cf54f0e86b67936d1569690071345e8662a29d4ae026236acc53352` |
| `inventure_native_z.py` | `4e6643642cc88868a0b46cc37abbf9c3d9ae30850aed0352e51ddd03d2fa380a` |
| `inventure_mcu.py` | `2f9216e819c5d9ca34330db87bbe4032eacbde2dc73aa05ec51316532a0dfe6b` |
| `inventure_z_home.py` | `030ab6cf028c8321fa877d0c7d553162061722d66f2a7af14b04f452460621cd` |

The overlay also rejects changes to the preceding MCU C/header/include files
outside the display driver and UART driver, allowing only release identity
replacement. This preserves X/Y opening-edge reference, dynamic zero, 5 mm
parking and held motors; valid-XY reuse for Z; T0 preparation; tray, temperature,
ownership, endstop and heartbeat guards; PC14 refinement and Z clearance. R26
does not add a fixed five-touch Z sequence or loosen a safety timeout.

## Install the paired update

Use the release assets from **inventure-transport-r26-rc1**. Install the host
first while the existing R24/R25 MCU remains installed. The host will use
`legacy_quiet` until the matching R26 application is present.

With the printer idle and heater targets off, use `FIRMWARE_RESTART` in Fluidd
once, wait for Ready, then install before starting Home or heating:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-transport-r26-rc1 &&
python3 install-new-host.py --repair-transport --serial /dev/ttyS5 --baud 750000 --apply
```

`--repair-transport` selects the same authenticated runtime/configuration profile
as `--repair-home-procedures`; it preserves the accepted Home code and existing
printer settings. From the preceding R25 RC4 runtime only the communication
adapter and its integrity manifest change. The installer backs up changed files
and service settings, verifies the running path/component identity after Ready,
and rolls back a failed activation. Do not disable its armed-MCU gate.

Next update **only the MCU application `INVENTUR.IVB`** using the established
InventureBoot 1.7 application-update procedure. Verify `SHA256SUMS` from the
release. Do not replace the bootloader or factory identity/counter banks.
Return to Klipper Ready before starting motion. Both updates are needed for
simultaneous Home animation and the revised MCU transmission implementation.

Read-only verification in Fluidd:

```gcode
INVENTURE_VERSION
INVENTURE_INFO
INVENTURE_OLED_STATUS
INVENTURE_HOME_Z_STATUS
```

Confirm the R26 MCU release, `RC8-COMMUNICATION-R26.1`,
`oled_transport_mode: cooperative_dma` and a populated `transport_health` after
approximately two seconds. `irq_ticks / 120` is the measured critical-section
body duration in microseconds at the 120 MHz MCU clock; it excludes instrumentation
entry/exit overhead and is not a complete worst-case interrupt-latency bound.
Error counters are cumulative since MCU start; compare their change during an
operation. Reading them does not clear faults, move motors or change ownership.

Review the preserved Home identity in `INVENTURE_HOME_Z_STATUS` before physical
acceptance. Software CI does not operate the printer. If a run faults, retain the
complete new Klipper log and these transport counters for that run.
