// R24 adds ownership transfer only. Existing motion and thermal engines stay intact.
DECL_CONSTANT("INVENTURE_NATIVE_HELD_SERVICE_ABI",1);
extern uint8_t inventure_native_stepper_idle(void);
static uint8_t held_service_owns_pins(void) {
    return control_mode==CT_SERVICE && held_service_live;
}
uint8_t inventure_native_queue_allowed(void) {
    return inventure_is_armed() && !native_z_refine_held;
}
void command_inventure_native_held_service(uint32_t *args) {
    uint32_t owner=args[0];
    irqstatus_t saved=irq_save();uint32_t now=timer_read_time();uint8_t reject=0;
    if(!owner)reject=1;
    else if(owner==held_service_owner) {
        // A reply retry cannot perform a second transfer or refresh a deadline.
        if(control_mode!=CT_SERVICE || !held_service_live || !control_ready(now)
                || !(GPIOE->ODR&BIT(10)))reject=4;
    } else if(control_mode!=CT_NATIVE || !control_ready(now))reject=4;
    else if(!inventure_native_stepper_idle() || native_z_guard_active
            || native_z_refine_held || native_z_refine_conflict || zh_active()
            || cm.active || fs.active || sl.active || motor_test.running
            || xy_seek_busy() || t0_heat_busy() || fan_manual_active()
            || feeder_running[0] || feeder_running[1] || transport_live
            || locks_on[0] || locks_on[1] || locks_pulling[0] || locks_pulling[1])reject=2;
    else if(!(GPIOE->ODR&BIT(10)) || !zh_contact_open() || (GPIOC->IDR&BIT(13))
            || !native_sensors_valid(now) || !service_sensors_valid(control_guard_mask|31,now)
            || (door_policy_enabled() && door_closed_mask()!=3))reject=3;
    for(unsigned i=0;i<4;i++)if(control_heater_duty[i])reject=2;
    for(unsigned i=0;i<10;i++)if(co[i].duty)reject=2;
    if(!reject && owner!=held_service_owner) {
        held_service_owner=owner;held_service_pending=held_service_live=1;
        held_service_deadline=now+MS(1000);
        control_mode=CT_SERVICE; // Deliberately no STEP, DIR, PWM or PE10 write.
    }
    unsigned mode=control_mode,enabled=!!(GPIOE->ODR&BIT(10));
    irq_restore(saved);
    sendf("inventure_native_held_service_state owner=%u mode=%c enabled=%c reject=%c",
          owner,mode,enabled,reject);
}
DECL_COMMAND(command_inventure_native_held_service,
             "inventure_native_held_service owner=%u");
