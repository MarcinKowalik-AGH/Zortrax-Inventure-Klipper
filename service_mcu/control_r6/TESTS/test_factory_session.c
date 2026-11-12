#include "control_test_support.inc"

static void factory_adc(unsigned hot) {
    control_adc();
    if(hot){inventure_adc_observe(ADC1,10,1427);inventure_adc_observe(ADC3,6,1427);}
}
static void factory_reset(uint32_t now,unsigned hot) {
    memset(&sl,0,sizeof(sl));control_reset(now);factory_adc(hot);
    memset(factory_reply,0,sizeof(factory_reply));
}
static void factory(unsigned action,unsigned tool,unsigned mode,unsigned monitor,
        unsigned duration,unsigned lease,unsigned token,unsigned sequence) {
    uint32_t a[]={action,tool,mode,monitor,duration,20160,lease,205,240,token,sequence};
    command_inventure_factory_session(a);
}
static void factory_advance(unsigned ms,unsigned hot) {
    mock_now+=MS(ms);command_inventure_heartbeat(NULL);factory_adc(hot);
    control_guard(mock_now);transport_sample(mock_now);control_guard(mock_now);
}
static void factory_input(unsigned tool,unsigned a,unsigned b) {
    unsigned pin=tool?15:10;
    if(a)GPIOD->IDR|=BIT(pin);else GPIOD->IDR&=~BIT(pin);
    GPIO_TypeDef *g=tool?GPIOG:GPIOF;pin=tool?10:1;
    if(b)g->IDR|=BIT(pin);else g->IDR&=~BIT(pin);
}
static void factory_motion_tick(void) {
    assert(cm.active && cm.timer_added);sched_del_timer(&cm_timer);
    mock_now=cm_timer.waketime;command_inventure_heartbeat(NULL);factory_adc(1);
    unsigned r=cm_event(&cm_timer);
    if(r==SF_RESCHEDULE)sched_add_timer(&cm_timer);
    else assert(!cm.active && !cm.timer_added && !cm.high);
    control_guard(mock_now);transport_sample(mock_now);control_guard(mock_now);
}
static void cold_home_and_hot_start(void) {
    for(unsigned tool=0;tool<2;tool++)for(unsigned wrap=0;wrap<2;wrap++) {
        factory_reset(wrap?0xffff0000u:0,0);
        gpio_pwm_write(feeder_clock,1); // previous auxiliary ownership left the shared clock enabled
        assert(TIM9->CCR2==1);
        factory(1,tool,IVT_STOP,1,1000,1500,501,0);
        assert(fs.active && !factory_reply[3] && !inventure_is_armed() && !TIM9->CCR2);
        motion(1,0,30,30,0,0,1000,10,1000,1,1,0,601);assert(cm.active);
        GPIOB->IDR|=BIT(11);motion_tick();assert(!cm.active && cm.reason==CR_INPUT);
        assert(GPIOE->BSRR==BIT(10)); // input establishes reference without releasing enable
        motion(1,0,20,20,0,0,1000,10,1000,0,0,0,602);
        while(cm.active)motion_tick();assert(GPIOE->BSRR==BIT(10));
        uint32_t run=cm.run;
        motion(1,tool?8:0,0,0,0,96,1000,10,1000,0,0,0,603);
        assert(!cm.active && cm.run==run); // cold E refused
        factory(2,tool,IVT_FORWARD,1,1000,1500,501,1);
        assert(factory_reply[3]==3 && fs.sequence==0 && transport[tool].mode==IVT_STOP);
        factory_adc(1);factory(2,tool,IVT_FORWARD,1,1000,1500,501,1);
        assert(fs.active && factory_reply[3]==0 && transport[tool].running);
        assert(TIM9->CCR2==1);
        GPIO_TypeDef *run_port=tool?GPIOG:GPIOC;
        uint32_t run_bit=BIT(tool?11:5);
        assert(run_port->BSRR==run_bit);
        assert(inventure_filter_pwm((void*)&TIM9->CCR2,200,256)==0);
        assert(outputs[tool?GPIO('B',7):GPIO('A',8)]==1);
        factory(2,tool,IVT_STOP,1,1000,1500,501,2);
        assert(!TIM9->CCR2 && run_port->BSRR==(run_bit<<16));
        factory(2,tool,IVT_REVERSE,1,1000,1500,501,3);
        assert(TIM9->CCR2==1 && run_port->BSRR==run_bit);
        assert(!outputs[tool?GPIO('B',7):GPIO('A',8)]);
        factory(3,tool,IVT_STOP,1,1000,1500,501,0);
        assert(!fs.active && fs.reason==2 && !cm.active && GPIOE->BSRR==(BIT(10)<<16));
        assert(!TIM9->CCR2 && !outputs[tool?GPIO('G',11):GPIO('C',5)]);
    }
    puts("PASS factory cold owner: both tools, wrap, homing holds enable, cold E/feeder blocked, hot transition accepted");
}
static void auto_and_replay(void) {
    for(unsigned tool=0;tool<2;tool++) {
        factory_reset(0,1);factory_input(tool,1,1);
        output(1,6+tool,64,5000,250,101);
        factory(1,tool,IVT_AUTO,0,100,1500,501,0);factory_advance(10,1);
        uint32_t starts=transport[tool].starts,deadline=transport[tool].deadline;
        assert(starts==1 && transport[tool].running && TIM9->CCR2==1);
        for(unsigned seq=1;seq<5;seq++) {
            factory(2,tool,IVT_AUTO,0,100,1500,501,seq);
            assert(transport[tool].starts==starts && transport[tool].deadline==deadline);
        }
        uint32_t lease=fs.deadline;
        factory(2,tool,IVT_AUTO,0,100,1500,501,4);assert(fs.deadline==lease);
        factory(2,tool,IVT_FORWARD,0,100,1500,501,4);assert(factory_reply[3]==1 && fs.deadline==lease);
        factory(1,tool,IVT_FORWARD,0,100,1500,501,0);assert(factory_reply[3]==1 && fs.deadline==lease);
        factory(1,tool,IVT_AUTO,0,100,1500,501,0);assert(factory_reply[3]==0 && fs.deadline==lease);
        factory_advance(100,1);assert(!transport[tool].running && !TIM9->CCR2);
        factory_advance(10,1);assert(transport[tool].starts==starts+1 && transport[tool].running && TIM9->CCR2==1);
        factory_input(tool,0,0);factory_advance(100,1);factory_advance(10,1);
        assert(!transport[tool].running && transport[tool].starts==starts+1);
        factory(3,tool,0,0,100,1500,501,0);
        assert(!fs.active && co[6+tool].duty==64 && !transport[tool].running);
        factory(1,tool,IVT_AUTO,0,100,1500,501,0);assert(!fs.active && !transport[tool].running);
    }
    puts("PASS factory actual AUTO starts: A/B gating, renewals never restart, exact replay and stale START cannot revive, normal STOP retains heat lease");
}
static void mixed_motion_and_PID(void) {
    for(unsigned tool=0;tool<2;tool++) {
        factory_reset(0xffff0000u,1);factory_input(tool,1,1);
        output(1,6+tool,64,1500,250,101);
        factory(1,tool,IVT_FORWARD,0,1000,1500,501,0);
        motion(1,tool?8:0,640,640,0,96,1280,10,2000,0,0,0,601);
        unsigned seq=0;uint32_t last=mock_now;
        while(cm.active) {
            factory_motion_tick();
            if((uint32_t)(mock_now-last)>=MS(100)) {
                uint32_t run=cm.run,sent=cm.sent[3],starts=transport[tool].starts;
                factory(2,tool,IVT_FORWARD,0,1000,1500,501,++seq);
                output(1,6+tool,64+(seq&1),1500,250,1000+seq);
                assert(fs.active && cm.run==run && cm.sent[3]==sent && transport[tool].starts==starts);
                assert(co[6+tool].duty>=64);last=mock_now;
            }
        }
        assert(fs.active && cm.sent[0]==640 && cm.sent[1]==640 && cm.sent[3]==96);
        assert(transport[tool].forward==96 && !transport[tool].reverse && transport[tool].budget==20160-96);
        assert(GPIOE->BSRR==BIT(10));
        output(1,7-tool,64,1500,250,777);assert(!co[7-tool].duty);
        motion(1,tool?0:8,0,0,0,32,1280,10,1000,0,0,0,602);
        while(cm.active)factory_motion_tick();
        assert(transport[tool].reverse==32 && transport[tool].budget==20160-96+32);
        uint32_t a[]={1,1536,1536,5000,1500,205,240,900,0,tool,tool,tool,1};
        command_inventure_load_stream(a);assert(!sl.active && fs.active && cm.token==602);
        control_command(2,0);assert(!fs.active && !transport[tool].running && !co[6+tool].duty);
    }
    puts("PASS factory DDA+PID: paired A/B+E, both E polarities and budget accounting, no restart on renewal, held enable, foreign output and old smooth owner excluded");
}
static void guards_and_STOP(void) {
    for(unsigned why=0;why<10;why++) {
        factory_reset(0,1);factory_input(0,1,1);output(1,6,64,5000,250,101);
        factory(1,0,IVT_FORWARD,1,1000,1500,501,0);
        assert(TIM9->CCR2==1);
        if(why==0){mock_now=fs.deadline;command_inventure_heartbeat(NULL);factory_adc(1);control_guard(mock_now);assert(fs.reason==3);}
        if(why==1){heartbeat_live=0;control_guard(mock_now);assert(fs.reason==6);}
        if(why==2){GPIOD->IDR|=BIT(11);control_guard(mock_now);assert(fs.reason==6);}
        if(why==3){inventure_adc_observe(ADC1,10,1500);control_guard(mock_now);assert(fs.reason==4);}
        if(why==4){inventure_adc_observe(ADC1,10,1200);control_guard(mock_now);assert(fs.reason==4);}
        if(why==5){mock_now+=MS(1001);command_inventure_heartbeat(NULL);control_guard(mock_now);assert(fs.reason==4);}
        if(why==6){transport[0].fault=3;control_guard(mock_now);assert(fs.reason==5);}
        if(why==7){GPIOE->IDR&=~BIT(15);control_guard(mock_now);assert(fs.reason==5);}
        if(why==8){uint32_t f[]={0,0,0};command_inventure_feeder(f);assert(fs.reason==2);}
        if(why==9){
            scheduled_count=0;force_safe();assert(fs.reason==7);
            assert(((GPIOE->MODER>>12)&3)==1); // PE6 returned to GPIO safe mode
        }
        assert(!fs.active && !cm.active && !transport[0].running && !co[6].duty && !TIM9->CCR2);
        factory(2,0,IVT_FORWARD,1,1000,1500,501,100);assert(!fs.active && factory_reply[3]==4);
    }
    factory_reset(0,1);output(1,6,64,1,250,101);factory(1,0,0,0,1000,1500,501,0);
    factory_advance(2,1);assert(!fs.active && fs.reason==4 && !co[6].duty);
    puts("PASS factory independent guards: lease, heartbeat, cable, hot/cold/stale ADC, heater expiry, transport fault, door, selected feeder STOP, shutdown; late renewal never revives");
}
static void pulse_age_and_status(void) {
    factory_reset(0xffff0000u,1);control_guard(mock_now);
    pulses[4]=1;edge_time[4]=mock_now;control_guard(mock_now);
    for(unsigned n=0;n<80;n++)factory_advance(1000,1);
    factory(1,0,0,0,1000,1500,501,0);assert(factory_reply[10]==80000);
    pulses[4]++;edge_time[4]=mock_now;factory_advance(10,1);
    factory_input(0,1,0);factory(0,0,0,0,1000,1500,501,0);
    assert(factory_reply[7]==1 && factory_reply[8]==0 && factory_reply[9]==2 && factory_reply[10]==10);
    puts("PASS factory pulse age: real accepted edges, raw A/B status, accumulated idle age across multiple CLOCK wraps during warm-up");
}

