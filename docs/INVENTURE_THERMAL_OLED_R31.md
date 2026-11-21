# Inventure R31 · Chamber temperatures and live OLED in Fluidd

The thermal card shows **Extruder 0**, **Extruder 1** and **Chamber**. The printer status card contains **OLED INVENTURE**. Host R31 works with `RC8-CONTROL-R7-PAD-THERMAL-R13`, the MCU already used by R30. Installation updates the Orange Pi host, printer configuration and Fluidd site.

## Install on Orange Pi

```sh
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-thermal-oled-r31 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-thermal-oled-r31" refs/tags/inventure-thermal-oled-r31 &&
python3 "$HOME/inventure-thermal-oled-r31/install-orange-pi.py" --configure-fluidd
```

Use the last line alone for an existing R31 checkout. The installer obtains the companion web ZIP and verifies it against the release's SHA256SUMS. It locates the active Klipper configuration, updates included files, retains existing nozzle identifiers and PID tuning, and backs up each changed file. It identifies the Fluidd site in the nginx configuration. Use `--fluidd-root PATH` on a host serving several Fluidd sites. Offline installation accepts `--fluidd-zip PATH/INVENTURE_FLUIDD_WEB_R31.zip` with the release's `SHA256SUMS` in the same directory. The installer reports the web root and the complete former site's directory after the switch. Existing connection settings, custom files and previous hashed web assets are retained. Reload Fluidd after installation. A cache-bypassing reload may be needed for an older installed PWA.

## Thermal card

| Display | Main reading | Target and feedback |
| --- | --- | --- |
| Extruder 0 | PC0 | Existing Klipper nozzle heater on PA0 |
| Extruder 1 | PF8 | Existing Klipper nozzle heater on PE5 |
| Chamber | PA4, center of the chamber | One target for two zones. PC1 measures the zone heated by PC9. PC2 measures the zone heated by PC8. Both side temperatures are shown below the label. |

Chamber power is the arithmetic mean of the two commanded PWM duty cycles. Both loops use Klipper PID with the starting gains recovered from r0323, Kp 22.2, Ki 0.25 and Kd 228. The native Klipper PID calculation and filter differ from the OEM implementation. A measured Klipper tune on this printer takes precedence. The common chamber target is 0 for off and up to 90 °C for heating, subject to the configured zone limits. The side sensors retain their native heater range checks. The center uses the shared ADC range and freshness checks on host and MCU. The installer links both chamber blowers to the two chamber heaters for heating and cooldown. The MCU retains its native heartbeat, sensor, fan tachometer and global door policy. [Inventure r0323 thermal audit, sections 5–8](https://drive.google.com/open?id=1rV1zIw0vAPuRa0iJxRr8-7rLHTG9EDD4) · [Inventure technical brochure, chamber rating](https://dts-asia.com/sites/default/files/product-doc/zortrax-inventure-and-materials.pdf) · [Klipper heater configuration](https://www.klipper3d.org/Config_Reference.html#heater_generic)

Start with a passive readout in the Fluidd console.

```gcode
INVENTURE_VERSION
INVENTURE_CHAMBER_STATUS
```

Turn the physical OLED to another menu page and observe its mirror. For a heating check with the machine idle, select the normal native controller and enter 40 in the Chamber target field. The G-code equivalent is below. Both zone targets should read 40 and both blowers should operate. After observing the rise, set the Chamber target to 0.

```gcode
INVENTURE_CONTROL MODE=NATIVE
M141 S40
INVENTURE_CHAMBER_STATUS
```

```gcode
M141 S0
```

`M191 S40` sets both sides and waits for both side thermometers to reach 39 °C. `PID_CHAMBER SIDE=LEFT TARGET=45` and `PID_CHAMBER SIDE=RIGHT TARGET=45` tune one side at a time. Begin a tune with both zone targets off. Inspect `PID_STATUS` before using `PID_SAVE`, which saves all pending Klipper settings and restarts the host. During individual control the panel shows both setpoints. `TURN_OFF_HEATERS` reaches both native heater objects.

## OLED

Fluidd subscribes to the lightweight `inventure_oled_preview` object over its existing authenticated Moonraker connection. It displays the same 256 × 64 four-bit SSD1322 pixels as the last completed host-to-MCU transfer. The cached PNG only changes with the acknowledged pixels. The panel supports zoom, collapse, display sleep and lost connection. MCU-owned boot screens are outside the host's framebuffer. [M300 preview reference](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/0c8148cef1f9786c23f061d8c4b977245cddcd86/M300_HOST/payload/m300_oled_preview.py) · [Upstream Fluidd source](https://github.com/fluidd-core/fluidd/tree/b2edab5bc690f5265a5aa1e56ae3f67669053fd4)

## Validation

The host suite covers paired target updates, independent feedback, partial failure, incomplete sensor data, paired shutdown, OLED pixel byte order and atomic web installation. Two Orange Pi configuration layouts are loaded with the project's pinned Klipper. They exercise legacy T1 relocation, separate includes, four native heaters, eight original ADC readers and both blower associations. Repeating setup produces no further configuration writes. The release workflow also executes the MCU register tests, rebuilds the R13 firmware, compiles the pinned Fluidd distribution and exercises its thermal and OLED controls with simulated Moonraker state in Chrome. Physical heater response, stable PID performance and agreement with the optical OLED are awaiting confirmation on the printer. [Host tests](../tests/test_chamber_dashboard.py) · [Real Klipper configuration test](../ci/check_native_nozzle_startup.py) · [Browser test](../ci/check_fluidd_browser.py)
