// Execute CONTROL-R5 with the actual board/PWM source and register mocks.
#define main motor_regression_main
#include "test_t0_heat_board.c"
#undef main

static void control_command(unsigned action,unsigned mode) {
    uint32_t args[]={action,mode};command_inventure_control(args);
}
static void control_set_door_policy(unsigned enabled) {
    uint32_t args[]={1,enabled};command_inventure_door_policy(args);
}
static void control_adc(void) {
    all_heat_adc();inventure_adc_observe(ADC3,9,3680);inventure_adc_observe(ADC3,14,3680);
}
static void control_reset(uint32_t now) {
    scheduled_count=0;
    memset(mock_tim,0,sizeof(mock_tim));memset(mock_gpio,0,sizeof(mock_gpio));
    memset((void*)feeder_running,0,sizeof(feeder_running));
    memset((void*)locks_on,0,sizeof(locks_on));memset((void*)locks_pulling,0,sizeof(locks_pulling));
    memset(transport,0,sizeof(transport));memset(assist_pending,0,sizeof(assist_pending));
    transport_live=0;auxiliary_ready=0;
    memset(&motor_test,0,sizeof(motor_test));memset(&xy_seek,0,sizeof(xy_seek));
    control_guard_mask=127;native_hot_mask=0;memset(control_tach_watch,0,sizeof(control_tach_watch));
    const int16_t caps[]={310,310,150,150,150,100,100,190};
    for(unsigned i=0;i<8;i++){control_min[i]=-20;control_max[i]=caps[i];}
    heat_reset(now);control_adc();
    GPIOB->IDR=BIT(10);GPIOE->IDR=BIT(15);GPIOC->IDR=BIT(14);
    door_policy_flags=DOOR_POLICY_DEFAULT_FLAGS;command_inventure_aux_config(NULL);
    control_command(1,CT_SERVICE);assert(control_mode==CT_SERVICE && !inventure_is_armed());
}
static void motion(unsigned action,unsigned dirs,uint32_t a,uint32_t b,uint32_t z,uint32_t e,
        unsigned hz,unsigned pulse,uint32_t ms,unsigned mask,unsigned value,unsigned hold,uint32_t token) {
    uint32_t args[]={action,dirs,a,b,z,e,hz,pulse,ms,mask,value,hold,token};command_inventure_motion(args);
}
static void motion_tick(void) {
    assert(cm.active && cm.timer_added);sched_del_timer(&cm_timer);
    mock_now=cm_timer.waketime;command_inventure_heartbeat(NULL);control_adc();
    unsigned r=cm_event(&cm_timer);
    if(r==SF_RESCHEDULE)sched_add_timer(&cm_timer);
    else assert(!cm.active && !cm.timer_added && !cm.high);
}
static void output(unsigned action,unsigned channel,unsigned duty,uint32_t ms,int limit,uint32_t token) {
    uint32_t args[]={action,channel,duty,ms,(uint32_t)limit,token};command_inventure_output(args);
}
static void advance(unsigned ms) {
    mock_now+=MS(ms);command_inventure_heartbeat(NULL);control_adc();guard_check(mock_now);cooling_update();
}
// Stress the actual production sampler with both scheduler phases, synthetic
// interrupt blackouts, packet-rate bursts and wrapping 32-bit MCU clock.
static void scheduler_native_service_stress(void) {
    control_reset(0);
    control_command(1,CT_NATIVE);
    assert(inventure_is_armed());
    sample_calls=sample_native_calls=sample_service_calls=sample_guard_runs=0;
    sample_skipped_slots=sample_reschedules=0;
    sample_peak_late_ticks=sample_peak_work_ticks=0;
    guard_div=0;fan_sample_ticks=fan_detector_ticks=0;
    quiet_ticks[0]=quiet_ticks[1]=0;
    sample_timer.waketime=mock_now+SAMPLE_TICKS;
    uint32_t previous=sample_timer.waketime;
    for(unsigned i=0;i<1800;i++) {
        mock_now=sample_timer.waketime; // on-time callback
        assert(sample_event(&sample_timer)==SF_RESCHEDULE);
        assert((uint32_t)(sample_timer.waketime-previous)==2*SAMPLE_TICKS);
        assert((int32_t)(sample_timer.waketime-mock_now)>=
               (int32_t)timer_from_us(SAMPLE_MARGIN_US));
        previous=sample_timer.waketime;
        if((i%250)==0)command_inventure_heartbeat(NULL);
    }
    assert(sample_native_calls==1800 && !sample_service_calls);
    assert(sample_guard_runs==18); // preserved 10 ms independent safety cadence
    assert(fan_sample_ticks==3600); // preserves 1-second tach window in NATIVE
    assert(!sample_reschedules && !sample_skipped_slots);
    // 20 ms noise suppression is expressed in 20 kHz-equivalent slots even
    // though normal XY/Z has only 10 kHz callbacks.
    assert(quiet_ticks[0]==401 && quiet_ticks[1]==401);

    // Native feeder transport still gets the full 20 kHz physical sampler.
    transport_live=1;
    mock_now=sample_timer.waketime;
    previous=sample_timer.waketime;
    sample_event(&sample_timer);
    assert(sample_timer.waketime-previous==SAMPLE_TICKS);
    assert(sample_service_calls==1);
    transport_live=0;

    // SERVICE diagnostics and all manual calibrated fan PWM stay at 20 kHz.
    control_reset(0);
    assert(control_mode==CT_SERVICE);
    sample_timer.waketime=mock_now+SAMPLE_TICKS;
    for(unsigned i=0;i<100;i++) {
        mock_now=sample_timer.waketime;previous=sample_timer.waketime;
        sample_event(&sample_timer);
        assert(sample_timer.waketime-previous==SAMPLE_TICKS);
    }

    // MCU wrap is not a special timer epoch. The same signed comparator is
    // valid for any short delta crossing ffffffff -> 00000000.
    control_reset(0xffffe000u);
    control_command(1,CT_NATIVE);
    mock_now=0xffffe000u;
    sample_timer.waketime=mock_now+SAMPLE_TICKS;
    for(unsigned i=0;i<1200;i++) {
        mock_now=sample_timer.waketime;
        sample_event(&sample_timer);
        assert((int32_t)(sample_timer.waketime-mock_now)>0);
        if(!(i%250))command_inventure_heartbeat(NULL);
    }

    // Delayed ISR cannot silently postpone the safety tick; physical elapsed
    // time must advance the 10ms guard and fan tach window in one callback.
    control_reset(0);
    control_command(1,CT_NATIVE);
    guard_div=0;sample_calls=0;sample_guard_runs=0;
    fan_sample_ticks=fan_detector_ticks=0;
    sample_timer.waketime=mock_now+SAMPLE_TICKS;
    mock_now=sample_timer.waketime;
    sample_event(&sample_timer);
    assert(!sample_guard_runs);
    mock_now=sample_timer.waketime+MS(13);
    sample_event(&sample_timer);
    assert(sample_guard_runs==1);
    assert(fan_sample_ticks>=260 && fan_sample_ticks<20000);
    assert((int32_t)(sample_timer.waketime-mock_now)>0);

    // Delayed callback MUST skip overdue polling slots, not run the same
    // GPIO twice for every missed 50 us and starve Klipper's step timers.
    control_reset(0);
    control_command(1,CT_NATIVE);
    sample_timer.waketime=mock_now+SAMPLE_TICKS;
    mock_now=sample_timer.waketime+2*SAMPLE_TICKS; // emulate ISR contention
    uint32_t old_skipped=sample_skipped_slots,old_resync=sample_reschedules;
    sample_event(&sample_timer);
    assert(sample_skipped_slots>old_skipped && sample_reschedules==old_resync+1);
    assert((int32_t)(sample_timer.waketime-mock_now)>=
           (int32_t)timer_from_us(SAMPLE_MARGIN_US));
    // A long callback taking more than the 100 us NATIVE period must also
    // resynchronize to the end of the callback, not its already-past start.
    sample_timer.waketime=mock_now+SAMPLE_TICKS;
    mock_now=sample_timer.waketime;
    mock_read_cycles=timer_from_us(110);
    old_resync=sample_reschedules;
    sample_event(&sample_timer);
    mock_read_cycles=0;
    assert(sample_reschedules>old_resync);
    // The mock timer advances again on the final read. Compare against
    // the measured callback completion, not the model's next future tick.
    assert((int32_t)(sample_timer.waketime -
           (sample_last_clock + sample_last_work)) > 0);
    assert(sample_peak_work_ticks>=timer_from_us(110));
    puts("PASS scheduler: NATIVE 10kHz / SERVICE 20kHz / 10ms guard / tach cadence / wrap / delayed ISR resync");
}

