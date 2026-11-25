# Inventure MCU R23 OLED — points 1–5 candidate

## Latest host update: verified OLED activation and all preparation stages

[`inventure-r23-leveling-oled-rc4`](INVENTURE_R23_LEVELING_STAGES.md) adds
live-process checks for the actually loaded OLED UI and illustrated Home,
heating, T0 selection and parking screens. The reported `Back / saved` view
identified a running menu without the RC3 wizard. MCU R23 remains unchanged.

## R23.2: preliminary leveling on the physical OLED

[`inventure-r23-leveling-oled-rc3`](INVENTURE_R23_LEVELING_OLED.md) adds the
original Inventure screw animations/messages and five-point result to the
physical Zortrax menu and to runs started with `INVENTURE_LEVEL`. The existing
MCU R23 application stays unchanged. This preliminary screw adjustment is
separate from the future printing-compensation mesh in Tune. The owner has
completed and saved the underlying measurement; the new OLED wizard still
awaits physical panel acceptance.

This candidate publishes the complete Inventure source and MCU application, including the previously separate R19–R22 changes and full-cycle acceptance. It targets the original Inventure STM32F207, dual-nozzle hardware, pads, sensors and OLED.

| Agreed change | Included implementation |
|---|---|
| 1. One Klipper planner | Native normal Home, G0/G1 and printing; no second XY homing in the T0 SERVICE excursion. |
| 2. Exclusive SERVICE/NATIVE handoff | Bounded T0 selection/cleaning, owner/run/token verification and independent MCU readback before importing position. |
| 3. MCU scheduling | Conditional 10 kHz NATIVE / 20 kHz SERVICE sampling, missed-slot recovery, real-time guards and timing diagnostics. These are sampling rates, not STEP limits. |
| 4. Independent policies | Strict SERVICE windows and independent NATIVE limits, with continued monitoring of hot/powered heaters and physical interlocks. |
| 5. Full operator cycles | Installed Fluidd/OLED command routes, 36 Home commands, 12 handoffs, nine refusal scenarios; MCU endurance over 500 simulated seconds and 13 clock wraps. |
| MCU OLED R23 | Startup logo, 16-level icons/animations, link-loss and fault messages, first shutdown cause, fenced reconnect and bounded display transfers. |
| Display optimization | Local animation without UART animation frames; 1307 SPI bytes per changed icon frame versus 8199 for a full frame; 128-byte scanline buffer and optimized Thumb-2 RLE filling. |

## Files and compatibility

- `INVENTURE_R23_MCU_OLED.zip` includes the application, dictionary, build reports, checksums, previews and `SOURCE_R23.zip` from the exact published source commit.
- `INVENTUR.IVB` is the application file for the existing **InventureBoot 1.7**. No bootloader update is included.
- MCU release: **`RC8-CONTROL-R7-HOMING-R23-OLED`**. The existing R22 host protocol remains compatible; the installer on this branch also validates the R23 capabilities.
- The release's automatic GitHub source archives include all points 1–5, host installers, MCU sources, tests and documentation.

Application SHA256:

```text
INVENTUR.IVB  ef83d8ca0c26c12ed11fbd27380b1ee014cb4baf977c7adebb178861df137645
klipper.bin   a7494efaf36f6082f6ca1f45f382ad9d8a3433392a30b3261e3dcac007834b65
```

## Verification

Publication requires the complete host suite, installed configuration/native startup, real MCU C register tests, exact display pixels and asset checks, the pinned GNU ARM 14.2.1 build, IVB/dictionary/ABI verification, the 2048-case RLE emulation and archive readback checks. The workflow publishes this prerelease only after those checks pass.

Software simulation and compilation do not establish physical acceptance. The new R21–R23 candidate has not been qualified on the physical Inventure. Repeated Home, printing, OLED disconnect/reconnect and timing measurements remain to be observed on the printer. [Timer too close issue #3](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/issues/3) remains open. Native `BED_MESH_CALIBRATE` still requires a configured and verified probe; OEM five-point leveling is a separate procedure.

[Full-cycle acceptance and remaining physical checks](INVENTURE_POINT5_FULL_CYCLE_ACCEPTANCE.md) · [Display implementation and provenance](INVENTURE_R23_M300_DISPLAY_PARITY.md) · [SERVICE/NATIVE policy](INVENTURE_R22_SERVICE_NATIVE_POLICY.md)
