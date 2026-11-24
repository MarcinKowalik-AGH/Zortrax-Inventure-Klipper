# R17 NATIVE to SERVICE handoff after independent Home X / Y

## Reproduction on MCU R16 (24 November 2026)
`INVENTURE_HOME_X`, `INVENTURE_HOME_Y` and `INVENTURE_HOME_XY`
returned success. `INVENTURE_HOME_ALL` then asked for pad clearance
confirmation, but failed before heating/selecting T0:
`MCU did not enter SERVICE mode: another_owner_is_busy`.

The generic MCU admission test `motor_test_busy()` delegates to
`control_busy()`, which intentionally treats any `CT_NATIVE` ownership
as busy. It was incorrectly reused for the explicit NATIVE -> SERVICE
handoff. The previous R38.6 firmware does not permit this transition
after an individual axis homing operation.

## Narrow correction (MCU source R17 candidate)

A dedicated NATIVE -> SERVICE admission path accepts transfer **only**
when PE10 motor enable is physically deasserted, native heater duty
for all four heaters is zero, no local Z guard or refinement holds
ownership, and no DDA, feeder, lock, heat, fan or motor test is active.
Heartbeat, fault and STOP latch checks remain mandatory. All other
mode transitions retain their original busy checks.

The existing host R38.6 drains motion, disables stepper enable and
turns off native heaters before requesting SERVICE. A separate
`INVENTURE_NATIVE_SERVICE_HANDOFF_ABI=1` constant distinguishes MCU
source R17; the build requires its presence and includes R17 identity.
The installer recognizes both R16 and R17, retaining R16 support for
cold-start homing, but only R17 is intended to correct the reported
sequential Home X/Y -> Home All defect.

## Validation and deployment gate

The register mock suite checks rejection with PE10 enabled, nonzero
native heater PWM, outstanding local Z guard/refinement, active motion
and feeder ownership; it also checks successful idle transfer without
STOP/restart and unchanged irreversible STOP latch semantics.

**This is source code, not a flashed firmware or a release asset.**
Before installing on hardware, build with the project's pinned GNU ARM
14.2.1 toolchain, verify the generated `INVENTUR.IVB` and bootloader
compatibility, then perform controlled mechanical acceptance. Do not
flash modified source files or reuse the old R16 IVB under the R17 name.

R38.6 Home Z geometry, approved platform leveling and OLED are unchanged.
