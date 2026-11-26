# Inventure Home RC4 — operator acceptance, 26 November 2026

The operator explicitly confirmed in the project conversation:

> ok potwierdam działanie x ,y ,z homingu.

This followed the RC4 installation instructions. The release/source identity
below pins the last supplied field-test version. The confirmation did not
include a new printer-side file-hash readback; distinguish this reported
acceptance from an independent on-printer byte audit.

- Release: `inventure-home-r24-rc4`.
- Commit: `51db0acb53447ca79d32efa96754ff5ffc603c0b`.
- Home status: `RC8-HOME-XY-RELEASE-ZERO-R24.2-XY5MM`.
- Frozen operator helper: `restored/operator_home_r24/rc4/inventure_operator_home.py`.
- Helper SHA256: `a8db616130e7bf4f90e791af275e073b5369714295136d6485e9044720f47132`.
- Runtime manifest SHA256: `dd427a00e334c679f7aee69075f960e1d91db6c5294529a63bcd38c6e1bdbd44`.
- MCU: `RC8-CONTROL-R7-HOME-R24-OLED`, InventureBoot 1.7.
- MCU IVB SHA256: `ceadbff262e6e37f5f380ccb5c896fbdfc4ebc301bef1d4e83aae00f9e18cb07`.

The installed RC4 native Z, factory driver and Z generator hashes are:

| Module | SHA256 |
| --- | --- |
| `inventure_native_z.py` | `4e6643642cc88868a0b46cc37abbf9c3d9ae30850aed0352e51ddd03d2fa380a` |
| `inventure_mcu.py` | `2f9216e819c5d9ca34330db87bbe4032eacbde2dc73aa05ec51316532a0dfe6b` |
| `inventure_z_home.py` | `030ab6cf028c8321fa877d0c7d553162061722d66f2a7af14b04f452460621cd` |

Preserve the reported working X/Y seek, measured release, dynamic origin,
5 mm park and holding, plus the original T0 preparation and Z engines,
coordinates, speeds, heating, contact refinement and parking. Keep this
record separate from simulations and later candidates.

## Newly authorized changes

In the same message the operator requested that Z preparation reuse both
known axes, home only Y if X is known, and only X if Y is known. This also
removes the unconditional XY pass in the combined Home XYZ route. Explicit
standalone Home X/Y/XY still intentionally remeasure their requested axes.

The operator also requested removal of the initial Fluidd homing confirmation
and asked whether Z checks the tray. The existing PC13 seated-tray check and
MCU native-Z tray guard are retained. This is a sensor/interlock check, not an
independent mechanical inspection of every tray latch.

The later candidate changes only operator routing and initial direct-command
review. This acceptance statement does not assert physical testing of those
new changes. A source invariant checks that the RC4 helper's other methods
and the three engine files remain unchanged.