static void dda_and_replay(void) {
    for(unsigned wrap=0;wrap<2;wrap++)for(unsigned dirs=0;dirs<16;dirs++) {
        control_reset(wrap?0xffffff00:0);
        motion(1,dirs,97,61,23,11,20000,10,30000,0,0,7,123);
        unsigned n=0;assert(cm.active && cm.direction==dirs);
        while(cm.active) {
            motion_tick();assert(++n<300);
            if(cm.active) {
                assert(cm.period>=CONFIG_CLOCK_FREQ/20000);
                if(cm.high)assert(cm_timer.waketime-mock_now==timer_from_us(10));
            }
            if(n%13==0) {
                uint32_t elapsed=cm.elapsed,run=cm.run;
                motion(1,dirs,97,61,23,11,20000,10,30000,0,0,7,123);
                assert(cm.run==run && cm.elapsed==elapsed);
                motion(1,dirs,97,61,23,11,19999,10,30000,0,0,7,123);
                assert(cm.run==run && cm.rate==20000); // same token/different parameters rejected
            }
        }
        assert(cm.sent[0]==97 && cm.sent[1]==61 && cm.sent[2]==23 && cm.sent[3]==11);
        assert(cm.phase==CM_DONE && cm.reason==CR_COMPLETE && cm.done==97);
        assert(cm.elapsed-cm.hold_start>=7 && GPIOE->BSRR==(BIT(10)<<16));
        motion(1,dirs,97,61,23,11,20000,10,30000,0,0,7,123);
        assert(!cm.active && cm.run==1);
    }
    // A diagonal vector can leave one motor completely stationary.
    control_reset(0);motion(1,1,640,0,0,0,1280,10,30000,0,0,0,1);
    while(cm.active)motion_tick();assert(cm.sent[0]==640 && !cm.sent[1]);
}
static void input_stops(void) {
    const uint8_t pins[]={GPIO('B',11),GPIO('C',15),GPIO('E',12),GPIO('C',14),GPIO('C',13),GPIO('G',2),
        GPIO('B',10),GPIO('E',15),GPIO('D',10),GPIO('F',1),GPIO('D',15),GPIO('G',10),
        GPIO('D',7),GPIO('D',6),GPIO('D',5),GPIO('A',15)};
    for(unsigned input=0;input<16;input++)for(unsigned polarity=0;polarity<2;polarity++) {
        control_reset(0);
        GPIO_TypeDef *g=gpio_pin_to_regs(pins[input]);uint32_t bit=BIT(pins[input]%16);
        if(polarity)g->IDR|=bit;else g->IDR&=~bit;
        motion(1,15,400,400,400,400,1000,10,30000,BIT(input),polarity?BIT(input):0,0,1);
        assert(!cm.active && cm.hit==BIT(input) && !cm.sent[0]); // asserted before enable
        if(polarity)g->IDR&=~bit;else g->IDR|=bit;
        motion(1,15,400,400,400,400,1000,10,30000,BIT(input),polarity?BIT(input):0,0,2);
        assert(cm.active);motion_tick();assert(cm.high && cm.sent[0]==1);
        if(polarity)g->IDR|=bit;else g->IDR&=~bit;
        motion_tick();assert(!cm.active && cm.reason==CR_INPUT && cm.sent[0]==1 && cm.hit==BIT(input));
    }
    // Mask means ANY matching stop input; X may be high while Y alone is observed.
    control_reset(0);GPIOB->IDR|=BIT(11);
    motion(1,0,100,100,0,0,500,10,30000,2,2,0,1);assert(cm.active);
    GPIOC->IDR|=BIT(15);motion_tick();assert(cm.hit==2);
}
static void motion_guards(void) {
    for(unsigned why=0;why<6;why++) {
        control_reset(0);
        if(why==1)control_set_door_policy(1);
        motion(1,0,100,100,100,100,400,10,30000,0,0,0,1);motion_tick();
        if(why==0){heartbeat_live=0;mock_now=cm_timer.waketime;assert(cm_event(&cm_timer)==SF_DONE);}
        if(why==1){GPIOB->IDR&=~BIT(10);motion_tick();}
        if(why==2){GPIOD->IDR|=BIT(11);motion_tick();}
        if(why==3)control_command(2,0);
        if(why==4){uint32_t a[]={2,255,255,0};command_inventure_motor_test(a);}
        if(why==5){uint32_t a[]={2,255,0};command_inventure_xy_seek(a);}
        assert(!cm.active && !cm.high && GPIOE->BSRR==(BIT(10)<<16));
    }
    control_reset(0);motion(1,0,2000000,0,0,0,1,10,600000,0,0,0,1);
    for(unsigned sec=0;sec<600;sec++) {
        cm_timer.waketime=mock_now+MS(1000);motion_tick();
    }
    assert(cm.elapsed==600000 && cm.reason==CR_TIME && !cm.active);
    control_reset(0);
    motion(1,0,1,0,0,0,20001,10,30000,0,0,0,1);assert(!cm.active);
    motion(1,0,1,0,0,0,20000,100,30000,0,0,0,2);assert(!cm.active);
    motion(1,0,2000001,0,0,0,400,10,30000,0,0,0,3);assert(!cm.active);
    control_reset(0);motion(1,0,100,0,0,0,1,10,50,0,0,0,4);
    advance(50);assert(!cm.active && cm.elapsed==50 && cm.reason==CR_TIME);
    control_reset(0);motion(1,0,100,0,0,0,1,10,5000,0,0,0,5);
    heartbeat_live=0;control_guard(mock_now);
    assert(!cm.active && cm.phase==CM_FAILED && cm.reason==CR_HEARTBEAT);
}
static void pwm_outputs(void) {
    for(unsigned h=0;h<4;h++) {
        control_reset(0xffffff00);
        output(1,h+6,192,180000,50,21);assert(co[h+6].duty==192);
        volatile uint32_t *reg=h==0?&TIM5->CCR1:h==1?&TIM9->CCR1:h==2?&TIM8->CCR4:&TIM8->CCR3;
        assert(*reg==64 && control_heater_duty[h]==192);
        uint8_t pin=h==0?GPIO('A',0):h==1?GPIO('E',5):h==2?GPIO('C',9):GPIO('C',8);
        assert(((gpio_pin_to_regs(pin)->MODER>>((pin%16)*2))&3u)==2);
        assert(cool_mask&(h<2?1:12));
        advance(10);uint32_t remaining=co[h+6].remaining;
        output(1,h+6,192,180000,50,21);assert(co[h+6].remaining==remaining);
        output(1,h+6,128,180000,50,21);assert(co[h+6].duty==192); // mismatched replay rejected
        for(unsigned sec=0;sec<180;sec++)advance(1000);
        assert(!co[h+6].duty && co[h+6].reason==3 && !control_heater_duty[h]);
        assert((th_heaters[h].port->MODER>>(2*__builtin_ctz(th_heaters[h].bit))&3u)==1);
        assert(control_cooldown_ms && cool_mask==13);
        output(1,h+6,192,180000,50,21);assert(!co[h+6].duty); // finished token cannot reenergize
    }
    for(unsigned fan=0;fan<6;fan++) {
        control_reset(0);output(1,fan,128,600000,0,1);assert(co[fan].duty==128);
        if(fan<4) {
            unsigned high=0;
            for(unsigned n=0;n<50;n++){control_fan_tick();high+=!!(fan_high&BIT(fan));}
            assert(high==25);
        } else {
            volatile uint32_t *r=fan==4?&TIM5->CCR2:&TIM4->CCR2;
            assert(*r==128);cool_mask=13;cool_applied=255;cooling_apply();
            unsigned pin=fan==4?1:13;GPIO_TypeDef *g=fan==4?GPIOA:GPIOD;
            assert(((g->MODER>>(pin*2))&3u)==2 && *r==128);
        }
        for(unsigned n=0;n<600;n++)advance(1000);
        assert(!co[fan].duty && co[fan].reason==3);
    }
}
static void nozzle_310_cutoff_and_link_loss(void) {
    control_reset(0);
    inventure_adc_observe(ADC1,10,1918); // about 309 C
    inventure_adc_observe(ADC3,6,1918);
    output(1,6,64,20000,310,1);output(1,7,64,20000,310,2);
    assert(co[6].duty && co[7].duty);
    inventure_adc_observe(ADC3,6,1924); // above 310 C
    guard_check(mock_now);
    assert(!co[6].duty && !co[7].duty && co[7].reason==4);

    control_reset(0);
    inventure_adc_observe(ADC1,10,1918);
    inventure_adc_observe(ADC3,6,1918);
    output(1,6,64,20000,310,1);output(1,7,64,20000,310,2);
    assert(co[6].duty && co[7].duty);
    assert(HEARTBEAT_TICKS==MS(5000));
    mock_now=heartbeat_deadline-MS(1);
    control_adc();
    inventure_adc_observe(ADC1,10,1918);inventure_adc_observe(ADC3,6,1918);
    guard_check(mock_now);
    assert(heartbeat_live && control_mode==CT_SERVICE && co[6].duty && co[7].duty);
    mock_now=heartbeat_deadline;
    control_adc();
    inventure_adc_observe(ADC1,10,1918);
    inventure_adc_observe(ADC3,6,1918);
    guard_check(mock_now);
    assert(!co[6].duty && !co[7].duty && !control_heater_duty[0] && !control_heater_duty[1]);
    assert(control_mode==CT_OFF && (cool_mask&1) && (fan_high&1));
    puts("PASS nozzles near 310 C: temperature cutoff and heartbeat loss turn off both heaters with hotend cooling");
}

