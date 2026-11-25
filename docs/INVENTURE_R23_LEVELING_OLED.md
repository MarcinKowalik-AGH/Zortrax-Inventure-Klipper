# Inventure R23.2 host: OEM platform leveling on the physical OLED

This update adds the complete physical-panel presentation of the preliminary
platform leveling procedure that the owner completed and saved on 25 November
2026. It uses the existing Inventure `INVENTURE_LEVEL` macro and the same live
procedure on the Zortrax OLED menu. No second measurement session is started.

This is the five-point measurement and three-knob mechanical adjustment of the
platform. The later bed mesh used to compensate Z during printing belongs in
Tune. This update does not add that mesh or change Tune.

## Start and operate

- Fluidd → Macros → **ZORTRAX INVENTURE** → **INVENTURE_LEVEL**.
- Physical panel → **Zortrax → Maintenance → Platform leveling** → confirm start.
- The physical OLED follows a run started from Fluidd automatically, including
  when the previous screen says that Home has finished.
- Follow the original tighten-all-knobs message. Access is offered only after
  the existing owner confirms that it is ready for the operator.
- For an adjustment, the OLED shows the original animated front, rear-left or
  rear-right screw, the original high/low message, the direction and number of
  turns supplied by the Inventure procedure. Use the panel's Up/Down and OK
  controls. If both rear screws need adjustment, **Next screw / view** displays
  the second instruction before **Adjusted / next** confirms the whole prompt.
- The OLED shows checking-front, checking-back and checking-center messages,
  then all five signed observations, platform depth and center deviation.
  **Review / save result** opens the existing save confirmation. Back/Stop opens
  the existing cancel confirmation.

Fluidd's existing prompt and the physical panel address the same run and prompt
IDs. The panel still requires an acknowledged display frame and a fresh OK
press. A stale Home screen, old screenshot, old prompt or repeated click cannot
confirm a replacement prompt. This firmware uses the physical panel controls;
the adjustment knobs under the platform are turned manually.

## Original Inventure firmware resources

`ci/extract_inventure_leveling_ui.py` reproduces the 14 original English screen
records, their text pointers and their image associations from the supplied
Inventure 1.6.1 r0323 application:

`9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`

The embedded catalog contains 10 original image descriptors and 18 original
50×50 grayscale frames. The six adjustment animations are linked from the
actual Inventure screen records at `0x08036908` through `0x08036950`; the
tighten-all-knobs record is `0x0803695C`. Original 13-row Inventure glyphs and
original grayscale pixels are sent to the SSD1322 and to the existing Fluidd
OLED mirror. The five-point result diagram and action footer are host controls.

The current M300 reference was distribution commit
`7ed37d50fa543fdd63c31f82b9f10f4aca43226e`, source
`89aede3cc176361f315a91633468a4ac400e1580`: procedure-owned OLED dialogs,
original firmware messages/animations and confirmation tied to the displayed
phase. Screw directions, turn counts, coordinates, temperatures, ownership and
contact arithmetic remain those of Inventure.

## Install on the existing Orange Pi

The existing **MCU R23 OLED / InventureBoot 1.7 stays installed**. No MCU flash
is required. The host update replaces only the authenticated menu-service and
OLED-graphics modules plus the bootstrap manifest when upgrading from
`inventure-r23-leveling-host-rc2`. Earlier authenticated R38.6/R18–R22 runtimes
also follow their existing verified migration path.

After saving or cancelling the current procedure, enter in Fluidd:

```gcode
TURN_OFF_HEATERS
FIRMWARE_RESTART
```

Wait for Ready. Then, as the Klipper user on the Orange Pi:

```bash
cd ~/inventure-src
git fetch origin --tags
git switch --detach inventure-r23-leveling-oled-rc3
python3 install-new-host.py --restore-field-profile --serial /dev/ttyS5 --baud 750000 --apply
```

The installer preserves the field profile, local tuning, SAVE_CONFIG block and
`inventure_platform_level.json`. Existing transactional verification and
rollback apply. It continues to refuse an active/armed procedure. The Fluidd
web package does not change for this update.

## Validation and physical evidence

The supplied `klippy(6).log(2).zip` records a physically completed and saved
five-point run: platform depth **138.408 mm**, center deviation **+0.010 mm**,
five measurements and zero requested screw corrections. The screenshot also
shows the stale Home screen that this host update fixes. See
`validation/INVENTURE_R23_LEVELING_PHYSICAL_2026-11-25.json`.

Software verification covers the exact staged/installed modules, complete
configuration startup, literal `INVENTURE_LEVEL` calls and corresponding panel
start/cancel after Home, stale-owner takeover, sequential screw dialogs,
run/prompt and display-receipt fencing, exact original animation pixels in the
physical OLED buffer, shared save state, refusal paths and upgrade/rollback
from the preceding public host. The unchanged MCU register/timing suite is run
as a regression check.

The new OLED wizard has not yet been observed on the physical printer. The
completed run did not require a screw correction; physical correction and
panel interaction with this new UI remain to be checked on the printer.
