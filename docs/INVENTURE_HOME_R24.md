# Inventure Home R24 RC5 — reuse known XY before Z

The operator confirmed working X/Y/Z after the RC4 field-test instructions.
[The accepted-source record](INVENTURE_ACCEPTED_HOME_R24_RC4_2026-11-26.md)
pins that release and its helper/engine hashes. RC5 preserves those homing,
release, dynamic-origin, 5 mm park and Z measurement implementations.

Before **Home Z and Home XYZ**, reuse each valid, continuously held XY
reference. With both known, do no XY homing; with X known, home only Y;
with Y known, home only X; with neither known, home Y then X. The old
unconditional XY pass in Home XYZ is removed. Explicit Home X/Y/XY still
remeasure their requested axes when the operator wants a fresh reference.
Progress reports `xy_homed_axes` and `xy_reused_axes` from the actual decision.

Direct Fluidd Home macros and G28 Z/XYZ start without the initial homing
review dialog. The physical OLED menu retains its own explicit confirmation.
Tray, idle, ownership, fault, temperature and contact checks remain active.

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
| Home All / XYZ | Reuse each valid XY reference, home only missing axes, then original T0/Z sequence |
| Restart, disabled drive or fault | Discard references and frame translations |

Fluidd macros, G28 and the physical OLED use the same motion owner. Direct
Home Z/XYZ commands omit the initial Fluidd review; the OLED menu keeps its own
confirmation. Internal PE12 homing
calls the core Z handler directly. The existing bounded initial 10 mm retreat
remains before XY where required; the full bottom reference remains over the pad.

## Tray check

The existing host `_z_home_inputs` reads fresh inputs and requires the tray
signal **PC13=0** before Z preparation starts. During native Z measurement,
the MCU's local tray guard supervises PC13 and aborts guarded Z movement if
that signal opens. The guard and its ABI remain unchanged in RC5.

This checks the tray sensor/interlock. It does not independently inspect
mechanical alignment or every latch. Removing the Fluidd review does not
replace or bypass the tray check. Installed-route tests verify refusal with
an absent tray; existing host and MCU tests also exercise loss during Z.

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
git switch --detach inventure-home-r24-rc5 &&
python3 install-new-host.py --repair-home-procedures --serial /dev/ttyS5 --baud 750000 --apply
```

The installer recognizes the authenticated RC1, RC2/RC3 and RC4 runtimes. Its R24.4
revision retains RC3's original-backup verification fix and the complete apply
regression. Only the operator helper and runtime manifest change when upgrading
RC4. The indexed original R38.6 backup, effective configuration, offline startup
and actual service/PID/path/Ready/profile are verified inside the existing
activation/rollback transaction. Saved P4 baud, PID, JSON, split configurations,
Fluidd groups and OLED graphics/menu files are preserved.

## Readback

```gcode
INVENTURE_VERSION
INVENTURE_HOME_Z_STATUS
```

Expected MCU: `RC8-CONTROL-R7-HOME-R24-OLED`.
Home release: `RC8-HOME-XY-RELEASE-ZERO-R24.3-XY5MM`.
Profile: `operator-home-r24`; `xy_reference_frame`: `measured_release`.
The display's independent R23.5 menu/graphics release label stays unchanged.

After restart, references and translations are empty. After successful X/Y,
`xy_references` records each newly measured `release_mm` / `release_steps`,
configured `reference_native_mm`, `post_home_backoff_mm: 5.0` and held enable.
`xy_frame_release_mm` is this run's coordinate translation, not a stored machine
correction. `configured_pad_xy_mm` retains the original configuration;
`working_pad_xy_mm` shows that same physical pad in the current release frame.

## Verification and limits

Installed-config simulation exercises 456 completed Fluidd/OLED/G28 requests
and 112 refusals across two independent switch-stroke and stop-overrun profiles.
It covers X→Y→Z, Y→X→Z, missing axes, repeated Z, explicit rehoming of X or Y
with different measured release values, partial/already-known XY before
Home XYZ, repeated Home All and lost-drive invalidation. Direct commands have
no initial review; the OLED menu keeps its confirmation. Missing-tray and
invalid explicit-ACK requests still refuse movement.
It runs the installed adapter, original factory interpreter and Z engine.

Checks include the measured-edge origin, exact reference+5 park, unchanged
other-axis position, no accumulated/stale correction, continuous enable,
unchanged heater targets and effective configuration, and the same physical Z
pad before/during/after the handoff. The actual pinned CoreXY bounds checker
accepts the translated physical boundary and rejects one further step.
Selected input failure, absent/zero release, excessive overrun, failed park and
invalid held-entry receipts discard references and prevent T0/Z certification.

Authenticated upgrades from RC1, RC3 and RC4 and exact rollback are tested.
An accepted-source invariant limits helper changes to operator routing and
checks the other helper methods and all three engine files unchanged. The
complete installer --apply regression includes real backup auditing, runtime
and configuration staging, offline Klipper startup, systemd parsing, atomic
activation and Fluidd setup with simulated HTTP/process/service boundaries.
MCU C ownership/guard regressions and ARM compilation remain in CI.

These are software and register-model checks of RC5's new routing/review
behavior. The operator's physical RC4 acceptance is recorded separately; it
does not claim a physical test of RC5.
