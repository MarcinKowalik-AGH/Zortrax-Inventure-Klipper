# Inventure R23.3 host: verified OLED activation and complete preparation screens

The operator's console output on 25 November shows a completed five-point run
but the old OLED view `Back / saved`, without the `leveling` data introduced in
RC3. This identifies a running menu that does not include the released wizard.
The saved measurement itself is valid: **138.401 mm**, center deviation
**+0.006 mm**, five observations and no requested screw corrections.

The installer now verifies the procedure UI actually loaded by the new Ready
Klipper process, in addition to its PID, runtime path and base host version.
The OLED reports `procedure_ui_release: RC8-LEVELING-UI-R23.3` only when its
menu and renderer versions agree. A Ready process with an older UI is refused
and the installation transaction rolls back. Diagnostic output from
`INVENTURE_ZORTRAX OP=STATUS` also includes `ui_release`.

## Procedure screens

The wizard now reads the current action of the matching platform-leveling
owner, without issuing hardware queries or changing its program. One public
phase can contain multiple actions: for example, `heating` can include Home,
actual heater waiting and selection of T0. The OLED follows those actions.

Preparation, Home X/Y/Z, nozzle heating, selection of T0 and final parking use
the illustrated project graphics shared with M300. A cleaning graphic is
selected only when the real owner is executing the cleaning module. Busy
preparation does not fall back to the generic waiting symbol. The physical
OLED and its Fluidd mirror receive the same framebuffer.

The original Inventure tighten-all-knobs animation, six screw-correction
animations, front/back/center messages and result screens from RC3 remain.
They take priority over old factory activity when the procedure reaches an
operator prompt or a completed observation. The shared run/prompt identities,
display receipt, fresh OK press, door access and cancel guards remain intact.

Entry points remain **INVENTURE_LEVEL** in the **ZORTRAX INVENTURE** macro
group and **Zortrax → Maintenance → Platform leveling** on the physical panel.
This is preliminary mechanical leveling. Tune remains for the separate mesh
used for compensation while printing.

## Update the Orange Pi

The existing **MCU R23 stays installed**. The IVB is byte-for-byte unchanged;
no MCU flash is needed. From RC3 the runtime changes are the menu, graphics and
OLED adapter plus their authenticated manifest. The field profile, tuning,
SAVE_CONFIG block and saved platform JSON are retained.

After the current procedure is saved or cancelled, use Fluidd:

```gcode
TURN_OFF_HEATERS
FIRMWARE_RESTART
```

Wait for Ready, then run on the Orange Pi as the Klipper user. The `&&`
operators prevent an old installer from running if a Git step fails.

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-r23-leveling-oled-rc4 &&
python3 install-new-host.py --restore-field-profile --serial /dev/ttyS5 --baud 750000 --apply
```

After Ready, check in Fluidd:

```gcode
INVENTURE_ZORTRAX OP=STATUS
```

The output must contain **`"ui_release": "RC8-LEVELING-UI-R23.3"`**.
`INVENTURE_OLED_STATUS` independently reports the matching
`procedure_ui_release` used by the installer's live-process check.

## Verification

The suite includes a complete real leveling-program simulation from T1,
including door pauses, Home, heating, mechanical T0 selection, all contact
points and a screw correction. The installed UI is observed throughout; busy
stages must not use the generic waiting icon. Separate tests cover the active
UI marker, rejection/rollback of an old UI in a Ready process and exact upgrade
and rollback from RC3. Prior command, owner, image, configuration and MCU
checks remain enabled.

`OLED_LEVELING_STAGES.png` is rendered from the authenticated installed OLED
buffers and included as a release asset. The underlying measurement has been
completed on the printer; the new UI activation and full screen sequence still
await confirmation on the physical panel. Software simulation is recorded
separately from those physical observations.
