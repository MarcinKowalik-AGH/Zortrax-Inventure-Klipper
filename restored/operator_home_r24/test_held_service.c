// Compile against the actual generated R24 board and existing independent plant.
#include "test_factory_session.c"
static uint8_t native_idle=1;
uint8_t inventure_native_stepper_idle(void) { return native_idle; }

static void held_reset(uint32_t clock) {
    held_service_owner=held_service_pending=held_service_live=0;native_idle=1;
    native_z_guard_active=native_z_refine_held=native_z_refine_conflict=0;
    native_xy_handoff_owner=native_xy_handoff_run=native_xy_handoff_token=0;
    factory_reset(clock,0);
    control_command(1,CT_NATIVE);
    assert(control_mode==CT_NATIVE);
    GPIOE->ODR|=BIT(10);native_dir_seen=7;native_dir_levels=5;
}
static void held_entry(uint32_t owner) {
    uint32_t args[]={owner};command_inventure_native_held_service(args);
}
int main(void) {
    for(unsigned wrap=0;wrap<2;wrap++) {
        held_reset(wrap?0xfffffff0u:0);
        uint32_t before[7];for(unsigned p=0;p<7;p++)before[p]=mock_gpio[p].ODR;
        held_entry(123);
        assert(control_mode==CT_SERVICE && held_service_owner==123 && held_service_pending);
        assert(!inventure_native_queue_allowed());
        for(unsigned p=0;p<7;p++)assert(before[p]==mock_gpio[p].ODR);
        uint32_t deadline=held_service_deadline;
        held_entry(123);assert(deadline==held_service_deadline);
        held_entry(124);assert(held_service_owner==123 && deadline==held_service_deadline);
        factory(1,0,0,0,1000,1500,124,0);
        assert(!fs.active && factory_reply[3]==4 && held_service_pending);
        factory(1,0,0,0,1000,1500,123,0);
        assert(fs.active && !factory_reply[3] && !held_service_pending);
        assert(GPIOE->ODR&BIT(10));
        motion(1,0,12,12,0,0,1000,10,1000,0,0,0,456);
        while(cm.active)factory_motion_tick();
        assert(cm.phase==CM_DONE && cm.reason==CR_COMPLETE);
        uint32_t handback[]={123,cm.run,456};
        command_inventure_factory_native_handoff(handback);
        assert(control_mode==CT_NATIVE && !fs.active && (GPIOE->ODR&BIT(10)));
        assert(!held_service_live);
        assert(inventure_native_queue_allowed());
        held_entry(123);assert(control_mode==CT_NATIVE); // Stale entry cannot re-enter SERVICE.
        GPIOE->ODR&=~BIT(10);control_command(1,CT_SERVICE);
        assert(control_mode==CT_SERVICE && !held_service_owns_pins());
        assert(inventure_filter_gpio(GPIOE,BIT(10),1)==0);
        control_command(1,CT_NATIVE);GPIOE->ODR|=BIT(10);
        held_entry(125);assert(control_mode==CT_SERVICE && held_service_owner==125);
        factory_advance(1001,0);
        assert(!held_service_owner && !held_service_pending && GPIOE->BSRR==(BIT(10)<<16));
    }
    for(unsigned condition=0;condition<10;condition++) {
        held_reset(0);
        switch(condition) {
        case 0:native_idle=0;break;
        case 1:GPIOE->ODR&=~BIT(10);break;
        case 2:GPIOC->IDR&=~BIT(14);break;
        case 3:GPIOC->IDR|=BIT(13);break;
        case 4:native_z_guard_active=1;break;
        case 5:native_z_refine_held=1;break;
        case 6:control_heater_duty[0]=1;break;
        case 7:co[6].duty=1;break;
        case 8:control_latched=1;break;
        case 9:heartbeat_live=0;break;
        }
        held_entry(200+condition);
        assert(control_mode==CT_NATIVE && !held_service_owner && !held_service_pending);
    }
    held_reset(0);held_entry(700);assert(control_mode==CT_SERVICE);
    expect_failure=1;
    if(!setjmp(failure)) {
        inventure_filter_gpio(GPIOA,BIT(7),1);
        assert(!"Native STEP during held SERVICE was accepted");
    }
    expect_failure=0;assert(strstr(last_failure,"held SERVICE"));
    inventure_filter_gpio(GPIOE,BIT(10),0);
    assert(!held_service_owner && !held_service_pending && GPIOE->BSRR==(BIT(10)<<16));
    assert(control_latched && control_mode==CT_OFF);
    puts("PASS R24 held-service: drained queues, continuous PE10 both ways, owner/retry/expiry, interlocks and native-STEP exclusion");
    return 0;
}
