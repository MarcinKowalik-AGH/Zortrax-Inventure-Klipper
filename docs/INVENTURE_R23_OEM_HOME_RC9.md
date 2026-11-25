# Inventure R23.7: recovery of the complete R38.6 Home preparation

This recovery is selected explicitly with `--restore-oem-home`. It restores
the complete pre-R19 Home implementation from immutable commit
`ee9cd2f3dc2c1f71d930b0b6825ed08233b72a2f` / tag
`inventure-homing-r38.6`. It changes no MCU image or printer setting.

## Why RC8 was insufficient

The operator clarified that the missing electrical release concerns **X/Y**:
the bellows press the carriage away from compressed endstop positions. This
must not be confused with the separate PC14 Z contact measurement.

R19 commit `f4b3eb218e4625ac97e7e9fad5de33f43f241e7b` replaced the composed
SERVICE Y/X seek-and-release with native `G28` and a zero-offset prepared
frame. The final `home_release` calls and their measured XY translation no
longer ran before T0 selection. Restoring the later pre-R23.5 native module in
RC8 did not undo that earlier change; that module already contained R19.

The older `prepare_native_home` calls `home` and `home_release` for Y, then X.
Each release is a bounded search for its selected input to become zero, at
the existing 1 mm/s, with the existing 5000-step (7.8125 mm) maximum. That
maximum is a failure budget, not a commanded fixed retreat. The interpreter
checks equal completed A/B counts, the selected input hit and direction, and
the absence of E/Z pulses. It records the actual release distance and uses it
when calculating the pad endpoint. The existing MCU owner/run/token handoff
preserves PE10 and restores native DIR levels before Klipper adopts XY.

## Exact recovery scope

Three runtime files are restored together:

| File | Frozen R38.6 Git blob |
| --- | --- |
| `inventure_native_z.py` | `9355e8f2de8880f80c5302f4270f34fbc7d5cf93` |
| `inventure_mcu.py` | `50fa4d260d2dee7038962c4f2376733ae34dc62d` |
| `inventure_z_home.py` | `6473c5473dec6b0889772ff10536390c6ad377c6` |

The native adapter receives only read-only source/version fields in
`get_status`; its executable procedure AST equals the frozen source. The
other two modules are byte-identical. The retained native backup at
`20261124T142803.217006Z/before/0`, identified in the operator's supplied
audit, has the first blob above. Recovery refuses activation without an
indexed backup matching its exact blob and SHA256.

This restores the older complete Home preparation, including its original
SERVICE ownership of preparatory XY. It is not the later R19 exclusive-native
Home architecture. Later host timing and policy diagnostic commands from R21
and R22 are consequently absent from this recovery driver. Normal native
printing and mesh source files are not replaced. OLED/menu source and macro
groups stay installed; all effective configuration values must remain equal
before activation. Calibration files are preserved.

Standalone cold Home X/Y/XY also remain exactly as in the frozen R38.6
adapter: native `G28`, with no new final-release algorithm. This recovery
therefore restores the verified **composite preparation** release path. It
does not claim that the old standalone route already implements every
release/holding behavior now requested by the operator.

## Installation on the Orange Pi

First preserve the current incident log. The reported 18:36 shutdown follows
a completed local refinement. Its primary shutdown line is still needed;
`Select NATIVE` is not that primary reason. Do not use another Home to obtain it.

```bash
inventure_diag_dir=$(mktemp -d "$HOME/inventure-home-incident.XXXXXX") &&
cp "$HOME/printer_data/logs/klippy.log" "$inventure_diag_dir/klippy.log"
grep -aE "MCU 'mcu' shutdown:|Transition to shutdown state:|Inventure native Z failed:" "$inventure_diag_dir/klippy.log" | tail -n 15
```

With the printer idle and targets off, use `FIRMWARE_RESTART` once in Fluidd
and wait for Ready. Install before any Home/heating command:

```bash
cd "$HOME/inventure-src" &&
git fetch origin --tags &&
git switch --detach inventure-r23-oem-home-rc9 &&
python3 install-new-host.py --restore-oem-home --serial /dev/ttyS5 --baud 750000 --apply
```

The installer validates the unchanged configuration, actual systemd unit,
new PID/runtime and loaded Home source metadata, then checks Ready. It does
not move, heat, reset or flash the MCU. MCU R23 / InventureBoot 1.7 remains
the installed application; this host recovery introduces no new MCU ABI.

Read-only verification:

```gcode
INVENTURE_VERSION
INVENTURE_HOME_Z_STATUS
```

Home status must report `home_procedure_profile: oem-home-r38.6`, release
`RC8-OEM-HOME-R38.6-RESTORE-R23.7` and baseline blob `9355e8f...`.
The OLED release remains `RC8-CONFIG-AUDIT-R23.5` because its files do not
change. Matching status proves loaded software identity, not physical
acceptance or resolution of the separate 18:36 shutdown.

## Verification

The recovery tests compare all three source identities, freeze the procedure
AST, execute the original seek/release generator and original factory
interpreter, verify nonzero measured XY offsets and a single native handoff,
and perform exact runtime rollback. Configuration-only startup loads the
real field include tree with this selected profile without opening UART.
The MCU source and existing binary hashes are unchanged. This recovery has
not been tested again on the physical printer.
