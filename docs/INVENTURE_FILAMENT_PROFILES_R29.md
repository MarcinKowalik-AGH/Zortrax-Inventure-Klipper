# Inventure R29 cassette load, unload and manual feed

Host R29 adds persistent load and unload settings for the left cassette (T0) and the right cassette (T1). It uses the Inventure R13 service MCU. The operator controls follow the M300 load/unload temperature, filament speed and manual purge controls. Inventure uses its own cassette sensors, selector, feeder and motion sequence. [M300 reference implementation](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/0c8148cef1f9786c23f061d8c4b977245cddcd86/M300_HOST/payload/m300_filament.py) · [Inventure cassette sequence](FACTORY_LOAD_2026-11-10.md)

## Install on the Orange Pi

With Klipper idle, run these commands in the shell.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-filament-profiles-r29 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-filament-profiles-r29" refs/tags/inventure-filament-profiles-r29 &&
python3 "$HOME/inventure-filament-profiles-r29/install-orange-pi.py" --configure-filament
```

If the worktree already exists, run the installer command in the last line. In Fluidd, run `INVENTURE_VERSION` and `ZORTRAX_FILAMENT_STATUS`. The expected host revision is `RC8-CONTROL-R7-FILAMENT-PROFILES-R29`. [R28 jam recovery controls](INVENTURE_FILAMENT_JAM_R28.md)

## Save settings

Use material-appropriate temperatures. The following values illustrate two independent settings records.

```gcode
ZORTRAX_FILAMENT_SAVE TOOL=T0 LOAD_TEMP=235 LOAD_SPEED=1.6 UNLOAD_TEMP=245 UNLOAD_SPEED=1.0
ZORTRAX_FILAMENT_SAVE TOOL=T1 LOAD_TEMP=230 LOAD_SPEED=1.4 UNLOAD_TEMP=235 UNLOAD_SPEED=0.8
ZORTRAX_FILAMENT_STATUS
```

| Setting | Unit | Applied to |
|---|---|---|
| `LOAD_TEMP` | °C | Automatic load and a separate forward manual move |
| `UNLOAD_TEMP` | °C | Automatic unload and a separate reverse manual move |
| `LOAD_SPEED` | mm/s of filament | Straight feed, forward manual portions and the feed component of coordinated purge strokes |
| `UNLOAD_SPEED` | mm/s of filament | U1/U2 retraction, the U3 retract and reverse manual portions |

A save takes effect for subsequent operations and survives a Klipper restart. Saving a single field preserves the other settings. For example, `ZORTRAX_FILAMENT_SAVE TOOL=T1 LOAD_TEMP=AUTO` restores the configured or material-specific temperature for T1 loading. The file `inventure_filament_settings.json` lives beside the active `printer.cfg`. The controller snapshots the settings when an operation starts.

For one run, `TEMP` and `SPEED` override the saved values. If a setting is absent, the existing tool configuration and OEM material table supply the defaults. `MATERIAL=Z-SUPPORT`, for example, selects the applicable factory temperature and unload lengths. A stored or explicitly specified temperature takes precedence. Service temperatures start at 180 °C and extend to the lower of the selected nozzle and MCU limits (up to 310 °C).

The speed in mm/s applies to filament at the extruder. The selector, travel, wipe and cassette feed motor keep their original motion settings. In a coupled X/E purge the factory X rate is the upper bound, equivalent to approximately 3.00 mm/s of filament with stock geometry. `ZORTRAX_FILAMENT_STATUS` shows the effective straight and oscillating rates. Full automatic operations also check the 595 s per-segment limit before motion. With the stock 250 mm long feed this requires a load speed of at least 0.421 mm/s.

## Operate the cassettes

Start one procedure in Fluidd. Choose the matching nozzle and allow the operation to reach its prompt before sending the next command.

| Intent | Example |
|---|---|
| Load the left cassette | `ZORTRAX_LOAD TOOL=T0` |
| Unload the right cassette | `ZORTRAX_UNLOAD TOOL=T1` |
| Override this run only | `ZORTRAX_LOAD TOOL=T1 TEMP=235 SPEED=1.5` |
| Run the full sequence and finish automatically | `ZORTRAX_UNLOAD TOOL=T0 INTERACTIVE=0` |
| Feed or retract at the final prompt | `ZORTRAX_FEED_MORE MM=5` or `ZORTRAX_RETRACT_MORE MM=5` |
| End manual feeding and shut down service heat | `ZORTRAX_FILAMENT_FINISH` |
| Feed an already loaded nozzle in a new session | `ZORTRAX_FILAMENT_JOG TOOL=T1 DIR=LOAD MM=5` |

Each new cassette procedure homes Z, Y and X, parks over the waste bin, heats and selects the requested nozzle. Normal load follows the original short or long branch. Normal unload retracts and checks the inlet sensor. Only a successful automatic operation enters the manual prompt. In the R7 cassette menu the OLED offers **Feed 5 mm**, **Retract 5 mm** and **Finish / heaters off**. The same controls are available in Fluidd.

At the prompt the nozzle stays over the waste bin at that operation's temperature. A portion can be 0.1–20 mm (default 5 mm). Its direction selects the saved speed, and an optional `SPEED` overrides one portion. Each accepted move opens a fresh 120 s prompt within the existing overall procedure timeout. Portions reuse the tool, reference and heater session. Finishing explicitly or allowing the prompt to expire wipes the nozzle if it fed a manual portion, parks above the bin and shuts down service heating. `INVENTURE_STOP` cancels an active operation.

After a session ends, `ZORTRAX_FILAMENT_JOG` heats, selects and moves the requested nozzle in a new session, then finishes automatically. Use a full `ZORTRAX_LOAD` to thread an empty guide tube from the cassette inlet. A manual length describes extruder travel. Physical flow at the nozzle is confirmed by the operator.

## Verification

Automated tests exercise both tool directions, persisted settings after reload, one-run overrides, single-use operator replies, OLED actions, timeout, stop, a completed unload followed by manual transport and standalone moves. Real Klipper configuration loading is checked for both a fresh installation and the field configuration. The first physical R29 cassette trial is pending.