static void temperature_stops(void) {
    for(unsigned i=0;i<7;i++) {
        control_reset(0);output(1,6,64,20000,45,1);assert(co[6].duty);
        latest_adc[i]=4095;control_guard(mock_now);
        assert(!co[6].duty && co[6].reason==4);
    }
    control_reset(0);output(1,6,64,20000,45,1);assert(co[6].duty && latest_adc[7]==1054);
    mock_now+=MS(1000);command_inventure_heartbeat(NULL);control_guard(mock_now);
    assert(!co[6].duty && co[6].reason==4); // no stale-ADC extension
    control_reset(0);output(1,6,64,20000,45,1);
    inventure_adc_observe(ADC1,10,280);control_guard(mock_now);assert(!co[6].duty); // >=45C
    control_reset(0);output(1,6,64,20000,45,1);GPIOD->IDR|=BIT(11);control_guard(mock_now);
    assert(control_mode==CT_OFF && !co[6].duty && co[6].reason==5);
    control_reset(0);uint32_t guard[]={7,1,-20,100};command_inventure_control_guard(guard);
    output(1,6,64,20000,45,1);assert(!co[6].duty); // explicitly enabled bad PC4 prevents heating
}
static unsigned heater_pin_mode(unsigned h) {
    unsigned shift = 2 * __builtin_ctz(th_heaters[h].bit);
    return (th_heaters[h].port->MODER >> shift) & 3u;
}

