# Inventure R23.4 host: Klipper and Zortrax Inventure on the OLED

Press OK on the printer status screen to enter the two-item launcher:
**Klipper** and **Zortrax Inventure**. The latter opens the original Inventure
sections: **Models, Maintenance, Material, Settings, Information**. Its title is
also **Zortrax Inventure**, matching the **ZORTRAX INVENTURE** Fluidd macro group.

This release extends the working R23.3 host UI. The operator confirmed the
previous leveling screens work on 25 November 2026. The existing R23 MCU stays
installed; the IVB is byte-for-byte unchanged. No MCU or bootloader update is
required for this menu release.

## Original menu and existing procedure owners

The read-only r0323 firmware audit supplies all **66 menu records**, including
duplicated material paths used during a pause. Every record is mapped to a
page, action, caption or spacer in
`docs/validation/INVENTURE_OEM_MENU_R0323.json`. Original firmware callback
addresses are provenance only; the host never invokes them.

| OLED path | Shared owner / Fluidd entry |
|---|---|
| Zortrax Inventure | `INVENTURE_MAIN_MENU` |
| Maintenance | `INVENTURE_MAINTENANCE` |
| Maintenance → Platform leveling | Existing five-point `INVENTURE_LEVEL` wizard, screw graphics and result/save/cancel controls |
| Maintenance → Nozzle alignment calibration | Existing enabled nozzle XY test; its geometry, material and temperature checks remain required |
| Maintenance → Hotend maintenance → Left / Right | Existing T0/T1 selection, heat, purge, wipe, feed and retract; `INVENTURE_NOZZLE_SELECT`, `INVENTURE_HEAT`, `INVENTURE_NOZZLE_PURGE`, `INVENTURE_CLEAN` |
| Maintenance → Home X/Y/Z/XY/XYZ | Existing native Home owner and `INVENTURE_HOME_*` macros |
| Maintenance → Move the platform | Existing referenced native Z; `INVENTURE_PLATFORM_UP` / `INVENTURE_PLATFORM_DOWN` |
| Maintenance → Fan test | Six existing fan channels; `INVENTURE_FAN_TEST_START`, `INVENTURE_FAN_CHANNEL`, `INVENTURE_FAN_TEST_END` |
| Material → Model / Support | `INVENTURE_MATERIAL_MENU`; existing load/unload owner for the selected tool |
| Material → Model / Support → Load from cartridge | `INVENTURE_LOAD TOOL=T0/T1`; saved temperature/speed profile and attended portions over the waste bin |
| Material → Model / Support → Unload | `INVENTURE_UNLOAD TOOL=T0/T1`; the same saved profile and manual finish/portion controls |
| Material → Load from spool | Existing model/support catalogue and external-spool service steps, with explicit temperature selection |
| Settings | `INVENTURE_SETTINGS`; original Language, Models sorting, Door lock, File checking, Ignore material endstops, followed by shared light/OLED settings |
| Information | `INVENTURE_INFORMATION`; versions, printer information and help |
| Models | Existing virtual-SD browser, sorting and qualified P15 job preflight |
| Paused print → Material/Maintenance | Original model/support paths, hotend maintenance and Save the print entry; native pause/resume/cancel ownership is retained |
| Klipper | `INVENTURE_KLIPPER_MENU`; printer/job/temperature status, Home/platform/nozzle controls, fans, settings, information and diagnostics |

The new page-entry macros appear in **ZORTRAX INVENTURE**. Existing visibility
choices and custom Fluidd groups are preserved. A page opened from Fluidd has
the same rows, selection and parent navigation as the physical panel. Its
buttons use the shared view token; a button from an earlier page is refused.
Power/Back returns one parent at a time, then to the printer status screen.

The interface remains English, as in the M300 adaptation. The original
**Polski** entry is retained and explains that an English-only adaptation is
active. It does not falsely report a language change. Similarly, entries that
need a disabled P13/P15 execution contract show the actual capability refusal.
`Save the print` does not claim persistence across a host restart. Models use
the configured host virtual SD, not an unconfigured MCU card reader.

The preliminary leveling procedure remains separate from the printing mesh
under Tune. This release adds no new mesh implementation or hardware procedure.

## Update the Orange Pi

After saving or cancelling the current procedure, enter in Fluidd:

```gcode
TURN_OFF_HEATERS
FIRMWARE_RESTART
```

Wait for Ready. Run on the Orange Pi as the Klipper user:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-r23-oem-menu-rc5 &&
python3 install-new-host.py --restore-field-profile --serial /dev/ttyS5 --baud 750000 --apply
```

The updater recognizes the exact preceding RC4 runtime. It replaces five UI
modules and their authenticated manifest, stages the requested launcher and
new macros, and preserves field tuning, SAVE_CONFIG, filament profiles and
platform/nozzle calibration files. Failed installation rolls back the runtime,
configuration and Fluidd transaction. The Ready process must report the
matching UI version and both launcher entries before installation succeeds.

Check in Fluidd:

```gcode
INVENTURE_ZORTRAX OP=STATUS
INVENTURE_MAIN_MENU
```

Status must contain **`"ui_release": "RC8-OPERATOR-MENU-R23.4"`**.
`INVENTURE_OLED_STATUS` also reports `operator_menu_root: launcher`,
`operator_menu_roots: ["Klipper", "Zortrax Inventure"]` and the matching
`procedure_ui_release`. The base host revision still identifies pinned R38.6;
the UI marker identifies this independently authenticated presentation layer.

## Verification and physical observations

Software acceptance executes literal installed Klipper macros with the real
parser/Jinja engine and compares physical OLED selections through the same
owners. T0 and T1 select/heat/clean/purge/load/unload are compared after a fresh
start and again after completed Home. The test checks parameters, saved filament
profiles, SERVICE handoff, simulated MCU access, cancellation and refusal while
printing. Original Home/leveling cycles, door pauses, screw corrections and stale
input guards remain tested. All 66 original records and their child order are
checked; 26 pages and 127 cursor positions render actual 8192-byte OLED buffers.

The release includes the rendered menu gallery, full page inventory and
installed operator-cycle report. These are software simulations. The successful
physical R23.3 leveling UI is prior evidence; the expanded R23.4 menu still needs
checking on the printer after installation.