static void fast_dispatch(uint32_t late,uint8_t hardware_finishes) {
    assert(cm.active && cm.fast_mask && cm_timer.func==cm_fast_event);
    sched_del_timer(&cm_timer);
    mock_now=cm_timer.waketime+late;
    if(hardware_finishes && mock_now-cm.fast_last_launch>=timer_from_us(2)) {
        TIM14->CR1&=~CM_PULSE_CEN;
        TIM13->CR1&=~CM_PULSE_CEN;
        TIM11->CR1&=~CM_PULSE_CEN;
    }
    unsigned res=cm_timer.func(&cm_timer);
    if(res==SF_RESCHEDULE)sched_add_timer(&cm_timer);
    else assert(!cm.active && !cm.fast_mask && !cm.timer_added);
}

static void fast_start(unsigned rate,unsigned b,unsigned e) {
    factory_reset(0xffff0000u,1);
    factory(1,0,IVT_STOP,0,1000,1500,501,0);
    motion(1,0,24000,b,0,e,rate,2,2000,0,0,0,601);
    assert(fs.active && cm.active && cm.fast_mask);
}

static void fast_factory_cadence(void) {
    const unsigned rates[]={32000,120680,125000};
    for(unsigned n=0;n<ARRAY_SIZE(rates);n++) {
        unsigned rate=rates[n],b=n==0?24000:17300,e=n==0?0:2073;
        fast_start(rate,b,e);
        assert(TIM14->ARR==120 && TIM13->ARR==120 && TIM14->CCR1==1 && TIM13->CCR1==1);
        assert(TIM14->CCMR1==CM_PULSE_PWM2 && TIM13->CCMR1==CM_PULSE_PWM2);
        assert(!TIM14->DIER && !TIM13->DIER && !TIM11->DIER);
        assert(((GPIOA->AFR[0]>>24)&255)==0x99 && ((GPIOA->MODER>>12)&15)==0xA);
        if(e) {
            assert(TIM11->ARR==240 && TIM11->CCR1==1 && TIM11->CCMR1==CM_PULSE_PWM2);
            assert(((GPIOB->AFR[1]>>4)&15)==3 && ((GPIOB->MODER>>18)&3)==2);
        }
        assert(cm.fast_accel_half==(n==0?480000u:550722u));
        uint32_t period=(CONFIG_CLOCK_FREQ+rate-1u)/rate, cruise=0;
        while(cm.active) {
            uint32_t before=cm.done,wake=cm_timer.waketime,last_launch=cm.fast_last_launch;
            uint8_t plateau=before+1>=cm.fast_cruise_ramp && cm.master-(before+1)>=cm.fast_cruise_ramp;
            uint32_t late=plateau && cruise==0?timer_from_us(100):plateau && cruise==1?60:0;
            fast_dispatch(late,1);
            if(cm.active) {
                assert(cm.done==before+1 && TIM14->CR1==(CM_PULSE_OPM|CM_PULSE_CEN));
                if(before)assert(cm.fast_last_launch-last_launch>=timer_from_us(CM_FACTORY_MIN_INTERVAL_US));
                if(plateau) {
                    uint32_t base=late>timer_from_us(1)?wake+late:wake;
                    assert(cm_timer.waketime-base==period);
                    cruise++;
                }
            }
        }
        assert(cruise>1000 && cm.fast_peak_late==timer_from_us(100));
        assert(cm.sent[0]==24000 && cm.sent[1]==b && cm.sent[3]==e && cm.reason==CR_COMPLETE);
        assert(transport[0].forward==e && !transport[0].reverse && transport[0].budget==20160-e);
        assert(!TIM14->CR1 && !TIM13->CR1 && !TIM11->CR1);
        assert(!TIM14->CCER && !TIM13->CCER && !TIM11->CCER);
        assert(((GPIOA->MODER>>12)&15)==5 && ((GPIOB->MODER>>18)&3)==1);
        assert(GPIOE->BSRR==BIT(10));
        factory(3,0,IVT_STOP,0,1000,1500,501,0);
    }
    puts("PASS fast factory cadence: 32/120.68/125 kHz plateaus, bounded jitter and delay, wrap, exact CoreXY/E counts, one-shot drain and GPIO restore");
}

