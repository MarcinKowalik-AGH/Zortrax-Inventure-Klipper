# R28 filament jam, missing material and broken filament

Host R28 uses MCU R13. T0 and T1 use the same guide-tube switch mechanism. The active print nozzle selects the input channel, feeder, waste-bin approach and return path. Fluidd and the OLED drive the same recovery session.

## Install on the Orange Pi

Run these commands over SSH while Klipper is idle.

```sh
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-filament-jam-r28 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-filament-jam-r28" refs/tags/inventure-filament-jam-r28 &&
python3 "$HOME/inventure-filament-jam-r28/install-orange-pi.py" --configure-jam
```

If the tagged worktree already exists, run its installer. In Fluidd, `INVENTURE_VERSION` should show host `RC8-CONTROL-R7-FILAMENT-JAM-R28` and MCU `RC8-CONTROL-R7-PAD-THERMAL-R13`. Run `ZORTRAX_JAM_STATUS` for the selected sources, native print configuration and recovery state. A confirmed material allocation supplies the source and cassette profile. Without it, set the source per nozzle before printing, for example `ZORTRAX_JAM_SOURCE TOOL=T0 SOURCE=CASSETTE`. A per-tool service profile such as `procedure_t1: {"material":"Z-FLEX"}` in `[inventure_mcu]` selects the flexible material allowance when there is no confirmed material allocation.

## During a native print

| What happened | Fluidd macro | OLED under Material → Filament recovery | Printer response |
| --- | --- | --- | --- |
| Filament has stopped feeding | `ZORTRAX_JAM_REPORT` | Report filament jam | Park over the bin. For a cassette, try R1 and R2. For an external spool, check presence, feed over the bin and request visible-flow confirmation. |
| Material is missing | `ZORTRAX_JAM_MISSING` | Material missing | Pause and park. Fit the material, then select **Load replacement** for an empty guide tube. Unload is also available. |
| The operator can see a break in the guide tube | `ZORTRAX_JAM_BROKEN` | Broken filament | Pause and park. Remove the broken piece and refit the tube. Unload the remaining material if needed. |

For a confirmed break, select **Broken pieces removed** after clearing the tube. The upstream filament may still be in A. Select **Load replacement**, then **Wipe nozzle** if loading has not already wiped it. Confirm visible flow over the bin, then select **Resume print**. A complete source exchange with **Replace filament** still requires A to clear before inserting replacement material. On an external spool, unload/load moves the extruder in 20 mm portions. Cassette unload uses U0–U3 and reload uses the factory short or long branch. The active prompt exposes the corresponding actions in both interfaces.

Automatic monitoring starts during a native virtual SD print with a known active tool and selected material source. An idle printer uses **Replacement steps** and **Test recovery at idle** in the same OLED menu, or `ZORTRAX_JAM_SERVICE_STEP` and `ZORTRAX_JAM_SERVICE_TEST` in Fluidd. [Idle service and native print prerequisites](INVENTURE_FILAMENT_JAM_R27.md)

## Comparison with Inventure 1.6.1 r0323

| Firmware condition | R28 behaviour |
| --- | --- |
| A detects filament at the inlet. B detects guide-tube pressure under tension. | T0 uses A=PD10, B=PF1, transport pulses P=PB12. T1 uses A=PD15, B=PG10, P=PD14. The cassette supplies a timed portion when A and B are both high. |
| Monitored cassette feed consumes the available E steps. Retraction credits steps back. | Each tool starts with 96,000 steps (100 mm). A feeder start replaces the balance with the material allowance. At 960 steps/mm this is 21 mm for the standard profile and 54 mm for Z-FLEX and Z-SEMIFLEX. Exactly zero is permitted. A negative balance after an issued E movement is the fault 2 condition. Missing pulses by themselves do not trigger it. |
| B remains high after four timed portions with monitoring enabled. | R13 reports feeder fault 3. With material remaining, the printer pauses for inspection of the cassette (warnings #176/#177). A single high B requests a top-up. |
| A clears. The original uses source and cassette inventory to select the warning. | External spool: empty (#161/#162). Cassette: verified remaining balance at or below 5% gives empty. With a higher verified balance the inlet fault gives cartridge error (#163/#164). With unknown inventory the prompt says **Material missing**. |
| Fault 2 with material remaining starts automatic recovery. | R1 allows up to 50 mm and requires ten fresh P edges, restarting the series after more than 2 s of silence. R2 allows up to another 50 mm and requires three new feeder starts. R1 failure leaves the print paused. R2 failure tries the cassette loader. An ordinary transport failure during reload parks the nozzle and offers replacement or retry. |
| After unsuccessful recovery, the model warning is #174. Support uses #175 or #178 according to tool PCB protection class. | R28 displays #174 for T0. For T1 it displays #175 at class 0–1 or #178 above class 1 when the thermal module supplies a fresh, valid PCB class. Without a qualified reading it displays **Extruder jam**. |

Stock firmware checks the signed balance at the end of an E motion block. Klipper R28 checks MCU samples of executed E steps and feeder starts on the active print tool. It can observe the threshold at a different instant. R28 uses the native print owner and saved file position for return. The stock parser progress gate and material metadata flags belong to the original ZCODE print engine. **Broken filament** records an operator observation. A transport alarm on its own cannot locate a break or distinguish it from slip or obstruction. The trigger and its sensor snapshot remain in `ZORTRAX_JAM_STATUS`.

The T0 printer log shows A staying high and B rising after two seconds of cassette reverse with E stationary. The owner confirmed the identical T1 mechanism. The release exercises both tools and both filament sources in software. Physical acceptance of recovery and return during an interrupted print is still open. [T0 field measurement](acceptance/INVENTURE_T0_CASSETTE_OPTICS_2026-11-20.md) · [Original transport audit, sections 4–5 and 11](https://drive.google.com/file/d/1mFJuBj8Dn8Mzxa0tJ5MFTogomOmaANOt/view)
