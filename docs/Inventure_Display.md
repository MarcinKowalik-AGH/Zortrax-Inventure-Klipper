# Inventure OLED

The Orange Pi composes the 256 × 64 grayscale image. Klipper sends changed regions over the Inventure P4 link. The MCU writes them to the SSD1322 over SPI. Fluidd displays the host image below the thermal graph. The three zoom buttons change the browser preview.

## Configuration

Open `inventure_display.cfg` next to the active `printer.cfg`. Its `[inventure_display]` section contains the panel preferences. Times are in seconds. The separate `inventure_base.cfg` contains the physical `[inventure_oled]` driver and Klipper's `[display_status]` for progress and messages. The physical driver publishes `inventure_oled_preview` to Moonraker.

| Option | Inventure default | Purpose |
|---|---|---|
| `timeout` | 300 s | Dim in timed mode |
| `sleep_timeout` | 900 s | Switch the panel off in timed mode |
| `normal_contrast`, `dim_contrast` | 159, 32 | Panel contrast at maximum brightness |
| `notice_seconds` | 10 s | Duration of the sleep notice |
| `timer_policy`, `idle_timeout` | `inventure`, 300 s | Start the native Inventure sleep notice after inactivity |
| `brightness`, `sleep_light` | 16, `manual` | Panel brightness level and chamber lighting during sleep |

In **Settings**, use **Sleep mode**, **OLED brightness**, **OLED dim time** or **OLED sleep time**. **OLED timers** also exposes the native idle/notice settings. A saved choice on the Orange Pi takes priority over the corresponding file default. Changing a timed threshold selects timed mode. Changing a native idle or notice threshold selects native mode.

## Project artwork

Choose **Zortrax Inventure → Settings → OLED icons** on the panel, or use **INVENTURE_OLED_ICONS** in the **ZORTRAX INVENTURE** macro group. **Previous** and **Next** cycle through the images. **Back** closes the gallery. To open one symbol by name, use the Fluidd console.

```gcode
INVENTURE_OLED_ICONS NAME=heat_nozzle
```

The gallery displays the same images on the panel and in Fluidd. An image of bed heating or a fan is a drawing in this gallery. Viewing it leaves heater targets, fans, motors and PID settings alone.

The activity set has 24 symbols with the original six intensity values (0, 4, 8, 11, 14, 15). The five project and homing symbols use black and full brightness. Each symbol has four distinct 50 × 50 frames at 500 ms per frame. The Inventure's r0323 screens use their own artwork. The MCU can show its local status and fault screens when the host connection is lost.

## Diagnostics

```gcode
INVENTURE_VERSION
INVENTURE_OLED_STATUS
```

Use the host and MCU revisions to identify the installed software. The sent and acknowledged counters describe MCU SPI transactions. The physical appearance of the panel must be checked on the printer. A button latency figure requires an input sample. The Fluidd canvas has the accessibility label “Live host-rendered 256 by 64 Inventure OLED screen”.
