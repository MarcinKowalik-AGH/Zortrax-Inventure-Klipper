# Inventure R33 · OLED graphics and Orange Pi timing

R33 uses the M300 v1.0.0 status typography and sprite caching technique on the Inventure. The physical OLED and Fluidd display the same 256 × 64 host framebuffer. Fluidd keeps the R32 thermal layout with Extruder 0, Extruder 1, Chamber and the OLED below the temperature graph.

## Display and transport

| Area | Behaviour in R33 |
|---|---|
| Ready and M117 | The lower 16 rows use the small 5×7 font. A single line begins at (2, 52). Longer text wraps at 42 characters onto two lines. The visible “Ready” pixels occupy 29 × 7 pixels. In the R32 native 16-column layout they occupied 78 × 11. The top 48 rows come from the installed Inventure renderer. The browser zoom enlarges the whole framebuffer by an integer factor. |
| Inventure artwork | Ten original r0323 icons keep their 50 × 50 size and original gray levels. Each icon is packed on its first use. The five Zortrax main branches show the selected category. Opening the menu from Fluidd and opening it on the panel use the same icon selection. |
| M300 activity artwork | Ten matching M300 symbols cover heating a nozzle, cooling, wiping a nozzle, filament jam, material missing, OLED brightness, link information, file reading and the two corresponding head fans. The stored four-frame sequences retain their source hashes. An active heat, cool or clean stage can advance every 500 ms. Menu choices and operator prompts display stationary symbols. The Inventure chamber fans and platform keep their own meanings. |
| Orange Pi | The renderer caches the text, icon rows and menu background. Moving between two choices with the same artwork draws a nine-pixel cursor over the cached background. Both UART encoders now derive windows from a single change map. The fast encoder evaluates packet costs against that same map. After an ACK the host refills the queue and schedules the next needed deadline. A changed operator view wakes the reactor straight away. |
| Physical OLED | Klipper sends SSD1322 windows to the MCU over its background UART queue. It can keep four bursts in flight. The MCU performs SPI/DMA and acknowledges completed transactions. Cursor movement with the same icon avoids its rectangle. A change of category also sends the changed artwork. Active printer work retains its bandwidth budget. |
| Fluidd | The Orange Pi publishes a navigation event and shares one indexed PNG across preview clients. Moonraker forwards the event on the WebSocket. Physical SPI acknowledgement is required for a physical confirmation choice. Browser tab visibility, preview zoom and hidden-panel cancellation follow R32. |

The artwork and compact status strip were compared against the published M300 source. [M300 v1.0.0](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/tree/c93868df931bbbccddbbe4a57abb83e8a1253da7) · [Inventure OEM atlas](../host/inventure_oem_assets.json) · [Activity frames and source hashes](../orange_pi/inventure_activity_sprites.json)

## Validation

| Check | Result |
|---|---|
| Status rendering | Both real pinned Klipper configurations render the compact Ready and a long display message. The native upper rows match byte for byte. |
| Artwork and MCU packets | Compared all 10 Inventure sprites against the original pixel renderer and all 40 shared activity frames against their source nibbles. The modeled SSD1322 reconstructs the requested frame. For the same images, the R32 and R33 window planners produced identical bounds, regions and encoded packets on 520 navigation transitions and 40 additional frame pairs. |
| Orange Pi code path on an x86-64 test host | On an identical, warm-cache cursor transition, median composition took 11.91 µs with R32 and 2.94 µs with R33. Window selection and packet preparation took 564.81 µs and 68.39 µs respectively. These are 500 interleaved samples per version. |
| UART cost model | Across 520 menu transitions, encoded-window planning used 273,074 modeled UART bytes versus 314,858 for fixed dirty regions on the same R33 frames, a 13.27% reduction. Counts include both directions of the link. |
| Event delivery | The WebSocket test uses Moonraker v0.9.3's JSON-RPC dispatcher and KlippyConnection, a connected Unix socket pair, and the pinned Klipper reactor and webhooks. All 20 simulated key changes delivered the expected PNG. Cancellation released the waiting request. The local host-event-to-WebSocket-receipt p95 was 0.49 ms. The production Fluidd thermal component was separately exercised in Chromium. |
| Upgrade | R32 to R33 installs the graphics module and artwork catalogue, keeps the operator's custom macro and is idempotent on reinstallation. The MCU remains R13. |

The timings above were collected on a local test host. The USB/UART interface of the owner's printer, Orange Pi CPU load, Wi-Fi, browser paint and the optical response of the panel have their own timing. The model and the WebSocket test can be reproduced with the checked-in scripts. [UART and host benchmark](../ci/benchmark_inventure_oled.py) · [Moonraker bridge test](../ci/check_oled_moonraker.py) · [Chromium check](../ci/check_fluidd_browser.py)

## Install and check the printer

Run the following in the **Orange Pi shell** while the printer is idle.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-oled-r33 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-oled-r33" refs/tags/inventure-oled-r33 &&
python3 "$HOME/inventure-oled-r33/install-orange-pi.py" --configure-fluidd
```

If that worktree already exists at the R33 tag, run its final installer command. Refresh Fluidd with a hard reload. The host revision ends in **THERMAL-OLED-R33** and uses the existing **PAD-THERMAL-R13** MCU. The update is confined to the Orange Pi.

In the **Fluidd console**, run `INVENTURE_VERSION`, then `INVENTURE_MAIN_MENU`. Use the printer's physical DOWN/UP keys several times. Compare the selection and icon on the physical OLED and the preview, then run `INVENTURE_OLED_STATUS`.

| Field under `ui_transport` | What the instrument measures |
|---|---|
| `button_to_host_frame_ms` | Host serial receipt of a selection-changing physical UP/DOWN press through completion of its host framebuffer, including reactor queue time. |
| `button_to_mcu_frame_ack_ms` | Host serial receipt of that button event through the matching completed frame on the MCU transport. Superseded frames that never complete contribute no sample. |
| `last_payload_bytes`, `last_frame_ms`, `frame_p95_ms` | Encoded payload bytes and elapsed physical transport time. The latter two start when the transmitter begins that frame. |

The first two fields contain the sample count, last value and p95 for the most recent 128 observations. Before the first qualifying press they show zero samples and null times. A press against a menu boundary or a held-key repeat does not create a new physical press sample. Returning to the status page shows the compact Ready text. This check only navigates the display.
