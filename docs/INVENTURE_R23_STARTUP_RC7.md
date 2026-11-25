# Inventure R23.5.1: validate the service before switching Klipper

RC6 wrote `WorkingDirectory="/home/klipper/klipper-inventure-r38.6"`.
Unlike an ExecStart argument list, this systemd setting is a single raw path:
the quotes remain literal and the parser rejects it as non-absolute. This is
an installer defect. The supplied log shows stock Klipper v0.14.0 at 13:31:04
UTC during the failed activation. Rollback returned to R38.6 at 13:32:36 UTC;
Moonraker then reported Ready, PID 3005 and the correct R38.6 runtime.
That Ready state confirms recovery of the previous installation, rather than
acceptance of the failed RC6 update.

RC7 writes the absolute working directory without quotes, escapes systemd
percent specifiers and preserves argument quoting for ExecStart. Both prepare
and apply validate the proposed service with the installed
`systemd-analyze --man=no verify`. A missing parser or rejected unit stops the
update before privileged activation or any service stop. After daemon-reload,
systemd must report `NeedDaemonReload=no`.

The startup identity checks remain mandatory: the running script, Moonraker
PID, runtime/config paths and software version must match, followed by the
loaded MCU/Home module paths, R23.5 UI/Home marker, menu roots and MCU ABI.
The report now records actual and expected identity fields in
`startup-observation.json` and `startup-final-observation.json`; a timeout names
the mismatching values. Failure during activation restores the previous
service and modified files through the existing rollback transaction.

## Update on the Orange Pi

Run as the Klipper user, with the printer idle and heater targets off:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-r23-startup-rc7 &&
python3 install-new-host.py --recover-startup --restore-field-profile --serial /dev/ttyS5 --baud 750000 --apply
```

The installer checks configuration and service syntax before stopping the
working host. It preserves existing tuning, calibration JSON and SAVE_CONFIG,
and installs the previously audited R23.5 configuration/menu/Home guards.
`--recover-startup` follows the normal strict path when already Ready.

Check the shell result for `installer_release: R23.5.1`, the selected runtime,
PID and Ready state. Read-only verification in Fluidd:

```gcode
INVENTURE_VERSION
INVENTURE_ZORTRAX OP=STATUS
INVENTURE_HOME_Z_STATUS
```

The UI and Home safety marker remains **RC8-CONFIG-AUDIT-R23.5**. The base
software version remains `inventure-homing-r38.6+klipper.ee2c6fb6b00b`.
Existing MCU **RC8-CONTROL-R7-HOMING-R23-OLED** and InventureBoot 1.7 remain
compatible; no MCU flash is required for this host update.

## Verification and limits

Regression tests use the actual systemd parser to reject the former quoted
path and accept the corrected unit, including paths containing spaces and
percent characters. A run-level regression injects the invalid RC6 unit and
verifies refusal before any service stop, preserving the active override,
printer configuration and Fluidd settings. Other tests cover daemon-reload
readback, startup mismatch details, successful identity reports and rollback.
The release CI repeats the full host/configuration/operator-cycle checks and
rebuilds the unchanged R23 MCU application.

No motion or heating is executed by the installer or its service syntax check.
The earlier physical Home collision remains unresolved by the surviving log;
software verification does not establish mechanical acceptance. Check the
installation and read-only status before resuming physical Home testing.
