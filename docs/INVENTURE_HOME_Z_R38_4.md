# Reading the Inventure Home Z shutdown on MCU R14

The November 22 Home All run reached the center pad position programmed in Klipper, X82.8 Y68.0, and started native PC14 refinement. The final reply reported 7968 upward pulses, zero downward pulses and zero contact/release cycles. The last input sample had PC14 high (open). T0 read 205.7 °C with a 200 °C target. About 210 ms later the STM32 sent its local safety shutdown. The previous run stopped about 117 ms after reporting 7991 upward pulses and zero cycles.

Both timings match the end of an 8020-pulse search at 4 ms per pulse. With PC14 held open, the R14 register test reproduces the shutdown at exactly 8020 pulses and reads back the terminal status `no contact`. The two field logs end before that terminal reply. They identify the stage of failure and do not establish the physical nozzle position, travel of the table, or electrical continuity at the pad.

## Retrieve the result from the stopped printer

Keep the controller powered and leave it in its current shutdown state. Install the host update on the Orange Pi. The installer restarts the Klipper service. A firmware reset or power cycle erases the controller's retained probe record.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-homing-r38.4 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-homing-r38.4" refs/tags/inventure-homing-r38.4 &&
python3 "$HOME/inventure-homing-r38.4/install-orange-pi.py"
```

When Klipper detects the existing MCU shutdown during configuration, it requests the retained native probe status. The result is written to `klippy.log`. To request the same read again, enter `INVENTURE_HOME_Z_STATUS` in the Fluidd console. In Ready, this command reports the host's current record. On shutdown it requests a read directly from the firmware over the existing Klipper connection. The request uses action zero with zero owner and token. It requires no homing, heat, arming or motion.

```bash
grep -F 'V9_NATIVE_Z_RETAINED' "$HOME/printer_data/logs/klippy.log" | tail -1
```

Compare the returned owner and token with the last native refinement reply in the original log. The host can match them automatically if it witnessed the run. A newly started host reports `matches_failed_run: null`. A reset controller with an empty record reports that explicitly. The recorded `no contact` status means the bounded search did not qualify a first contact. `no release` identifies a separate failure after closure. `guard dropped` identifies an interrupted local interlock or verification. A terminal `stopped` can also follow a separate shutdown. The pulse counters describe electrical commands to the driver.

For a cold check after the printer returns to Ready, `INVENTURE_INPUTS` reads the physical inputs without enabling motion or heat. The `raw.nozzle_contact` field is 1 for an open PC14 circuit and 0 for closed contact. The native sequence assumes T0 is physically lowered over the clear metal pad. The switch position, cleanliness of the electrical contact and physical travel require observation on the printer.

R38.4 preserves the failed result when the macro clears the temporary Z coordinate. It also reports upward steps, downward steps and the search limit in progress updates. The physical homing sequence, heater targets and MCU R14 image are identical to R38.3. This release validates readback in software. The reported Home All run failed to establish a working Z reference.