static void service_to_native_heat(void) {
    control_reset(0);
    struct gpio_pwm native[4];
    for (unsigned h = 0; h < 4; h++)
        native[h] = gpio_pwm_setup(co_pins[2+h], co_cycles[2+h], 256);

    // Klipper configured the pins once. CLEAN heats T0 in SERVICE, then
    // releases the service outputs; Home XYZ reuses the same native handles.
    output(1, 6, 102, 1500, 208, 101);
    assert(heater_pin_mode(0) == 2 && TIM5->CCR1 == 154);
    control_command(2, CT_OFF);
    for (unsigned h = 0; h < 4; h++)
        assert(heater_pin_mode(h) == 1);
    assert(!co[6].duty && TIM5->CCR1 == 154);
    TIM5->CNT = 71;
    uint32_t shared_ccr = TIM5->CCR3;
    control_command(1, CT_NATIVE);
    assert(inventure_is_armed());
    for (unsigned h = 0; h < 4; h++) {
        assert(heater_pin_mode(h) == 2);
        assert(*(volatile uint32_t *)native[h].reg == 256);
        assert(!control_heater_duty[h]);
        gpio_pwm_write(native[h], 154);
        assert(*(volatile uint32_t *)native[h].reg == 154 && control_heater_duty[h] == 102);
    }
    assert(TIM5->CNT == 71 && TIM5->CCR3 == shared_ccr);
    control_command(1, CT_NATIVE);
    assert(TIM5->CCR1 == 154 && control_heater_duty[0] == 102);
    control_command(2, CT_OFF);
    assert(control_latched && heater_pin_mode(0) == 1);
    gpio_pwm_write(native[0], 154);
    assert(TIM5->CCR1 == 256 && !control_heater_duty[0]);
    control_command(1, CT_NATIVE);
    assert(!inventure_is_armed() && heater_pin_mode(0) == 1);

    // Only T0 was configured by Klipper in this installation. A service-only
    // T1 lease must expire before a handoff, and its pin must stay OFF.
    control_reset(0);
    native[0] = gpio_pwm_setup(co_pins[2], co_cycles[2], 256);
    output(1, 7, 64, 500, 208, 102);
    output(2, 6, 0, 0, 0, 0);
    control_command(1, CT_NATIVE);
    assert(control_mode == CT_SERVICE && heater_pin_mode(0) == 1 && co[7].duty == 64);
    advance(500);
    assert(!co[7].duty && heater_pin_mode(1) == 1);
    control_set_door_policy(1);
    GPIOB->IDR &= ~BIT(10);
    control_command(1, CT_NATIVE);
    assert(control_mode == CT_SERVICE && heater_pin_mode(0) == 1);
    GPIOB->IDR |= BIT(10);
    control_command(1, CT_NATIVE);
    assert(inventure_is_armed() && heater_pin_mode(0) == 2);
    for (unsigned h = 1; h < 4; h++)
        assert(heater_pin_mode(h) == 1);
    puts("PASS CLEAN to native Home heating: GPIO mode, safe first duty, existing PWM handles, shared timer and interlocks");
}

