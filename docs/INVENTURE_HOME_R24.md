# Inventure Home R24 RC4 — dynamic XY release reference

Home X/Y assign the configured reference coordinate at the **measured reverse
release** of that axis's endstop, then park 5 mm from that event with the drives
held. The recorded field configuration uses reference coordinate 0, so the
finished positions are X=5.00 and Y=5.00. The measured release displacement is
fresh for each axis and each Home, including explicit rehoming. Values such as
0.13 or 0.09 mm from a screenshot are never installed as corrections.

The bounded approach, selected input, reverse speed (1 mm/s), 5000/640 mm
release search bound and motor holding are retained. Native stop overrun is
counted as travel already made after the electrical release; only the remaining
travel to reference+5 is commanded. A stop already beyond that park is refused.
The reference becomes reusable only after the park succeeds with the limit open.

## One working frame with the original physical Z point

The previous host retained a contact-based native frame while SERVICE already
used the release-based frame. RC4 translates native XY to the measured release
and accounts for that translation exactly once in the held SERVICE handoff.
Native coordinates, G-code and SERVICE procedures therefore share the measured
release reference. Each configured `position_endstop` value is retained; the
reference event does not authorize replacing factory/configured coordinates.

Existing configured pad coordinates and travel bounds retain their physical
meaning. Their working-coordinate representations are translated by the actual
measured displacement for each axis. Neither the original physical travel
envelope nor the physical Z contact point is moved. Config files, calibration
JSON and tuned values are not rewritten with measured corrections.

The frozen R38.6 native Z module, factory driver and Z generator are byte for
byte unchanged from RC3. The adapter translates their frame parameters and pad
coordinates. T0 selection/cleaning, temperatures, PE12 bottom homing, PC14
measurement, stable cycles, final clearance and configured Z parking retain
their existing engines. Changing coordinates does not add an XY homing pass.

| Request | XY behavior |
| --- | --- |
| Home X | Measure X release, establish its reference, park at reference+5; retain Y |
| Home Y | Measure Y release, establish its reference, park at reference+5; retain X |
| Home XY | Y then X, each with its own fresh release measurement |
| Home Z with valid held XY | Reuse both references; original T0/Z sequence |
| Home Z with missing/invalid XY | Home only the missing axes, then original T0/Z sequence |
| Home All | Explicitly remeasure Y and X, then original T0/Z sequence |
| Restart, disabled drive or fault | Discard references and frame translations |

Fluidd macros, G28 and the physical OLED use the same operator route. G28 Z
and G28 without selectors keep the existing confirmation. Internal PE12 homing
calls the core Z handler directly. The existing bounded initial 10 mm retreat
remains before XY where required; the full bottom reference remains over the pad.

## Install

This is a **host update** for the existing
`RC8-CONTROL-R7-HOME-R24-OLED` MCU and InventureBoot 1.7. The MCU application,
binary and dictionary are identical to RC1/RC2/RC3; CI verifies their hashes.
An already installed R24 application needs no reflash. R23 cannot run this
profile because it lacks the held NATIVE-to-SERVICE ownership capability.

With the printer idle, turn heater targets off, run `FIRMWARE_RESTART` once in
Fluidd and wait for Ready. Install before another Home or heating operation:

```bash
cd "$HOME/inventure-src" &&
git fetch origin --tags &&
git switch --detach inventure-home-r24-rc4 &&
python3 install-new-host.py --repair-home-procedures --serial /dev/ttyS5 --baud 750000 --apply
```

The installer recognizes the authenticated RC1 and RC2/RC3 runtimes. Its R24.3
revision retains RC3's original-backup verification fix and the complete apply
regression. Only the operator helper and runtime manifest change when upgrading
RC3. The indexed original R38.6 backup, effective configuration, offline startup
and actual service/PID/path/Ready/profile are verified inside the existing
activation/rollback transaction. Saved P4 baud, PID, JSON, split configurations,
Fluidd groups and OLED graphics/menu files are preserved.

## Readback

```gcode
INVENTURE_VERSION
INVENTURE_HOME_Z_STATUS
```

Expected MCU: `RC8-CONTROL-R7-HOME-R24-OLED`.
Home release: `RC8-HOME-XY-RELEASE-ZERO-R24.2-XY5MM`.
Profile: `operator-home-r24`; `xy_reference_frame`: `measured_release`.
The display's independent R23.5 menu/graphics release label stays unchanged.

After restart, references and translations are empty. After successful X/Y,
`xy_references` records each newly measured `release_mm` / `release_steps`,
configured `reference_native_mm`, `post_home_backoff_mm: 5.0` and held enable.
`xy_frame_release_mm` is this run's coordinate translation, not a stored machine
correction. `configured_pad_xy_mm` retains the original configuration;
`working_pad_xy_mm` shows that same physical pad in the current release frame.

## Verification and limits

Installed-config simulation exercises 384 completed Fluidd/OLED/G28 requests
and 96 refusals across two independent switch-stroke and stop-overrun profiles.
It covers X→Y→Z, Y→X→Z, missing axes, repeated Z, explicit rehoming of X or Y
with different measured release values, Home All and lost-drive invalidation.
It runs the installed adapter, original factory interpreter and Z engine.

Checks include the measured-edge origin, exact reference+5 park, unchanged
other-axis position, no accumulated/stale correction, continuous enable,
unchanged heater targets and effective configuration, and the same physical Z
pad before/during/after the handoff. The actual pinned CoreXY bounds checker
accepts the translated physical boundary and rejects one further step.
Selected input failure, absent/zero release, excessive overrun, failed park and
invalid held-entry receipts discard references and prevent T0/Z certification.

Authenticated upgrades from RC1 and RC3 and exact rollback are tested. The
complete installer --apply regression includes real backup auditing, runtime
and configuration staging, offline Klipper startup, systemd parsing, atomic
activation and Fluidd setup with simulated HTTP/process/service boundaries.
MCU C ownership/guard regressions and ARM compilation remain in CI.

These are software and register-model checks. RC4's coordinate update has not
been exercised by us on the physical printer.
