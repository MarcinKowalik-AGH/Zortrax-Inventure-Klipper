# Inventure R32 — Fluidd and Orange Pi OLED

R32 puts the Inventure display below the graph in **Thermals**, centers the 256 × 64 image, starts at ×2, and adds separate ×1, ×2 and ×3 buttons on the right. Menu changes now reach Fluidd as soon as the Orange Pi renders them. The previous R31.1 web build attached the preview to print history and displayed the last frame acknowledged by the physical panel.

## Install on Orange Pi

With the printer idle, run the following in the Orange Pi shell. A new detached worktree keeps earlier installations available.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-oled-r32 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-oled-r32" refs/tags/inventure-oled-r32 &&
python3 "$HOME/inventure-oled-r32/install-orange-pi.py" --configure-fluidd
```

If the R32 worktree is already present at that tag, rerun the final command. The installer discovers the active nginx root, verifies the released web bundle using SHA256SUMS, backs up changed files, updates the host and macro configuration, and sets the existing Fluidd installation to English. A local Fluidd server configuration is preserved. Use the browser's hard refresh after installation. The host reports `RC8-CONTROL-R7-THERMAL-OLED-R32`. The installed controller is `RC8-CONTROL-R7-PAD-THERMAL-R13`.

## Comparison with M300 RC34

| Operator feature | Inventure R32 |
|---|---|
| Dashboard layout | The thermal card shows Extruder 0, Extruder 1 and Chamber. Chamber reports the center thermometer and both side readings. Its target controls both side PID zones. OLED INVENTURE stays with the temperature card when the dashboard is rearranged. |
| Display controls | Zoom is saved in the browser. The canvas uses an integer device-pixel scale, aligned origins and a centered image. A narrow panel scrolls horizontally. Hide, a collapsed thermal card and a hidden tab suspend preview requests. Sleep and disconnect have distinct indications. |
| Orange Pi rendering | A changed menu selection wakes a bounded Klipper event wait. Multiple clients share the encoded PNG. An unchanged image reuses the cached bytes; animation-only updates are limited to eight per second. A slow browser decodes one image with one replaceable pending image. The fallback uses the object status API. |
| Physical OLED and UART | The existing sender selects changed windows by encoded cost, uses PackBits or raw packets, and recycles up to four SPI2/DMA credits on ACK. It retains one pending frame and paces optional pixels during active printer work. The host now reads just link age and fault for this check. Input receipts and MCU acknowledgement still protect a physical OK press. |
| Operator menus | `INVENTURE_MAIN_MENU`, `INVENTURE_SETTINGS` and `INVENTURE_INFORMATION` open the current Inventure menu on the physical panel and in Fluidd. Choices use the same procedure handler and one-use view token. Old replies expire after navigating to a different menu or completing the action. |
| Brightness and sleep | Set OLED brightness from 1 to 16. DIM and SLEEP each accept 1–60 minutes from the last interaction, including during a print or pause. SLEEP caps the effective dim time. The Inventure sleep screen fades before cutoff when there is time between dim and sleep. A wake gesture restores the selected brightness. Values persist on the host. Existing IDLE/NOTICE configurations retain their seconds-based policy. |
| Fluidd reconnect and navigation | A lost connection releases pending preview work, reconnects with backoff, and discards stale replies. Sidebar handling restores interrupted lazy-loaded routes and includes the M300 refresh action. |
| Tool widget and Tune | The All/X/Y/XY/Z buttons in the Tool widget call the matching installed Inventure homing macro. Other profiles keep their standard G28 behaviour. Tune shows four corners and the center, the active point, recorded heights, and verified platform depth and center deviation. The same view accompanies the operator's screw-adjustment prompt. Viewing the map issues no motion command. |
| Macros | Printer operations use the ZORTRAX INVENTURE group, with a separate Klipper group. Extra tool, cassette, chamber, door and nozzle calibration controls account for the different button count. The previous 36-button comparison lists the mapping for each M300 macro. |

M300 RC34 provided the reference renderer, event protocol, browser drawing code and reconnect behaviour. Inventure uses its own OLED artwork, buttons, SPI transport, tool selector and firmware geometry. [M300 RC34](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/tree/38e1c70e5ed7c974c709b9923613e22994832e57) · [M300 installed Orange Pi source](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/tree/f2301f829da9fa12db2e9a54768dcd39024ee635) · [Inventure physical display transport](M300_TRANSFER_COMM_OLED_2026-11-10.md) · [Operator macro mapping](INVENTURE_FLUIDD_R30.md)

## Validation and printer check

The host tests exercise installation, print ownership, old configuration files, menu inputs, one-use tokens, display preferences and power states. The pinned Klipper reactor and real WebRequest parser exercise a single button event, 15 rapid selections and cancellation. Two complete Klipper configurations load with T0/T1, two independent chamber PID controllers and three chamber thermometers. The production web build is type-checked and exercised in Chromium with simulated Moonraker updates. The browser check covers thermal setpoints, card location, centered zoom, pixel updates before a periodic status update, cancellation, loss of connection, pad geometry and homing dispatch. The MCU suite uses register mocks. The offline transport benchmark compares 520 menu transitions.

In Fluidd run the following commands. The second command opens the menu and the third reports the physical transport. No heating or homing is needed for this check.

```gcode
INVENTURE_VERSION
INVENTURE_MAIN_MENU
INVENTURE_OLED_STATUS
```

Compare two presses of UP or DOWN on the printer with the selection shown below Thermals. Test the three zoom buttons, Hide/Show and the brightness editor. Hardware button-to-pixel latency, real Wi-Fi reconnect time and the brightness of the sleep fade still need measurement on the owner's Inventure. Browser and reactor tests do not measure those physical quantities.

## What remains compared with a working M300 print installation

| Area | Evidence still needed or work open |
|---|---|
| Native dual extrusion and print controls | Qualify the shared E drive, pressure advance for the active extruder, selector, both offset signs in a real two-material print, purge and resume. Fluidd displays Pressure Advance / Smooth Time when the active extruder reports pressure_advance. Motors Off appears while a stepper is enabled. Print history is populated by this printer's Moonraker jobs. |
| Platform and profiles | The field-tested five-point screw procedure and central T0/T1 alignment have separate roles. A first-layer compensation mesh and Orca start/end, cancellation and recovery sequences still require Inventure-specific print trials. |
| Thermal and airflow | Tune and accept the two nozzle and two chamber PID loops on the machine. Qualify the six fan channels, tach failure response, and chamber cooldown through an actual print. |
| Doors, tray and bin | Verify global door-policy coverage through the older native-print path and the physical latch, tray and bin transitions during pause/resume. |
| Cassette, external spool and RFID | Accept interrupted, missing and broken filament recovery during a native print. Qualify identity, material consumption and replacement across restarts. |
| Jobs, media and persistent counters | Durable replay of a completed job, power-loss restoration, physical SD/ZCODE and print-time round-trip to the OEM counters need separate implementation or acceptance. |
| Panel hardware | M300's encoder, optional acoustic buzzer mapping, continuously dimmable chamber light and MCU-backed display-preference record cannot be assumed on the R13 controller. Inventure stores the OLED preferences on Orange Pi and has three binary RGB light outputs. |

Earlier on-printer evidence includes two saved central-pad calibrations, cold T0/T1 center checks and a saved five-point leveling run with platform depth 138.349 mm and center deviation +0.031 mm. [Calibration acceptance](acceptance/INVENTURE_PAD_R23_2026-11-19.md) · [Broader OEM coverage register](INVENTURE_OEM_COVERAGE_R27_1_2026-11-19.md)
