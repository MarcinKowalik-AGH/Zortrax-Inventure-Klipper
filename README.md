# Zortrax Inventure for Klipper

This port runs Klipper on the Orange Pi with the original Zortrax Inventure controller, dual-nozzle selector, contact pads and OLED. The printer menu, Fluidd macros and host messages are in English. The five main OLED entries are Models, Maintenance, Material, Settings and Information.

**Host R30 with MCU R13** installs two Fluidd groups. **ZORTRAX INVENTURE** contains the English `INVENTURE_*` printer controls. **Klipper** contains native print, PID and display utilities supported by the installed profile. R29 cassette settings and manual feed are available for both nozzles. [R30 release](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-fluidd-r30) · [M300 comparison, validation and outstanding work](docs/INVENTURE_FLUIDD_R30.md)

<a id="aktualizacja-orange-pi"></a>

## Update an existing Orange Pi installation

With Klipper idle, run the following in the Orange Pi shell. The installer updates the host, installs the printer macros and assigns their Fluidd categories in Moonraker, and restarts Klipper. Keep the installed R13 MCU.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-fluidd-r30 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-fluidd-r30" refs/tags/inventure-fluidd-r30 &&
python3 "$HOME/inventure-fluidd-r30/install-orange-pi.py" --configure-fluidd
```

If this tagged worktree already exists, run the final installer command there. Once Klipper is ready, enter the following in the **Fluidd console**.

```gcode
INVENTURE_VERSION
INVENTURE_CASSETTE_STATUS
```

The host should report `RC8-CONTROL-R7-FLUIDD-R30` and the MCU `RC8-CONTROL-R7-PAD-THERMAL-R13`. Refresh the existing Fluidd page after the installer reports `GROUPS_INSTALLED` or `ALREADY_GROUPED`. The category editor can show additional diagnostic and recovery controls. [Setup, backup and recovery](docs/INVENTURE_FLUIDD_R30.md)

## Printer procedures

| Task | Operator interface | What it measures or controls |
|---|---|---|
| Load or unload a cassette | `INVENTURE_FILAMENT_SAVE`, `INVENTURE_LOAD`, `INVENTURE_UNLOAD`, `INVENTURE_FEED_MORE`, `INVENTURE_CASSETTE_JOG` | Separate temperatures and speeds for both nozzles, with manual portions over the waste bin. [Operator commands](docs/INVENTURE_FILAMENT_PROFILES_R29.md) |
| Recover filament transport | `INVENTURE_JAM_REPORT`, `INVENTURE_JAM_MISSING`, `INVENTURE_JAM_BROKEN`, OLED recovery prompts | Cassette and external spool recovery for the selected nozzle. [Requirements and operator actions](docs/INVENTURE_FILAMENT_JAM_R28.md) |
| Level the platform | `INVENTURE_LEVEL`, `INVENTURE_LEVEL_NEXT`, `INVENTURE_LEVEL_PROGRESS`, `INVENTURE_LEVEL_SAVE`, `INVENTURE_LEVEL_STOP` | Five tray pads and three adjustments using T0. The saved report contains platform depth and center deviation. |
| Set the door policy | **Settings → Door lock**, `INVENTURE_DOOR_LOCK` | One global setting for the front door and lid. Read its current state with `INVENTURE_DOOR_POLICY`. |
| Align the nozzles | `INVENTURE_PAD_CALIBRATE ACTION=STATUS` | The full central pad procedure measures T0, T1 and T0 after reseating. It reports the relative XY offset and Z difference. |
| Inspect service procedures | `INVENTURE_PROCEDURE ACTION=LIST` | Homing, parking, selecting a nozzle, wiping, purging, loading and unloading. Use `INVENTURE_PROCEDURE ACTION=PARAMS TOOL=T0 PROCEDURE=SELECT` to inspect one operation. |
| Inspect the shared menu | `INVENTURE_ZORTRAX OP=CATALOG` | Available firmware-backed actions and their status. The OLED and the console use the same procedure handlers. |

Two full central pad runs at 100 °C were saved on R23 with empty nozzles and a 0.1 mm edge tolerance. Individual cold center measurements on R25 returned T0 at (81.7, 68.3) mm and T1 at (70.8, 68.8) mm. The R26.3 five-point run completed and was saved on the printer, with a platform depth of 138.349 mm and a center deviation of +0.031 mm. [Full calibration runs](docs/acceptance/INVENTURE_PAD_R23_2026-11-19.md) · [Individual cold measurements](docs/acceptance/INVENTURE_PAD_COLD_XY_2026-11-19.md)

## Service reference

- Homing and contact detection use the X/Y endstops, the PE12 lower Z endstop and PC14 nozzle contact. [Native homing](docs/NATIVE_Z_COREXY.md) · [Recovered contact and homing sequence](docs/Z_CONTACT_AND_HOMING_R0323.md)
- T0 and T1 share the service sequence for homing, selecting a tool, parking over the waste bin and wiping. Material transport uses the selected tool's feeder and service profile. [Modular procedures](docs/MODULAR_PROCEDURES_2026-11-10.md) · [Cartridge loading and unloading](docs/FACTORY_LOAD_2026-11-10.md)
- The central pad alignment procedure has separate commands and saved results. [Nozzle calibration](docs/NOZZLE_PAD_CALIBRATION.md) · [Original nozzle XY alignment](docs/NOZZLE_XY_ALIGNMENT_R0323.md)
- Native Klipper control and MCU service control have separate output owners. The version report identifies the running host and MCU, supported ABIs, heater limits and connection state. [MCU control and pins](docs/CONTROL_R5_REFERENCE.md) · [Door policy](docs/OEM_DOOR_POLICY.md)
- The repository retains the firmware evidence, previous commands and physical trial results. [Source provenance](docs/SOURCE_AND_LIMITATIONS.md) · [R26/R26.1 firmware audit in Polish](docs/INVENTURE_PLATFORM_LEVEL_R26.md) · [Archived R26.1 Polish README and service examples](README_PL.md)
