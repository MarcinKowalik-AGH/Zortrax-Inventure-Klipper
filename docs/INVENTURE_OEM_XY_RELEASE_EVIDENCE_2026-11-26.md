# Original r0323 XY reference: direct binary evidence

On 26 November 2026 (Poland), the original application was read again from
the project's supplied archive, rather than inferred from the Klipper port
or its success messages. The earlier statement that the factory XY
procedure uses a release phase is supported by the original machine code.
That fact does not establish that the standalone Klipper Home X/Y macros
implement the same procedure.

## Exact input

Archive: `ZORTRAX_INVENTURE_KOMPLET_2026-10-02_CZESC_1_z_2.zip`.
Member: `ZORTRAX_INVENTURE_HANDOFF_KOMPLET_2026-10-02/10_AUDYT_FIRMWARE/sources/application_r0323.bin`.

- Load address: `0x08010000`.
- Length: 164,784 bytes.
- SHA256: `9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`.
- X wrapper: `0x0801D59C`; Y wrapper: `0x0801D5A2`.
- Shared XY function: `0x0801D430`.
- X descriptor: `0x08036CD4`; Y descriptor: `0x08036CF0`.

Bytes were hash-checked before disassembly with Capstone 5.0.6 in ARM
Thumb/M-class mode. No application code was executed on a printer, no UART
was opened, and no new physical test was performed. The original firmware
binary is not added to this repository.

## Control flow that distinguishes approach from release

| Original address | Observed operation |
| --- | --- |
| `0x0801D460` | Calls the descriptor's approach direction callback at offset `0x10`. |
| `0x0801D496` | Reads the selected axis input through `0x08012FF0`; the zero-input branch continues the approach. |
| `0x0801D4A6` | Reads the input again after the intervening delay, before accepting the active state. |
| `0x0801D4B0` | Calls the descriptor's opposite direction callback at offset `0x14`. |
| `0x0801D4BA` | Reads the selected input during reverse travel. |
| `0x0801D4C4` | `cbnz r0, 0x0801D504`: while input is still nonzero, continue reverse steps. |
| `0x0801D504` | Reverse step/delay/count loop; the descriptor's `0x1388` bound is checked. |
| `0x0801D4C6` | Reached on the zero-input branch: calls the descriptor's reference-coordinate callback at offset `0x18`. |
| `0x0801D4D0` | Stores the returned reference coordinate for the selected axis. |
| `0x0801D4DC` | Combines that reference with the measured counter for the subsequent target. |
| `0x0801D4E6` | Sets F4000; subsequent code queues motion and drains the planner. |

The direction callbacks are X approach `0x08013F24`, X reverse
`0x08013F44`, Y approach `0x08013F74`, and Y reverse `0x08013F64`.
The X callback retains the OEM system-bit-14 direction choice. The input
reader applies the axis descriptor's input mapping; PB11 and PC15 have the
previously documented non-inverted mapping.

Thus the reference-coordinate assignment is **after reverse travel has
released the selected input**, not at the first active approach reading.
This statement concerns the reference event. Additional planner motion
follows it, so it must not be expanded into a claim that the entire factory
procedure ends motionlessly at the electrical edge. Factory coordinate
offsets must also be preserved; this evidence does not authorize assigning
an arbitrary native zero or changing input polarity.

## Consequence for the current Inventure incident

The original firmware fact and the Klipper implementation must be assessed
separately. The restored composite Home preparation contains explicit
`home` then `home_release` actions. The standalone native Home X/Y route
still uses `_home_limit` and core G28; its default retract/second approach
is not proof of the factory final reverse-release reference phase.

Likewise, identical adapter traces in the 24 UI simulations do not prove
the physical edge used by core homing. They show that those existing calls
are preserved. The earlier assurance of full equivalence was too broad.

No runtime procedure, configuration, MCU source or release image is changed
by this evidence record. The already identified unconditional T0
preparation in operator Home Z still releases native ownership and repeats
XY; reuse of a valid, continuously held XY reference is not established by
restoring the old composed preparation alone.
