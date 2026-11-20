# Inventure R27 filament recovery

R27 adds English filament recovery controls to Fluidd and the Inventure OLED. It runs with the R13 application for InventureBoot 1.7. The idle service test uses the same motion, heater, mechanical nozzle selector, cassette loader, cleaning pass and waste-bin coordinates as the existing factory procedures.

## Install on Orange Pi

While the printer is idle, run the following in the Orange Pi shell.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-filament-jam-r27 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-filament-jam-r27" refs/tags/inventure-filament-jam-r27 &&
python3 "$HOME/inventure-filament-jam-r27/install-orange-pi.py" --configure-jam
```

If that worktree already exists at the tag, rerun the installer. After Klipper is ready, run `INVENTURE_VERSION` and `ZORTRAX_JAM_STATUS` in Fluidd. Check for host `RC8-CONTROL-R7-FILAMENT-JAM-R27` and MCU `RC8-CONTROL-R7-PAD-THERMAL-R13`.

## Exercise a nozzle on an idle printer

When the filament tip is still inside the cassette and the guide tube is empty, use the ordinary **Load from cartridge** action. The idle jam test exercises recovery on an already loaded path. For a T0 load that ends at `l3_no_transport`, see the [left cassette measurements and cold transport check](acceptance/INVENTURE_T0_CASSETTE_OPTICS_2026-11-20.md).

Use a clear tray and an installed waste bin. Start with a known working filament path. The test homes the axes, parks over the bin, heats the requested nozzle, then mechanically selects it before retraction. Set the service temperature appropriate for the filament actually in that path. The example uses 235 °C. Run one test to completion before starting the next.

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_JAM_SERVICE_TEST TOOL=T0 SOURCE=CASSETTE TARGET=235
```

For an external spool on T1, substitute the following test, adjusting the target for its material.

```gcode
ZORTRAX_JAM_SERVICE_TEST TOOL=T1 SOURCE=EXTERNAL TARGET=235
```

The same test is available on the OLED under **Material → Filament recovery → Test recovery at idle**. Select the tool, source and temperature before pressing Start. Use `ZORTRAX_JAM_STATUS` for the compact `service_test` result, `INVENTURE_PROCEDURE ACTION=STATUS` for the full trace and `INVENTURE_PROCEDURE ACTION=STOP` to stop it. On success the selected nozzle returns to its waste bin and heating ends. In the final `factory.result`, `CASSETTE_R2` confirms that the MCU saw the two required transport signals. `CASSETTE_RELOAD_L1` and `CASSETTE_RELOAD_L4` identify the successful loader fallback. `EXTERNAL_CHECK_FLOW` asks the operator to inspect the nozzle output. The motion and sensor counts alone cannot verify that molten material left the nozzle.

## Recovery during a native print

Use a qualified Inventure native print configuration with physical T0/T1 selection, the shared extrusion stepper, its calibrated tool frame, the service envelope, a valid XYZ reference and a resumable virtual SD job. The `print_profile` field lists missing or inactive native print components. The November R26.3 hardware log has a heater-only extruder profile, a separate T1 heater and 138 mm native XY travel. Its supported first physical test is the idle service test above. Installing macros alone does not commission the mechanisms required to return to a model.

The confirmed material settings select the source independently for each tool. With no material selection available, use **Material → Filament recovery** on the OLED or select it explicitly in Fluidd before the print, for example:

```gcode
ZORTRAX_JAM_SOURCE TOOL=T0 SOURCE=CASSETTE
ZORTRAX_JAM_SOURCE TOOL=T1 SOURCE=EXTERNAL
ZORTRAX_JAM_STATUS
```

An explicit source selection survives Klipper restarts in `inventure_jam_sources.json` beside `printer.cfg`. A confirmed native material selection takes precedence. With missing or invalid settings the source is unknown until selected. A/B switch readings and an unreadable tag cannot distinguish an external spool from an unrecognized cassette. With no verified inventory, the host reports that material is missing and leaves the cause open. A verified matched tag and, when enabled, a fresh consumption ledger provide the original 5% threshold. The optional legacy material preflight has its own cassette RFID requirement.

| Condition | Print response |
| --- | --- |
| Active cassette extruder jam | Pause, save the current print state, retract 1 mm, increase clearance by 1 mm and move to the bin. R1 feeds up to 50 mm and needs 10 fresh movement edges, with the pulse series restarted after a gap longer than 2 seconds. R2 feeds up to another 50 mm and needs 3 new feeder starts. An unsuccessful R1 leaves the job paused. An unsuccessful R2 starts one cassette loading attempt. |
| Cassette feeder jam, missing material or empty cassette | Pause and display the reason for the active tool. Inspect the cassette, its seating or its filament path and use Retry when ready. |
| External spool runout | Pause and request filament. The external path uses the nozzle extrusion drive without the cassette feeder. Inspect actual flow over the bin and confirm it on the OLED or in Fluidd. |
| Confirmed recovery | Wipe the nozzle, leave the brush for the bin and return to the saved XY, then Z. Restore the extrusion reference and print speed before continuing the same job. |

`ZORTRAX_JAM_REPORT` reports a jam on the active nozzle of a running print. The automatic monitor distinguishes a cassette feeder fault from an exhausted transport budget using MCU feedback. R13 exposes executed forward and reverse steps and the current budget. Budget decisions in this implementation use MCU samples; stock r0323 checks the end of an E motion block. The port admits recovery against a live native print owner and its saved SD file. This replaces the stock progress-counter gate. The stock external path observes material presence. The bounded reverse/feed, wipe and visible-flow confirmation on an external spool are Klipper additions. A blocked external nozzle with filament still at the inlet can be reported manually.

For an active print, use `ZORTRAX_JAM_RETRY` after clearing the indicated path, `ZORTRAX_JAM_CONFIRM_FLOW` after an external-spool flow prompt, and `ZORTRAX_JAM_CANCEL` to stop the intervention. The OLED and Fluidd operate the same recovery session. The active prompt determines whether Retry, Confirm flow or Cancel is available. A stopped drive, changed tool, lost reference or changed print file invalidates the return to the model. Global door settings apply. For physical acceptance record the selected tool, source, recovery stages, observed nozzle output and whether extrusion resumed at the same point on the model.

[Original Inventure r0323 transport audit, section 11](https://drive.google.com/file/d/1mFJuBj8Dn8Mzxa0tJ5MFTogomOmaANOt/view)
