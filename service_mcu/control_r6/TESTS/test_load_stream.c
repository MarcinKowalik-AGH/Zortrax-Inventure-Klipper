#include "control_test_support.inc"

static void hot_adc(void) {
    control_adc();inventure_adc_observe(ADC1,10,1427);inventure_adc_observe(ADC3,6,1427);
}
static void load_reset(uint32_t now) {
    memset(&sl,0,sizeof(sl));control_reset(now);hot_adc();
    output(2,6,0,0,0,0);
}
static void stream(unsigned action,unsigned steps,unsigned rate,unsigned maximum,
                   unsigned lease,unsigned token,unsigned sequence) {
    uint32_t a[]={action,steps,rate,maximum,lease,205,240,token,sequence,0,0,0,1};
    command_inventure_load_stream(a);
}
static void renewal(unsigned sequence) {
    stream(2,0,0,0,1500,101,sequence);
}
static void next_step(void) {
    motion_tick();hot_adc();control_guard(mock_now);cooling_update();
}
static void continuous(unsigned steps,uint32_t start_time) {
    load_reset(start_time);output(1,6,64,1500,250,100);
    stream(1,steps,1536,steps*1000u/1536u+2000,1500,101,0);
    assert(sl.active && cm.active && feeder_running[0] && cm.run==1 && co[6].duty==64);
    uint32_t seq=0,last_renew=mock_now,last_rise=0,max_gap=0,pulses=0;
    while(cm.active) {
        next_step();
        if(cm.high) {
            if(pulses){uint32_t gap=mock_now-last_rise;if(gap>max_gap)max_gap=gap;}
            last_rise=mock_now;pulses++;
        }
        if((uint32_t)(mock_now-last_renew)>=MS(250) && sl.active) {
            uint32_t done=cm.done,run=cm.run;
            renewal(++seq);output(1,6,64+(seq&1),1500,250,1000+seq);
            assert(sl.active && cm.active && cm.run==run && cm.done==done);
            assert(feeder_running[0] && outputs[GPIO('C',5)]==1 && outputs[GPIO('A',8)]==1);
            assert(co[6].duty>=64 && cm.direction==0);
            last_renew=mock_now;
        }
    }
    control_guard(mock_now);
    assert(!sl.active && sl.reason==1 && !feeder_running[0]);
    assert(cm.run==1 && sl.sent==steps && cm.sent[3]==steps && pulses==steps);
    assert(cm.sent[0]==0 && cm.sent[1]==0 && cm.sent[2]==0);
    assert(max_gap<MS(3));assert(co[6].duty>=64);
    uint32_t elapsed=sl.elapsed;
    stream(1,steps,1536,steps*1000u/1536u+2000,1500,101,0);
    assert(!sl.active && cm.run==1 && sl.sent==steps && sl.elapsed==elapsed);
    printf("PASS continuous E=%u, one run, elapsed_ms=%u, max_step_gap_us=%u, renewals=%u\n",
           steps,elapsed,max_gap/(CONFIG_CLOCK_FREQ/1000000u),seq);
}
static void guards_and_replay(void) {
    for(unsigned why=0;why<12;why++) {
        load_reset(0);output(1,6,64,1500,250,100);
        stream(1,171800,1536,115000,1500,101,0);next_step();assert(sl.active);
        if(why==0)control_command(2,0);
        if(why==1){heartbeat_live=0;control_guard(mock_now);}
        if(why==2){GPIOD->IDR|=BIT(11);control_guard(mock_now);}
        if(why==3){inventure_adc_observe(ADC1,10,1500);control_guard(mock_now);}
        if(why==4){inventure_adc_observe(ADC1,10,1200);control_guard(mock_now);}
        if(why==5){co[6].remaining=1;mock_now+=MS(2);control_guard(mock_now);control_guard(mock_now);}
        if(why==6){mock_now+=MS(1001);command_inventure_heartbeat(NULL);control_guard(mock_now);}
        if(why==7){uint32_t args[]={2};command_inventure_motion(args);control_guard(mock_now);}
        if(why==8){mock_now=sl.deadline;command_inventure_heartbeat(NULL);hot_adc();control_guard(mock_now);}
        if(why==9){gpio_out_write(feeder_run[0],0);feeder_running[0]=0;control_guard(mock_now);}
        if(why==10){scheduled_count=0;force_safe();assert(sl.reason==7);}
        if(why==11){GPIOE->IDR&=~BIT(15);next_step();}
        assert(!sl.active && !cm.active && !feeder_running[0] && !co[6].duty);
        renewal(500);assert(!sl.active && !cm.active && !feeder_running[0]);
    }
    load_reset(0);stream(1,10000,1536,10000,1500,101,0);assert(sl.active);
    uint32_t deadline=sl.deadline,run=cm.run;
    mock_now+=MS(100);command_inventure_heartbeat(NULL);hot_adc();
    stream(1,10000,1536,10000,1500,101,0);assert(sl.deadline==deadline && cm.run==run);
    renewal(1);deadline=sl.deadline;
    mock_now+=MS(100);command_inventure_heartbeat(NULL);hot_adc();renewal(1);
    assert(sl.deadline==deadline);renewal(0);assert(sl.deadline==deadline);
    stream(2,0,0,0,1500,102,2);assert(sl.deadline==deadline);
    output(1,7,64,1500,250,200);assert(!co[7].duty && sl.active);
    motion(1,0,0,0,0,10,960,10,2000,0,0,0,500);assert(cm.run==run && cm.planned[3]==10000);
    control_command(2,0);
}
static void invalid_starts(void) {
    for(unsigned what=0;what<6;what++) {
        load_reset(0);
        if(what==0)inventure_adc_observe(ADC1,10,155);
        if(what==1)GPIOD->IDR|=BIT(11);
        if(what==2)control_mode=CT_NATIVE;
        if(what==3)co[7].duty=1;
        unsigned steps=what==4?400001:10000,rate=what==5?4000:1536;
        stream(1,steps,rate,10000,1500,101,0);
        assert(!sl.active && !cm.active && !feeder_running[0]);
    }
}

