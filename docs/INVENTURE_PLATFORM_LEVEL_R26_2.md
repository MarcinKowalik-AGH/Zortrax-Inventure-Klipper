# Five-point platform leveling on the Zortrax Inventure

Host R26.2 with MCU R13 uses the original tray's five conductive pads to guide adjustment of its three knobs. Start **Maintenance → Platform leveling** on the OLED or run **`ZORTRAX_LEVEL`** in Fluidd. The printer selects T0 mechanically before the first measurement. The only setup decisions for the operator concern the tray, its knobs and the separate global door setting.

## Install R26.2

On an existing Orange Pi installation, with the printer idle, run these commands in the shell.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-platform-level-r26.2 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-platform-level-r26.2" refs/tags/inventure-platform-level-r26.2 &&
python3 "$HOME/inventure-platform-level-r26.2/install-orange-pi.py" --configure-leveling
```

If the tagged worktree already exists, rerun the final installer command. Once Klipper is ready, enter `INVENTURE_VERSION` and `ZORTRAX_LEVEL_STATUS` in Fluidd. Expect host `RC8-CONTROL-R7-PLATFORM-LEVEL-R26.2`, MCU `RC8-CONTROL-R7-PAD-THERMAL-R13`, and `phase: idle` on startup. This is a host update for an existing R13 controller. [Tagged release](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-platform-level-r26.2)

## Run the procedure

1. Fit the original Inventure tray and ensure all five conductive pads are clean. Use the machine on which a full T0/T1 central pad calibration was saved. The host reads the measured T0 center and nozzle height data from `inventure_nozzle_pad.json` beside the active `printer.cfg`. Set native Klipper heater targets to zero before the service procedure takes control.
2. In Fluidd, run `ZORTRAX_LEVEL` without parameters. The direct Klipper equivalent is `ZORTRAX_PLATFORM_LEVEL ACTION=START`. On the OLED, choose **Maintenance → Platform leveling → Yes / Start**. The host establishes the lower Z reference on PE12, sets the initial clearance, homes Y then X, and positions the nozzles over the waste bin.
3. When prompted, tighten the three knobs under the platform and acknowledge with `ZORTRAX_LEVEL_NEXT` or **Knobs tightened / next**. Heating and the mechanical T0 selector stroke follow. The printer measures the center with T0 and then the four corners.
4. If an adjustment is needed, the printer parks over the waste bin and identifies the knob, direction and number of turns. Adjust that knob and choose **Adjusted / next** or run `ZORTRAX_LEVEL_NEXT`. Confirm each instruction separately. The printer remeasures the affected pair and returns to the center for a final reading.
5. After heater cutoff, return to the bottom endstop, parking and the confirmed end of the MCU session, `ZORTRAX_LEVEL_STATUS` reports `phase: candidate`. Read the platform depth and center deviation. Choose **Review / save result** on the OLED or run `ZORTRAX_LEVEL_SAVE` to save an accepted result. An out-of-tolerance center is flagged as a deformed tray. The normal save action requires an in-tolerance result.

At any stage, read progress with `ZORTRAX_LEVEL_STATUS` or stop with `ZORTRAX_LEVEL_CANCEL`. A saved report appears as `inventure_platform_level.json` beside `printer.cfg`. Turning the knobs changes the tray mechanically. The report records `applied_to_print: false`, while print homing establishes its own Z reference. Nozzle offsets remain in the separate central pad calibration file.

## Heater and selector sequence

| Phase | T0 target | T1 target | Physical action |
|---|---:|---:|---|
| Initial, verified T0 | 200 °C | Off | Wait above the T0 waste-bin position, then press the T0 selector. |
| Initial, verified T1 | 200 °C | 200 °C | Wait above the T1 waste-bin position, then press the T0 selector. |
| Initial, selector state unknown | 200 °C | 200 °C | Wait above the overlap of the two configured waste-bin purge paths, then press the T0 selector. |
| Sampling | 200 °C | Off | T0 probes every pad. |
| End of run | Off | Off | The table returns to PE12 and the carriage parks over the bin. |

The host uses the selector state only when the native tool module is enabled, bound, and records a known selector position. After a restart or on a service-only configuration, the state may be `UNKNOWN`. In that case the host uses clearance for the lower of the two nozzles. If the two configured bin paths have no common parking position, it rejects the start before moving. The common position comes from the purge paths in the machine configuration. It is a computed location, with hardware verification on the user's printer still pending.

The firmware r0323 heater branch uses 200 °C and a ready threshold of 199 °C. Its selection routine approaches the selector at F8000, presses it at F250 and returns over the waste bin. R26.2 waits for the active service heaters to reach at least 199 °C and executes the T0 selector stroke for every starting state. After dual preheating, ending that MCU session releases the drivers, so the host establishes fresh position references for T0-only probing. The bed and chamber receive no leveling heat target.

## Tray geometry and contact sequence

Five distinct pads require at least six visits because the center serves as both the initial reference and the final check. Repeated corner pairs after adjustments add visits. The host translates the OEM geometry to carriage coordinates using the saved T0 center as its origin.

| Visit | Pad ID | Tray position | OEM X, Y (mm) | Offset from measured center (mm) |
|---|---:|---|---|---|
| 1 | 2 | Center reference | 67.5, 65.5 | 0, 0 |
| 2 | 0 | Front right | 127.5, 13.0 | +60, −52.5 |
| 3 | 1 | Front left | 7.5, 13.0 | −60, −52.5 |
| 4 | 4 | Rear left | 7.5, 118.0 | −60, +52.5 |
| 5 | 3 | Rear right | 127.5, 118.0 | +60, +52.5 |
| Last | 2 | Center deviation | 67.5, 65.5 | 0, 0 |

The OEM starting position for knob preparation is logical Z40. The initial center reference starts at logical Z2 and searches up to 10 mm. Subsequent probes start from a 3 mm travel clearance relative to the center release and use a 15 mm search window. The measurement engine finds the PC14 contact and release edges. The center reference requires 101 consecutive cycles without a better release position. The later readings require 151. R13 caps guarded XY travel at 20,000 steps/s, around 31.25 mm/s on a single axis. Actual travel time can exceed the stock firmware's time.

The reference for adjustment is the highest of the four original corner readings. It stays fixed while the operator adjusts the rear left and rear right knobs independently, followed by the shared front knob if needed. Each correction identifies the direction and rounds the required turn up to the next quarter turn.

| Check | Inclusive accepted range at 802 Z steps/mm |
|---|---|
| Individual rear corner relative to the fixed reference | −160 to +80 steps |
| Mean of the two front deviations, truncated toward zero | −159 to +79 steps |
| Final center deviation from the mean of the latest four corner deviations | −401 to +401 steps (±0.5 mm) |

## Global door setting

While the printer is idle, open **Settings → Door lock** on the OLED and choose **Enable door lock** or **Disable door lock**. The separate Fluidd macro `ZORTRAX_DOOR_LOCK` displays the current state and offers both settings. For a read-only console query, use `INVENTURE_DOOR_POLICY` and check `enabled`. R13 restores the default `false` after an MCU restart.

| Setting | During heating and motion | At a knob prompt |
|---|---|---|
| Off | The front door and lid may remain open. | Adjust the knobs and continue. |
| On | Both covers must be closed. Opening either during active motion stops the procedure. | The printer parks and confirms that the motors, heat and lock drives are off. Open the cover to adjust the knobs, leave the selector in place, then close both covers before continuing. |

R13 monitors the door and lid switches during an active service session. It has no mechanical feedback for the lock bolts, and the service session keeps the lock coils off. After releasing the motors for a screw adjustment, the host rehomes, checks the PE12 reference against the original datum and resumes the relevant pair. A change exceeding 79 Z steps (about 0.10 mm) stops the comparison.

## Verification and provenance

The recorded successful R23 and R25 hardware trials exercised nozzle calibration on the central pad. The acceptance record for a complete R26.2 **five-point platform leveling** run on the physical printer is still open. Software coverage includes known and unknown initial selector states, pad order, screw instructions, global door behavior, stale confirmations and saving the report. [Full central pad trials](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/blob/inventure-platform-level-r26.2/docs/acceptance/INVENTURE_PAD_R23_2026-11-19.md) · [Cold center trials](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/blob/inventure-platform-level-r26.2/docs/acceptance/INVENTURE_PAD_COLD_XY_2026-11-19.md)

The Inventure listing is from firmware 1.6.1 r0323, entry `0x0801E094`. Pad coordinates come from `0x0801DC20`, the center reference from `0x0801DD34`, the point probes from `0x0801DEA8`, and the original corner reference from `0x0801DF60`. Heater selection is at `0x0801E11E–0x0801E164`, with standby restoration at `0x0801E52C–0x0801E542`. The listing places the initial knob prompt before heating. The M300 project provides the English OLED/Fluidd interaction pattern. All pad positions, tool changes, temperatures and tolerances above belong to Inventure. [Archived Inventure firmware audit and listing references](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/blob/inventure-platform-level-r26.2/docs/INVENTURE_PLATFORM_LEVEL_R26.md) · [Zortrax Inventure platform leveling guide](https://support.zortrax.com/inventure-platform-leveling/) · [M300 Classic leveling reference](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/5919602d0b24967c98cd07d39e1b88b41717ff89/docs/en/LEVELING.md)