// Regression: Home X -> Home Y -> Home XY leaves CT_NATIVE armed, even
// after the stepper enable has been released. Home All must then acquire
// SERVICE for T0 selection without STOP/latch or an MCU restart.
static void native_to_service_quiescent_handoff(void) {
    control_reset(0);
    control_command(1, CT_NATIVE);
    assert(inventure_is_armed() && control_mode==CT_NATIVE);
    // A queued/energized native axis must never be handed to SERVICE.
    GPIOE->ODR |= BIT(10);
    control_command(1, CT_SERVICE);
    assert(control_mode==CT_NATIVE && !control_latched);
    GPIOE->ODR &= ~BIT(10); // Klipper motor_off + wait_moves completed.
    // An active native heater, guard, direct motion or feeder also blocks.
    control_heater_duty[0]=80;
    control_command(1, CT_SERVICE);assert(control_mode==CT_NATIVE);
    control_heater_duty[0]=0;
    co[6].duty=50;
    control_command(1, CT_SERVICE);assert(control_mode==CT_NATIVE);
    co[6].duty=0;
    native_z_guard_active=1;
    control_command(1, CT_SERVICE);assert(control_mode==CT_NATIVE);
    native_z_guard_active=0;
    native_z_refine_held=1;
    control_command(1, CT_SERVICE);assert(control_mode==CT_NATIVE);
    native_z_refine_held=0;
    cm.active=1;
    control_command(1, CT_SERVICE);assert(control_mode==CT_NATIVE);
    cm.active=0;
    feeder_running[0]=1;
    control_command(1, CT_SERVICE);assert(control_mode==CT_NATIVE);
    feeder_running[0]=0;
    locks_pulling[1]=1;
    control_command(1, CT_SERVICE);assert(control_mode==CT_NATIVE);
    locks_pulling[1]=0;
    native_z_refine_conflict=1;
    control_command(1, CT_SERVICE);assert(control_mode==CT_NATIVE);
    native_z_refine_conflict=0;
    // Exact previously failing transition: idle native after X/Y/XY.
    control_command(1, CT_SERVICE);
    assert(control_mode==CT_SERVICE && !inventure_is_armed());
    assert(!control_latched && !fault);
    control_command(1, CT_NATIVE);
    assert(control_mode==CT_NATIVE && inventure_is_armed());
    // STOP remains fail-closed, not an alternative handoff.
    control_command(2, 0);
    assert(control_mode==CT_OFF && control_latched);
    control_command(1, CT_SERVICE);
    assert(control_mode==CT_OFF && control_latched);
    puts("PASS guarded NATIVE->SERVICE after X/Y/XY: no active motors, heaters, Z guard, feeder or STOP bypass");
}

