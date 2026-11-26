# Inventure Home R24 RC2 — XY 5 mm backoff

After the measured limit release, each homed X/Y axis now moves another
**5 mm inward**, at the existing release speed (1 mm/s), with motors held.
The release measurement and coordinate translation are retained. The other
axis is not moved. Home Z, T0 selection/cleaning, PC14 refinement, PE12
reference and final Z parking keep the exact RC1 engine files.

**Already running the R24 MCU from RC1? Install only the host update below.**
No application or bootloader reflash is needed. RC2 contains the same R24 MCU
application and dictionary; CI checks their SHA256 against RC1. The installer
authenticates RC1, stages its replacement and changes only the operator helper
and runtime manifest, with rollback to the original bytes.

Independent Home X/Y use one bounded approach followed by reverse travel
to the selected limit's release, then the additional 5 mm park. Success
retains the shared motor enable.
Home Z uses previously verified, continuously held XY and homes only missing
axes. Home All explicitly homes Y and X. Both then use the original T0
selection/cleaning and R38.6 native Z measurement engines.

This is a matched host and MCU update for **InventureBoot 1.7**. R23 cannot
perform NATIVE-to-SERVICE transfer while holding PE10, so updating only the
host cannot implement the required retained-XY behavior. The new R24 command
checks drained native step queues, inactive owners/guards, heater outputs off,
tray/contact and existing sensor/door interlocks. It transfers ownership
without writing STEP, DIR, heater PWM or PE10. It reserves the following T0
session owner and expires an unused entry. Queueing native steps during
SERVICE is rejected. Existing SERVICE-to-NATIVE handback remains in use.

## Operator behavior

| Request | XY homing | Result |
| --- | --- | --- |
| Home X | X approach, reverse release, +5 mm X | Hold motors and retain verified X |
| Home Y | Y approach, reverse release, +5 mm Y | Hold motors and retain verified Y |
| Home XY | Y then X, each with release and +5 mm | Retain both references |
| Home Z after valid X/Y | No repeated XY homing | Prepare T0, full lower Z reference over pad, working Z and park |
| Home Z with one missing/invalid axis | Home only that axis | Same original T0/Z sequence |
| Home Z without valid XY | Y then X | Same original T0/Z sequence |
| Home All | Y then X explicitly | Same original T0/Z sequence |
| Restart, disabled drive, interrupted motion or fault | Discard old references | Numerical coordinates alone never authorize reuse |

Macros, the physical OLED and literal G28 share these entry points. G28 Z
and G28 without selectors use the same existing operator confirmation as
the Home Z/All macros. The internal lower PE12 homing calls the core handler
directly and cannot recurse through this dispatch.

The initial pre-XY table retreat remains bounded at 10 mm with PE12 as an
early stop. It does not establish a full Z reference. The full downward
PE12 reference remains after T0 is over the center pad. The accepted local
PC14 engine, stable-cycle rule, measured release, MCU 1 mm clearance and
configured final park are not rewritten.

The XY approach budget is 160 mm. The reverse search uses the existing
1 mm/s and 5000/640 mm failure bound. The separate 5 mm backoff starts only
after that measured release succeeds. The measured datum is not reset to
the park position. A fresh
release and positive measured movement are required. The existing native
rail datum, measured release translation and pad endpoint are retained;
no printer geometry or calibration is retuned. A native stop overrun is
accounted for in the retained starting position.

## Install on the printer

Keep the printer idle with heater targets off. With the R24 MCU already
installed, use `FIRMWARE_RESTART` once in Fluidd, wait for Ready, then run
the following before any Home or heating command.

When upgrading from R23, first install the application `INVENTUR.IVB` from
`INVENTURE_HOME_R24_BOOT17.zip` using the existing InventureBoot 1.7 application
update and verify `SHA256SUMS`. Keep the saved P4 UART choice (750000 baud on
the recorded field unit). The host refuses activation without the R24 MCU
capability. An existing R24 application needs no reflash.

```bash
cd "$HOME/inventure-src" &&
git fetch origin --tags &&
git switch --detach inventure-home-r24-rc2 &&
python3 install-new-host.py --repair-home-procedures --serial /dev/ttyS5 --baud 750000 --apply
```

The installer requires the R24 MCU capability, the indexed original R38.6
backup, unchanged effective printer settings, offline startup and live
PID/path/Ready/profile verification. Existing PID, calibration JSON, split
configuration, macro groups, OLED graphics and menu choices are preserved.
The preceding R23 and R23.7.1 source/release inputs remain available.

Read-only status:

```gcode
INVENTURE_VERSION
INVENTURE_HOME_Z_STATUS
```

Expected MCU: `RC8-CONTROL-R7-HOME-R24-OLED`. Home status:
`RC8-HOME-XY-RELEASE-Z-REUSE-R24.1-XY5MM`, profile `operator-home-r24` and
`g28_dispatch: shared_operator_home_r24`. The display's R23.5 release label
remains because its graphics/menu files have not changed.

## Verification and limits

The installed-config test exercises literal Fluidd macros, G28 and physical
OLED selection/confirmation, X→Y→Z and Y→X→Z, partial/missing XY, repeated Z,
Home All and lost-drive invalidation. It runs the original factory
interpreter, T0 generator and native Z engine against simulated physical
interfaces and checks unchanged configuration and heater targets. Selected
input failure, missing/zero-step release, failed backoff and invalid held-entry receipts
must stop before T0 or Z certification. C register tests execute the actual
R24 ownership overlay, guard/lease failures, continuous enable, native queue
exclusion and return to ordinary SERVICE. ARM compilation validates the
actual command dictionary and boot container. RC2 verifies 126 completed
requests and 42 refusals across the three interfaces. Each release must be
followed by exactly +5 mm on that axis before another seek or SERVICE entry;
the other axis, measured reference, held enable and heater targets are checked.
The RC1 upgrade and exact rollback are tested; native Z, the T0 driver and
Z generator are checked byte for byte unchanged.

These are software and MCU register-model checks. The additional 5 mm
backoff has not been tested by us on the physical printer. The separate
18:36 incident's primary shutdown cause is not inferred from these checks.
