# Inventure R30 — Fluidd controls and M300 comparison

R30 installs the **ZORTRAX INVENTURE** and **Klipper** categories on the active Orange Pi printer. Machine controls use `INVENTURE_*`. The R29 cassette profiles, R28 recovery, five-point leveling and central-pad measurement remain the underlying procedures. The controller for this release is `RC8-CONTROL-R7-PAD-THERMAL-R13`. The host installer does not flash it.

## Installation and first check

Run this command sequence on the idle Orange Pi.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-fluidd-r30 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-fluidd-r30" refs/tags/inventure-fluidd-r30 &&
python3 "$HOME/inventure-fluidd-r30/install-orange-pi.py" --configure-fluidd
```

If that worktree already exists, run the last line. Add `--check` to preview the configuration paths without changing the printer. The installer reads the active Klipper checkout, follows includes, backs up changed configuration files, updates host modules, restarts Klipper, and waits for Moonraker to see the new macros. Refresh the browser after `GROUPS_INSTALLED` or `ALREADY_GROUPED`. The `groups` lists in that output show exactly what the running printer exposes. [Installer](../install-orange-pi.py) · [Fluidd group installer](../setup-fluidd.py)

These Fluidd console commands read the installed state.

```gcode
INVENTURE_VERSION
INVENTURE_CASSETTE_STATUS
INVENTURE_LEVEL_PROGRESS
INVENTURE_PAD_CALIBRATE ACTION=STATUS
PID_STATUS
```

The expected host is `RC8-CONTROL-R7-FLUIDD-R30`. The group editor retains existing category IDs, operator visibility, custom button settings and unrelated macros. New secondary diagnostics and stage-specific recovery replies are initially hidden; recovery also exposes the applicable replies in its active prompt. The installer recognises published definitions in nested includes and the form saved by Klipper. Custom legacy definitions remain in the user's files. Historical commands such as `ZORTRAX_LOAD` still work through console aliases when the corresponding controls are installed. Bench macros are opt-in. [Configuration templates](../config) · [Legacy command map](../orange_pi/inventure_fluidd.py)

If Klipper updated and group installation was interrupted, repeat the group operation from the existing worktree.

```bash
python3 "$HOME/inventure-fluidd-r30/setup-fluidd.py" --apply --wait 60
```

The group installer prints the exact JSON backup path. Passing that path to `setup-fluidd.py --rollback PATH --apply` restores the saved category state if it still matches this installation. A site that authenticates loopback requests can supply its Moonraker API key through the `MOONRAKER_API_KEY` environment variable. [Moonraker database API](https://moonraker.readthedocs.io/en/latest/external_api/database/)

## All 36 printer buttons in the M300 reference

The left column omits the common M300 prefix `ZORTRAX_`; the middle column omits the Inventure prefix `INVENTURE_`. The input was the operator's M300 backup `20261121-095908.zip`, SHA-256 `e1403a92ca62c42e29bc0bb666faf9e97ccb1805b0ec346e5dcbfddef276f97a`, and both Fluidd screenshots. The M300 code reference is the Orange Pi branch at `0c8148c`. [M300 operator macros](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/0c8148cef1f9786c23f061d8c4b977245cddcd86/M300_HOST/payload/m300_operations.cfg) · [M300 grouping implementation](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/0c8148cef1f9786c23f061d8c4b977245cddcd86/M300_HOST/setup_fluidd.py)

| M300 button | Inventure button | Behaviour and limits |
|---|---|---|
| BUZZER | — | The r0323 audit leaves the audible device and its pin unconfirmed. Hardware identification is open. |
| CHANGE | CHANGE, JAM_EXCHANGE | Starts exchange for the active print tool through the existing recovery owner. Cassette and external spool use their own sequences. |
| DIAGNOSTICS | DIAGNOSTICS | Versions, panel I/O and cooling in one request. |
| FAN_LOWER, FAN_UPPER | FAN_CHANNEL | The Inventure uses six independent channels. The R13 application calls F0 head/front, F1 model/bottom, F2 left chamber, F3 right chamber, F4 rear and F5 transfer. These are R13 channel IDs. Four channels have tachometers. |
| FAN_TEST_START, FAN_TEST_END, FANS | FAN_TEST_START, FAN_TEST_END, FANS | Shared per-channel chooser, test cancellation and live cooling readings. Manual ON uses the existing cold, idle diagnostic profile and explicit confirmation. The native motion profile can read status; the inherited bench backend rejects ON with native actuators enabled. |
| FULL_PLATFORM_PREHEAT, HEAT_PLATFORM | — | The stock tray has no independent heater. Inventure has two chamber zones. Native chamber preheat and its material-dependent stability checks require separate commissioning. |
| HEAT | HEAT | T0 or T1, optional TARGET, existing service heater and OLED/Fluidd confirmation. |
| HELP | HELP | Opens the existing Inventure operator help. |
| HOME_ALL, HOME_X, HOME_XY, HOME_Y, HOME_Z | HOME_ALL, HOME_X, HOME_XY, HOME_Y, HOME_Z | Uses the Inventure reference routines in the native-Z profile. A profile that loads the separate P03 motion module retains its own native HOME_XY command. |
| INFORMATION, LANGUAGE, MAIN_MENU, SETTINGS | INFORMATION, LANGUAGE, MAIN_MENU, SETTINGS | Open the matching page on the physical OLED with the OEM menu enabled. The supported operator language is English. |
| LED_BRIGHTNESS, LIGHT | LIGHT | MASK=0..7 selects the three binary RGB outputs. Continuous chamber-light brightness requires a suitable MCU output and has no R13 equivalent. |
| LEVEL | LEVEL, LEVEL_NEXT, LEVEL_PROGRESS, LEVEL_SAVE, LEVEL_STOP | T0, five conductive tray pads, three adjustment knobs and an operator-reviewed result. The central-pad inter-nozzle calibration is a separate PAD_CALIBRATE command. |
| LOAD, UNLOAD | LOAD, UNLOAD | Independent cassette temperatures and speeds for each nozzle, plus FEED_MORE, RETRACT_MORE and FILAMENT_FINISH at the waste bin. |
| MODELS_SORT | MODELS_SORT | Persistent date ascending/descending, name or size for host files. MODELS opens the browser. |
| OLED_BRIGHTNESS | OLED_BRIGHTNESS | LEVEL=1..16 sets the display controller's awake brightness and persists across host restarts. |
| OLED_TIMERS, SLEEP_MODE | OLED_TIMERS, SLEEP_MODE | IDLE and NOTICE are seconds and ENABLED is 0/1. The values follow the Inventure display's own sleep sequence. |
| PLATFORM_DOWN, PLATFORM_UP | PLATFORM_DOWN, PLATFORM_UP | Default 1 mm at 1 mm/s, configurable within 0.1..5 mm and mm/s. Displays direction and distance, verifies the current Z reference, seated tray and direction-specific input, then synchronises native position. Up decreases native nozzle-to-tray Z. |
| PROCEDURE | PROCEDURES | Lists the services and current menu state. Shared maintenance and platform prompts accept one reply from Fluidd or the physical OLED. |
| REPRINT | — | MODELS browses host files. The M300 completed-job snapshot, bed-clear confirmation and replay are open for Inventure. |
| SCREEN, STATUS | SCREEN, PRINTER_STATUS | Wakes the OLED and shows menu state, or reads full printer status. |

Additional machine controls cover selecting, wiping and purging either nozzle, the global door setting, cassette options and stage-specific jam recovery. `INVENTURE_FILAMENT_SAVE TOOL=T1 LOAD_TEMP=235 LOAD_SPEED=1.6` saves two fields of the T1 profile. Speeds are filament mm/s. `INVENTURE_LOAD TOOL=T0` and `INVENTURE_UNLOAD TOOL=T1` run the corresponding cassette procedure. With the hot tool waiting over the bin, `INVENTURE_FEED_MORE MM=5` uses the current session. [Material controls](../config/INVENTURE_MATERIAL.cfg) · [R29 procedure and units](INVENTURE_FILAMENT_PROFILES_R29.md)

## All nine Klipper buttons

| M300 button | Inventure R30 behaviour |
|---|---|
| CANCEL_PRINT | Wraps the existing Klipper cancel handler and turns off native heaters. A site-provided cancel macro is preserved. |
| END_PRINT | Waits for buffered native motion, disables native heaters and turns off the configured part fan. A console invocation during a running or paused print is rejected. The native file validator still enforces its own command vocabulary. Physical park, cooldown and end-of-job acceptance remain separate. |
| OLED_TIMERS | Reads or saves the physical Inventure display timers. Available with the OLED module. |
| PID_HOTEND | TOOL=T0 or T1 resolves the configured heater by its physical R5 output **and** matching ADC channel. Native Klipper PID tuning requires an idle printer and the armed NATIVE controller. Use a target appropriate to the hotend and material. |
| PID_STATUS | Reports configured heaters, control type, PID coefficients and pending Klipper settings. |
| PID_SAVE | Requires a completed pending PID result and an idle printer. Executes Klipper SAVE_CONFIG, which saves **all** pending changes and restarts the host. Review PID_STATUS first. |
| BED_MESH | Appears with an actual site-configured bed_mesh. Supports status, clear and loading an existing profile. R30 never issues a rectangular probing pattern on the plastic tray. |
| PID_BED | Appears only for an independently configured heater_bed. The stock R13 field configuration has no such heater. |
| START_PRINT | Appears with enabled P15, qualified start and qualified P14 integration. The console launcher requires the checked file's current single-use TOKEN and ACK=START_CHECKED_JOB. The recorded service profile has six standard buttons and no production launcher. A site-defined start macro is preserved. |

The M300 Orca `START_PRINT` sequence does concurrent heating, homing, mesh selection and priming. The Inventure P15 launcher consumes a verified job manifest. An Orca start block that invokes the M300 macro cannot be transferred to the current Inventure service profile. [Klipper controls](../config/INVENTURE_KLIPPER.cfg) · [Inventure preflight](../host/inventure_preflight.py) · [Klipper PID calibration](https://www.klipper3d.org/Config_checks.html#calibrate-pid-settings)

## Validation

| Check | Result |
|---|---|
| Host regression, installers and native recovery | 750 test methods passed, including 11 dashboard regressions. Tests exercise both nozzles, preflight, visibility and custom overrides, the PID save key casing, jog directions, bounds and position synchronisation. |
| Actual pinned Klipper configuration loader | Loaded the corrected historical field configuration and a fresh installation with includes. Each had both 310 °C native nozzle limits, eight shared ADC readers and six standard Fluidd buttons. A repeated installation produced no configuration changes. |
| UI migration | The field fixture produced 79 Inventure macro sections, the fresh fixture with an existing full diagnostics include produced 99. Both had 54 visible machine buttons plus 6 visible Klipper buttons with empty Fluidd preferences. Existing user visibility can change the count. |
| Optional profile and old scripts | Verified conditional installation for an owner-configured mesh, bed heater and qualified preflight, retention of user END_PRINT, native cancel delegation, and old console aliases without duplicate stock buttons. |
| Moonraker and OLED | Tested the HTTP dialog with a local Moonraker API test server, readback after write, preservation of unrelated preferences, repeat install, backup, rollback and concurrent edit detection. Both OLED transport variants processed brightness changes. Model benchmark covered 504 screen transitions. |
| R13 MCU | Rebuilt using GNU Arm 14.2.Rel1 and ran the eight register-mock programs. The 103,488-byte InventureBoot payload container matched SHA-256 `7bb8a9334d2aade7eb38f4d949d73f2e10870cde4b12e3c8803ba7cdff0b8f35`. |

The recorded earlier printer trials include two full central-pad calibrations, individual cold centers and a saved five-point platform run with depth **138.349 mm** and center deviation **+0.031 mm**. The R30 dashboard, physical OLED brightness changes, PID tuning and manual platform movement still need an on-printer trial. The host tests and MCU register tests do not measure motion, light output or thermal performance on the user's machine. [Pad acceptance](acceptance/INVENTURE_PAD_R23_2026-11-19.md) · [Cold centers](acceptance/INVENTURE_PAD_COLD_XY_2026-11-19.md) · [Earlier Inventure hardware and firmware audit](INVENTURE_OEM_COVERAGE_R27_1_2026-11-19.md)

## Remaining work compared with M300 and the intended Inventure print profile

| Area | What remains to complete or physically accept |
|---|---|
| Native dual print | Qualify the shared E drive, T0/T1 selector and physical tool ownership, the signs of the saved offsets in actual deposition, and a dual-material registration print. |
| Orca start and end | Qualify the full startup, wipe, prime, tool changes, end parking, chamber cooldown and first layer. Test normal EOF, operator cancel and fault separately. |
| Bed compensation | Choose and qualify Inventure-specific compensation using the conductive areas. Five-point screw leveling and nozzle offsets have separate roles. M300's 25-point coordinates belong to a different machine. |
| Thermal control | Tune both native nozzles and qualify the two chamber zones, material targets, stability and tool-PCB limits. A separate bed-heater PID applies only to modified hardware. |
| Fans and ventilation | Qualify all six channel policies through heat-up, layer cooling, pause and post-print ventilation, including tach failures and protective fallback. |
| Doors, lid, tray and bin | Propagate the user-selected global door policy through the older native P15/P16 path. Verify physical lock positions, tray/bin signals and pause/resume behaviour under a real job. |
| Filament recovery and RFID | Verify missing, broken and jammed paths during a native print for both cassette and external source, with flow confirmed before resuming. Complete field checks of cartridge identity, consumption, persistence and replacement. |
| Pause, power loss and file replay | Qualify park/return and extrusion coordinates after a real pause. Add durable completed-job replay, checkpoint restoration and operator confirmation after a restart. |
| Media and front panel | Verify native access to the printer's physical SD and classic ZCODE playback. Identify the acoustic device, qualify light modulation if supported, and verify the complete power/sleep policy. |
| Field UI acceptance | Check category rendering after refreshing the user's Fluidd, old scripted commands, restored OLED settings, and one confirmed platform step in each direction from a valid native Z reference. |

The 85-requirement OEM audit records the wider port status, supporting evidence and outstanding physical tests. [OEM coverage register](INVENTURE_OEM_COVERAGE_R27_1_2026-11-19.md)
