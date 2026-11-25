# Preserve the accepted Inventure Home procedure

The operator requested integration of existing, physically tested procedures
into the OLED menu and Fluidd macros. That integration must preserve their
behavior. It does not authorize another reconstruction of homing, selector
movements, cleaning or contact measurement.

## Three completed physical runs in the supplied log

The supplied `klippy(9).log(2).zip` contains three native Z completion records
under the R38.6 runtime started on 25 November 2026 at 07:16:06 UTC. Each reports
`terminal: done`, `reference_established: true` and `coordinate_saved: true`.

| Run | Contact cycles | Release steps from the bottom | Local downward clearance |
|---|---:|---:|---:|
| 1 | 311 | 111802 | 1 mm |
| 2 | 102 | 111807 | 1 mm |
| 3 | 332 | 111818 | 1 mm |

The log identifies MCU `RC8-CONTROL-R7-HOMING-R23-OLED-75cc39fe56c8`.
Each preceding T0 selection/cleaning preparation completed, its MCU-to-native
handoff was verified and its reported native XY offsets were `[0.0, 0.0]`.
The resulting pad position was native X82.8 Y68.0.

These are physical execution records supplied by the operator, rather than
additional simulated cycles. They support the operator's statement that the
procedure had worked three times. They do not establish the absence of every
mechanical defect or prove the byte identity of a module not hashed in the log.

## Verified source differences and limits

The public native module before R23.5 is
`restored/scheduler_r21/inventure_native_z.py`, Git blob
`3fc954974a4a375e003b50c8624304301457f1de`.
R23.5 installs `restored/configuration_audit_r23/inventure_native_z.py`, blob
`eb92dd3eb7bb54aee5b710b34957494250a547e9`.

An AST comparison finds changes in exactly three methods: `_home_limit`,
`_native_xy_origin_for_t0` and `get_status`. R23.5 adds per-axis raw-switch
checks, a final simultaneous X/Y raw-level requirement, audit logging and
module/version status fields. Those checks change whether an existing
procedure may continue and therefore are behavioral changes, even though the
main movement methods retain their previous bodies.

In the latest failed run, the added audit reports Y homing complete with its
switch verified, then X homing complete with its switch verified. The final
joint check rejects Y with `Unexpected y_endstop ... (expected 1)` before T0
preparation. The log alone does not establish the physical cause of the
operator-reported impacts.

Comparison with the older OEM seek/release implementation is not a proven
diagnosis of this incident. The three successful runs already used the native
preparation and zero-offset handoff. Do not introduce a new final XY release,
change homing order or change selector/pad offsets on that hypothesis.

The older status did not report the loaded native module's hash. Identify the
exact accepted installed bytes from the retained runtime manifest/backups
before selecting a recovery implementation. Do not treat the shared R38.6
base version string as proof of a particular restored module.

This evidence/preservation update changes no executable procedure, printer
configuration, installer, firmware or motion parameter. It is not a repair
release and does not authorize another physical Home test.
