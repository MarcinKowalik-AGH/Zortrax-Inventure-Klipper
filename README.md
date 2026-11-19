# Zortrax Inventure for Klipper

This port runs Klipper on the Orange Pi with the original Zortrax Inventure controller, dual-nozzle selector, contact pads and OLED. The printer menu, Fluidd macros and host messages are in English. The five main OLED entries are Models, Maintenance, Material, Settings and Information.

**Host R27.1 with MCU R13** adds filament jam controls to the OLED and Fluidd. The recovery path depends on the active nozzle and whether its filament comes from a cassette or an external spool. The status command reports the printer's recovery state. [R27.1 operator instructions and hardware prerequisites](docs/INVENTURE_FILAMENT_JAM_R27_1.md) · [R27.1 release](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-filament-jam-r27.1)

<a id="aktualizacja-orange-pi"></a>

## Update an existing Orange Pi installation

With Klipper idle, run the following in the Orange Pi shell. The installer updates the host, adds the filament recovery buttons and native Klipper pause support to the active configuration, and restarts Klipper. Keep the installed R13 MCU.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-filament-jam-r27.1 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-filament-jam-r27.1" refs/tags/inventure-filament-jam-r27.1 &&
python3 "$HOME/inventure-filament-jam-r27.1/install-orange-pi.py" --configure-jam
```

If this tagged worktree already exists, run the final installer command there. Once Klipper is ready, enter the following in the **Fluidd console**.

```gcode
INVENTURE_VERSION
ZORTRAX_JAM_STATUS
```

The host should report `RC8-CONTROL-R7-FILAMENT-JAM-R27.1` and the MCU `RC8-CONTROL-R7-PAD-THERMAL-R13`. On a fresh installation, add `--configure-leveling` to the same installer command to install the leveling and global door policy buttons as well. [Five-point leveling instructions](docs/INVENTURE_PLATFORM_LEVEL_R26_2.md#run-the-procedure)

## Printer procedures

| Task | Operator interface | What it measures or controls |
|---|---|---|
| Recover filament transport | `ZORTRAX_JAM_STATUS`, `ZORTRAX_JAM_REPORT`, OLED recovery prompts | Cassette and external spool recovery for the selected nozzle. [Requirements and operator actions](docs/INVENTURE_FILAMENT_JAM_R27_1.md) |
| Level the platform | `ZORTRAX_LEVEL`, `ZORTRAX_LEVEL_NEXT`, `ZORTRAX_LEVEL_STATUS`, `ZORTRAX_LEVEL_SAVE`, `ZORTRAX_LEVEL_CANCEL` | Five tray pads and three adjustments using T0. The saved report contains platform depth and center deviation. |
| Set the door policy | **Settings → Door lock**, `ZORTRAX_DOOR_LOCK` | One global setting for the front door and lid. Read its current state with `INVENTURE_DOOR_POLICY`. |
| Align the nozzles | `ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS` | The full central pad procedure measures T0, T1 and T0 after reseating. It reports the relative XY offset and Z difference. |
| Inspect service procedures | `INVENTURE_PROCEDURE ACTION=LIST` | Homing, parking, selecting a nozzle, wiping, purging, loading and unloading. Use `INVENTURE_PROCEDURE ACTION=PARAMS TOOL=T0 PROCEDURE=SELECT` to inspect one operation. |
| Inspect the shared menu | `INVENTURE_ZORTRAX OP=CATALOG` | Available firmware-backed actions and their status. The OLED and the console use the same procedure handlers. |

Two full central pad runs at 100 °C were saved on R23 with empty nozzles and a 0.1 mm edge tolerance. Individual cold center measurements on R25 returned T0 at (81.7, 68.3) mm and T1 at (70.8, 68.8) mm. The R26.3 five-point run completed and was saved on the printer, with a platform depth of 138.349 mm and a center deviation of +0.031 mm. [Full calibration runs](docs/acceptance/INVENTURE_PAD_R23_2026-11-19.md) · [Individual cold measurements](docs/acceptance/INVENTURE_PAD_COLD_XY_2026-11-19.md)

## Service reference

- Homing and contact detection use the X/Y endstops, the PE12 lower Z endstop and PC14 nozzle contact. [Native homing](docs/NATIVE_Z_COREXY.md) · [Recovered contact and homing sequence](docs/Z_CONTACT_AND_HOMING_R0323.md)
- T0 and T1 share the service sequence for homing, selecting a tool, parking over the waste bin and wiping. Material transport uses the selected tool's feeder and service profile. [Modular procedures](docs/MODULAR_PROCEDURES_2026-11-10.md) · [Cartridge loading and unloading](docs/FACTORY_LOAD_2026-11-10.md)
- The central pad alignment procedure has separate commands and saved results. [Nozzle calibration](docs/NOZZLE_PAD_CALIBRATION.md) · [Original nozzle XY alignment](docs/NOZZLE_XY_ALIGNMENT_R0323.md)
- Native Klipper control and MCU service control have separate output owners. The version report identifies the running host and MCU, supported ABIs, heater limits and connection state. [MCU control and pins](docs/CONTROL_R5_REFERENCE.md) · [Door policy](docs/OEM_DOOR_POLICY.md)
- The repository retains the firmware evidence, previous commands and physical trial results. [Source provenance](docs/SOURCE_AND_LIMITATIONS.md) · [R26/R26.1 firmware audit in Polish](docs/INVENTURE_PLATFORM_LEVEL_R26.md) · [Archived R26.1 Polish README and service examples](README_PL.md)