static void fast_factory_guards(void) {
    factory_reset(0,1);
    motion(1,0,100,100,0,0,32000,2,1000,0,0,0,601);
    assert(!cm.active && !TIM14->CR1);
    factory(1,1,IVT_STOP,0,1000,1500,501,0);
    motion(1,0,100,100,0,0,32000,2,1000,0,0,0,601);
    assert(fs.active && !cm.active && !TIM14->CR1);
    factory_reset(0,1);
    factory(1,0,IVT_STOP,0,1000,1500,501,0);
    motion(1,0,100,100,0,0,32000,2,1000,1,1,0,601);
    motion(1,0,100,100,1,0,32000,2,1000,0,0,0,601);
    motion(1,0,100,100,0,101,32000,2,1000,0,0,0,601);
    motion(1,0,100,100,0,0,32000,2,1000,0,0,1,601);
    motion(1,0,100,100,0,0,32000,10,1000,0,0,0,601);
    motion(1,0,100,100,0,0,125001,2,1000,0,0,0,601);
    assert(fs.active && !cm.active && !TIM14->CR1);
    enable_pclock((uint32_t)(uintptr_t)TIM14);
    TIM14->CR1=CM_PULSE_CEN;
    motion(1,0,100,100,0,0,32000,2,1000,0,0,0,601);
    assert(!cm.active && TIM14->CR1==CM_PULSE_CEN);

    for(unsigned fault_kind=0;fault_kind<8;fault_kind++) {
        fast_start(120680,24000,10);
        fast_dispatch(0,1);
        assert(cm.done==1);
        if(fault_kind==0) {
            fast_dispatch(MS(1)+1,1);
            assert(cm.reason==CR_TIMING);
        } else if(fault_kind==1) {
            fast_dispatch(0,0);
            assert(cm.reason==CR_TIMING);
        } else if(fault_kind==2) {
            mock_read_cycles=timer_from_us(3);
            fast_dispatch(0,1);
            mock_read_cycles=0;
            assert(cm.active && cm.reason==CR_TIMING && cm.done==2);
            fast_dispatch(timer_from_us(10),1);
            assert(cm.reason==CR_TIMING && cm.fast_peak_ticks==timer_from_us(6));
        } else if(fault_kind==3) {
            GPIOD->IDR|=BIT(11);
            fast_dispatch(0,1);
            assert(cm.reason==CR_HEARTBEAT);
        } else if(fault_kind==4) {
            fs.deadline=cm_timer.waketime;
            fast_dispatch(0,1);
            assert(cm.reason==CR_HEARTBEAT);
        } else if(fault_kind==5) {
            GPIOE->IDR&=~BIT(15);
            fast_dispatch(0,1);
            assert(cm.reason==CR_DOOR);
        } else if(fault_kind==6) {
            control_command(2,0);
            assert(cm.reason==CR_STOP);
        } else {
            scheduled_count=0;
            force_safe();
            assert(cm.reason==CR_SHUTDOWN);
        }
        assert(!cm.active && cm.done==(fault_kind==2?2u:1u));
        assert(!TIM14->CR1 && !TIM13->CR1 && !TIM11->CR1);
        if(fault_kind!=7)assert(GPIOE->BSRR==(BIT(10)<<16));
        else assert(((GPIOE->MODER>>20)&3)==1); // raw_safe wrote other GPIOE latches after PE10
        assert(((GPIOA->MODER>>12)&15)==5 && ((GPIOB->MODER>>18)&3)==1);
    }
    puts("PASS fast factory scope and guards: T0 only, timer ownership, rate/move validation, overlap/late/CPU abort, cable, lease, door, STOP and shutdown");
}

int main(void) {
    cold_home_and_hot_start();auto_and_replay();mixed_motion_and_PID();guards_and_STOP();pulse_age_and_status();
    fast_factory_cadence();fast_factory_guards();
    puts("PASS R7 FACTORY session register suite");return 0;
}