static void service_strict_native_broad_guards(void) {
    // Unrelated failed chamber-center ADC blocks strict SERVICE, not cold
    // NATIVE positioning. Independent absolute overtemperature stays enabled.
    control_reset(0);
    latest_adc[4]=4095;
    assert(!service_sensors_valid(control_guard_mask|31,mock_now));
    control_command(1,CT_NATIVE);
    assert(inventure_is_armed() && !fault && control_mode==CT_NATIVE);
    control_guard(mock_now);
    assert(!fault && inventure_is_armed());

    control_reset(0);
    control_command(1,CT_NATIVE);
    control_heater_duty[0]=64;
    latest_adc[0]=4095;
    expect_failure=1;
    if(!setjmp(failure)){control_guard(mock_now);assert(!"powered native heater missing ADC");}
    expect_failure=0;
    assert(fault==4 && first_safety_trip.reason==4);
    assert(strstr(last_failure,"thermal sensor guard"));

    control_reset(0);
    control_command(1,CT_NATIVE);
    inventure_adc_observe(ADC1,10,1250);
    assert(native_sensors_valid(mock_now) && (native_hot_mask&1));
    control_heater_duty[0]=0;
    latest_adc[0]=4095;
    assert(!native_sensors_valid(mock_now)); // still-hot heater kept guarded

    control_reset(0);
    control_command(1,CT_NATIVE);
    latest_adc[5]=4095;
    assert(native_sensors_valid(mock_now)); // idle PSU ADC is not a homing veto
    control_heater_duty[0]=64;
    assert(!native_sensors_valid(mock_now)); // powered requires PSU telemetry

    control_reset(0);
    control_command(1,CT_NATIVE);
    latest_adc[0]=1918;
    assert(native_sensors_valid(mock_now)); // 309C < absolute 310C limit
    latest_adc[0]=1930;
    assert(!native_sensors_valid(mock_now)); // >310C always rejected
    puts("PASS SERVICE strict and NATIVE broad: unrelated sensor fault tolerated, hot heaters monitored");
}

static void service_native_policy_telemetry(void) {
    control_reset(0);
    policy_queries=0;memset(policy_reply,0,sizeof(policy_reply));
    // Diagnostic queries must not alter the SERVICE guard mask or any
    // actuators and must not manufacture a native heater requirement.
    control_guard_mask=BIT(0)|BIT(2)|BIT(5);
    uint32_t before_mode=control_mode,before_mask=control_guard_mask;
    command_inventure_policy_query(NULL);
    assert(policy_queries==1);
    assert(policy_reply[0]==CT_SERVICE && policy_reply[1]==1);
    assert(policy_reply[2]==before_mask && policy_reply[3]==0);
    assert(policy_reply[4]==0 && policy_reply[5]==0);
    assert(control_mode==before_mode && control_guard_mask==before_mask);
    control_command(1,CT_NATIVE);
    command_inventure_policy_query(NULL);
    assert(policy_queries==2 && policy_reply[0]==CT_NATIVE
           && policy_reply[1]==2 && policy_reply[2]==before_mask);
    assert(policy_reply[3]==0 && policy_reply[4]==0 && policy_reply[5]==0);

    // Mark T0 hot, then turn its PWM off. The required NATIVE thermistor
    // is still tracked independently of the SERVICE sensor-mask settings.
    inventure_adc_observe(ADC1,10,1250);
    assert(native_sensors_valid(mock_now));
    control_heater_duty[0]=64;
    command_inventure_policy_query(NULL);
    assert(policy_reply[3]&1 && policy_reply[4]&1
           && (policy_reply[5]&(BIT(0)|BIT(5)|BIT(6)))==
              (BIT(0)|BIT(5)|BIT(6)));
    control_heater_duty[0]=0;
    command_inventure_policy_query(NULL);
    assert(policy_reply[3]&1 && !(policy_reply[4]&1) && policy_reply[5]&1);

    // A different temperature in a valid hard envelope must not become
    // an implicit requirement inherited from a SERVICE custom minimum.
    control_min[0]=250;control_max[0]=260;
    assert(!service_sensors_valid(control_guard_mask|31,mock_now));
    assert(native_sensors_valid(mock_now));
    control_guard(mock_now);assert(control_mode==CT_NATIVE && !fault);

    // Physical tray protection is unchanged by the selected policy.
    native_z_guard_active=1;GPIOC->IDR|=BIT(13);
    expect_failure=1;
    if(!setjmp(failure)){control_guard(mock_now);assert(!"PC13 trip bypass");}
    expect_failure=0;
    assert(fault==7);
    command_inventure_policy_query(NULL);
    assert(policy_reply[6]==7 && policy_reply[0]==CT_NATIVE);
    puts("PASS separated service/native policy telemetry: effective mask, hot/energized requirements, PC13 fail-closed");
}

