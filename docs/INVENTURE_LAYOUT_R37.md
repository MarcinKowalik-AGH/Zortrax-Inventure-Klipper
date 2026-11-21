# Inventure R37 · Shared configuration layout

R37 installs a real Klipper `[inventure_display]` object in `inventure_display.cfg`, in the same position in the configuration tree as `m300_display.cfg` in M300 Classic. Both the physical panel and its Fluidd mirror continue to use the Inventure framebuffer. The host release is `RC8-CONTROL-R7-LAYOUT-R37`. The compatible controller is `RC8-CONTROL-R7-DISPLAY-R14`.

## Active Orange Pi configuration

The paths below are relative to the active `printer.cfg` directory. The installer discovers it from the running Klipper process.

| M300 Classic role or file | Inventure file | Contents |
|---|---|---|
| Physical OLED and display status in the base | `inventure_base.cfg` | `[inventure_oled]`, SPI link and Inventure navigation. `[display_status]` supplies M73 progress and M117 messages on standard installations. |
| `m300_display.cfg` | `inventure_display.cfg` | `[inventure_display]`, with timed dim/off, contrast, sleep notice and Inventure idle, brightness and sleep light preferences. |
| `m300_temperature_sensors.cfg` | `inventure_temperature_sensors.cfg` | Inventure T0/T1 conversion and the 205 thresholds for the chamber and board sensors. |
| Thermal configuration | `inventure_heaters.cfg`, `inventure_heater_control.cfg`, `inventure_chamber.cfg`, `inventure_heater_monitors.cfg`, `inventure_cooling.cfg` | Inventure hardware channels, PID settings, left and right chamber heating with the center sensor, heater monitors and cooling. |
| `m300_services.cfg`, `m300_operations.cfg` | `inventure_services.cfg`, `inventure_operations.cfg` | Printer service configuration and the operator macros present in the installation. |
| `klipper_calibration.cfg` | `klipper_calibration.cfg` | Applicable Klipper PID controls. Tuned values in the active SAVE_CONFIG block retain priority. |

The organizer only creates roles used by the active installation. Site-owned include overrides and a separate generic Klipper display retain their owners. Upgrading a managed R34–R36 tree keeps compatibility include files at the former paths. The settings themselves live at the new paths.

## Display and menu

The first five options in the display file are `timeout`, `sleep_timeout`, `normal_contrast`, `dim_contrast` and `notice_seconds`. Their shipped values are 300 s, 900 s, 159, 32 and 10 s. Inventure's default `timer_policy: inventure` starts its native notice after `idle_timeout: 300`. Timed mode uses the two independent dim/off thresholds. A legacy `[inventure_oled] sleep_timeout` migrates to `[inventure_display] idle_timeout`. Preferences saved by the operator from the OLED or Fluidd retain priority. The configured SPI link, Inventure artwork and local MCU fault display stay on the Inventure driver.

Both interfaces present **Light**, **Sleep mode**, **Language**, **Models sorting**, **OLED brightness**, **OLED dim time** and **OLED sleep time** in M300 order. **Light** offers the chamber's white/off command and access to sleep lighting. Inventure-specific Settings also include the global door lock, file checks, filament endstops, native timers and the icon gallery. In Maintenance, Home X/Y/Z/XY/XYZ call the installed Inventure native homing procedure. The same menu exposes the existing directional platform controls, extruder heating, fan test, nozzle alignment and hotend maintenance. The Material menu retains model/support and cartridge/spool choices. Z homing uses the Inventure T0 contact procedure. Selecting Home XY, Home Z, Home XYZ or Light shows the matching project animation on both screens. Each image advances every 500 ms.

## Install

Run in the Orange Pi shell while Klipper is idle.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-layout-r37 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-layout-r37" refs/tags/inventure-layout-r37 &&
python3 "$HOME/inventure-layout-r37/install-orange-pi.py" --configure-fluidd --organize-config
```

The installer prints the active configuration, backups and newly created files. Adding `--check` displays the migration plan. If this tag is already checked out, run its installer directly. An Orange Pi with MCU R14 needs the host update only. After Klipper is ready, `INVENTURE_VERSION` identifies the release and `INVENTURE_SETTINGS` opens the shared Settings page.

## Validation and design reference

The tests load the active includes with the pinned Klipper parser, exercise fresh and field installations, round-trip PID tuning, check legacy timer migration, and compare panel and web menu actions. The release pipeline checks the MCU image, host startup, Moonraker OLED transport and compiled Fluidd dashboard before publishing the archives. Verification on a physical printer is reported by the operator.

The release passed 802 host tests and the pinned Klipper startup checks for an existing and a fresh configuration. Restoring the R33.1 and R36 host-module backups reproduced the original bytes. A full R36 configuration, generated with the published R36 installer and edited to 137 s idle and brightness 9, loaded in R37 with both values preserved. A second R37 pass staged zero files. All four PID profiles survived a save and reload. A Moonraker test compared the returned OLED bitmaps across 20 events including four gallery frames.

[Project rule](../AGENTS.md) · [M300 configuration at the reference revision](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/tree/67e300b8259886a7d5033c3e6c290d9e10012f32/host/config) · [Inventure display guide](Inventure_Display.md)
