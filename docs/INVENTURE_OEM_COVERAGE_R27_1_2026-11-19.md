# Zortrax Inventure V9 firmware coverage as of 19 November 2026

The machine has completed homing, nozzle selection, loading and wiping on both nozzles, two full central-pad measurements and a five-point platform leveling run. The published R27.1 host with R13 MCU adds modular filament recovery. The last available physical configuration is a service and calibration profile. It does not establish full native dual-material printing or complete parity with Inventure 1.6.1/r0323. [R27.1 source at the audited commit](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/tree/1dec4a4a1331c31ab0b22af39b6de56edc3e9e9e) · [Recorded setup and native print requirements](INVENTURE_FILAMENT_JAM_R27.md)

## Basis for the comparison

The original requirements come from *ZORTRAX INVENTURE V9 / r0323 — MASTER wiedzy do odtworzenia funkcji w Klipperze*, revision 1.2, 7 October 2026, sections 13.3 and 14.2, and *ZORTRAX INVENTURE V9 — Audyt domknięcia portu Klipper*, 24 October 2026. The master specifies the observable behaviour. The plan assigns 85 requirements to 24 work packages. Six original V9 test registers contain 164 entries in total, with additional thermal and RFID groups. The November acceptance records supersede earlier open items for the same physical operation.

Later archive packages changed numbering. For example, the October plan calls fan automation P07, while the later implementation series placed it in P06. The plan assigns P20 to the physical SD slot and P21 to checkpoint recovery. Later RC1 packages named P20 and P21 delivered the Models and Material menus respectively. The archive also has separate P18 documents for settings and checkpoint work. Every package number in the tables below follows the **24 October plan**. Evidence from other packages is matched by function.

**Field** means the recorded operation actually ran on the owner’s Inventure. **Software** includes implemented or tested logic and identifies relevant archived components where applicable. **Partial** identifies a narrower field result or implementation. **Open** names a missing integration, an unaccepted hardware path, or both as specified. An item marked **by design** records a deliberate project choice. The last hardware configuration inspected came from the R26.3 leveling session. Installing R27.1’s macros can add pause and recovery controls, but does not by itself commission native dual extrusion.

## Findings that affect the installed machine