static void configurable_channels(void) {
    for(unsigned tool=0;tool<2;tool++)for(unsigned channel=0;channel<2;channel++)
    for(unsigned edir=0;edir<2;edir++)for(unsigned fdir=0;fdir<2;fdir++) {
        load_reset(0);output(1,6+tool,64,1500,250,100);
        uint32_t a[]={1,1536,1536,5000,1500,205,240,101,0,tool,channel,edir,fdir};
        command_inventure_load_stream(a);
        assert(sl.active && sl.tool==tool && sl.channel==channel);
        assert(cm.direction==(edir?8:0));
        assert(outputs[GPIO(channel?'B':'A',channel?7:8)]==fdir);
        if(tool)TIM9->CNT=123;
        unsigned seq=0;uint32_t last=mock_now;
        while(cm.active) {
            next_step();
            if((uint32_t)(mock_now-last)>=MS(250) && sl.active) {
                a[0]=2;a[8]=++seq;command_inventure_load_stream(a);
                output(1,6+tool,64,1500,250,1000+seq);
                assert(feeder_running[channel] && co[6+tool].duty==64);
                if(tool)assert(TIM9->CCR2==1 && TIM9->CNT==123);
                last=mock_now;
            }
        }
        control_guard(mock_now);
        assert(!sl.active && sl.reason==1 && sl.sent==1536 && !feeder_running[channel]);
    }
    puts("PASS configurable stream: both nozzle outputs, both cassette channels, both directions; shared TIM9 clock preserved during T1 PID updates");
}

int main(void) {
    continuous(171800,0);continuous(171800,0xffff0000u);
    guards_and_replay();invalid_starts();configurable_channels();
    puts("PASS R6 stream: continuous paired load; PID while E active; no motion restart on renew; all independent guards, replay and STOP");
    return 0;
}
