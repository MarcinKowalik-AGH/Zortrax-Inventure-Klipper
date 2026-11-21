# Home Z in Fluidd and on the Inventure OLED

R38.3 connects **Home Z** to the native homing sequence that reached the center pad on the printer. At confirmation, the host reads Klipper's current X and Y references. With both axes referenced, it uses the existing Z-only sequence. If either reference is missing, it uses the existing full XYZ sequence. Both routes establish the lower endstop PE12, measure T0 release at PC14 and park at the configured Z clearance. Home All always requests the full sequence.

## Operating the control

Select **Home Z** on Fluidd's Tool card or on the OLED. The console macro is `INVENTURE_HOME_Z`. Confirm the existing pad and clearance prompt with T0 physically lowered and the tray seated. After a restart, the full sequence homes the platform to PE12, homes Y then X, moves to the configured center pad, heats T0, measures the contact release and parks. Subsequent Home Z runs can use the current XY reference. The same prompt accepts the configured or supplied nozzle target. MCU R14 is compatible.

## Evidence

On 15 November the printer completed `INVENTURE_HOME_XYZ ACK=PAD_AND_CLEARANCE_CHECKED` in NATIVE mode. The STM32 reported 181 cycles and a release 139.356608 mm from the bottom reference. Klipper saved the working zero and parked at X82.8 Y68.0 Z10.0. A later Home All attempt reached STM32 refinement and ended in a local MCU shutdown before the first completed contact cycle. That log does not identify the physical cause.

R38.2 rejected Home Z before the confirmation dialog when either XY reference was missing. The correction is in the operator's axis selection. Native homing parameters, motor movements and the MCU contact routine are inherited from the preceding release.

The regression test compares the entire sequence from the button with a direct native invocation, including heating, endstop requests, STM32 refinement, setting zero and parking. It covers unknown, partial and valid XY references as well as loss of the reference before confirmation and a failed contact measurement. The installed field and fresh profiles exercise the Fluidd macros and the OLED confirmation against a simulated MCU.

All 820 host tests passed. A separate replay against the 15 November host produced identical motion, heater, guard and refinement calls for Home All, Home Z after restart and Home Z with known XY. Physical verification of the R38.3 button on the printer is pending. [15 November homing implementation](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/blob/ad7c0994116ee7d1f4b3c91ddcf4e4dd46556674/orange_pi/inventure_native_z.py)
