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

The operator identified what actuates B on the left assembly. When the extruder pulls filament faster than the cassette supplies it, the filament comes under tension and the guide tube presses the B mechanism. PF1 goes high. Simply advancing the free filament tip past A does not create this pressure.

Stock Inventure 1.6.1 r0323 checks A for filament at the inlet. With cassette AUTO enabled and the feeder idle and fault-free, A and B both high request a timed top-up. Long loading runs the feeder forward in L2 and checks A. In L3 the feeder runs forward with E, and the firmware counts new accepted PB12 edges. After a gap longer than 2 seconds it starts counting the series again. The inlet readings alone cannot locate the filament tip at the extruder.

The T0 record at `0x08029A64` was checked directly in `ZORTRAX_INVENTURE_KOMPLET_2026-10-02_CZESC_1_z_2.zip`. Its A callback at `0x08013C20` reads GPIO D with mask `0x0400`, and its B callback at `0x08013C50` reads GPIO F with mask `0x0002`. The accepted-edge counter and auxiliary feeder-start counter have separate addresses. [Stock record and firmware hash](../evidence/CASSETTE_R0323_START.json) · [Stock transport audit, sections 3, 6 and 11](https://drive.google.com/file/d/1mFJuBj8Dn8Mzxa0tJ5MFTogomOmaANOt/view)

## Two separate R27 runs

| Time | Operation | MCU-confirmed E steps | Transport feedback | Outcome |
| --- | --- | --- | --- | --- |
| 11:38:44 | Idle jam recovery, T0 cassette | Retract 960, then feed 48,000 in R1 | Total 7, no new edges. R1 requires 10. | Stopped in R1. |
| 12:19:52 | Ordinary T0 cassette load, AUTO | 19,693 in L1, then 240,000 in L3 | Total 7, no new edges. L3 requires 6. | `l3_no_transport` before L4. |

The 240,000 E steps correspond to 250 mm of commanded extrusion at 960 steps/mm. Actual filament travel was not measured. The final L3 report showed `a=1`, `b=0`, `starts=7`, and the last accepted PB12 edge was over 21 hours old. An earlier T0 load on 10 November recorded **183 new PB12 edges** and operator-confirmed flow. [Earlier physical load](../evidence/CASSETTE1_PHYSICAL_LOAD_2026-11-10.json)

The host replay started the counter at 7. With no new edges it reproduced the L1 and L3 step totals above and the same error. With fresh edges it reached L4. On the R13 register model, actuating PD10 and PF1 left the PB12 count at 7. One falling and one rising edge on PB12, separated by more than 20 ms, raised it to 9.

## Cold forward feed from the spool outlet at 13:26

The operator unloaded the path, cut the filament at the spool outlet and then reported that it entered the inlet. A ten-second forward run of the left cassette feeder completed in seven MCU windows. The extruder received no E move in this operation.

| Fluidd report | T0_A (PD10) | T0_B (PF1) | PB12 accepted edges, total |
| --- | ---: | ---: | ---: |
| 13:26:30, before | 0 | 0 | 300 |
| 13:26:40, after | 1 | 0 | 300 |

The change on A confirms arrival at the left inlet. B was low in both snapshots, as expected with no pull from the extruder. The PB12 delta for this run was zero. The distance travelled past A was not measured. [Feeder test and output fields](../FEEDER_RUN_2026-11-09.md)

## Loaded T0 cassette reverse test at 13:45

At 13:43:03 the standard T0 cassette load completed L1 through L4 at a target of 235 °C. The PB12 count rose from 300 to 306 by entry into L4 and reached 426 by completion. The host reported `factory_load_completed` and `cleaning_done=true`. No operator flow confirmation was recorded for this load. The same console log also shows the short load completing at L1 at 12:56:14 and 12:57:48.

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_PROCEDURE ACTION=AUTO TOOL=T0 SPEED_PROFILE=OEM TARGET=235
```

After loading, the operator ran the left cassette feeder in reverse for two seconds. This command issued no E move.

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_FEEDER_RUN CHANNEL=0 DIRECTION=0 DURATION=2
```

| Fluidd report | T0_A (PD10) | T0_B (PF1) | PB12 accepted edges, total |
| --- | ---: | ---: | ---: |
| 13:45:25, before | 1 | 0 | 426 |
| 13:45:27, after | 1 | 1 | 428 |

The loaded-path check passed on T0. The guide-tube switch activated as the cassette pulled back, while the inlet switch stayed high. The two extra PB12 edges give a count only. The reverse distance in millimetres was not measured. R27 samples A and B at the start and end of this command, so a shorter activation could fall between the two readings.

During a monitored print from a cassette, stock r0323 checks how much net E feed has been used since the last timed feeder start. If the remaining allowance is negative at the end of an E motion block, it sets fault 2. A break in the tube could therefore raise a transport alarm while A still detects filament. This run establishes the response of the loaded T0 path. A run with an actual break is still open. [Stock transport audit, sections 4.3, 5 and 11.1](https://drive.google.com/file/d/1mFJuBj8Dn8Mzxa0tJ5MFTogomOmaANOt/view)