| Area | Evidence and consequence |
|---|---|
| Nozzle and tray calibration | Both 100 °C central-pad runs were saved with empty nozzles and a 0.1 mm edge tolerance. At five-point leveling, R26.3 saved 138.349 mm with a center error of +25 Z steps (+0.031 mm), five readings, no corrective knob instructions and `applied_to_print=false`. The field result supersedes the open acceptance note written when R26.3 was released. [Two full runs](acceptance/INVENTURE_PAD_R23_2026-11-19.md) · [Five-point result](../README.md#printer-procedures) · [Manufacturer’s five-point procedure](https://support.zortrax.com/inventure-platform-leveling/) |
| Native dual print | The recorded configuration has a heater-only `[extruder]`, `[heater_generic inventure_t1]`, and no `[extruder_stepper shared_e]` or native tool/operation owner. The leveling path reads the saved pad file. An ordinary native T1 change still needs the correct physical selection and one application of the signed offsets. A two-material overlap print is the acceptance test. [Native print prerequisites](INVENTURE_FILAMENT_JAM_R27.md#recovery-during-a-native-print) · [Saved calibration consumer](../orange_pi/inventure_leveling_transport.py) |
| Fans | The recorded native configuration binds F0, F2 and F3 to both nozzle heaters using `heater_fan` and `heater_temp=40`. It has no `[inventure_fans]`. The reference control for F0/F5 uses the hotter nozzle, with a rising command from 40 to 60 °C and a normal nonzero minimum of 96/255 before per-channel scaling. F2/F3 use chamber activity, READY, material and a hold of about 10 s. F1 cools the part. F4 handles print and final ventilation. Four channels have tach feedback. PCB_FAN has a fixed supply. The R13 service MCU also has its own output- and temperature-dependent cooling mask. [Pinned software baseline](../source/SHA256SUMS) · [R13 service cooling](../service_mcu/control_r6/SOURCE_MCU/inventure_control.inc) |
| Manual fan test and residual heat | The host checks for an idle printer, zero heater targets and outputs, and a cold-printer acknowledgement. Its entry check does not compare measured nozzle temperatures. The MCU manual-test admission checks its session and fault state. During an accepted manual ON phase in the unarmed service path, cooling selects the tested channel in preference to the normal cooling mask. An idle hot nozzle therefore has no demonstrated automatic protection from losing its usual airflow in that diagnostic. Use the test only on a physically cold printer pending correction and a regression for residual heat. [Host entry check](../orange_pi/inventure_mcu.py) · [MCU diagnostic](../service_mcu/control_r6/SOURCE_MCU/inventure_fan_manual.inc) · [Cooling selection](../service_mcu/control_r6/SOURCE_MCU/inventure_cooling.inc) |
| Global Door lock | The front and lid have separate switches and one user policy in Fluidd and the English OLED. R13 resets the policy to off on MCU restart. The lock-command mask reports the commanded coils. Leveling monitors the switches when enabled and keeps the coils off in its service session. The optional older native preflight P15 still requires both switches closed for 0.5 s. P16 also checks both switches even when lock-command confirmation is optional. Neither check consults the R13 policy. These paths were inactive in the recorded leveling profile. Native print integration needs to route the door decision through the global preference while retaining tray and bin handling. [Native preflight](../host/inventure_preflight_core.py) · [Interlock evaluator](../host/inventure_interlocks_core.py) · [Policy](OEM_DOOR_POLICY.md) · [Leveling interlocks](INVENTURE_PLATFORM_LEVEL_R26_2.md#global-door-setting) |
| Platform Up/Down | Service operations physically moved the table in both directions. The OLED action defaults to 1 mm and requires a valid native Z, `inventure_motion` and `inventure_operations`. The last field configuration has `inventure_native_z` but lacks these two dependencies. Startup, ordinary operator moves and fault-specific Z behaviour need distinct records. [Menu implementation](../orange_pi/inventure_menu_service.py) |
| Heaters and sensors | The stock four heating channels are the two nozzles and the two sides of the chamber. The tray has no dedicated bed heater. Both nozzle heaters ran in service. In the recorded native profile both use `watermark`, `max_power=0.40` and `max_temp=310`. The two chamber control loops and the separate material-dependent tool-PCB protection are open at the print-profile level. The field record contains no independent bed-heater control. [Sensor and output map](CONTROL_R5_REFERENCE.md) · [Physical nozzle heating](evidence/LOAD230_R3_PHYSICAL_2026-11-10.json) |
| Operator controls with incomplete backends | `Save print` shows an information message. `Browse SD card` lists host files. `File checking` and `Ignore material endstops` check for callable setters, which are absent from the inspected host modules. Each feature needs an implementation or an explicit interface decision. [Menu dispatcher](../orange_pi/inventure_menu_service.py) · [Current-session pause contract](../host/inventure_cycle.py) |

## Work packages from the original plan

| Plan ID | Scope | Current outcome |
|---|---|---|
| P01 | Printing configuration and Klippy | Service profile ran on the Orange Pi. The qualified native dual-print profile has not been demonstrated on this machine. |
| P02 | Procedure ownership and position validity | Software guards exist. Shared ownership across normal printing, park, service, cancel and return needs a physical job. |
| P03 | CoreXY, homing and Z reference | Physical XY, bottom Z and nozzle contact have evidence. Manual platform, wake and fault-specific Z are separate routes. |
| P04 | Shared extruder drive and mechanical selector | Physical selection is proven. The shared E drive and automatic coordinate frame still need commissioning for native dual printing. |
| P05 | ADC, heaters and regulators | Two nozzles used in service. Both chamber zones, all eight measurements and the native print regulators require qualification. |
| P06 | Thermal faults, tool PCB and fault reporting | Local cutoffs and watchdog exist. Material-dependent PCB checks and the P13 cooling pause are open on hardware. |
| P07 | Automatic fan operation | Manual outputs and feedback were exercised. The six-channel OEM print policy is not active in the recorded field profile. |
| P08 | Full and abbreviated nozzle changes, purge and cleaning | Wiping and selector motion are proven in service. In-print variants and the three purge programmes need separate evidence. |
| P09 | Five conductive pads and platform depth | Successful five-point run and save. The successful run needed no corrective knob turn. The live correction branch and downstream depth application have separate status. |
| P10 | Nozzle Z and printed XY alignment | The electrical central-pad extension has two saved full runs. The stock-style comb print and applying the result in a production job still need acceptance. |
| P11 | Material catalogue | Reference records and model/support selections exist. Verify the source, material and applied profile together. |
| P12 | Two RFID readers | Client, frame checks and tag handling exist in the project. Both physical slots and persistent readback await a recorded test. |
| P13 | Cassette feed and external spool transport | Actual load and wipe on both tools. Per-branch loading, complete unloads and spool replacements still need identified runs. |
| P14 | Jam recovery and material accounting | R27.1 has modular recovery and replacement. Four source/tool combinations and consumption writeback remain open on the printer. |
| P15 | Preflight and print start | Preflight, manifest and Orca start components exist. A native full-profile start including chamber readiness remains open. |
| P16 | Front door, lid, locks, tray and waste bin | The service path honours the global policy. Legacy native P15/P16 still demand closed covers. Resolve the discrepancy, then verify both locks, tray and bin across start and resume. |
| P17 | Pause, resume, cancel and end | The host supports current-session pause and recovery. Full normal, programmed, material and thermal sequences and their terminal states remain to be verified. |
| P18 | Settings and nonvolatile import | Archived settings tooling and individual host persistence exist. Boot-time filtering, ownership and end-to-end migration are partial. |
| P19 | Native Orca G-code and classic ZCODE | Profiles, conversion and static inspection exist. Direct classic playback on this application is open. |
| P20 | Printer SD slot and FAT | Application-level print access through the physical slot is open. Models currently reads the host filesystem. |
| P21 | Saved job and restoration | Checkpoint work exists in the archive. R27.1 provides no durable Save print / restore route from the active menu. |
| P22 | OLED and menu actions | The physical display and English navigation exist. Some menu items only inform or require a missing backend. |
| P23 | POWER, sleep, RGB and maintenance | Display sleep and basic services exist. Printer-wide power transitions, light policy, audible hardware and manual service wizards remain partial. |
| P24 | Build, regression and hardware acceptance | Software regression is repeatable. Hardware acceptance is recorded per operation and configuration. |

## Individual requirements

### Controller and wiring

[Controller and service reference](CONTROL_R5_REFERENCE.md) · [Release scope](SOURCE_AND_LIMITATIONS.md)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-HW-01 | P24 | Controller and reference image | **Field + records.** The V9 connects over P4 at 750000 baud. Version output identifies InventureBoot 1.7 and the R13 application. The reference audit identifies stock 1.6.1/r0323 separately. | Pair each later machine log with its actual host and MCU version. |
| REQ-HW-02 | P06 | Outputs at startup and shutdown | **Partial.** The MCU has local cooling, heater expiry and stop logic. Field logs also show shutdowns. | The electrical states of every load through boot, loss of host and restart have no complete field record. |
| REQ-HW-03 | P01 | Time bases | **Software.** The source distinguishes MCU clocks, sampled inputs, motion timing and host timeouts. The release reports a 5 s heartbeat timeout. | Electrical timing under load and its relation to the original firmware remain separate measurements. |
| REQ-HW-04 | P24 | Connectors and auxiliary signals | **Partial.** The wiring audit records the fan, thermal and service connectors. The receiver on PG13 and the purpose of PG4 remain unresolved. | Keep these two signals unassigned until identified on the board. |

### Motion, position and calibration

[Native homing](NATIVE_Z_COREXY.md) · [Full pad field results](acceptance/INVENTURE_PAD_R23_2026-11-19.md) · [Leveling implementation](../orange_pi/inventure_platform_level.py)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-MOT-01 | P03 | CoreXY scale and frames | **Partial.** Real XY motion reached both nozzles’ central pad positions and the tray pads. Service and native print coordinates have separate owners. | Verify full print travel and tool-relative distances across the native envelope. |
| REQ-MOT-02 | P03 | Step, direction and enable | **Partial.** A/B/Z and tool-dependent E directions are represented in the source. Motion and material flow have field evidence. | Electrical pulse widths and timing margins for every driver remain a separate bench item. |
| REQ-MOT-03 | P03 | Planner limits | **Partial.** Klipper plans native moves. MCU service motion has its own axis and rate limits. | Accept component limits and combined XYZE moves in a representative native print. |
| REQ-MOT-04 | P19 | Feed and flow quantisation | **Design / open.** Native G-code uses Klipper feed/flow semantics. The stock classic record format uses its own quantisation. | A classic playback path would need reference file comparisons for speed and extrusion. |
| REQ-MOT-05 | P02 | Queue synchronisation | **Software.** The planner and ownership code distinguish queued moves, completion and interrupted motion. | Verify the physical stop and return position during a real pause, tool change and cancel. |
| REQ-MOT-06 | P03 | X and Y references | **Field.** The physical service and homing trials reached repeatable XY references used by pad scanning and leveling. | The established homing sequence is a baseline for later print tests. |
| REQ-MOT-07 | P03 | Lower Z endstop | **Field.** PE12 reference, release and further moves are present in machine logs, including successful five-point leveling. | Retain the field trial as the baseline for bottom referencing. |
| REQ-MOT-08 | P03 | Nozzle contact and Z reference | **Field.** PC14 contact and release were measured on both nozzles. Full pad runs included repeated Z measurements and T0 reseating. | Retain the PE12 datum and PC14 events when assessing later print coordinates. |
| REQ-MOT-09 | P03 | Manual, service, startup and fault Z motions | **Partial.** Service Z moved both ways on the machine. The menu has Move the platform with guarded 1 mm Up/Down steps by default. It requires the native motion and operations objects and homed Z. | The recorded profile lacks those menu dependencies. Operator jog, startup/wake Z and fault-specific Z have distinct acceptance criteria. |
| REQ-MOT-10 | P04 | Parking and service coordinates | **Partial.** Selector, brush and waste-bin moves ran in recorded cleaning and pad procedures. The audit enumerates 15 service locations. | Check the unused locations, tool frames and clearance when leaving and returning to a model. |
| REQ-MOT-11 | P04 | Tool offsets during printing | **Integration open.** Both full pad results were saved. Leveling reads the saved calibration. The separate native toolframe supports coordinate changes. | Connect calibration to ordinary tool changes exactly once and measure real T0/T1 overlap in X, Y and Z. |
| REQ-MOT-12 | P09 | Five-point platform and knobs | **Field.** On R26.3, five measurements produced a saved depth of 138.349 mm and a center deviation of +0.031 mm. The run requested zero corrective knob adjustments. | The initial knob prompt exists. The rear/front correction and recheck branches have software tests. A physical correction branch needs its own record. |
| REQ-MOT-13 | P10 | Printed XY combs and nozzle Z | **Partial.** The central pad method is field-tested. The separate stock-style procedure generates 28 segments per nozzle and offers Y/X line selection and save. | A physical two-material comb, line choice and repeat after applying the correction would close the printed-pattern route. |
| REQ-MOT-14 | P02 | Failure, cancellation and reference invalidation | **Partial.** Service stop and failed probing are visible in logs. The native code invalidates a lost or conflicting reference. | Verify who owns XYZ after cancel, a dropped connection and a return from a paused native print. |

### Heating and temperature supervision

[MCU service outputs and sensor map](CONTROL_R5_REFERENCE.md) · [T0 field heating result](evidence/LOAD230_R3_PHYSICAL_2026-11-10.json) · [MCU temperature and cooling code](../service_mcu/control_r6/SOURCE_MCU/inventure_cooling.inc)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-TH-01 | P05 | Eight temperature channels | **Partial.** ADC mapping covers T0, T1, left, right, chamber center, PSU, mainboard and tool PCB. Earlier bench diagnostics reported the tool PCB reading outside range. | Establish a valid tool-PCB measurement and plausibility of all eight channels in the operating profile. |
| REQ-TH-02 | P05 | Two nozzles and two chamber heaters | **Partial.** The two nozzles actually heated. The V9 reference maps separate left/right chamber heaters. The last field configuration includes only the two nozzle heaters. | Qualify left/right selection, temperature response and shutdown independently before treating chamber regulation as commissioned. |
| REQ-TH-03 | P05 | PID and ready-to-print | **Partial.** Service runs used the nozzle regulator. The recorded native heaters used watermark control, 40% maximum power and a 310 °C maximum. The stock READY latch has its own semantics. | Verify the print profile’s nozzle control, both chamber regulators and start gating at the material targets. |
| REQ-TH-04 | P06 | Temperature supervision | **Partial.** Local sensor checks, heater lease and a 5 s host-loss watchdog exist. The software models implausibility, failure to rise, overshoot and a fall after READY. | Show per-channel fault handling in the qualified profile, separately from the watchdog. |
| REQ-TH-05 | P06 | Fatal priority and output shutdown | **Partial.** MCU and host separate latched faults, output shutdown and position ownership. Normal completion has its own path. | Compare precedence and final actuator states for simultaneous events in controlled tests. |
| REQ-TH-06 | P06 | Extruder PCB and P13 pause | **Software / field open.** The thermal model has material-dependent PCB limits, preflight and a cooling pause. The audited preflight uses 55 or 63 °C depending on the job. | Qualify sensor PC4, the hold until cooling and the guarded return to print on the applicable material profile. |
| REQ-TH-07 | P17 | End-of-job chamber cooling | **Software / field open.** The original successful END cools the nozzles and ramps the chamber towards standby with rear ventilation. The project includes a cooldown policy. | Verify the chamber ramp, rear fan and terminal state after a completed native print and after cancel. |

### Fans and tach feedback

[Current channel names and pins](../orange_pi/inventure_mcu.py) · [MCU service policy](../service_mcu/control_r6/SOURCE_MCU/inventure_control.inc) · [Manual fan test](../service_mcu/control_r6/SOURCE_MCU/inventure_fan_manual.inc)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-FAN-01 | P07 | Six commands and fixed PCB fan | **Partial.** Six commanded channels were identified and exercised manually. PCB_FAN has a fixed supply. The OEM model has separate nozzle-temperature and material-dependent curves. | The field setup used three heater_fan sections. Commission the normal print policy with one output owner and check by physical connector. |
| REQ-FAN-02 | P07 | Four tach channels and fault | **Partial.** F0–F3 have tach feedback. The reference normalises pulses for PWM duty and the model latches after 100 bad evaluations. F4/F5 have no tach input. | Validate channel assignment, thresholds and the visible fault on the installed machine. The manual residual-heat issue is listed above. |
| REQ-FAN-03 | P07 | Part and rear fan during a job | **Software / field open.** The reference separates F1 part cooling from F4 rear ventilation, including successful-end cooling. Local protective cooling is a separate MCU function. | Verify layer commands, start, pause, END and cooling with the native print owner. |

### Material, tags and readers

[Preflight handler](../host/inventure_preflight.py) · [Consumption and writeback](../host/inventure_consumption.py) · [Manual source and tag qualification](INVENTURE_FILAMENT_JAM_R27.md)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-RF-01 | P12 | Both reader links | **Software / field open.** The archived client implements two UART reader paths, retries and the PN532-compatible protocol. R27.1 also accepts a separately selected material source for each nozzle. | Record identification from an actual cassette on each reader. |
| REQ-RF-02 | P12 | Tag format and integrity | **Software.** The archived client checks identity, data records and their CRC. The parser distinguishes nominal stock and remaining stock. | Verify the fields and readback on known cassettes on both sides. |
| REQ-RF-03 | P11 | Material catalogue and identifiers | **Software.** The reference catalogue has 26 records including special entries. The external selection contains 17 model and five support materials. | Check menu selection, tag identity, job compatibility and the corresponding heater/transport profile together. |
| REQ-RF-04 | P12 | RFID preflight, empty and reused cassette | **Software / field open.** The material and preflight modules distinguish empty, incompatible and unreadable media. A recovery source can be selected without a tag. | Check both readers with present, absent and exchanged media, including persistence of the correct source. |
| REQ-RF-05 | P14 | Remaining material and writeback | **Software / field open.** The ledger and optional RFID write/readback path exist. R27 can use the 5% rule only with a verified matched inventory. | Compare the recorded balance before and after a real job and reinsertion. Counting E steps alone cannot establish the balance on the tag. |

### Filament transport and recovery

[Factory transport and operator observations](FACTORY_PROCEDURE_AUDIT_2026-11-12.md) · [Physical cassette load](evidence/CASSETTE1_PHYSICAL_LOAD_2026-11-10.json) · [Replacement workflow](INVENTURE_FILAMENT_JAM_R27_1.md)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-FIL-01 | P13 | Cassette transport sensors | **Partial.** On T0, forward feed from the spool outlet raised A (PD10) while B (PF1) stayed low in both readings. After loading, two seconds of cassette reverse with E stationary raised B while A stayed high. PB12 registered two edges. [T0 field record, 20 November](acceptance/INVENTURE_T0_CASSETTE_OPTICS_2026-11-20.md) | Locate the physical PB12 pickup, repeat the correlation on T1 and check a break with material still present at the inlet. |
| REQ-FIL-02 | P13 | Shared feeder clock and RUN/DIR | **Partial.** There is separate feeder selection and direction plus a common clock. An actual cassette feed and its transport pulses were recorded. | Verify both directions and the right-hand feeder across load, unload and interruption. |
| REQ-FIL-03 | P13 | Cassette loading | **Partial.** Flow and wiping were observed on both nozzles. On 20 November T0 AUTO completed both the short branch at L1 and the long branch at L4. [T0 load results](acceptance/INVENTURE_T0_CASSETTE_OPTICS_2026-11-20.md) | Record the corresponding short and long branches on T1 and exercise the L4 recovery fallback separately. |
| REQ-FIL-04 | P13 | Cassette unloading | **Partial.** The U0–U3 sequence and its clear-inlet condition are implemented. Retraction has physical evidence. | A completed automatic unload on each cassette with the inlet cleared is still a distinct field result. |
| REQ-FIL-05 | P13 | External spool loading and unloading | **Partial.** The operator can choose a spool, load or retract measured nominal portions and wipe. RFID is optional for the standalone replacement sequence. | Verify a real T0 and T1 spool exchange, inlet transitions and visible extrusion after replacement. |
| REQ-FIL-06 | P13 | Feeder assistance during retraction | **Software / field open.** The transport specification distinguishes a pure E retraction from general XYZE travel, and depends on source and sensor state. | Check auxiliary feeder direction and timing on native print retractions for cassette and spool. |
| REQ-FIL-07 | P14 | Classifying runout and jam | **Software / field open.** R27 distinguishes feeder faults, exhausted transport budget and missing material. A blocked external nozzle can still have material at the inlet. | Verify empty, broken and blocked cases against the message shown to the operator. |
| REQ-FIL-08 | P14 | Automatic recovery and return | **Software / field open.** R27.1 provides bounded recovery, cassette fallback, unload/load/wipe steps and a guarded resume. External assisted recovery is a Klipper extension. | Four physical combinations remain open across T0 and T1, each with cassette and external spool, during a real job. |

### Tools, purge and maintenance

[Modular service operations](MODULAR_PROCEDURES_2026-11-10.md) · [Printed comb procedure](NOZZLE_XY_ALIGNMENT_R0323.md) · [Implemented maintenance actions](../orange_pi/inventure_menu_service.py)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-TOOL-01 | P04 | Mechanical nozzle selection | **Field.** T0 → T1 → T0 reseating was completed on the central pad. Five-point leveling also selected T0 automatically. | The field evidence covers the lever sequence for these procedures. |
| REQ-TOOL-02 | P08 | Full and short tool changes | **Partial.** The toolchange module and service selector exist. The field configuration uses a heater-only native extruder and a separate T1 heater. | Qualify the shared E stepper, preloads and offsets in both full and short changes during a dual-material print. |
| REQ-TOOL-03 | P08 | Three purge quantities | **Software / field open.** The reference distinguishes 20/50/100 purge programmes and the project has a purge planner and service output. | Check the commanded total, visible extrusion and final park for the relevant route on each tool. |
| REQ-TOOL-04 | P08 | Wipe, retract and finish over bin | **Partial.** The operator observed cleaning and flow on both tools. Shared wipe and bin parking run in the pad sequence. EMPTY pad runs request zero retraction. | The loaded-material path, final wipe timing and standalone completion need route-specific evidence. |
| REQ-TOOL-05 | P23 | Manual nozzle replacement | **Open workflow.** Hotend maintenance exposes separate heat, feed, retract, purge and wipe actions. | The complete guided stock nozzle-removal and refit workflow needs an identified entry point and an operator acceptance record. |
| REQ-TOOL-06 | P23 | Brush and bin maintenance | **Partial.** The project supports automatic nozzle wiping, parking and bin input. Stock also prompts the operator to clean the brush and empty the bin. | Verify the human maintenance prompts, acknowledgement and return to the correct job state. |

### Print, interlocks and lifecycle

[Cycle state and restart contract](../host/inventure_cycle.py) · [Preflight](../host/inventure_preflight.py) · [Door policy](OEM_DOOR_POLICY.md) · [Leveling with the door policy](INVENTURE_PLATFORM_LEVEL_R26_2.md)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-CYC-01 | P02 | Print state and event priority | **Software / field open.** The host has a print state machine, ownership and physical fault reports. The original has separate application, pause, result and fatal states. | Verify priority between material, door policy, thermal fault and cancellation during a job. |
| REQ-CYC-02 | P15 | Start from idle | **Software / field open.** A preflight module and Orca start logic exist. The native field profile lacks the complete shared-E dual-print setup. | Exercise file and material checks, tray, bin, configured door policy, homing, selection and start of first layer in one run. |
| REQ-CYC-03 | P15 | Heating and READY at start | **Software / field open.** The preflight and thermal layers represent heat readiness. Field nozzle heating succeeded during service. | Verify both chamber zones and the required nozzle targets during start from a cold machine. |
| REQ-CYC-04 | P17 | Normal, programmed and material pauses | **Partial.** Native pause controls and the optional P17 lifecycle exist. R27 adds material intervention and OLED prompts. | Check the differences in cause, motion drain, park and heaters during actual jobs. |
| REQ-CYC-05 | P17 | Resume from ordinary pause | **Software / field open.** The cycle guard tracks the job and tool. R27 tracks return position and restored extrusion reference for an intervention. | Check actual return to the same printed feature, tool, Z, E and speed after an ordinary pause. |
| REQ-CYC-06 | P17 | PCB pause and restored-job pause | **Partial.** Stock P13 waits for PCB cooling and P15 restores a saved job. The port has PCB-pause logic and a current-session pause route. | Qualify P13 with the PCB channel. Durable restore and its special sequence are open. |
| REQ-CYC-07 | P17 | Cancel and stop | **Partial.** The service STOP and native cancellation paths exist. Field records show interrupted service motion. | Verify controlled print cancellation, park, heat/locks cleanup and invalidation of the old job. |
| REQ-CYC-08 | P17 | Successful end and end failures | **Software / field open.** Cycle and Orca END logic exist. The original distinguishes success, cancel and different failure reasons. | Verify chamber ramp, nozzle and fan outputs, platform and completion screen on each applicable exit. |
| REQ-CYC-09 | P23 | System idle, sleep and wake | **Partial.** The port dims and sleeps the display. OEM also has printer-level transitions, heater/driver state, axis validity, 15-minute menu idle and a separate result timeout. | Verify cold idle, result-screen idle, wake and any needed homing in the physical system. |
| REQ-CYC-10 | P16 | Door and lid control | **Partial.** The global menu setting and both switches are implemented. R13 defaults to off at MCU restart. The command mask reports an electrical request. In leveling the service leaves the coils idle. | Reconcile the older native P15/P16 checks with the global option. Check actual bolt operation in modes that command locking and decide persistence across restart. |
| REQ-CYC-11 | P16 | Build tray and waste bin | **Partial.** The inputs are mapped. The original has separate preflight, running and resume behaviour, including a tray pause and a bin/brush prompt. | Verify missing tray before start, tray loss during printing, and the bin prompt at start and resume. |
| REQ-CYC-12 | P15 | Progress and operating time | **Partial.** The port reports run durations and virtual job progress. The reference also accumulates completed minute segments in nonvolatile settings. | Check reported progress and retained runtime across pause, END and restart. |

### Files, printer SD and saved jobs

[Models browser and Save print handler](../orange_pi/inventure_menu_service.py) · [Cycle restart status](../host/inventure_cycle.py)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-SD-01 | P20 | Physical model card and filesystem | **Open for print.** The MCU has a physical SPI3 SD slot. The bootloader can use an SD update. The current Models browser targets the host virtual SD directory. | Qualify mounting and reading print jobs from the printer slot inside the running application. |
| REQ-SD-02 | P22 | File browser, paths and sorting | **Partial.** The OLED sorts and previews supported host files. A model start through this menu uses the preflight manifest route. | Verify equivalent directory and media behaviour if the physical card is the selected source. |
| REQ-SD-03 | P19 | Classic job header | **Software.** The archived P19 inspector parses the classic header and was exercised on real files. The R27.1 browser can list .zcode by extension. | Wire compatibility checks to a qualified playback path and report unsupported files before motion. |
| REQ-SD-04 | P19 | Classic record execution | **Open for print.** The stock dispatch table for opcodes 0x01 through 0x13 has been documented. The converter and static inspection are available. | Demonstrate a complete classic .zcode job with motion, E, tool, thermal, fan and pause commands. |
| REQ-SD-05 | P19 | Normal END and file I/O errors | **Software / integration open.** The reference specifies short reads, retries, EOF and explicit END. A static reader can validate records offline. | Qualify the executing job path and its state after clean EOF, explicit END and lost media. |
| REQ-SD-06 | P21 | Save the print | **Open in R27.1.** The OLED Save print action gives an information message. The published print cycle explicitly reports that its pause checkpoint does not survive reboot. Archived work includes a checkpoint model. | Implement durable saving through the active print owner and verify the file is recoverable after restart. |
| REQ-SD-07 | P21 | Import a stock saved job | **Offline / integration open.** The audit specifies the 329-byte print.zsave layout and the archived work covers offline interpretation. | Qualify import of a known save with the matching model and file position. |
| REQ-SD-08 | P21 | Restore a saved job | **Open on hardware.** The prototype and the stock recovery sequence are documented. The current port offers same-session resume. | Verify source identity, position, tool, temperatures and continuation after a controlled host restart. |
| REQ-SD-09 | P20 | Remount and reopen a job | **Open on hardware.** The reference distinguishes reopening the original source after media changes and a saved-job restore. | Verify card removal, remount, source identity and the correct next record. |

### Retained settings

[Door policy at restart](OEM_DOOR_POLICY.md) · [Material-source file](../orange_pi/inventure_jam_sources.py) · [Leveling result file](../orange_pi/inventure_platform_level.py)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-NVM-01 | P18 | Nonvolatile settings layout | **Software.** The audited schema has 53 slots. Archived tools read and interpret saved data. Only some slots have known active semantics. | Check compatibility against a known settings backup and retain the unexplained entries. |
| REQ-NVM-02 | P18 | Persisting and migrating settings | **Partial.** The port already saves individual calibration and material-source files on the host. Settings migration was prototyped separately. | Verify which preferences, depths, nozzle corrections, source choices and runtime are restored and consumed after restart. |
| REQ-NVM-03 | P18 | Persistent flags and overrides | **Partial.** The original filters flags and activates only selected overrides on boot. The port explicitly resets the global door option with the MCU. | Document intentional differences and verify each retained setting in the component that uses it. |

### Boot and restoration

[Update and active release](../README.md) · [Installer and application scope](SOURCE_AND_LIMITATIONS.md)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-BOOT-01 | P01 | Power-up and wake sequence | **Partial.** The installed custom bootloader and R13 handshake are verified. The host panel has wake handling and the MCU has local cooling. | Accept a complete wake sequence, with the correct actuator and position state, independently of cold boot. |
| REQ-BOOT-02 | P24 | Installation and return to stock | **Field + records.** Bootloader 1.7 and application updates work on the owner’s machine. The archive records an earlier return to stock. | Retain the known-good recovery and build records. This audit did not perform another physical restore. |

### Diagnostics

[Fan and service actions](../orange_pi/inventure_menu_service.py) · [Controller diagnostics](CONTROL_R5_REFERENCE.md)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-DIAG-01 | P23 | Fan test and device information | **Partial.** The OLED and console expose version, fan controls and feedback. Operators exercised the individual outputs. | Check the original diagnostic’s four tach-channel results and limits on the installed printer. |
| REQ-DIAG-02 | P06 | Error codes and event reporting | **Partial.** UART/status data expose causes, affected tool, temperature and motion. The OEM distinguishes warnings, pause reasons and latched fatal faults. | Reconcile the remaining error-to-action and error-to-screen mappings, including acknowledgement and recovery. |

### OLED, controls and indicators

[English menu](../orange_pi/inventure_oem_menu.py) · [Action dispatch](../orange_pi/inventure_menu_service.py) · [OLED and key handling](../orange_pi/inventure_oled.py) · [MCU panel and RGB](../service_mcu/control_r6/SOURCE_MCU/inventure_panel.c)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-UI-01 | P22 | OLED display and transport | **Field + software.** The project brought up the physical panel and provides the host/MCU transport, rendering, status and keys. | Keep hardware panel operation as a baseline. Each screen still needs the corresponding action checked separately. |
| REQ-UI-02 | P22 | Static and dynamic menus | **Partial.** The five main menus are present. The OEM inventory contains 78 static objects plus dynamic views. The current menu dispatch depends on the configured backend. | Trace remaining entries from selection to physical completion or an explicit unavailable result. |
| REQ-UI-03 | P22 | Language and artwork | **By design.** The public port and operator interface use English by project decision. The OEM audit also inventories Polish strings, fonts and icons. | Assess completeness of English operator messages and glyphs. Polish UI parity is outside the chosen port scope. |
| REQ-UI-04 | P22 | Up, Down and OK | **Partial.** Physical inputs and menu navigation exist. Confirmation is used for leveling and material workflows. | Check repeat/long holds, cancel/back and the first button press after wake at the panel. |
| REQ-UI-05 | P23 | Power key | **Partial.** The PA15 input is identified. The port receives power-key events. The original dispatch depends on the application state and gesture length. | Verify short and long press in idle, print, pause and result states without conflating display wake with printer wake. |
| REQ-UI-06 | P23 | RGB status and lighting preference | **Partial.** The pin map and original light modes are documented. The MCU accepts RGB masks and has a fault-light handler. | Verify actual colours for operating states, the chosen preference after restart and fault behaviour. |
| REQ-UI-07 | P23 | Buzzer | **Hardware identity open.** The Inventure audit did not identify the device driven by PG13 or establish a programmable OEM buzzer sequence. | Physical identification is required before assigning a pin or implementing an audible indication. |
| REQ-UI-08 | P23 | Diagnostic overlays | **Partial.** Status, version, service and fault screens exist on the OLED. | Verify warnings, fatal overlays, acknowledgement and priority when another modal prompt is already open. |

### Slicer integration

[Inventure OrcaSlicer integration](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-OrcaSlicer-integration) · [Klipper release](../README.md)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-HIST-01 | P19 | Orca integration and reference jobs | **Partial.** There is a separate Inventure Orca project with single/dual profiles, a converter and job examples. | Keep converter validation separate from acceptance of direct native stock-file playback and a dual Klipper print. |

### Acceptance evidence

[Recorded full pad runs](acceptance/INVENTURE_PAD_R23_2026-11-19.md) · [Cold pad results](acceptance/INVENTURE_PAD_COLD_XY_2026-11-19.md) · [Published service-test workflow](../.github/workflows/orange-pi-host.yml)

| Requirement | Plan | Function | Status and evidence | Remaining acceptance or scope |
|---|---|---|---|---|
| REQ-QA-01 | P24 | Requirement coverage and release evidence | **Audited.** This matrix names all 85 original requirements and all 24 work packages. The project preserves software tests, release identifiers and specific field evidence. | Update the affected row with release, configuration and result when a physical branch is accepted. |

## Verification on 19 November

The audited source tree matches tag `inventure-filament-jam-r27.1` (commit `1dec4a4a1331c31ab0b22af39b6de56edc3e9e9e`). The Orange Pi host suites were rerun from that tree. All 626 general tests, 31 jam integration/monitor/motion/service/source tests and 13 filament-jam state tests passed, for **670 host tests** in total. All **8 MCU register-mock programs** passed. Those checks exercise software behaviour. In a separate read-only check of the optional P16 evaluator with lock-command confirmation disabled, closed covers passed, an open front door produced `OPEN_FRONT_DOOR` and an open lid produced `OPEN_LID`. The field outcomes in this report come from existing printer recordings and operator observations. This review performed no new printer motion, heating or flashing.

The work-package IDs and requirement-to-package assignments were checked against the October plan. All 24 packages and all 85 distinct requirement IDs are represented. [Host CI commands](../.github/workflows/orange-pi-host.yml) · [MCU test runner](../service_mcu/control_r6/TESTS/run_mcu_tests.py)
