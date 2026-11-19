# Zortrax Inventure for Klipper

This port runs Klipper on the Orange Pi with the original Zortrax Inventure controller, dual-nozzle selector, contact pads and OLED. The printer menu, Fluidd macros and host messages are in English. The five main OLED entries are Models, Maintenance, Material, Settings and Information.

**Host R26.2 with MCU R13** starts five-point platform leveling directly from the OLED or Fluidd. At the waste bin it preheats according to the known selector state, then physically selects T0 before the first contact with the platform. The operator adjusts the three knobs as prompted. The separate **Settings → Door lock** option controls door and lid supervision. [Five-point procedure, temperatures and controls](docs/INVENTURE_PLATFORM_LEVEL_R26_2.md) · [R26.2 release](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-platform-level-r26.2)

<a id="aktualizacja-orange-pi"></a>

## Update an existing Orange Pi installation

With Klipper idle, run the following in the Orange Pi shell. The installer updates the host, installs the Fluidd leveling and door macros, and restarts Klipper. An installed R13 MCU can run this release.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-platform-level-r26.2 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-platform-level-r26.2" refs/tags/inventure-platform-level-r26.2 &&
python3 "$HOME/inventure-platform-level-r26.2/install-orange-pi.py" --configure-leveling
```

If this tagged worktree already exists, run the final installer command there. Once Klipper is ready, enter the following in the **Fluidd console**.

```gcode
INVENTURE_VERSION
ZORTRAX_LEVEL_STATUS
```

The host should report `RC8-CONTROL-R7-PLATFORM-LEVEL-R26.2`, the MCU `RC8-CONTROL-R7-PAD-THERMAL-R13`, and a fresh leveling session `phase: idle`. To start, fit the original tray, check that its five contact pads are clean, and use **Maintenance → Platform leveling** or `ZORTRAX_LEVEL`. This requires the saved full T0/T1 central pad calibration beside the active `printer.cfg`. [Full operator instructions](docs/INVENTURE_PLATFORM_LEVEL_R26_2.md)

## Printer procedures

| Task | Operator interface | What it measures or controls |
|---|---|---|
| Level the platform | `ZORTRAX_LEVEL`, `ZORTRAX_LEVEL_NEXT`, `ZORTRAX_LEVEL_STATUS`, `ZORTRAX_LEVEL_SAVE`, `ZORTRAX_LEVEL_CANCEL` | Five tray pads and three adjustments using T0. The saved report contains platform depth and center deviation. |
| Set the door policy | **Settings → Door lock**, `ZORTRAX_DOOR_LOCK` | One global setting for the front door and lid. Read its current state with `INVENTURE_DOOR_POLICY`. |
| Align the nozzles | `ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS` | The full central pad procedure measures T0, T1 and T0 after reseating. It reports the relative XY offset and Z difference. |
| Inspect service procedures | `INVENTURE_PROCEDURE ACTION=LIST` | Homing, parking, selecting a nozzle, wiping, purging, loading and unloading. Use `INVENTURE_PROCEDURE ACTION=PARAMS TOOL=T0 PROCEDURE=SELECT` to inspect one operation. |
| Inspect the shared menu | `INVENTURE_ZORTRAX OP=CATALOG` | Available firmware-backed actions and their status. The OLED and the console use the same procedure handlers. |

The nozzle alignment and five-point leveling tests have separate hardware records. Two full central pad runs at 100 °C were saved on R23 with empty nozzles and a 0.1 mm edge tolerance. Individual cold center measurements on R25 returned T0 at (81.7, 68.3) mm and T1 at (70.8, 68.8) mm. Physical acceptance of the full R26.2 five-point leveling cycle is pending. [Full calibration runs](docs/acceptance/INVENTURE_PAD_R23_2026-11-19.md) · [Individual cold measurements](docs/acceptance/INVENTURE_PAD_COLD_XY_2026-11-19.md)

## Service reference

- Homing and contact detection use the X/Y endstops, the PE12 lower Z endstop and PC14 nozzle contact. [Native homing](docs/NATIVE_Z_COREXY.md) · [Recovered contact and homing sequence](docs/Z_CONTACT_AND_HOMING_R0323.md)
- T0 and T1 share the service sequence for homing, selecting a tool, parking over the waste bin and wiping. Material transport uses the selected tool's feeder and service profile. [Modular procedures](docs/MODULAR_PROCEDURES_2026-11-10.md) · [Cartridge loading and unloading](docs/FACTORY_LOAD_2026-11-10.md)
- The central pad alignment procedure has separate commands and saved results. [Nozzle calibration](docs/NOZZLE_PAD_CALIBRATION.md) · [Original nozzle XY alignment](docs/NOZZLE_XY_ALIGNMENT_R0323.md)
- Native Klipper control and MCU service control have separate output owners. The version report identifies the running host and MCU, supported ABIs, heater limits and connection state. [MCU control and pins](docs/CONTROL_R5_REFERENCE.md) · [Door policy](docs/OEM_DOOR_POLICY.md)
- The repository retains the firmware evidence, previous commands and physical trial results. [Source provenance](docs/SOURCE_AND_LIMITATIONS.md) · [R26/R26.1 firmware audit in Polish](docs/INVENTURE_PLATFORM_LEVEL_R26.md) · [Archived R26.1 Polish README and service examples](README_PL.md)
