# Zortrax Inventure project rules

## Shared configuration and operator interface

Use Zortrax M300 Classic as the reference for the organization of both ports. Before adding or changing a feature, read the corresponding active M300 configuration, menu and host implementation. Follow its file role, section and option names, order, units, English menu labels, navigation, macro grouping and installation conventions wherever the operator is doing the same thing.

Use the Inventure printer prefix. For example, the counterpart of `m300_display.cfg` and `[m300_display]` is `inventure_display.cfg` and `[inventure_display]`. Put the physical panel and Klipper display status in the base configuration. Keep sensor conversions, heater control, services, operations and the common Klipper calibration controls in their respective files. Each setting has one active owner. Preserve existing operator choices, tuned values and the Klipper save block during upgrades.

Implement each entry through the Inventure hardware owner. Use this printer's wiring, sensor maps, temperature limits, PID zones, interlocks, tool selection, motion and filament procedures. Keep the two nozzles, chamber, cassette and spool paths visible where relevant. Group model-specific entries alongside their related function. Document hardware differences when an M300 entry has no Inventure counterpart.

The physical OLED and Fluidd must expose the same actions and settings for shared controls. Use English in the adaptation. Verify the installed include tree, startup and upgrade from the preceding public release. Publish tested installation changes through the Inventure GitHub repository and identify the compatible MCU. Report software verification and observations on a physical printer separately.

[Reference M300 project](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper)
