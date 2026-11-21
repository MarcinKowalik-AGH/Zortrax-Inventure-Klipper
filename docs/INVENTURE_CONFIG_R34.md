# Inventure R34 configuration and OLED

R33.1 already had three Fluidd thermal rows, two chamber PID loops, adjustable OLED brightness and saved timers. Its installer appended new heater and macro sections to the active Klipper configuration. It did not organize Moonraker. R34 adds an opt-in migration of the running installation and aligns the operator display defaults with M300 release 1.0.2. The matching Inventure controller is MCU R13.

## Install on Orange Pi

With the printer idle, run these lines in its shell.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-config-r34 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-config-r34" refs/tags/inventure-config-r34 &&
python3 "$HOME/inventure-config-r34/install-orange-pi.py" --configure-fluidd --organize-config
```

Use the last line alone for an existing R34 worktree. Add `--check` to preview the paths. On a working R33.1 dashboard the shorter `--organize-config` updates the host and organizes the configuration without downloading the Fluidd site again. The installer reports the active configuration, participating Klipper files, created files and backup paths. `--moonraker-config PATH` selects a nonstandard Moonraker configuration explicitly.

## What appears in Configuration

Paths below are relative to the directory containing the active `printer.cfg`.

| File | Active contents |
|---|---|
| `inventure/temperature_sensors.cfg` | Linear ADC conversion for both nozzles and the 205 original ADC threshold/temperature pairs for the chamber, electronics and PSU sensors. Klippy reads these values and checks agreement with the controller curve. |
| `inventure/heaters.cfg` | The board outputs, sensor types/channels, PWM and existing temperature/power limits of the eligible native heaters. The sections use the actual heater names from this installation. |
| `inventure/display.cfg` | Existing hardware and menu options from the local OLED section plus brightness and idle timer defaults. |
| `inventure/calibration.cfg` | Installed Klipper PID buttons previously in the main file. |
| `inventure/operations.cfg`, `inventure/services.cfg` | Installed project macros and supported service definitions previously in the main file. Existing macro include files continue working. Files are created when corresponding sections exist. |
| `printer.cfg` | Hardware definitions, include links, adjustable native heater control and PID values, site macros and Klipper’s existing `SAVE_CONFIG` tail. |
| `moonraker.conf` and its active local includes | Server, socket, access policy and custom updaters. The migration removes duplicate trusted-client entries, the stock Fluidd web updater for the project-managed site, and the stock fluidd-config updater only when its configuration is unused. |

The installer compares every existing effective Klipper option before writing the organized tree. It preserves local overrides and records the original files for rollback. A repeated application to the same configuration produces no configuration edits. A changed Moonraker file causes a service restart. If its local configuration endpoint was readable before the update, the installer also checks the reloaded section list. Fluidd macro groups continue to use Moonraker's database.

### Thermal paths

| Device | Sensor | Calculation and use |
|---|---|---|
| Extruder 0 | PC0, ADC channel 0 | 12-bit ADC, 3300 mV reference, 5 mV/°C. |
| Extruder 1 | PF8, ADC channel 1 | Same linear conversion, separate heater and control settings. |
| Left chamber zone | PC1, channel 2 | OEM threshold table, left PID drives PC9. |
| Right chamber zone | PC2, channel 3 | Same table, right PID drives PC8. |
| Chamber center | PA4, channel 4 | Same table, independent center reading in Fluidd and the three-sensor chamber monitor. |

`Chamber` applies one setpoint to the two side controllers. Targets for T0 and T1 retain their 310 °C limit in the dashboard configuration. The sensor curves describe ADC conversion. Each printer obtains its own PID results through tuning. R34 retains the current coefficients and control mode during organization. It also fixes a startup failure after tuning a nozzle originally configured with watermark control. Klipper can retain its old `max_delta` alongside saved PID values, which the adapter now accepts as the inactive watermark setting.

## OLED controls

| Setting or event | Inventure R34 |
|---|---|
| Fresh operator defaults | Brightness 16/16, dim after 5 minutes, off after 15 minutes, notice during the final 10 seconds. The timers run from the last operator activity, including during printing and pause. |
| Operator changes | `INVENTURE_OLED_BRIGHTNESS LEVEL=8`, `INVENTURE_OLED_TIMERS DIM=5 SLEEP=15`, `INVENTURE_SLEEP_MODE ENABLED=1`. These controls are also available under Settings. Each minute timer accepts 1–60. The sleep threshold caps the effective dim threshold while preserving the preferred value. |
| Dimming and sleep | On the SSD1322, register C1 controls contrast. Maximum awake contrast is 159 and maximum dim contrast is 32, scaled by the chosen brightness. The cached notice uses the M300 project’s 50×50 moon at (202,7). After its acknowledgement, the host fades it in 16 steps and switches the panel off at the deadline. UART carries contrast updates during the fade. |
| Wake | Pressing a button on a dark screen wakes it. Its release cannot accept a menu selection. The panel receives the new pixels before DISPLAY ON. A confirmed menu already visible at reduced brightness accepts the first press. |
| Stored settings | Inventure keeps operator preferences in the Orange Pi settings file, normally `inventure_menu_settings.json`. M300 additionally synchronizes its preferred timers with the MCU. Inventure R13 has no corresponding record. Saved legacy IDLE/NOTICE settings continue in seconds. Existing minute-mode records keep their original thresholds. Read-only Inventure status queries allow observation without waking the screen. |

In Fluidd, `INVENTURE_VERSION` should show `RC8-CONTROL-R7-CONFIG-R34`, MCU `RC8-CONTROL-R7-PAD-THERMAL-R13` and `temperature_map_source: printer_config` after organization. `INVENTURE_OLED_STATUS` reports the active timer policy, thresholds, brightness and panel phase. An existing operator setting takes priority over the default in the configuration file.

## Validation

The pinned Inventure Klipper loads an organized field configuration and a separate installation with an existing include tree. Each test saves synthetic PID coefficients for all four heaters through the actual Klipper autosave writer and reloads the resulting configuration. The ADC test compares all 4096 input codes against the released curves. The Moonraker v0.9.3 parser reloads the cleaned files with the same server options, effective trusted clients, CORS and custom updater. Transport tests verify the reference moon pixels, contrast commands during fading, first-frame acknowledgement and the consumed wake gesture. Physical panel brightness and the current files on the owner’s Orange Pi require observation on that printer.

The M300 comparison uses release 1.0.2 at commit `67e300b8259886a7d5033c3e6c290d9e10012f32`. [M300 configuration audit](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/67e300b8259886a7d5033c3e6c290d9e10012f32/docs/en/CONFIGURATION_AUDIT.md) · [M300 display configuration](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/67e300b8259886a7d5033c3e6c290d9e10012f32/host/config/m300_display.cfg)
