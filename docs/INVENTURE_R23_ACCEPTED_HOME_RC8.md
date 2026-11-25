# Inventure R23.6: restore the recorded native Home procedure

The supplied installer audit identifies two retained copies of the native
module before R23.5, in installations `20261125T132634.084458Z` and
`20261125T135716.311355Z`. Both have Git blob
`3fc954974a4a375e003b50c8624304301457f1de` and SHA256
`3e82ecc981bcf2b6e009dd10d579c9b1c8de1084c3e63f7c5d15ab4af72425d2`.
The active R23.5 native file is blob
`eb92dd3eb7bb54aee5b710b34957494250a547e9`.

The earlier session in the supplied log contains the operator's three completed
physical native Z references: 311, 102 and 332 contact cycles, with the same
R38.6 base and R23 MCU. Comparing that session's effective configuration with
the later failed session finds identical `[printer]`, X/Y/Z stepper and
`[inventure_native_z]` settings. See the
[accepted-run evidence and remaining limits](INVENTURE_ACCEPTED_HOME_2026-11-25.md).

## Exact change

R23.6 restores the pre-R23.5 native source. Every AST node outside the
`InventureNativeZ.get_status` method must match the recorded reference; the
bootstrap and regression test enforce this. Consequently the G28 sequence,
T0 preparation, selector/wipe calls, coordinates, speeds, temperatures, contact
measurement, ownership checks and previous acceptance conditions are retained.
The later raw-X/Y checks added in R23.5 are reverted with that source.

Only `get_status` differs from the reference, retaining loaded-file identity
and reporting the restoration identifier and baseline blob.
The resulting complete module has blob
`5263263a899be9d08dbeed495d04e8663371ed4c`; it is intentionally distinguished
from the reference file's identity because of those status fields.

Upgrade from RC7 changes only `inventure_native_z.py` and the authenticated
runtime manifest. Existing menu/graphics modules and the systemd startup fix
remain installed. Earlier source layers are immutable and the RC7 layout is
recognized explicitly for transactional upgrade and exact rollback.

## Install on the Orange Pi

Run as the Klipper user, with the printer Ready, idle and heater targets off:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-r23-accepted-home-rc8 &&
python3 install-new-host.py --restore-accepted-home --serial /dev/ttyS5 --baud 750000 --apply
```

This recovery flag requires an existing installer backup for this exact runtime
whose native file matches the reference bytes. It verifies that the staged
configuration preserves every effective setting and rechecks the reference
backup's digest before switching. Missing/unverified backups or any effective
configuration change stop recovery before the service switch. Existing
SAVE_CONFIG, nozzle-pad and platform calibration files are preserved.

RC7's actual systemd parser validation runs before stopping Klipper. Activation
and rollback retain strict runtime/module/PID/MCU identity checks. The new Ready
process must additionally report the matching restoration ID and baseline.
Installation does not perform a Home, selector motion, heating or MCU flash.

Read-only checks after installation:

```gcode
INVENTURE_VERSION
INVENTURE_HOME_Z_STATUS
INVENTURE_ZORTRAX OP=STATUS
```

Native status must show `procedure_restore_release: RC8-ACCEPTED-HOME-R23.6`
and `procedure_baseline_blob: 3fc954974a4a375e003b50c8624304301457f1de`.
Menu UI remains `RC8-CONFIG-AUDIT-R23.5`; the base software version remains
`inventure-homing-r38.6+klipper.ee2c6fb6b00b`.
MCU `RC8-CONTROL-R7-HOMING-R23-OLED` / InventureBoot 1.7 stays compatible.

## Verification and physical limits

Tests compare the complete executable AST with the saved reference, exercise
RC7-to-R23.6 upgrade and exact rollback, reject a modified saved reference,
and verify that configuration normalization preserves all effective settings.
The full installed OLED/Fluidd cycle suite continues to exercise the shared
macro route. Native G28 failures are injected at the homing interface and must
stop T0 preparation; they are distinguished from the removed raw-level checks.
Local validation passes 1002 tests. The supplied complete configuration also
passes offline startup with the restored runtime, all 92 macros, identical
effective settings and byte-identical nozzle-pad/platform calibration files.
[Verification record](validation/INVENTURE_R23_ACCEPTED_HOME_RESTORE.json).

This is restoration of the recorded procedure, not proof of the physical cause
of the reported Y impacts. It neither introduces another release movement nor
changes directions, geometry, speed or offsets on a hypothesis. Software
verification does not establish that another physical Home cannot collide.
Check installation identity before any further mechanical test.
