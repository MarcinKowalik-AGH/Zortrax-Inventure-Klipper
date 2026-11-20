# Left cassette optical inputs and T0 loading on 20 November 2026

The operator actuated the two optical elements of the left filament sensor separately. The first element in the test raised **T0_A (PD10)**. After it was released, the second raised **T0_B (PF1)**. The test identifies the elements by actuation order. Their orientation on the mounted board and the physical source of the PB12 signal are still open.

| Console time | Diagnostic action | PD10 (A) | PF1 (B) | PB12 level | PB12 accepted edges, total |
| --- | --- | ---: | ---: | ---: | ---: |
| 12:37:16 | BASELINE | 0 | 0 | 1 | 7 |
| 12:37:59 | BASELINE | 1 | 0 | 1 | 7 |
| 12:38:25 | COMPARE | 1 | 0 | 1 | 7 |
| 12:38:36 | BASELINE | 0 | 0 | 1 | 7 |
| 12:38:51 | COMPARE | 0 | 1 | 1 | 7 |

The 12:37:59 baseline already contained the active first element, which explains the empty `changes` at 12:38:25. The final comparison recorded `PF1` changing from 0 to 1. Both comparisons reported `pulse_delta.t0_pb12 = 0`.

## How the firmware uses the three inputs

Stock Inventure 1.6.1 r0323 checks A for filament at the inlet. With cassette AUTO enabled and the feeder idle and fault-free, A and B both high request a timed top-up. Long loading runs the feeder forward in L2 and checks A. In L3 the feeder runs forward with E, and the firmware counts new accepted PB12 edges. After a gap longer than 2 seconds it starts counting the series again. The inlet readings alone cannot locate the filament tip at the extruder.

The T0 record at `0x08029A64` was checked directly in `ZORTRAX_INVENTURE_KOMPLET_2026-10-02_CZESC_1_z_2.zip`. Its A callback at `0x08013C20` reads GPIO D with mask `0x0400`, and its B callback at `0x08013C50` reads GPIO F with mask `0x0002`. The accepted-edge counter and auxiliary feeder-start counter have separate addresses. [Stock record and firmware hash](../evidence/CASSETTE_R0323_START.json) · [Stock transport audit, sections 3, 6 and 11](https://drive.google.com/file/d/1mFJuBj8Dn8Mzxa0tJ5MFTogomOmaANOt/view)

## Two separate R27 runs

| Time | Operation | MCU-confirmed E steps | Transport feedback | Outcome |
| --- | --- | --- | --- | --- |
| 11:38:44 | Idle jam recovery, T0 cassette | Retract 960, then feed 48,000 in R1 | Total 7, no new edges. R1 requires 10. | Stopped in R1. |
| 12:19:52 | Ordinary T0 cassette load, AUTO | 19,693 in L1, then 240,000 in L3 | Total 7, no new edges. L3 requires 6. | `l3_no_transport` before L4. |

The 240,000 E steps correspond to 250 mm of commanded extrusion at 960 steps/mm. Actual filament travel was not measured. The final L3 report showed `a=1`, `b=0`, `starts=7`, and the last accepted PB12 edge was over 21 hours old. An earlier T0 load on 10 November recorded **183 new PB12 edges** and operator-confirmed flow. [Earlier physical load](../evidence/CASSETTE1_PHYSICAL_LOAD_2026-11-10.json)

The host replay started the counter at 7. With no new edges it reproduced the L1 and L3 step totals above and the same error. With fresh edges it reached L4. On the R13 register model, actuating PD10 and PF1 left the PB12 count at 7. One falling and one rising edge on PB12, separated by more than 20 ms, raised it to 9.

## Next physical check on R27

Refit the left sensor in its working position, engage the filament in the cassette feed path and leave the guide tube disconnected at the cold extruder with its outlet free. Mark an accessible section of filament so movement can be seen. With the printer idle, run this short feeder-only test in Fluidd.

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_FEEDER_RUN CHANNEL=0 DIRECTION=1 DURATION=10
```

Read `pulse_delta.t0_pb12` in the automatic `V9_FEEDER_RUN` report at `phase=done`. Record the mark movement and the counter together. A positive delta with visible travel confirms transport feedback under these test conditions. If the mark stays still, check that the feeder engages the filament. If filament travels and the delta stays zero, trace the PB12 sensing path and its wiring. This check uses only the cassette drive and does not heat either nozzle. [Feeder test and output fields](../FEEDER_RUN_2026-11-09.md)