static void first_fault_cause_snapshotted(void) {
    control_reset(0);
    control_command(1,CT_NATIVE);
    GPIOC->IDR |= BIT(13);
    native_z_guard_active=1;
    expect_failure=1;
    if(!setjmp(failure)){control_guard(mock_now);assert(!"tray interlock failure");}
    expect_failure=0;
    assert(fault==7 && first_safety_trip.reason==7);
    assert((first_safety_trip.flags & (BIT(0)|BIT(4))) == (BIT(0)|BIT(4)));
    assert(strstr(last_failure,"build tray PC13 interlock"));
    assert(!inventure_is_armed());
    puts("PASS first fault identifies PC13 tray and snapshots raw inputs before safe shutdown");
}

static void native_ownership(void) {
    control_reset(0);control_command(1,CT_NATIVE);assert(inventure_is_armed());
    assert(inventure_filter_gpio(GPIOE,BIT(10),1)==1);
    assert(inventure_filter_gpio(GPIOA,BIT(7),1)==1);
    assert(inventure_filter_gpio(GPIOB,BIT(9),1)==1);
    for(unsigned h=0;h<4;h++) {
        unsigned idx=h+2;struct gpio_pwm p=gpio_pwm_setup(co_pins[idx],co_cycles[idx],256);
        gpio_pwm_write(p,192);assert(control_heater_duty[h]==64);
    }
    motion(1,0,10,10,10,10,400,10,30000,0,0,0,1);assert(!cm.active);
    output(1,0,128,20000,0,1);assert(!co[0].duty);
    control_command(2,0);assert(!inventure_is_armed() && control_latched && control_mode==CT_OFF);
    assert(inventure_filter_gpio(GPIOE,BIT(10),1)==0 && inventure_filter_gpio(GPIOA,BIT(7),1)==0);
    control_command(1,CT_NATIVE);assert(!inventure_is_armed()); // queued native pulses cannot resume
    control_command(1,CT_SERVICE);assert(control_mode==CT_OFF);
    for(unsigned cause=0;cause<3;cause++) {
        control_reset(0);
        if(cause==2)control_set_door_policy(1);
        control_command(1,CT_NATIVE);assert(inventure_is_armed());
        if(cause==0)heartbeat_live=0;
        if(cause==1){control_heater_duty[0]=64;latest_adc[0]=4095;} // powered heater, not unused chamber ADC
        if(cause==2)GPIOB->IDR&=~BIT(10);
        expect_failure=1;
        if(!setjmp(failure)){control_guard(mock_now);assert(!"native guard failed to trip");}
        expect_failure=0;assert(!inventure_is_armed() && control_latched && fault);
    }
    control_reset(0);control_command(1,CT_NATIVE);
    struct gpio_pwm t1=gpio_pwm_setup(co_pins[3],co_cycles[3],256);
    inventure_adc_observe(ADC3,6,1918);
    gpio_pwm_write(t1,192);assert(control_heater_duty[1]==64);
    mock_now=heartbeat_deadline-MS(1);
    control_adc();inventure_adc_observe(ADC3,6,1918);
    guard_check(mock_now);
    assert(inventure_is_armed() && control_heater_duty[1]==64);
    mock_now=heartbeat_deadline;
    control_adc();inventure_adc_observe(ADC3,6,1918);
    expect_failure=1;
    if(!setjmp(failure)){guard_check(mock_now);assert(!"native T1 heartbeat watchdog did not trip");}
    expect_failure=0;
    assert(fault==1 && control_latched && control_mode==CT_OFF && !control_heater_duty[1]);
    assert((cool_mask&1) && (fan_high&1));

    // An energized native heater requires fresh fan tach edges; an OFF fan
    // cannot defeat the independent ten-second fan watchdog.
    control_reset(0);control_command(1,CT_NATIVE);control_heater_duty[0]=64;
    control_guard(mock_now);assert(control_tach_watch[0]);
    mock_now+=MS(10000);command_inventure_heartbeat(NULL);control_adc();
    expect_failure=1;
    if(!setjmp(failure)){control_guard(mock_now);assert(!"missing fan tach did not trip");}
    expect_failure=0;assert(fault==5 && control_cooldown_ms && cool_mask==13);
    assert((GPIOG->MODER>>(4*2)&3u)==1);
}
static void auxiliary_controls(void) {
    for(unsigned channel=0;channel<2;channel++)for(unsigned direction=0;direction<2;direction++) {
        control_reset(0);control_set_door_policy(1);
        uint32_t f[]={channel,direction,1500};command_inventure_feeder(f);
        assert(feeder_running[channel] && outputs[GPIO(channel?'G':'C',channel?11:5)]==1);
        assert(outputs[GPIO(channel?'B':'A',channel?7:8)]==direction);
        uint32_t l[]={channel,1};command_inventure_lock(l);assert(locks_on[channel] && locks_pulling[channel]);
        volatile uint32_t *r=channel?&TIM5->CCR3:&TIM5->CCR4;assert(*r==256);
        sched_del_timer(&lock_timer[channel]);mock_now=lock_timer[channel].waketime;
        assert(lock_hold(&lock_timer[channel])==SF_DONE && *r==60 && !locks_pulling[channel]);
        // One common STOP owns legacy feeders and locks too.
        control_command(2,0);assert(!feeder_running[channel] && !locks_on[channel] && *r==0);
        assert(!scheduled_count && outputs[GPIO(channel?'G':'C',channel?11:5)]==0);
        assert(control_mode==CT_SERVICE && !control_latched);
        f[2]=10;command_inventure_feeder(f);assert(feeder_running[channel]);
        heartbeat_live=0;control_guard(mock_now);assert(!feeder_running[channel] && control_mode==CT_OFF);
    }
}
static void global_door_policy(void) {
    uint32_t lid_lock[]={0,1},front_lock[]={1,1};
    control_reset(0);
    assert(!door_policy_enabled() && door_closed_mask()==3);
    command_inventure_lock(lid_lock);command_inventure_lock(front_lock);
    assert(!scheduled_count && !locks_on[0] && !locks_on[1]);
    assert(!TIM5->CCR3 && !TIM5->CCR4);

    control_set_door_policy(1);
    command_inventure_lock(lid_lock);command_inventure_lock(front_lock);
    assert(locks_on[0] && locks_on[1] && TIM5->CCR3==256 && TIM5->CCR4==256);
    sched_del_timer(&lock_timer[0]);
    assert(lock_hold(&lock_timer[0])==SF_DONE && TIM5->CCR4==60);
    assert(scheduled_count==1 && locks_pulling[1]);
    control_set_door_policy(0); // an idle, energized lock cannot veto release
    assert(!door_policy_enabled() && !scheduled_count);
    assert(!locks_on[0] && !locks_on[1] && !locks_pulling[0] && !locks_pulling[1]);
    assert(!TIM5->CCR3 && !TIM5->CCR4);
    assert(lock_hold(&lock_timer[1])==SF_DONE && !TIM5->CCR3);

    GPIOB->IDR&=~BIT(10);GPIOE->IDR&=~BIT(15);
    assert(door_closed_mask()==0);
    control_command(1,CT_NATIVE);
    assert(inventure_is_armed());
    control_set_door_policy(1); // never change an armed owner's policy
    assert(!door_policy_enabled());
    assert(!inventure_filter_gpio(GPIOA,BIT(2),1));
    assert(!inventure_filter_gpio(GPIOA,BIT(3),1));
    assert(!inventure_filter_pwm((void*)&TIM5->CCR3,256,256));
    assert(!inventure_filter_pwm((void*)&TIM5->CCR4,256,256));
    command_inventure_lock(lid_lock);command_inventure_lock(front_lock);
    assert(!scheduled_count && !TIM5->CCR3 && !TIM5->CCR4);
    control_guard(mock_now);
    assert(inventure_is_armed() && !fault);
    control_command(2,0);
    puts("PASS global door policy: default OFF, idle coil release, raw/pwm/native lock gating, live raw door inputs");
}
int main(void) {
    scheduler_native_service_stress();
    dda_and_replay();input_stops();motion_guards();pwm_outputs();temperature_stops();
    nozzle_310_cutoff_and_link_loss();
    service_to_native_heat();native_ownership();native_to_service_quiescent_handoff();service_strict_native_broad_guards();service_native_policy_telemetry();first_fault_cause_snapshotted();auxiliary_controls();global_door_policy();
    puts("PASS CONTROL-R5: 4-driver DDA, 16 directions, all 16 stop inputs/both polarities, wraps, replay, durations, hardware active-LOW PWM, all 6 fans, native ARM/STOP latch, thermal freshness, both feeders/locks and common STOP");
    return 0;
}
