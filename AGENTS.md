# Zortrax Inventure project rules

## Shared configuration and operator interface

Use Zortrax M300 Classic as the reference for the organization of both ports. Before adding or changing a feature, read the corresponding active M300 configuration, menu and host implementation. Follow its file role, section and option names, order, units, English menu labels, navigation, macro grouping and installation conventions wherever the operator is doing the same thing.

Use the Inventure printer prefix. For example, the counterpart of `m300_display.cfg` and `[m300_display]` is `inventure_display.cfg` and `[inventure_display]`. Put the physical panel and Klipper display status in the base configuration. Keep sensor conversions, heater control, services, operations and the common Klipper calibration controls in their respective files. Each setting has one active owner. Preserve existing operator choices, tuned values and the Klipper save block during upgrades.

Implement each entry through the Inventure hardware owner. Use this printer's wiring, sensor maps, temperature limits, PID zones, interlocks, tool selection, motion and filament procedures. Keep the two nozzles, chamber, cassette and spool paths visible where relevant. Group model-specific entries alongside their related function. Document hardware differences when an M300 entry has no Inventure counterpart.

The physical OLED and Fluidd must expose the same actions and settings for shared controls. Use English in the adaptation. Verify the installed include tree, startup and upgrade from the preceding public release. Publish tested installation changes through the Inventure GitHub repository and identify the compatible MCU. Report software verification and observations on a physical printer separately.

Exercise the literal commands emitted by Fluidd buttons and the corresponding OLED selections through the installed Klipper macros. Cover normal use after restart and after the preceding procedure, including confirmation, cancellation and refusal under a real interlock condition. Record which stages reached the hardware interface in simulation and which were observed on the printer.

[Reference M300 project](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper)

## Preserve procedures accepted on the physical printer

An instruction to integrate an accepted procedure into the project, OLED menu,
Fluidd macros or a macro group authorizes that integration. Preserve the
procedure's existing sequence, coordinates, speeds, distances, temperatures,
timing, ownership transfers, reference calculations and acceptance conditions.
Use the same existing public entry point for OLED and Fluidd. Do not replace or
reconstruct its motion logic while performing UI or configuration integration.

An OEM or M300 presentation reference does not authorize rewriting an already
accepted Inventure procedure. Treat a suspected movement defect as a separate
investigation: identify the physically accepted version and show the exact
behavioral difference before changing it. Simulation results do not replace
physical acceptance or authorize speculative motion changes.

Keep accepted implementation references and execution evidence separate from
new candidates. Do not label a source file physically accepted solely because
it resembles a successful log; recover its exact identity from release/runtime
records or backups. Record any unresolved identity rather than inventing it.
For the current Home incident, start with
[the three completed runs and regression scope](docs/INVENTURE_ACCEPTED_HOME_2026-11-25.md).
For standalone Home X/Y and the actual G28 route, also read
[the source and execution audit](docs/INVENTURE_HOME_XY_REFERENCE_2026-11-25.md).
The original r0323 binary independently confirms that the XY reference is
assigned after the reverse release phase. Keep this firmware fact separate
from which Klipper entry point actually implements it; see
[the instruction-level evidence](docs/INVENTURE_OEM_XY_RELEASE_EVIDENCE_2026-11-26.md).

## R24 operator Home repair

The operator explicitly requested separate firmware-style X/Y release homing,
held drives, and the accepted T0/Z sequence with reuse of valid XY. The
`operator-home-r24` profile implements that routing around the frozen R38.6
engines. It requires the matched R24 MCU held-service capability; do not
activate its host on R23 or remove motor disable from ordinary SERVICE entry.
Read [the R24 contract and verification limits](docs/INVENTURE_HOME_R24.md).
The new combination still needs physical acceptance. Preserve its tested
sequence during later UI/configuration integration; a source comparison or
simulation alone does not establish acceptance on the actual printer.
