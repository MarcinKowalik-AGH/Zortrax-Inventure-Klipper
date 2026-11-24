/* M300-style, first-cause display metadata for Inventure V9.
 * No actuator writes, timer changes, flash or host recovery side effects.
 * The catalog uses native reasons; only the verified Inventure cable code
 * #138 is numbered. M300 bed/cable OEM numbers are not Inventure codes.
 * SPDX-License-Identifier: GPL-3.0-or-later */
enum {
    DISPLAY_NONE, DISPLAY_SHUTDOWN, DISPLAY_TIMER, DISPLAY_STEPPER,
    DISPLAY_LINK, DISPLAY_ESTOP, DISPLAY_SENSOR, DISPLAY_GUARD,
    DISPLAY_CABLE, DISPLAY_FAN, DISPLAY_DOOR, DISPLAY_TRAY, DISPLAY_Z,
    DISPLAY_SPI, DISPLAY_KIND_COUNT
};
struct inventure_display_error {
    uint16_t native_id;
    uint8_t kind, fatal;
    uint32_t revision;
};
struct inventure_display_text {
    uint16_t code;
    uint8_t icon;
    const char *title, *hint1, *hint2;
};
static struct inventure_display_error display_error;
static const struct inventure_display_text display_catalog[DISPLAY_KIND_COUNT] = {
    {0,9,"No active error","Printer ready",""},
    {0,10,"Klipper shutdown","Read console / klippy.log","Fix cause, then restart"},
    {0,10,"Timer too close","Check timing / link","Read console / klippy.log"},
    {0,10,"Stepper timing error","Read console / klippy.log","FIRMWARE_RESTART needed"},
    {0,11,"Klipper link lost","Check host and UART","No automatic resume"},
    {0,10,"Emergency stop","Heaters / motion stopped","FIRMWARE_RESTART needed"},
    {0,4,"Temperature ADC fault","Check sensors / wiring","Read console / klippy.log"},
    {0,10,"MCU guard latched","Read console / klippy.log","FIRMWARE_RESTART needed"},
    {138,5,"Extruder cable error","Check main head cable","Inspect connectors"},
    {0,10,"Cooling fan feedback","Check head/chamber fans","Heaters remain disabled"},
    {0,10,"Door or lid open","Close doors and lid","Fix cause, then restart"},
    {0,6,"Build tray Z interlock","Check tray seating","FIRMWARE_RESTART needed"},
    {0,6,"Z reference failed","Check nozzle and pad","Read console / klippy.log"},
    {0,11,"OLED transfer failed","Check display wiring","MCU reset required"},
};
DECL_CONSTANT("INVENTURE_DISPLAY_ERROR_ABI",1);
DECL_CONSTANT("INVENTURE_LOCAL_OLED_ANIMATION_ABI",1);
DECL_CONSTANT("INVENTURE_LOCAL_OLED_NATIVE_GRAY4",1);
DECL_CONSTANT("INVENTURE_LOCAL_SPLASH_ABI",1);
DECL_CONSTANT("INVENTURE_LOCAL_OLED_ANIMATION_MS",500);

static uint8_t display_board_kind(void) {
    switch(inventure_board_fault()) {
    case 1: return inventure_board_guard_input()==1 ? DISPLAY_CABLE :
                   inventure_board_guard_input()==2 ? DISPLAY_LINK : DISPLAY_GUARD;
    case 4: return DISPLAY_SENSOR;
    case 5: return DISPLAY_FAN;
    case 6: return DISPLAY_DOOR;
    case 7: return DISPLAY_TRAY;
    case 8: return DISPLAY_Z;
    default:return DISPLAY_SHUTDOWN;
    }
}

void inventure_oled_capture_shutdown(uint16_t native_id) {
    /* Invoked by the scheduler before shutdown callbacks. Prefer the local
     * board's immutable first trip to a later generic scheduler consequence. */
    if(display_error.fatal)return;
    uint8_t kind=DISPLAY_SHUTDOWN;
    if(inventure_board_fault() && inventure_board_fault()!=255)kind=display_board_kind();
    else if(native_id==_DECL_STATIC_STR("Timer too close"))kind=DISPLAY_TIMER;
    else if(native_id==_DECL_STATIC_STR("Stepper too far in past"))kind=DISPLAY_STEPPER;
    else if(native_id==_DECL_STATIC_STR("ADC out of range"))kind=DISPLAY_SENSOR;
    else if(native_id==_DECL_STATIC_STR("Command request"))kind=DISPLAY_ESTOP;
    display_error.kind=kind;display_error.native_id=native_id;
    display_error.fatal=1;display_error.revision++;
}

static struct inventure_display_error display_snapshot(void) {
    irqstatus_t flags=irq_save();
    struct inventure_display_error result=display_error;
    irq_restore(flags);
    /* Also works when the board calls its shutdown hook directly in an
     * electrical emergency, without the scheduler's generic error path. */
    if(inventure_board_fault() && inventure_board_fault()!=255) {
        result.kind=display_board_kind();result.fatal=1;
        result.revision|=0x80000000u;
    } else if(!result.fatal && oled_display_failed) {
        result.kind=DISPLAY_SPI;result.revision|=0x40000000u;
    }
    return result;
}

void command_inventure_display_error_query(uint32_t *args) {
    (void)args;
    struct inventure_display_error state=display_snapshot();
    sendf("inventure_display_error code=%hu kind=%c fatal=%c native_id=%hu revision=%u",
          display_catalog[state.kind].code,state.kind,state.fatal,
          state.native_id,state.revision);
}
DECL_COMMAND_FLAGS(command_inventure_display_error_query,HF_IN_SHUTDOWN,
                   "inventure_display_error_query");
