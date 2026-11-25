# Inventure R23.5: persistent startup repair, shared Home and configuration audit

**Installer superseded by RC7:** RC6 quoted `WorkingDirectory` incorrectly.
Use [the corrected RC7 installer](INVENTURE_R23_STARTUP_RC7.md); the configuration
layout and UI/Home safety version described below remain R23.5.

The supplied log shows a working `klipper-inventure-r38.6` runtime followed,
after a restart, by `klipper-inventure-candidate`. That second runtime stops
while configuring a heater fan: the `inventure_r5` pin provider is absent.
The supplied systemd output has no visible command in
`50-inventure-release.conf`; the older `inventure.conf` selects the candidate
working directory and environment file. This explains the repeated failed
startup before any MCU connection. It does not establish the cause of the
reported earlier Home XYZ collision; that event is not preserved completely.

## Startup recovery

The installer now publishes its service override using a complete temporary
file followed by an atomic rename and readback. An interrupted copy cannot
truncate the active override. The command and working directory explicitly
select the verified runtime, without `$KLIPPER_ARGS` choosing an older tree.
An existing empty override is backed up and can be repaired. A Ready process
must report the requested MCU module path, native Home module path, Home safety
version, OLED UI version, launcher, PID and configuration path.

`--recover-startup` permits repair from only the exact missing Inventure
pin/sensor registration errors. It refuses unrelated configuration errors,
MCU connection timeouts and shutdowns. Offline configuration validation and
rejection of automatic delayed G-code precede activation. After activation,
fresh idle MCU status and normal MCU compatibility must pass inside the
rollback transaction. The flag uses the ordinary strict path when already
Ready. It neither flashes the MCU nor automatically resets a safety shutdown.

Run as the Klipper user, with the printer idle and heater targets off:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-r23-startup-rc7 &&
python3 install-new-host.py --recover-startup --restore-field-profile --serial /dev/ttyS5 --baud 750000 --apply
```

Check in Fluidd:

```gcode
INVENTURE_VERSION
INVENTURE_ZORTRAX OP=STATUS
INVENTURE_HOME_Z_STATUS
```

The UI and Home safety version must be **RC8-CONFIG-AUDIT-R23.5**. The runtime
remains the authenticated R38.6 base plus its versioned restorations. Existing
MCU **RC8-CONTROL-R7-HOMING-R23-OLED** and InventureBoot 1.7 stay installed.

## Configuration roles

| File | Owner |
|---|---|
| `printer.cfg` | Explicit includes, main heater headers and the original SAVE_CONFIG tail |
| `inventure_base.cfg` | MCU registration, machine limits, axes, extruder geometry, physical OLED and display status |
| `inventure_temperature_sensors.cfg` | Existing sensor conversion table |
| `inventure_heater_control.cfg` | Existing per-zone control defaults and tuning |
| `inventure_heaters.cfg` | Four heater outputs, sensor channels, limits and PWM timing |
| `inventure_heater_monitors.cfg` | Existing heater verification settings |
| `inventure_cooling.cfg` | Native hotend/chamber cooling policy |
| `inventure_chamber.cfg` | Two-zone chamber service |
| `inventure_services.cfg` | Native Z, pause/resume and virtual SD |
| `inventure_operations.cfg` | The supplied active procedure and operator macros |
| `klipper_calibration.cfg` | Existing PID controls |
| `inventure_display.cfg` | Existing panel preferences |

The normalization compares every effective setting before accepting a move,
is idempotent and retains SAVE_CONFIG and calibration bytes. It preserves the
supplied 92 active macros. Hidden but usable diagnostic controls are retained.
An old standalone `fluidd.cfg`, old config copies and Fluidd-group snapshots
are outside the active include tree; the export archives them separately.
Moonraker's active configuration and access policy are retained. The installer
backs up existing files and does not delete inactive operator backups.

## Procedure verification

OLED Home now executes the same installed `INVENTURE_HOME_*` macros as Fluidd.
The native homing sequence, T0 selection/wipe program, coordinates, speeds,
thermal targets and contact arithmetic retain their prior implementation.
An additional fresh physical PB11/PC15 check follows native Home X/Y. Before
SERVICE/T0 selection, both XY switches must confirm the native origin.
A failed check invalidates that axis instead of leaving a usable false zero.
The log records `INVENTURE_HOME_AUDIT` begin/done stages for later diagnosis.

The full-cycle test now installs the supplied field configuration. It covers
36 repeated native Home cycles, the original nine refusal scenarios, both
Home-to-leveling origins, 24 paired OLED/Fluidd procedure cases (48 starts),
saved T0/T1 filament parameters, cancellation, stale buttons and printing
interlocks. Four added fault cases simulate a completed G28 with an unconfirmed
physical X/Y switch; none may enter T0 selection or start heating.

The startup/PID check now uses the actual authenticated deployed overlays,
rather than the historical raw host files. All four PID zones survive
SAVE_CONFIG and reload. Recovery tests cover stale MCU rejection, wrong module
paths and interruption before an override is published. Prior owner, thermal,
filament, contact, door, transport, timeout and compiled MCU checks remain.

These are software tests using simulated physical interfaces. They cannot
certify mechanical clearance, switch polarity/wiring, nozzle condition or the
cause of the reported collision. The expanded Home route and new switch guard
still need controlled physical acceptance after Ready and read-only input
verification. This release does not claim that every printer operation is
physically accepted or can never damage hardware.
