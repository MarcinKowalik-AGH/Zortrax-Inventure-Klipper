# Inventure R35 host and R14 display firmware

R35 uses the Inventure 1.6.1 r0323 sensor table, graphics and sleep messages. Host R34 used the M300 activity sprite set and selected the M300 minute timers on new installations. R35 keeps the M300 design for modular files, changed display regions, acknowledgements and bounded recovery. The Inventure controller provides the machine data and handles its own faults.

## Install

With the printer idle, install the host in the Orange Pi shell.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-native-ui-r35 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-native-ui-r35" refs/tags/inventure-native-ui-r35 &&
python3 "$HOME/inventure-native-ui-r35/install-orange-pi.py" --configure-fluidd --organize-config
```

An existing checkout at this tag can run the last command directly. Add `--check` for a read-only migration preview. The installer uses the running Klipper stage, backs up changed files and reports the active configuration paths. An R13 controller can run the new host, graphics and heater configuration. For autonomous fault pages, local brightness and sleep lighting, use the paired R14 application.

Download **INVENTURE_MCU_NATIVE_OLED_R14_BOOT17.zip** from this release. Extract it and copy **INVENTUR.IVB** to the SD card for the usual **InventureBoot 1.7 application update**. The Orange Pi installer does not flash the board. R14 reads the P4 rate already selected in InventureBoot. The application starts at 0x08010000. [GitHub release](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-native-ui-r35)

In the Fluidd console after reconnecting:

```gcode
INVENTURE_VERSION
INVENTURE_OLED_STATUS
```

Expect host `RC8-CONTROL-R7-NATIVE-UI-R35`, MCU `RC8-CONTROL-R7-DISPLAY-R14` and `temperature_map_source: printer_config`. The `local_oled_diag` snapshot reports current panel ownership state, the most recent takeover, board fault, implicated input, local SPI failure and actual stored RAM brightness. It arrives in the regular OLED status response. An empty snapshot during initial connection can be queried again after the first sample.

## Configuration files

Paths are relative to the active `printer.cfg`.

| File | Contents on Inventure |
|---|---|
| `inventure/temperature_sensors.cfg` | T0 PC0 and T1 PF8 use 3300 mV, a 12-bit ADC and 5 mV/°C. Chamber left PC1, right PC2 and center PA4, the PSU, mainboard and tool PCB use the 205 original ADC thresholds. The table starts at 0x080296B6. |
| `inventure/heaters.cfg` | Heater pins, sensor channels, PWM periods, smooth time, power and temperature limits of the configured V9 heaters. |
| `inventure/heater_control.cfg` | The existing control algorithm, PID coefficients or watermark hysteresis for each native heater, grouped by T0, T1, chamber left and chamber right. Explicit heater options and values tuned later with Klipper’s `SAVE_CONFIG` take precedence. A conflicting custom overlay stays in its original file. |
| `inventure/chamber.cfg` | The common chamber target and its two independently regulated heaters, left PC9 and right PC8. PA4 supplies the center reading on the Fluidd Chamber row. |
| `inventure/cooling.cfg`, `inventure/heater_monitors.cfg` | Present native heater fans and heater verification sections. Installations without those sections create no empty files. |
| `inventure/display.cfg` | The installed Inventure panel options and the site's defaults for brightness, idle, wake notice and sleep lighting. Explicit operator choices persist in `inventure_menu_settings.json`. |
| `moonraker.conf` and its includes | Keep the installation's socket, access policy, macro groups and updater settings. The organizer removes duplicate trusted-client entries and conflicting stock Fluidd updaters from the R34 setup. |

The complete stock firmware SHA-256 is `9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`. The 820 sensor-table bytes hash to `dbe1775fa947a9b697230e4045b9c8012d1778c3abef8f875f06ed5a1384dc9e`. Field settings are taken from the printer's effective configuration during migration. Klipper still writes subsequent tuning into the standard saved section at the end of the main file.

## OLED, link and local faults

| Feature | Inventure implementation |
|---|---|
| Artwork | The host uses the existing 403-glyph r0323 atlas. The supplementary catalogue is decoded from r0323, including the 9-frame progress symbol at 0x0802E158, 4-frame hourglass at 0x0802F738, error octagon at 0x0802A3CC, warning triangle at 0x0802F9CC and crescent at 0x0802F3CC. Both animations have a 500 ms frame period. Heat and wipe screens use the progress symbol, cooldown uses the hourglass. The operation label identifies the activity. The crescent has the same pixel digest on both machines. |
| Sleep and wake | The panel shows the r0323 messages “Sleep Mode” and “Press the button to wake up.” The default Inventure host policy dims while work or heating is active. At idle, it shows the notice and turns off after acknowledgement and the notice interval. The port defaults are 300 s idle, 10 s notice and brightness 16/16. The exact OEM inactivity interval is still unverified. The first press on a sleeping panel wakes it. |
| Site choices | In the settings menu, **OLED timers** exposes idle seconds and notice seconds. Separate minute-based dim/off settings are available for operators who selected them. R35 recognizes the exact R34 installer-generated 5/15-minute text block. Other timer configurations and saved operator preferences are retained. An intentional byte-identical copy of that default block in a managed file follows the migration unless a saved operator preference overrides it. |
| Sleep lighting | **Sleep lighting** supports Dynamic (magenta), White, Off and Keep current light. Keep current light is the port default. The MCU can temporarily apply the selected mode while the OLED sleeps, and restore the previous RGB mask on wake or release. A new manual colour selection takes ownership. A board fault has red-light priority. |
| Orange Pi and Fluidd | Orange Pi composes frames and caches glyphs, stationary backgrounds and sprite rows. It sends changed SSD1322 regions with the existing bounded transport. Fluidd consumes the host framebuffer through Moonraker. The regular R14 diagnostic reply also confirms the local brightness. No additional polling loop is required. |
| MCU on its own | R14 differentiates host display lease expiry, an SPI/DMA transfer failure, an intentional release, and a latched board shutdown. For a native guard it can distinguish PD11 cable loss (#138), heartbeat expiry, invalid thermal sensing, fan feedback, enabled door/lid interlock, tray Z interlock and Z reference failure. Other Klipper shutdowns use a general message. The MCU renders the local warning/error using a 128-byte row buffer. A failed local SPI render is available on reconnection. |
| Persistence | User settings live on the Orange Pi. The MCU retains acknowledged brightness and incident details in RAM until reset. At cold boot the panel uses its Inventure initialization contrast until host preferences arrive. |

To reproduce the display catalogue with an audited copy of the application:

```bash
python3 ci/extract_inventure_oled_assets.py application_r0323.bin /tmp/inventure-icons.json /tmp/inventure-fault-glyphs.inc
cmp /tmp/inventure-icons.json orange_pi/inventure_activity_sprites.json
cmp /tmp/inventure-fault-glyphs.inc service_mcu/control_r6/SOURCE_MCU/inventure_oled_fault_glyphs.inc
```

## Verification

The native Klipper startup test loads the converted field configuration and a separate include-based installation. Each saves and reloads PID coefficients for both nozzles and both chamber zones. The regression suite checks the R34 field migration, user overrides, all 4096 ADC values, original sprite pixels, native sleep/wake, display preferences and the MCU's local pages. Register tests exercise the original motion and thermal guards. The GNU Arm 14.2.1 release build checks the dictionary embedded in the application and the InventureBoot 1.7 container. The Moonraker WebSocket integration test compares the delivered preview bitmap to the host frame. Physical brightness, RGB colour, panel-to-screen latency and the installed printer configuration still require observation on the printer.

On the release tree, 787 host and integration tests passed. Two independent ARM builds produced the same 106244-byte IVB (SHA-256 `f4342f906793fe377a3a27112d84a3a2e82c154973031657d41e100e037d1035`). The loopback Moonraker run delivered 20 display events with matching bitmaps.
