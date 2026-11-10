// Execute CONTROL-R5 with the actual board/PWM source and register mocks.
#define main motor_regression_main
#include "test_t0_heat_board.c"
#undef main

static void control_command(unsigned action,unsigned mode) {
    uint32_t args[]={action,mode};command_inventure_control(args);
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
    control_guard_mask=127;memset(control_tach_watch,0,sizeof(control_tach_watch));
    const int16_t caps[]={310,310,150,150,150,100,100,190};
    for(unsigned i=0;i<8;i++){control_min[i]=-20;control_max[i]=caps[i];}
    heat_reset(now);control_adc();
    GPIOB->IDR=BIT(10);GPIOE->IDR=BIT(15);GPIOC->IDR=BIT(14);
    door_policy_flags=0;command_inventure_aux_config(NULL);
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
        control_reset(0);door_policy_flags=BIT(3);
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
        control_reset(0);motion(1,0,100,100,100,100,400,10,30000,0,0,0,1);motion_tick();
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
        control_reset(0);control_command(1,CT_NATIVE);assert(inventure_is_armed());
        if(cause==0)heartbeat_live=0;
        if(cause==1)latest_adc[2]=4095;
        if(cause==2)GPIOB->IDR&=~BIT(10);
        expect_failure=1;
        if(!setjmp(failure)){control_guard(mock_now);assert(!"native guard failed to trip");}
        expect_failure=0;assert(!inventure_is_armed() && control_latched && fault);
    }
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
        control_reset(0);uint32_t f[]={channel,direction,1500};command_inventure_feeder(f);
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
int main(void) {
    dda_and_replay();input_stops();motion_guards();pwm_outputs();temperature_stops();
    native_ownership();auxiliary_controls();
    puts("PASS CONTROL-R5: 4-driver DDA, 16 directions, all 16 stop inputs/both polarities, wraps, replay, durations, hardware active-LOW PWM, all 6 fans, native ARM/STOP latch, thermal freshness, both feeders/locks and common STOP");
    return 0;
}
