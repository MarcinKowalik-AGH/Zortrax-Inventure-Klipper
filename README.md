# Zortrax Inventure for Klipper

This port runs Klipper on the Orange Pi with the original Zortrax Inventure controller, dual-nozzle selector, contact pads and OLED. The printer menu, Fluidd macros and host messages are in English. From the status screen, OK opens **Klipper** and **Zortrax Inventure**. The Zortrax submenu contains Models, Maintenance, Material, Settings and Information.

**R23.4 host operator menu:** the original 66-record firmware tree, including model/support material and pause menus, is mapped to existing Inventure procedure owners. OLED and Fluidd use shared actions and page navigation. The new entry macros remain in **ZORTRAX INVENTURE**. The previous R23.3 leveling UI was accepted by the operator; this expanded menu is software verified and awaits physical acceptance. Existing MCU R23 stays installed. [Update, complete menu and verification](docs/INVENTURE_R23_OPERATOR_MENU.md)

**R23.1 host fix — leveling after Home:** operator SERVICE entry now drains native moves, releases the shared motor enable through Klipper, drains that queued write and checks ownership again before asking the MCU for SERVICE. This resolves the missing host step behind `another_owner_is_busy` after a completed Home. Existing MCU R23 stays installed. [Update and verification](docs/INVENTURE_R23_LEVELING_SERVICE_ENTRY.md)

**Inventure R23 OLED candidate:** this branch combines the single Klipper motion owner, exclusive SERVICE/NATIVE handoff, MCU scheduler and separate safety policies (points 1–4), full operator-cycle acceptance (point 5), and the local MCU OLED graphics, error recovery and transport optimizations. The compatible application is `RC8-CONTROL-R7-HOMING-R23-OLED` for the existing InventureBoot 1.7. [Candidate release and software/physical verification](docs/INVENTURE_R23_RELEASE.md) · [MCU display implementation](docs/INVENTURE_R23_M300_DISPLAY_PARITY.md) · [Full operator-cycle tests](docs/INVENTURE_POINT5_FULL_CYCLE_ACCEPTANCE.md)

**Current project branch: `orange-pi-all-axis`.** The `inventure-homing-r38.6` tag pins the latest published Home Z sequence and retains the accepted five-point platform-leveling implementation. `main` contains the older RC8 source kit. The current installer can also restore the accepted FULL pad calibration already published in this repository; it preserves an existing calibration file. [Published state, installation and known R38.6/R16 errors](docs/INVENTURE_FRESH_W2_R38_6.md)

**Complete OLED installation:** the fresh-host installer now includes the original DUAL-THERMAL-V2 renderer that incremental updates retained on the old Orange Pi. It shows both nozzles and both chamber zones, with the current compact footer, graphics and transport optimizations. Its original source, provenance, exact checksum and framebuffer integration test are included in the repository. Existing verified R38.6 installations are restored in place, with the renderer and runtime manifest backed up together. [Why the dependency was missing and how it is restored](docs/INVENTURE_FRESH_W2_R38_6.md#odtworzony-oled-z-dwoma-dyszami-i-dwiema-strefami-komory)

**R38.6 performs one XY homing before T0 preparation and the native Z reference.** MCU R16 preserves the XY reference through the ownership handoff and lowers the table 1 mm locally after contact measurement. [Sequence, installation and verification](docs/INVENTURE_HOME_SEQUENCE_R38_6.md)

**Moving the printer to a new Orange Pi / W2:** use [`install-new-host.py`](install-new-host.py) and the [complete W2 installation procedure](docs/INVENTURE_FRESH_W2_R38_6.md). Fetching the R38.6 worktree alone does not switch the service. The old `kinematics: none` diagnostic skeleton needs an explicitly recovered machine profile and a separate pinned Klipper runtime; the ordinary updater below is for an existing native Inventure installation. The new installer verifies the actual running path, host version, MCU R16 and UART, and writes a deployment receipt.

**MCU R15 restores native heater control after nozzle cleaning.** Install the MCU application on printers running Orange Pi host R38.4. [Release and installation](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-heater-handoff-r15) · [Failure trace and verification](docs/INVENTURE_HEATER_HANDOFF_R15.md)

**Home Z shutdown readback in R38.4.** The Orange Pi can read the final native Z probe counters from a stopped MCU R14. A host-only update can recover the current incident while it is still in MCU memory. [R38.4 release and recovery instructions](docs/INVENTURE_HOME_Z_R38_4.md) · [Home Z field results and safety audit](docs/INVENTURE_HOME_Z_SAFETY_AUDIT_2026-11-22.md)

**R38.3 makes Home Z usable immediately after a restart.** Fluidd and the OLED use the full native homing sequence when XY is unknown and the Z-only sequence when both axes are referenced. [R38.3 release](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-homing-r38.3) · [Homing sequence and evidence](docs/INVENTURE_HOME_Z_R38_3.md)

**R38.2 fixes the Orange Pi upgrade when Klipper reports that the primary MCU is already shut down. The installer preserves the verified host, checks the Fluidd macro groups and reports the MCU state separately.** [R38.2 release](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-homing-r38.2) · [Installer audit and recovery](docs/INVENTURE_INSTALLER_R38_2.md)

**R38.1 fixes Home All from Fluidd and OLED, controller handoff at idle, cassette selection, heater targets and the six-channel fan test.** The release audit distinguishes working service procedures from functions that still require native print commissioning. [R38.1 release](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-homing-r38.1) · [Procedure audit, installation and physical evidence](docs/INVENTURE_HOMING_R38.md)

**Host R38.4 and MCU R14** use the M300 configuration and menu layout with Inventure hardware. Display preferences live in `inventure_display.cfg` under `[inventure_display]`. The physical panel, Inventure sensors and both chamber heating zones each retain their own configuration. Fluidd shows Extruder 0, Extruder 1, Chamber and the OLED. [R37 release](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-layout-r37) · [File and menu mapping](docs/INVENTURE_LAYOUT_R37.md) · [Project rules](AGENTS.md)

<a id="aktualizacja-orange-pi"></a>

## Update an existing native Inventure installation

With Klipper idle, run the following in the Orange Pi shell. The installer updates the host, installs the printer macros and assigns their Fluidd categories in Moonraker, and restarts Klipper. Install MCU R16 from the same release for the continuous XY ownership transfer and local post-measurement retreat.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-homing-r38.6 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-homing-r38.6" refs/tags/inventure-homing-r38.6 &&
python3 "$HOME/inventure-homing-r38.6/install-orange-pi.py" --configure-fluidd --organize-config
```

If the R38.6 worktree already exists at that tag, run its final installer command. If you see `HOST_INSTALLED_MCU_SHUTDOWN`, review the shutdown cause in `klippy.log`. After correcting the cause, issue `FIRMWARE_RESTART` once in Fluidd and wait for Ready. Once Klipper is ready, enter the following in the **Fluidd console**.

```gcode
INVENTURE_VERSION
INVENTURE_MAIN_MENU
INVENTURE_OLED_STATUS
```

The host should report `RC8-CONTROL-R7-HOMING-R38.6` and the MCU `RC8-CONTROL-R7-HOMING-R16`. Refresh the existing Fluidd page after the installer reports the active `fluidd_root`. The category editor can show additional diagnostic and recovery controls. [Setup, backup and recovery](docs/INVENTURE_FLUIDD_R30.md)

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
