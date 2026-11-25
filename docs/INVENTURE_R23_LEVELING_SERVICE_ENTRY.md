# Inventure R23.1 host — start leveling after Home

This host update addresses `MCU did not enter SERVICE mode: another_owner_is_busy` when `INVENTURE_LEVEL` follows a completed native Home. The physical R23 MCU correctly requires PE10 to be disabled before SERVICE can own the shared motor outputs. The old operator entry waited for motion to finish but left Klipper's motor enable asserted. The Home T0 preparation already released that enable explicitly; the general leveling entry did not.

The host now waits for native moves, verifies that the printer is still idle, calls Klipper's `stepper_enable.motor_off()`, waits for that queued write, checks the owner/interlocks again and then requests SERVICE. MCU refusals remain effective. It does not issue STOP/OFF, clear a latch, fabricate a position or retry a rejected ownership transfer.

**Existing MCU `RC8-CONTROL-R7-HOMING-R23-OLED` remains installed. This is a host update; no new MCU flash is required.** The application and its SHA256 remain identical to `inventure-mcu-r23-oled-rc1`:

```text
INVENTUR.IVB  ef83d8ca0c26c12ed11fbd27380b1ee014cb4baf977c7adebb178861df137645
```

## Update the Orange Pi Zero 2W

When the printer is idle, turn off the heater targets and run `FIRMWARE_RESTART` once in Fluidd. Wait for Ready before running the installer, so it starts from the unarmed state it requires.

In the Orange Pi shell, as user `klipper`:

```bash
cd "$HOME/inventure-src" &&
git fetch origin tag inventure-r23-leveling-host-rc2 &&
git switch --detach inventure-r23-leveling-host-rc2 &&
python3 install-new-host.py \
  --restore-field-profile \
  --serial /dev/ttyS5 \
  --baud 750000 \
  --apply
```

The installer recognizes the exact published R23 RC1 runtime and transactionally replaces only `klippy/extras/inventure_mcu.py` and its runtime manifest for this correction. Existing configuration, tuning, saved calibration and OLED renderer retain their owners. The host revision string remains R38.6; the manifest records `operator_service_r23` with release `RC8-OPERATOR-SERVICE-ENTRY-R23.1`.

After Ready, run in Fluidd:

```gcode
TURN_OFF_HEATERS
INVENTURE_LEVEL
```

The OLED route is **Maintenance → Platform leveling** and uses the same host entry. Follow the three-knob prompt and confirm each adjustment separately with `INVENTURE_LEVEL_NEXT`. Read progress with `INVENTURE_LEVEL_PROGRESS`, save a completed candidate with `INVENTURE_LEVEL_SAVE`, or cancel with `INVENTURE_LEVEL_STOP`.

For the previous RC1 host, an idle operator can explicitly release the native motors with `M84` before `INVENTURE_LEVEL`. The corrected host performs that ownership step itself.

## Verification scope

The regression model rejects SERVICE while the physical shared enable is held, reproducing the reported error with the RC1 host. It checks queued disable completion, a new owner/fault during drain, missing native interfaces and a real MCU-style guard refusal. Installed-config tests exercise Home XYZ followed immediately by the literal Fluidd macro and the OLED confirmation, plus cancellation. Upgrade tests check the exact RC1 runtime, unchanged calibration and byte-for-byte rollback.

These are software and simulated-interface results. The operator's log confirms two successful Home XYZ runs on R23 and the subsequent reported leveling-start refusal. Physical acceptance of the corrected leveling entry and a completed five-point run remains to be reported. Issue #3 remains open for its broader timing qualification.
