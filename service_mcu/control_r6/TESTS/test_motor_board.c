// Execute the actual board source against the same register mocks as R3.
#define main thermal_regression_main
#include "test_t0_heat_board.c"
#undef main

static void motor_reset(uint32_t clock) {
    if (xy_seek.timer_added) sched_del_timer(&xy_seek_timer);
    memset(&xy_seek,0,sizeof(xy_seek));
    if (motor_test.timer_added) sched_del_timer(&motor_timer);
    memset(&motor_test,0,sizeof(motor_test));
    door_policy_flags=DOOR_POLICY_DEFAULT_FLAGS;
    heat_reset(clock);
    GPIOB->IDR=BIT(10); GPIOE->IDR=BIT(15); GPIOC->IDR=BIT(14);
}
static void motor_command(unsigned a,unsigned m,unsigned d,uint32_t token) {
    uint32_t args[]={a,m,d,token}; command_inventure_motor_test(args);
}
static void motor_tick(uint8_t heartbeat) {
    assert(motor_test.timer_added);
    sched_del_timer(&motor_timer);
    mock_now=motor_timer.waketime;
    if(heartbeat)command_inventure_heartbeat(NULL);
    unsigned result=motor_test_event(&motor_timer);
    if(result==SF_RESCHEDULE)sched_add_timer(&motor_timer);
    else assert(!motor_test.timer_added && !motor_test.running);
}
static void motor_locked(void) {
    assert(!inventure_is_armed());
    assert(inventure_filter_gpio(GPIOA,BIT(7),1)==0);
    assert(inventure_filter_gpio(GPIOA,BIT(6),1)==0);
    assert(inventure_filter_gpio(GPIOB,BIT(8),1)==0);
    assert(inventure_filter_gpio(GPIOB,BIT(9),1)==0);
    for(unsigned i=0;i<4;i++)assert(t0_heat_gpio_level(i)==1);
    assert(inventure_filter_gpio(GPIOE,BIT(10),1)==0);
}
static void door_command(unsigned action,unsigned enabled) {
    uint32_t args[]={action,enabled};command_inventure_door_policy(args);
}
static void round_trip(unsigned m,uint32_t clock,uint8_t unlocked) {
    motor_reset(clock);
    door_command(1,!unlocked);
    if(unlocked) {
        assert(door_policy_flags==BIT(3));
        GPIOB->IDR&=~BIT(10);GPIOE->IDR&=~BIT(15);
    }
    motor_command(1,m,1,42);
    assert(motor_test.running && motor_test.phase==MT_FORWARD);
    assert(GPIOE->BSRR==BIT(10));
    uint32_t started=motor_test.started;
    unsigned high_forward=0,high_return=0,events=0;
    while(motor_test.running) {
        uint8_t previous_high=motor_test.high;
        uint32_t before=motor_timer.waketime;
        motor_tick(1);motor_locked();
        if(!previous_high && motor_test.high) {
            assert(mt_drivers[m].step_port->BSRR==mt_drivers[m].step_bit);
            if(motor_test.phase==MT_FORWARD)high_forward++; else high_return++;
            assert(motor_timer.waketime-before==MT_HIGH);
        }
        if(motor_test.running && ++events%100==0) {
            uint32_t run=motor_test.run;
            motor_command(1,m,1,42); // identical retry must not extend or restart
            motor_command(1,(m+1)%4,1,43); // another driver cannot replace this run
            assert(motor_test.run==run && motor_test.started==started);
        }
        assert(++events<6000);
    }
    assert(motor_test.phase==MT_DONE && motor_test.reason==MT_COMPLETE);
    assert(high_forward==mt_drivers[m].steps && high_return==mt_drivers[m].steps);
    assert(motor_test.forward==motor_test.returned && !motor_test.high);
    assert((uint32_t)(mock_now-started)<MT_TIMEOUT);
    assert(GPIOE->BSRR==(BIT(10)<<16));
    motor_command(1,m,1,42); // retry after completion also cannot restart
    assert(!motor_test.running && motor_test.run==1);
    motor_command(1,m,1,43);assert(motor_test.running && motor_test.run==2);
    motor_command(2,255,255,0); // STOP ignores invalid motor/direction
    assert(!motor_test.running && !motor_test.timer_added && motor_test.reason==MT_STOP);
}
static void abort_cases(void) {
    for(unsigned why=0;why<6;why++) {
        motor_reset(0xffffff00u);door_command(1,1);
        motor_command(1,0,1,100+why);motor_tick(1);
        assert(motor_test.high);
        switch(why) {
        case 0: heartbeat_live=0;break;
        case 1: GPIOB->IDR&=~BIT(10);break;
        case 2: GPIOE->IDR&=~BIT(15);break;
        case 3: GPIOB->IDR|=BIT(11);break;
        case 4: GPIOC->IDR|=BIT(15);break;
        case 5: motor_test.started=mock_now-MT_TIMEOUT;break;
        }
        motor_tick(0);
        assert(motor_test.phase==MT_ABORT && !motor_test.high);
        assert(GPIOE->BSRR==(BIT(10)<<16));
        assert(!motor_test.timer_added);
    }
    // START guards, including the printer's currently unqualified bottom Z=1.
    motor_reset(0);GPIOE->IDR|=BIT(12);motor_command(1,2,1,1);
    assert(!motor_test.running && !motor_test.run);
    motor_reset(0);GPIOC->IDR&=~BIT(14);motor_command(1,2,1,1);
    assert(!motor_test.running);
    motor_reset(0);motor_command(1,4,1,1);motor_command(1,0,2,1);motor_command(1,0,1,0);
    assert(!motor_test.running);
    motor_reset(0);all_heat_adc();uint32_t heat[]={1,0};command_inventure_heat_test(heat);
    motor_command(1,0,1,1);assert(!motor_test.running && t0_heat.phase==TH_HEATING);
    heat_reset(0);all_heat_adc();motor_reset(0);motor_command(1,0,1,1);
    command_inventure_heat_test(heat);assert(t0_heat.phase==TH_IDLE && motor_test.running);
    command(FM_START,0);assert(!fan_manual_active() && motor_test.running);
    inventure_board_shutdown(); // scheduler clears timers on real shutdown
    assert(!motor_test.running && motor_test.reason==MT_SHUTDOWN);
    assert(GPIOE->BSRR==(BIT(6)<<16)); // later raw_safe_outputs keeps PE10 low too
}
static void door_policy_cases(void) {
    // Enabling requires both closed inputs. Queries and invalid actions
    // cannot change the policy; input masks preserve lid=1/front=2 order.
    motor_reset(0);assert(!door_policy_enabled() && door_closed_mask()==3);
    door_command(1,1);assert(door_policy_enabled());
    door_command(0,0);door_command(2,0);door_command(1,2);
    assert(door_policy_enabled());
    GPIOB->IDR&=~BIT(10);assert(door_closed_mask()==1);
    motor_command(1,0,1,1);assert(!motor_test.running);
    GPIOB->IDR|=BIT(10);GPIOE->IDR&=~BIT(15);assert(door_closed_mask()==2);
    motor_command(1,0,1,2);assert(!motor_test.running);
    GPIOB->IDR&=~BIT(10);assert(!door_closed_mask());
    door_command(1,0);assert(!door_policy_enabled());
    motor_command(1,0,1,3);assert(motor_test.running);
    uint32_t start=motor_test.started,run=motor_test.run;
    door_command(0,1);door_command(1,1);door_command(1,0);
    assert(!door_policy_enabled() && motor_test.running);
    assert(motor_test.started==start && motor_test.run==run);
    motor_tick(1);motor_command(2,0,0,0);
    door_command(1,1);assert(door_policy_enabled());
    motor_command(1,0,1,4);assert(!motor_test.running);
    // Turning off the door policy never masks X, Y, Z or nozzle contact.
    for(unsigned why=0;why<4;why++) {
        motor_reset(0);door_command(1,0);
        unsigned m=why<2?0:2;
        if(why==0)GPIOB->IDR|=BIT(11);
        if(why==1)GPIOC->IDR|=BIT(15);
        if(why==2)GPIOE->IDR|=BIT(12);
        if(why==3)GPIOC->IDR&=~BIT(14);
        motor_command(1,m,1,10+why);assert(!motor_test.running);
    }
    // Reject a policy change without readiness or during heat/cooldown.
    motor_reset(0);door_command(1,1);heartbeat_live=0;door_command(1,0);assert(door_policy_enabled());
    motor_reset(0);door_command(1,1);all_heat_adc();uint32_t heat[]={1,0};command_inventure_heat_test(heat);
    door_command(1,0);assert(door_policy_enabled());
    // Normal shutdown restores the default and still releases the driver.
    motor_reset(0);door_command(1,1);motor_command(1,0,1,99);
    inventure_board_shutdown();assert(!door_policy_enabled() && !motor_test.running);
}
static void seek_command(unsigned action,unsigned axis,uint32_t token) {
    uint32_t args[]={action,axis,token};command_inventure_xy_seek(args);
}
static void seek_tick(uint8_t heartbeat) {
    assert(xy_seek.timer_added);sched_del_timer(&xy_seek_timer);
    mock_now=xy_seek_timer.waketime;
    if(heartbeat)command_inventure_heartbeat(NULL);
    unsigned result=xy_seek_event(&xy_seek_timer);
    if(result==SF_RESCHEDULE)sched_add_timer(&xy_seek_timer);
    else assert(!xy_seek.running && !xy_seek.timer_added);
}
static void seek_hit_cases(void) {
    for(unsigned axis=0;axis<2;axis++)for(unsigned high=0;high<2;high++) {
        motor_reset(0xffffff00u);door_command(1,0);
        GPIOB->IDR&=~BIT(10);GPIOE->IDR&=~BIT(15);
        seek_command(1,axis,41);assert(xy_seek.running && motor_test_busy());
        while(xy_seek.steps<17 || xy_seek.high!=high) {
            seek_tick(1);motor_locked();
            assert(GPIOA->BSRR==((uint32_t)XS_STEP_BITS<<(xy_seek.high?0:16)));
            assert(xy_seek.period>=CONFIG_CLOCK_FREQ/XS_HZ);
            if(xy_seek.high)assert(xy_seek_timer.waketime-mock_now==MT_HIGH);
        }
        uint32_t steps=xy_seek.steps,run=xy_seek.run,last=xy_seek.last_tick;
        seek_command(1,axis,41);seek_command(1,axis^1,42);
        assert(xy_seek.run==run && xy_seek.last_tick==last);
        motor_command(1,0,1,51);assert(!motor_test.running && xy_seek.running);
        door_command(1,1);assert(!door_policy_enabled());
        if(axis==0)GPIOB->IDR|=BIT(11);else GPIOC->IDR|=BIT(15);
        seek_tick(1);
        assert(xy_seek.phase==XS_HIT && xy_seek.reason==XS_ENDSTOP);
        assert(xy_seek.steps==steps && !xy_seek.high && !motor_test_busy());
        assert(xy_seek.hit_mask&BIT(axis));
        assert(GPIOA->BSRR==(XS_STEP_BITS<<16));
        assert(GPIOE->BSRR==(BIT(10)<<16));
        seek_command(1,axis,41);assert(xy_seek.run==run && !xy_seek.running);
        // New request on an already asserted switch does not emit a pulse.
        seek_command(1,axis,43);
        assert(xy_seek.reason==XS_ALREADY && !xy_seek.steps && !xy_seek.running);
    }
    // X may remain pressed while the subsequent pure Y move seeks PC15.
    motor_reset(0);GPIOB->IDR|=BIT(11);seek_command(1,1,80);
    assert(xy_seek.running);for(unsigned n=0;n<5;n++)seek_tick(1);
    GPIOC->IDR|=BIT(15);seek_tick(1);
    assert(xy_seek.reason==XS_ENDSTOP && xy_seek.hit_mask==3);
}
static void seek_limits_and_stops(void) {
    for(unsigned axis=0;axis<2;axis++) {
        motor_reset(0xffffff00u);seek_command(1,axis,61);
        unsigned emitted=0;
        while(xy_seek.running) {
            uint8_t previous=xy_seek.high;seek_tick(1);
            if(!previous && xy_seek.high)emitted++;
            assert(emitted<=XS_STEPS);
        }
        assert(xy_seek.reason==XS_DISTANCE && emitted==XS_STEPS);
        assert(xy_seek.elapsed_ms>32000 && xy_seek.elapsed_ms<XS_MS);
        assert(xy_seek.period==CONFIG_CLOCK_FREQ/XS_HZ);
    }
    for(unsigned why=0;why<7;why++) {
        motor_reset(0);door_command(1,1);seek_command(1,0,71);seek_tick(1);assert(xy_seek.high);
        if(why==0)heartbeat_live=0;
        if(why==1)GPIOB->IDR&=~BIT(10);
        if(why==2)GPIOC->IDR|=BIT(15);
        if(why==3)xy_seek.elapsed_ms=XS_MS;
        if(why==4)seek_command(2,255,0);
        if(why==5)motor_command(2,255,255,0);
        if(why==6)inventure_board_shutdown();
        if(xy_seek.running)seek_tick(0);
        assert(!xy_seek.running && !xy_seek.high);
        assert(xy_seek.phase==XS_FAILED || xy_seek.phase==XS_STOPPED);
    }
    // Accumulate 40 seconds across the 35.8s native timer wrap. Never
    // silently extend a long seek when the 120MHz uint32 clock wraps.
    motor_reset(0);seek_command(1,0,90);
    for(unsigned n=0;n<40;n++) {
        xy_seek_timer.waketime=mock_now+MS(1000);seek_tick(1);
    }
    assert(xy_seek.elapsed_ms==40000 && xy_seek.reason==XS_TIME);
    motor_reset(0);all_heat_adc();uint32_t heat[]={1,0};command_inventure_heat_test(heat);
    seek_command(1,0,91);assert(!xy_seek.running);
    motor_reset(0);seek_command(1,0,92);command_inventure_heat_test(heat);
    assert(t0_heat.phase==TH_IDLE && xy_seek.running);
    command(FM_START,0);assert(!fan_manual_active());
    seek_command(2,0,0);motor_reset(0);
    seek_command(1,2,1);seek_command(1,0,0);seek_command(3,0,1);
    assert(!xy_seek.running && !xy_seek.run);
}
static void repeat_command(unsigned action,unsigned axis,uint32_t token) {
    uint32_t args[]={action,axis,token};command_inventure_xy_repeat(args);
}
static void repeat_to_slow(unsigned axis,uint8_t initially_pressed) {
    motor_reset(0xffffff00u);
    if(initially_pressed) {
        GPIOB->IDR|=BIT(11);GPIOC->IDR|=BIT(15);
    }
    repeat_command(1,axis,100);
    assert(xy_seek.running && xy_seek.repeat);
    if(!initially_pressed) {
        while(xy_seek.steps<20)seek_tick(1);
        if(axis==0)GPIOB->IDR|=BIT(11);else GPIOC->IDR|=BIT(15);
        seek_tick(1);
    }
    assert(xy_seek.stage==XR_BACKOFF && !xy_seek.steps && !xy_seek.high);
    assert(xy_seek.first_steps==(initially_pressed?0:20));
    assert(GPIOE->BSRR==BIT(10)); // never release the driver between phases
    unsigned emitted=0;
    while(xy_seek.stage==XR_BACKOFF && xy_seek.running) {
        if(xy_seek.steps>=64) {
            if(axis==0)GPIOB->IDR&=~BIT(11);else GPIOC->IDR&=~BIT(15);
        }
        uint8_t high=xy_seek.high;seek_tick(1);motor_locked();
        if(!high && xy_seek.high)emitted++;
        assert(GPIOE->BSRR==BIT(10));
        if(xy_seek.high)assert(xy_seek.period>=CONFIG_CLOCK_FREQ/XR_BACKOFF_HZ);
        assert(emitted<=1280);
    }
    assert(emitted==1280 && xy_seek.backoff_steps==1280 && xy_seek.released);
    assert(xy_seek.stage==XR_SLOW && xy_seek.running && !xy_seek.high);
    assert(GPIOE->BSRR==BIT(10));
}
static void repeat_success_cases(void) {
    for(unsigned axis=0;axis<2;axis++)for(unsigned pressed=0;pressed<2;pressed++) {
        repeat_to_slow(axis,pressed);
        while(xy_seek.steps<1270 || xy_seek.high!=pressed) {
            seek_tick(1);motor_locked();
            assert(xy_seek.period>=CONFIG_CLOCK_FREQ/XR_SLOW_HZ);
            assert(GPIOE->BSRR==BIT(10));
        }
        uint32_t slow=xy_seek.steps,run=xy_seek.run,elapsed=xy_seek.elapsed_ms;
        repeat_command(1,axis,100);assert(xy_seek.run==run && xy_seek.elapsed_ms==elapsed);
        if(axis==0)GPIOB->IDR|=BIT(11);else GPIOC->IDR|=BIT(15);
        seek_tick(1);
        assert(xy_seek.phase==XS_HIT && xy_seek.reason==XS_ENDSTOP);
        assert(xy_seek.steps==slow && xy_seek.released && xy_seek.backoff_steps==1280);
        assert(!xy_seek.running && !xy_seek.high && !xy_seek.timer_added);
        assert(GPIOE->BSRR==(BIT(10)<<16));
        repeat_command(1,axis,100);seek_command(1,axis,100);
        assert(xy_seek.run==run && !xy_seek.running); // cross-mode token cannot restart
    }
}
static void repeat_failure_cases(void) {
    // A stuck target must stop after exactly the bounded backoff, with no
    // slow approach and no extra pulse on the opposite endstop.
    motor_reset(0);GPIOB->IDR|=BIT(11);repeat_command(1,0,200);
    while(xy_seek.running)seek_tick(1);
    assert(xy_seek.reason==XS_STUCK && xy_seek.steps==1280 && !xy_seek.released);
    repeat_to_slow(0,0);GPIOB->IDR|=BIT(11);seek_tick(1);
    assert(xy_seek.reason==XS_EARLY_CONTACT && !xy_seek.steps && !xy_seek.running);
    repeat_to_slow(0,0);
    while(xy_seek.running)seek_tick(1);
    assert(xy_seek.reason==XS_DISTANCE && xy_seek.steps==2560);
    // STOP, heartbeat loss and an unexpected second switch apply in every
    // stage; normal heating/fan starts remain excluded during the reversal.
    for(unsigned stage=0;stage<3;stage++)for(unsigned why=0;why<4;why++) {
        if(stage==2)repeat_to_slow(0,0);
        else {
            motor_reset(0);
            if(stage==1)GPIOB->IDR|=BIT(11);
            repeat_command(1,0,201);
        }
        if(why==0)repeat_command(2,255,0);
        if(why==1){heartbeat_live=0;seek_tick(0);}
        if(why==2){GPIOC->IDR|=BIT(15);seek_tick(1);}
        if(why==3)motor_command(2,255,255,0);
        assert(!xy_seek.running && !xy_seek.high);
    }
    motor_reset(0);GPIOB->IDR|=BIT(11);repeat_command(1,0,211);
    all_heat_adc();uint32_t heat[]={1,0};command_inventure_heat_test(heat);
    assert(t0_heat.phase==TH_IDLE);command(FM_START,0);assert(!fan_manual_active());
    inventure_board_shutdown();assert(!xy_seek.running && xy_seek.reason==XS_SHUTDOWN);
}
int main(void) {
    thermal_regression_main();
    for(unsigned m=0;m<4;m++)for(unsigned off=0;off<2;off++) {
        round_trip(m,0,off);round_trip(m,0xffffff00u,off);
    }
    abort_cases();
    door_policy_cases();
    seek_hit_cases();seek_limits_and_stops();
    repeat_success_cases();repeat_failure_cases();
    puts("PASS XY REPEAT R4: existing first approach, exact 1280-pair backoff, 5ms switch release, 640Hz slow approach, retained enable between stages, both axes/initial states/edge phases, stuck/early/missing target failures, cross-mode duplicate tokens, STOP, heartbeat, unrelated switch, shutdown and thermal exclusion");
    puts("PASS XY SEEK R3: paired edges, 100us HIGH, both target switches on HIGH/LOW, wrap, exact 102400 pulse limit, 40s timeout, no retract, already-active, X-held/Y-seek, unexpected switch, duplicate START, mutual exclusion, both STOP commands, shutdown and native STEP locks");
    puts("PASS MOTOR R2: exact round trips with both door policies, OEM bit3/masks, default OFF, rejected policy changes, 100us pulses, timer wrap, duplicate START, STOP, XY/Z limits, heartbeat, deadline, shutdown, heat exclusion and native STEP locks");
    return 0;
}
