# Home X/Y and G28: correction and source audit, 25 November 2026

The earlier inference that standalone Home X/Y must be incorrect because
`_home_limit` uses `G28` was unsupported. That method already existed in the
original homing adapter. It must not be replaced on that basis. The R19
change to the **composed preparation before T0 selection** is a separate
change; it does not establish that the independent X/Y method changed.

This audit adds diagnostics and verification only. No runtime procedure,
configuration setting, endstop polarity, motor holding behavior or MCU image
is changed. The existing explicit R23.7.1 / `inventure-r23-oem-home-rc9.1`
recovery already contains the original standalone method. The printer's
currently loaded profile still requires live readback.

## What the commands actually call

The supplied configuration archive `20261125-134650.zip` contains no
`[gcode_macro G28]`, `[homing_override]` or `[safe_z_home]`. Loading the real
pinned Klipper with the recovery field configuration registers
`PrinterHoming.cmd_G28` from `klippy/extras/homing.py` as the G28 handler.

| Entry | Additional Inventure behavior |
| --- | --- |
| Literal `G28 X` / `G28 Y` | Core homing of the requested axis using the configured rail. The operator Home preparation is not called. |
| Literal `G28 Z` | Native lower PE12 reference. This is not the heated PC14 working-zero measurement. |
| `INVENTURE_HOME_X` / `INVENTURE_HOME_Y` | `INVENTURE_HOME_REQUEST AXES=X/Y`: idle/owner checks, platform clearance and only the requested XY axis. With no working Z reference, lower PE12 homing establishes clearance first. |
| `INVENTURE_HOME_XY` | The same independent-axis adapter, in its existing Y-then-X order. |
| `INVENTURE_HOME_Z` / `INVENTURE_HOME_ALL` | Operator confirmation, T0 preparation and cleaning, lower PE12 reference, PC14 working reference and parking. Their preparatory XY implementation depends on the loaded profile. |

The source of `_home_limit` is unchanged between the original macro commit
`ad7c099`, original R38.6 commit
`ee9cd2f3dc2c1f71d930b0b6825ed08233b72a2f`, the frozen source and the
restored R38.6 adapter. It temporarily scales the rail's homing speeds,
directly calls `printer.lookup_object('homing').cmd_G28` for one axis, waits
and restores those speeds in `finally`. The supplied X/Y settings are
5 mm/s first speed, 2 mm/s second speed and 1 mm retract; the adapter's
default XY factor is 1.5. These values were read, not retuned.

### Where motor release occurs

Successful independent Home X/Y does not call `motor_off()` and leaves the
shared enable asserted at completion. Idle timeout and fault/shutdown paths
are separate; the supplied idle timeout is 900 seconds.

There **is** a `stepper_enable.motor_off()` in `_prepare_t0`, before SERVICE
entry for operator Home Z/All. The old composed preparation subsequently
performs its own XY seek/release. This release must not be described as an
instruction at the end of independent Home X/Y, or silently removed as part
of an OLED change. It is exposed by the read-only audit below.

R19 replaced the composed SERVICE XY seek/release and its measured offsets
with native G28 preparation. R23.7.1 explicitly restores the earlier
composed preparation, including its original ownership transitions. See
[the exact recovery scope](INVENTURE_R23_OEM_HOME_RC9.md). The operator's
requirement concerning X/Y opening under bellows force must not be confused
with PC14 Z refinement or reinterpreted as a new arbitrary retreat.

## Physical records and software verification

The previously supplied `klippy(9).log(2).zip` contains independent success
messages in the session starting at 07:16:06 UTC on 25 November:

| Log line | Completion message |
| --- | --- |
| 3288 | `INVENTURE_HOME X done` |
| 3360 | `INVENTURE_HOME Y done` |
| 3609 | `INVENTURE_HOME X done` |
| 3621 | `INVENTURE_HOME Y done` |
| 4044 | `INVENTURE_HOME Y done` |
| 4076 | `INVENTURE_HOME X done` |

These records support the operator's report that the independent commands
worked. They are historical completion records, not a new physical test or
a hash of every module loaded during that session. Exact source identities
come from the frozen source and indexed backup described in the recovery
document; the evidence types remain distinct.

`ci/check_inventure_home_xy.py` loads the actual installed recovery and
compares the independent-axis adapter with the recorded pre-UI adapter,
Git blob `3fc954974a4a375e003b50c8624304301457f1de`. It exercises literal
Fluidd macros and physical OLED selection handlers in 24 simulated cases:
X, Y, X→Y, Y→X, X→Y→X and Y→X→Y, from both OFF/unreferenced and
NATIVE/referenced states, through each interface. It verifies identical
interface traces, only the requested XY axes, enable held at completion,
no factory/T0 selection, no new heating and unchanged existing targets.

The core G28 handler is identified from the loaded configuration. Motion
and MCU interfaces are simulated during the sequence comparisons; this
does not validate physical switch timing or prove the absence of impacts.
Configuration bytes and authenticated runtime source are checked unchanged.

The primary cause of the separate 18:36 shutdown after completed PC14
refinement remains unclassified. Passing this audit does not resolve it.

## Read the currently running Orange Pi without moving it

In the Orange Pi SSH terminal:

```bash
cd "$HOME/inventure-src" &&
git fetch origin feature/inventure-r23-m300-display-parity &&
git show FETCH_HEAD:tools/inventure_g28_audit.py | python3
```

This neither switches the checkout nor installs a host. The script uses
three Moonraker GET requests and reads source files. It reports the live
PID/runtime/version, loaded Home profile, configured G28 overrides, Home
macro bodies, rail settings, source hashes and `motor_off` call locations.
Source is parsed as text/AST, never imported or executed. Disk hashes are
explicitly distinguished from the live identity returned by Moonraker.
It sends no G-code, opens no UART, changes no service and writes no
configuration. No Home, heating or restart is needed for this readback.
