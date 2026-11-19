# R27.1 filament replacement

Install the host with `install-orange-pi.py --configure-jam`. MCU R13 is sufficient. An operator can select the source separately for each tool without RFID. Native material preflight rules still apply where enabled.

## Test a replacement on an idle printer

On the OLED choose **Material → Filament recovery → Replacement steps**. Select the step, tool, source and temperature. In Fluidd use the macro below.

Fit the waste bin, clear the travel area and choose the service temperature for the actual filament. Each step is a separate factory service operation. Wait for completion and inspect the filament before starting the next step. Enter service mode before each step.

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_JAM_SERVICE_STEP TOOL=T0 SOURCE=CASSETTE STEP=UNLOAD TARGET=235
```

Remove the cartridge or external spool and clear any broken pieces. Insert replacement material. Run the same command with STEP=LOAD, then STEP=WIPE after verifying flow. Check progress with `INVENTURE_PROCEDURE ACTION=STATUS`. For external filament, select SOURCE=EXTERNAL. Each external load or unload moves a nominal 20 mm over the bin. Request another portion as needed. The cassette uses U0–U3 for unload and the factory loader for reload. TOOL=T1 uses the other nozzle. Change 235 to the temperature appropriate for the installed material. Use `INVENTURE_PROCEDURE ACTION=STOP` to stop an active service step.

## Exchange during a qualified native print

Start with `ZORTRAX_JAM_EXCHANGE` or **Material → Filament recovery → Replace broken filament**. A jam already waiting for operator intervention also offers **Unload filament**. The host pauses the active job and parks the active nozzle over its waste bin. Follow the same prompts in Fluidd or on the OLED.

1. Select Unload filament. On an external spool, retract another 20 mm if needed, up to 100 mm per unload sequence.
2. Remove the old material and broken pieces. Confirm the empty path before inserting replacement material. Inlet A must read clear.
3. Insert replacement material and select Load replacement. External feed uses 20 mm portions up to 250 mm per sequence. Cassette loading uses MCU transport feedback.
4. Wipe the nozzle if the loader has not already wiped it. Inspect visible flow. Confirm flow, then select Resume print. Stop recovery leaves the job paused.

Source selection, thermal and position prerequisites, and recovery diagnostics are described in the [R27 operator guide](INVENTURE_FILAMENT_JAM_R27.md). The exchange is a Klipper extension. Physical acceptance is pending separately for both sources and both nozzles.
