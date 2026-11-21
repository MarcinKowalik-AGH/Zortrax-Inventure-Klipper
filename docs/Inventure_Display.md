# Inventure OLED

The Orange Pi composes the 256 × 64 grayscale image. Klipper sends changed regions over the Inventure P4 link. The MCU writes them to the SSD1322 over SPI. Fluidd displays the host image below the thermal graph. The three zoom buttons change the browser preview.

## Configuration

The organized installation stores the physical panel's `[inventure_oled]` section and the explicit Klipper `[display_status]` section in `inventure/display.cfg`, included by the active `printer.cfg`. The latter supplies the progress and message reported by `M73` and `M117`. The physical driver creates `inventure_oled_preview` for Moonraker automatically. The installer respects existing display sections, including a site-owned generic display.

Use **Settings → OLED brightness**, **OLED timers** and **OLED sleep mode** to edit the panel preferences. The port defaults to 300 seconds of inactivity and a 10-second notice. Saved operator settings take precedence. The Inventure firmware supplies the native menu graphics, fault screens and thermal calibration.

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
