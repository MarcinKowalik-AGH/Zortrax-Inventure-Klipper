// SPDX-License-Identifier: GPL-3.0-or-later
// Persistent generic capabilities: tests and geometry are host parameters.
#line 12000 "inventure_control.inc"
DECL_CONSTANT("INVENTURE_CONTROL_ABI",1);
DECL_CONSTANT("INVENTURE_MOTION_ABI",1);
DECL_CONSTANT("INVENTURE_OUTPUT_ABI",1);
DECL_CONSTANT("INVENTURE_LOAD_STREAM_ABI",1);
DECL_CONSTANT("INVENTURE_CONTROL_MAX_HZ",20000);
DECL_CONSTANT("INVENTURE_CONTROL_MAX_STEPS",2000000);
DECL_CONSTANT("INVENTURE_CONTROL_MAX_MS",600000);
DECL_CONSTANT("INVENTURE_CONTROL_SENSOR_MASK",127);
enum { CT_OFF,CT_NATIVE,CT_SERVICE };
enum { CM_IDLE,CM_RUNNING,CM_HOLD,CM_DONE,CM_STOPPED,CM_FAILED };
enum { CR_NONE,CR_COMPLETE,CR_INPUT,CR_STOP,CR_HEARTBEAT,CR_DOOR,CR_TIME,CR_SHUTDOWN };
static struct {
    uint32_t run,token,planned[4],sent[4],error[4],master,done,rate,pulse;
    uint32_t elapsed,fraction,last_tick,max_ms,hold_ms,hold_start,period;
    uint16_t stop_mask,stop_value,hit;
    uint8_t phase,reason,active,high,direction,timer_added;
} cm;
static struct timer cm_timer;
static struct {
    struct gpio_pwm pwm;
    uint32_t token,remaining,last_tick,fraction,requested_ms;
    uint16_t duty,requested_duty;
    int16_t limit;
    uint8_t initialized,reason;
} co[10]; // six fans, then T0/T1/PC9/PC8
// One continuous service load, with selectable nozzle, cassette and directions.
// Renewal changes only the watchdog
// deadlines: neither the E trajectory nor RUN/DIR pins are restarted.
static struct {
    uint32_t token,steps,rate,max_ms,lease_ms,deadline,sequence,sent,elapsed;
    int16_t min_c,max_c;
    uint8_t active,reason,tool,channel,e_direction,feeder_direction;
} sl;
static void sl_guard(uint32_t now);
static uint8_t control_guard_mask=127,control_fan_phase,control_heat_seen;
static uint32_t control_cooldown_ms,control_cooldown_tick;
static uint8_t control_tach_watch[4];
static int16_t control_min[8]={-20,-20,-20,-20,-20,-20,-20,-20};
static int16_t control_max[8]={310,310,150,150,150,100,100,190};
static const uint8_t co_pins[6]={GPIO('A',1),GPIO('D',13),GPIO('A',0),GPIO('E',5),GPIO('C',9),GPIO('C',8)};
static const uint32_t co_cycles[6]={5632,2096640,5632,12800,16776960,16776960};
static uint16_t control_inputs(void) {
    const uint8_t pins[16]={GPIO('B',11),GPIO('C',15),GPIO('E',12),GPIO('C',14),
      GPIO('C',13),GPIO('G',2),GPIO('B',10),GPIO('E',15),GPIO('D',10),GPIO('F',1),
      GPIO('D',15),GPIO('G',10),GPIO('D',7),GPIO('D',6),GPIO('D',5),GPIO('A',15)};
    uint16_t raw=0;
    for(unsigned i=0;i<16;i++)if(gpio_pin_to_regs(pins[i])->IDR&BIT(pins[i]%16))raw|=BIT(i);
    return raw;
}
static uint8_t control_ready(uint32_t now) {
    return initialized && sample_started && !fault && !control_latched && heartbeat_live
        && !iv9_due(now,heartbeat_deadline) && !(GPIOD->IDR&BIT(11));
}
static int16_t control_temp(unsigned i,uint32_t now,uint8_t *valid) {
    uint8_t seen=cool_native_claimed?cool_native_seen:cool_local_seen;
    uint16_t raw=cool_native_claimed?latest_adc[i]:cool_local_raw[i];
    uint32_t stamp=cool_native_claimed?adc_time[i]:cool_local_time[i];
    *valid=!!(seen&BIT(i)) && (uint32_t)(now-stamp)<MS(1000)
        && raw>(i<2?8:0) && raw<4095;
    return i<2?(int32_t)raw*660/4095:lut_temperature(raw);
}
static uint8_t control_sensors_valid(uint8_t mask,uint32_t now) {
    for(unsigned i=0;i<8;i++)if(mask&BIT(i)) {
        uint8_t valid;int16_t t=control_temp(i,now,&valid);
        if(!valid || t<control_min[i] || t>=control_max[i])return 0;
    }
    return 1;
}
static uint8_t control_aux_allowed(void) {
    return control_mode==CT_SERVICE && !sl.active && !fault && !control_latched && heartbeat_live;
}
static uint8_t control_busy(void) {
    if(control_mode==CT_NATIVE || cm.active)return 1;
    for(unsigned i=0;i<2;i++)if(feeder_running[i] || locks_on[i])return 1;
    for(unsigned i=0;i<10;i++)if(co[i].duty)return 1;
    return 0;
}
static void cm_clear_steps(void) {
    motor_test_write(GPIOA,BIT(6)|BIT(7),0);
    motor_test_write(GPIOB,BIT(8)|BIT(9),0);cm.high=0;
}
static void cm_end(uint8_t reason) {
    motor_test_write(GPIOE,BIT(10),0);cm_clear_steps();
    cm.active=0;cm.reason=reason;
    cm.phase=reason==CR_COMPLETE || reason==CR_INPUT?CM_DONE:reason==CR_STOP?CM_STOPPED:CM_FAILED;
}
static void control_motion_cancel(void) {
    if(!cm.active)return;
    if(cm.timer_added)sched_del_timer(&cm_timer);
    cm.timer_added=0;cm_end(CR_STOP);
}
static void cm_elapsed(uint32_t now) {
    uint32_t dt=now-cm.last_tick,f=cm.fraction+dt%MS(1);cm.last_tick=now;
    cm.elapsed+=dt/MS(1)+f/MS(1);cm.fraction=f%MS(1);
}
static uint_fast8_t cm_event(struct timer *timer) {
    cm.timer_added=0;if(!cm.active)return SF_DONE;
    uint32_t now=timer_read_time();cm_elapsed(now);
    uint8_t why=!control_ready(now)?CR_HEARTBEAT:
        door_policy_enabled()&&door_closed_mask()!=3?CR_DOOR:
        cm.elapsed>=cm.max_ms?CR_TIME:0;
    uint16_t hit=(uint16_t)~(control_inputs()^cm.stop_value)&cm.stop_mask;
    if(!why && hit){cm.hit=hit;why=CR_INPUT;}
    if(why){cm_end(why);return SF_DONE;}
    if(cm.phase==CM_HOLD) {
        if(cm.elapsed-cm.hold_start>=cm.hold_ms){cm_end(CR_COMPLETE);return SF_DONE;}
        timer->waketime=now+MS(1);
    } else if(cm.high) {
        cm_clear_steps();
        if(cm.done==cm.master) {
            if(!cm.hold_ms){cm_end(CR_COMPLETE);return SF_DONE;}
            cm.phase=CM_HOLD;cm.hold_start=cm.elapsed;timer->waketime=now+MS(1);
        } else timer->waketime=now+cm.period-cm.pulse;
    } else {
        uint32_t a=0,b=0;
        for(unsigned i=0;i<4;i++) {
            cm.error[i]+=cm.planned[i];
            if(cm.error[i]>=cm.master) {
                cm.error[i]-=cm.master;cm.sent[i]++;
                if(i<2)a|=BIT(i?6:7);else b|=BIT(i==2?8:9);
            }
        }
        if(a)motor_test_write(GPIOA,a,1);
        if(b)motor_test_write(GPIOB,b,1);
        cm.high=1;cm.done++;
        uint32_t ramp=cm.done<cm.master-cm.done?cm.done:cm.master-cm.done;
        uint32_t hz=400u+16u*ramp;if(hz>cm.rate)hz=cm.rate;
        cm.period=CONFIG_CLOCK_FREQ/hz;
        timer->waketime=now+cm.pulse;
    }
    cm.timer_added=1;return SF_RESCHEDULE;
}
static void control_output_off(unsigned i) {
    if(i>=6 && (co[i].duty || control_heater_duty[i-6])) {
        control_cooldown_ms=5000;control_cooldown_tick=timer_read_time();
    }
    co[i].duty=0;co[i].remaining=0;
    if(i>=6) {
        unsigned h=i-6;
        raw_output(th_heaters[h].port, __builtin_ctz(th_heaters[h].bit),1);
        control_heater_duty[h]=0;
    } else if(i<4) {
        GPIO_TypeDef *port=i<2?GPIOB:GPIOC;unsigned pin=i<2?15-i:i==2?7:6;
        uint8_t on=!!(cool_mask&BIT(i));motor_test_write(port,BIT(pin),on);
        fan_gpio_observe(port,BIT(pin),on);fan_set_duty(i,on?255:0);
    } else if(co[i].initialized) {
        control_pwm_bypass=1;gpio_pwm_write(co[i].pwm,0);control_pwm_bypass=0;
    }
}
static void control_stop_all(void) {
    if(sl.active) {
        sl.sent=cm.sent[3];sl.elapsed=cm.elapsed;sl.active=0;sl.reason=2;
    }
    uint8_t was_native=control_mode==CT_NATIVE;
    control_motion_cancel();xy_seek_cancel();
    if(motor_test.running) {
        if(motor_test.timer_added)sched_del_timer(&motor_timer);
        motor_test.timer_added=0;motor_test_end(MT_STOP);
    }
    t0_heat_end(TH_STOP,timer_read_time());fan_manual_abort();
    for(unsigned i=0;i<10;i++)control_output_off(i);
    for(unsigned i=0;i<2;i++)if(auxiliary_ready) {
        if(feeder_running[i])sched_del_timer(&feeder_timer[i]);
        if(locks_pulling[i])sched_del_timer(&lock_timer[i]);
        feeder_running[i]=locks_on[i]=locks_pulling[i]=0;
        raw_output(i?GPIOG:GPIOC,i?11:5,0);
        gpio_pwm_write(locks[i],0);
        transport_stop_channel(i);
    }
    motor_test_write(GPIOE,BIT(10),0);cm_clear_steps();
    if(was_native) {control_latched=1;control_mode=CT_OFF;}
    cool_mask=control_cooling_mask();cool_applied=255;cooling_apply();
}
static void control_shutdown(void) {
    if(sl.active) {
        sl.sent=cm.sent[3];sl.elapsed=cm.elapsed;sl.active=0;sl.reason=7;
    }
    if(control_heat_seen || control_heater_duty[0] || control_heater_duty[1]
       || control_heater_duty[2] || control_heater_duty[3]) {
        control_cooldown_ms=5000;control_cooldown_tick=timer_read_time();
    }
    control_mode=CT_OFF;control_latched=1;
    if(cm.active) {
        // sched_timer_reset owns removal during actual MCU shutdown.
        cm.timer_added=0;cm_end(CR_SHUTDOWN);
    }
    for(unsigned i=0;i<10;i++){co[i].duty=0;co[i].remaining=0;co[i].reason=6;}
    for(unsigned i=0;i<4;i++)control_heater_duty[i]=0;
}
static uint8_t control_cooling_mask(void) {
    uint32_t now=timer_read_time();uint8_t mask=0,all_cold=1;
    if(co[6].duty || co[7].duty)mask|=1;
    if(co[8].duty || co[9].duty)mask|=12;
    for(unsigned i=0;i<5;i++) {
        uint8_t valid;int16_t t=control_temp(i,now,&valid);
        if(!valid || t>40)all_cold=0;
        if(valid && t>40)mask|=i<2?1:i==2?4:i==3?8:0;
    }
    if(all_cold)control_heat_seen=0;
    else if(control_heat_seen)mask|=13; // known hotend + chamber cooling paths
    if(control_cooldown_ms)mask|=13;
    return mask;
}
static void control_fan_tick(void) {
    if(control_mode!=CT_SERVICE)return;
    control_fan_phase=(control_fan_phase+1)%50;
    for(unsigned i=0;i<4;i++) {
        if(!co[i].duty)continue;
        uint16_t duty=(cool_mask&BIT(i))?256:co[i].duty;
        uint8_t level=(uint32_t)control_fan_phase*256u<(uint32_t)duty*50u;
        GPIO_TypeDef *port=i<2?GPIOB:GPIOC;
        unsigned pin=i<2?15-i:i==2?7:6;
        motor_test_write(port,BIT(pin),level);fan_gpio_observe(port,BIT(pin),level);
        fan_set_duty(i,(duty*255u+128)/256u);
    }
}
static uint8_t control_service_fan(unsigned i) {
    return control_mode==CT_SERVICE && i<6 && co[i].duty;
}
static void control_guard(uint32_t now) {
    sl_guard(now);
    if(control_cooldown_ms) {
        uint32_t ms=(uint32_t)(now-control_cooldown_tick)/MS(1);
        if(ms){control_cooldown_tick+=ms*MS(1);control_cooldown_ms=ms>=control_cooldown_ms?0:control_cooldown_ms-ms;}
    }
    if(!control_mode)return;
    if(!control_ready(now)) {
        if(control_mode==CT_NATIVE)trip(1);
        uint8_t moving=cm.active;
        control_stop_all();if(moving){cm.reason=CR_HEARTBEAT;cm.phase=CM_FAILED;}
        for(unsigned i=0;i<10;i++)co[i].reason=5;
        control_mode=CT_OFF;return;
    }
    if(control_mode==CT_NATIVE) {
        if(door_policy_enabled()&&door_closed_mask()!=3)trip(6);
        if(!control_sensors_valid(control_guard_mask|31,now))trip(4);
        for(unsigned h=0;h<4;h++)if(control_heater_duty[h])control_heat_seen=1;
        uint8_t needed=control_cooling_mask();
        if(control_heater_duty[0]||control_heater_duty[1])needed|=1;
        if(control_heater_duty[2]||control_heater_duty[3])needed|=12;
        for(unsigned i=0;i<4;i++) {
            if(!(needed&BIT(i))){control_tach_watch[i]=0;continue;}
            if(!control_tach_watch[i]){control_tach_watch[i]=1;edge_time[i]=now;}
            if((uint32_t)(now-edge_time[i])>=MS(10000))trip(5);
        }
        return;
    }
    // The independent 10ms sampler also enforces slow-motion deadlines and
    // stops. A one-Hz STEP period must not postpone a short time limit.
    if(cm.active) {
        cm_elapsed(now);
        uint8_t reason=door_policy_enabled()&&door_closed_mask()!=3?CR_DOOR:
            cm.elapsed>=cm.max_ms?CR_TIME:0;
        uint16_t hit=(uint16_t)~(control_inputs()^cm.stop_value)&cm.stop_mask;
        if(!reason && hit){cm.hit=hit;reason=CR_INPUT;}
        if(reason) {
            if(cm.timer_added)sched_del_timer(&cm_timer);
            cm.timer_added=0;cm_end(reason);
        }
    }
    for(unsigned i=0;i<10;i++)if(co[i].duty) {
        uint32_t dt=now-co[i].last_tick,f=co[i].fraction+dt%MS(1);
        co[i].last_tick=now;uint32_t ms=dt/MS(1)+f/MS(1);co[i].fraction=f%MS(1);
        uint8_t off=ms>=co[i].remaining?3:0;
        if(!off)co[i].remaining-=ms;
        if(i>=6) {
            uint8_t valid;int16_t t=control_temp(th_heaters[i-6].sensor,now,&valid);
            if(!valid || t>=co[i].limit || !control_sensors_valid(control_guard_mask|31,now))off=4;
        }
        if(off){control_output_off(i);co[i].reason=off;}
    }
}
void command_inventure_control(uint32_t *args) {
    unsigned action=args[0],mode=args[1];irqstatus_t flags=irq_save();uint8_t reject=0;
    uint32_t now=timer_read_time();
    if(action==2)control_stop_all();
    else if(action>2 || mode>2)reject=1;
    else if(action==1) {
        if(mode==CT_OFF){control_stop_all();control_mode=CT_OFF;}
        else if(mode==control_mode){} // idempotent; never renew any output timer
        else if(control_latched)reject=4;
        else if(motor_test_busy() || t0_heat_busy() || fan_manual_active())reject=2;
        else if(!control_ready(now))reject=3;
        else if(mode==CT_NATIVE && (!control_sensors_valid(control_guard_mask|31,now)
             || (door_policy_enabled()&&door_closed_mask()!=3)))reject=5;
        else control_mode=mode;
    }
    uint8_t selected=control_mode,armed=inventure_is_armed(),active=control_busy();
    uint8_t latch=control_latched,guards=control_guard_mask;irq_restore(flags);
    sendf("inventure_control_state mode=%c armed=%c active=%c latched=%c fault=%c guard_mask=%c reject=%c",
          selected,armed,active,latch,fault,guards,reject);
}
DECL_COMMAND_FLAGS(command_inventure_control,HF_IN_SHUTDOWN,"inventure_control action=%c mode=%c");
static uint8_t cm_request(uint32_t *args,uint32_t now) {
    uint8_t reject=0;uint32_t token=args[12],master=0;
    if(token==cm.token) {
        for(unsigned i=0;i<4;i++)if(args[i+2]!=cm.planned[i])reject=1;
        if(args[1]!=cm.direction)reject=1;
        if(args[6]!=cm.rate || timer_from_us(args[7])!=cm.pulse || args[8]!=cm.max_ms
           || args[9]!=cm.stop_mask || args[10]!=cm.stop_value || args[11]!=cm.hold_ms)reject=1;
    } else {
        for(unsigned i=0;i<4;i++){if(args[i+2]>2000000u)reject=1;if(args[i+2]>master)master=args[i+2];}
        if(args[1]>15 || !master || args[6]<1 || args[6]>20000 || args[7]<2 || args[7]>100
           || args[7]+2u>1000000u/args[6] || !args[8] || args[8]>600000
           || args[11]>60000 || !token)reject=1;
        if(!reject && (control_mode!=CT_SERVICE || !control_ready(now)))reject=3;
        if(!reject && (sl.active || cm.active || motor_test.running || xy_seek_busy() || t0_heat_busy() || fan_manual_active()))reject=2;
        if(!reject) {
            cm.run++;cm.token=token;cm.master=master;cm.done=0;cm.direction=args[1];
            for(unsigned i=0;i<4;i++){cm.planned[i]=args[i+2];cm.sent[i]=cm.error[i]=0;}
            cm.rate=args[6];cm.pulse=timer_from_us(args[7]);cm.max_ms=args[8];
            cm.stop_mask=args[9];cm.stop_value=args[10];cm.hold_ms=args[11];
            cm.elapsed=cm.fraction=cm.hit=cm.high=cm.reason=0;cm.last_tick=now;
            cm.phase=CM_RUNNING;cm.active=1;
            for(unsigned i=0;i<4;i++)raw_output(mt_drivers[i].dir_port,
                __builtin_ctz(mt_drivers[i].dir_bit),!!(cm.direction&BIT(i)));
            cm_clear_steps();
            cm.hit=(uint16_t)~(control_inputs()^cm.stop_value)&cm.stop_mask;
            if(cm.hit)cm_end(CR_INPUT);
            else {
                motor_test_write(GPIOE,BIT(10),1);
                cm_timer.func=cm_event;cm_timer.waketime=now+MS(20);sched_add_timer(&cm_timer);cm.timer_added=1;
            }
        }
    }
    return reject;
}
void command_inventure_motion(uint32_t *args) {
    unsigned action=args[0];irqstatus_t flags=irq_save();uint8_t reject=0;
    uint32_t now=timer_read_time();
    if(action==2)control_motion_cancel();
    else if(action>2)reject=1;
    else if(action==1)reject=cm_request(args,now);
    uint32_t run=cm.run,last_token=cm.token,elapsed=cm.elapsed,done=cm.done;
    uint8_t phase=cm.phase,reason=cm.reason,active=cm.active;uint16_t hit=cm.hit;
    irq_restore(flags);
    sendf("inventure_motion_state run=%u token=%u phase=%c reason=%c reject=%c active=%c events=%u elapsed_ms=%u hit=%hu",
          run,last_token,phase,reason,reject,active,done,elapsed,hit);
}
DECL_COMMAND_FLAGS(command_inventure_motion,HF_IN_SHUTDOWN,
 "inventure_motion action=%c directions=%c a=%u b=%u z=%u e=%u rate=%u pulse_us=%hu max_ms=%u stop_mask=%hu stop_value=%hu hold_ms=%u token=%u");
void command_inventure_motion_driver(uint32_t *args) {
    unsigned i=args[0];if(i>3)return;
    irqstatus_t flags=irq_save();uint32_t run=cm.run,p=cm.planned[i],s=cm.sent[i];
    uint8_t d=!!(cm.direction&BIT(i));irq_restore(flags);
    sendf("inventure_motion_driver run=%u channel=%c planned=%u sent=%u direction=%c",run,i,p,s,d);
}
DECL_COMMAND_FLAGS(command_inventure_motion_driver,HF_IN_SHUTDOWN,"inventure_motion_driver_query channel=%c");
void command_inventure_output(uint32_t *args) {
    unsigned action=args[0],i=args[1],duty=args[2];uint32_t duration=args[3],token=args[5];
    int16_t limit=(int16_t)args[4];irqstatus_t flags=irq_save();uint8_t reject=0;
    uint32_t now=timer_read_time();
    if(i>9){irq_restore(flags);return;}
    if(action==2){control_output_off(i);co[i].reason=2;}
    else if(action>2 || duty>256 || (action==1 && (!duration || duration>600000 || !token)))reject=1;
    else if(action==1 && token==co[i].token) {
        if(duty!=co[i].requested_duty || duration!=co[i].requested_ms || limit!=co[i].limit)reject=1;
    } else if(action==1) {
        if(control_mode!=CT_SERVICE || !control_ready(now))reject=3;
        else if((sl.active && i!=6+sl.tool) || (cm.active && !(sl.active && i==6+sl.tool && cm.token==sl.token))
                || motor_test.running || xy_seek_busy() || t0_heat_busy() || fan_manual_active())reject=2;
        else if(i>=6 && (limit<40 || limit>control_max[th_heaters[i-6].sensor]
                 || !control_sensors_valid(control_guard_mask|31,now)))reject=5;
        else if(i>=6) {
            uint8_t valid;int16_t t=control_temp(th_heaters[i-6].sensor,now,&valid);
            if(!valid || t>=limit)reject=5;
        }
        if(!reject) {
            co[i].token=token;co[i].duty=co[i].requested_duty=duty;
            co[i].remaining=co[i].requested_ms=duration;co[i].limit=limit;co[i].reason=1;
            co[i].last_tick=now;co[i].fraction=0;
            if(i>=4) {
                if(i>=6 && duty) {cool_mask|=i<8?1:12;cool_applied=255;cooling_apply();}
                control_pwm_bypass=1;
                co[i].pwm=gpio_pwm_setup(co_pins[i-4],co_cycles[i-4],i>=6?256:0);
                co[i].initialized=1;gpio_pwm_write(co[i].pwm,i>=6?256-duty:duty);
                control_pwm_bypass=0;
            }
            if(i>=6 && duty)control_heat_seen=1;
            if(!duty)control_output_off(i);
        }
    }
    uint16_t applied=co[i].duty;uint32_t left=co[i].remaining,last_token=co[i].token;
    irq_restore(flags);
    sendf("inventure_output_state channel=%c duty=%hu remaining_ms=%u token=%u reject=%c reason=%c",
          i,applied,left,last_token,reject,co[i].reason);
}
DECL_COMMAND_FLAGS(command_inventure_output,HF_IN_SHUTDOWN,
 "inventure_output action=%c channel=%c duty=%hu duration_ms=%u limit_c=%hi token=%u");
void command_inventure_control_guard(uint32_t *args) {
    unsigned i=args[0],enabled=args[1];int16_t lo=args[2],hi=args[3];
    irqstatus_t flags=irq_save();uint8_t reject=0;
    static const int16_t caps[8]={310,310,150,150,150,100,100,190};
    if(i>7 || enabled>1 || lo< -30 || hi<=lo || hi>caps[i])reject=1;
    else if(control_busy() || t0_heat_busy() || motor_test.running || xy_seek_busy())reject=2;
    else {control_min[i]=lo;control_max[i]=hi;if(enabled)control_guard_mask|=BIT(i);else control_guard_mask&=~BIT(i);}
    irq_restore(flags);
    sendf("inventure_guard_state sensor=%c enabled=%c min_c=%hi max_c=%hi reject=%c",i,
          i<8?!!(control_guard_mask&BIT(i)):0,i<8?control_min[i]:0,i<8?control_max[i]:0,reject);
}
DECL_COMMAND(command_inventure_control_guard,"inventure_control_guard sensor=%c enabled=%c min_c=%hi max_c=%hi");

static void sl_guard(uint32_t now) {
    if(!sl.active)return;
    sl.sent=cm.sent[3];sl.elapsed=cm.elapsed;
    uint8_t why=0,valid;int16_t temperature=control_temp(sl.tool,now,&valid);
    if(!control_ready(now))why=6;
    else if(cm.token!=sl.token || cm.planned[0] || cm.planned[1] || cm.planned[2]
            || cm.direction!=(sl.e_direction?8:0) || cm.planned[3]!=sl.steps)why=5;
    else if(!cm.active) {
        if(cm.reason==CR_COMPLETE && cm.sent[3]==sl.steps) {
            if(feeder_running[sl.channel])sched_del_timer(&feeder_timer[sl.channel]);
            feeder_running[sl.channel]=0;gpio_out_write(feeder_run[sl.channel],0);
            sl.active=0;sl.reason=1;return;
        }
        why=5;
    } else if(iv9_due(now,sl.deadline) || !feeder_running[sl.channel])why=3;
    else if(!valid || temperature<sl.min_c || temperature>=sl.max_c
            || !control_sensors_valid(control_guard_mask|31,now) || co[6+sl.tool].reason>=3)why=4;
    if(why){control_stop_all();sl.reason=why;}
}

void command_inventure_load_stream(uint32_t *args) {
    unsigned action=args[0];uint32_t steps=args[1],rate=args[2],maximum=args[3],lease=args[4];
    int16_t minimum_c=args[5],maximum_c=args[6];uint32_t token=args[7],sequence=args[8];
    unsigned tool=args[9],channel=args[10],e_direction=args[11],feeder_direction=args[12];
    irqstatus_t flags=irq_save();uint32_t now=timer_read_time();uint8_t reject=0;
    sl_guard(now);
    if(action==3)control_stop_all();
    else if(action>3)reject=1;
    else if(action==1 && token && token==sl.token) {
        // Idempotent START replay never restarts motion or renews its lease.
        if(steps!=sl.steps || rate!=sl.rate || maximum!=sl.max_ms || lease!=sl.lease_ms
                || minimum_c!=sl.min_c || maximum_c!=sl.max_c || sequence
                || tool!=sl.tool || channel!=sl.channel || e_direction!=sl.e_direction
                || feeder_direction!=sl.feeder_direction)reject=1;
    } else if(action==1) {
        if(!steps || steps>400000 || rate<960 || rate>1536 || !token || sequence
                || tool>1 || channel>1 || e_direction>1 || feeder_direction>1
                || lease<250 || lease>1500 || !maximum || maximum>600000
                || maximum<(steps*1000u/rate)+1000u
                || minimum_c<180 || minimum_c>230 || maximum_c<=minimum_c+5 || maximum_c>250)reject=1;
        if(!reject && token==cm.token)reject=4;
        if(!reject && (control_mode!=CT_SERVICE || !control_ready(now) || !auxiliary_ready))reject=3;
        if(!reject && (sl.active || cm.active || feeder_running[0] || feeder_running[1]
                || locks_on[0] || locks_on[1]))reject=2;
        for(unsigned i=0;i<10;i++)if(!reject && i!=6+tool && co[i].duty)reject=2;
        uint8_t valid;int16_t t=control_temp(tool<2?tool:0,now,&valid);
        if(!reject && (!valid || t<minimum_c || t>=maximum_c
                || !control_sensors_valid(control_guard_mask|31,now) || co[6+tool].reason>=3))reject=3;
        if(!reject) {
            uint32_t motion[]={1,e_direction?8:0,0,0,0,steps,rate,10,maximum,0,0,0,token};
            reject=cm_request(motion,now);
            if(!reject) {
                uint32_t feeder[]={channel,feeder_direction,lease};command_inventure_feeder(feeder);
                sl.token=token;sl.steps=steps;sl.rate=rate;sl.max_ms=maximum;sl.lease_ms=lease;
                sl.min_c=minimum_c;sl.max_c=maximum_c;sl.sequence=sl.sent=sl.elapsed=0;
                sl.tool=tool;sl.channel=channel;sl.e_direction=e_direction;sl.feeder_direction=feeder_direction;
                sl.deadline=now+MS(lease);sl.reason=0;sl.active=1;
            }
        }
    } else if(action==2) {
        if(!sl.active || token!=sl.token || !sequence || sequence<sl.sequence || lease!=sl.lease_ms)reject=4;
        else if(sequence>sl.sequence) {
            // Refresh the existing timer, without changing RUN, DIR or PWM.
            sl.sequence=sequence;sl.deadline=now+MS(sl.lease_ms);
            sched_del_timer(&feeder_timer[sl.channel]);feeder_until[sl.channel]=sl.deadline;
            feeder_timer[sl.channel].waketime=sl.deadline;sched_add_timer(&feeder_timer[sl.channel]);
        }
    }
    uint32_t current_token=sl.token,planned=sl.steps,sent=sl.sent,elapsed=sl.elapsed,seq=sl.sequence;
    uint32_t remaining=sl.active?(uint32_t)(sl.deadline-now)/MS(1):0;
    uint8_t active=sl.active,reason=sl.reason,feeder=feeder_running[sl.channel];irq_restore(flags);
    sendf("inventure_load_stream_state token=%u active=%c reason=%c reject=%c planned=%u sent=%u elapsed_ms=%u remaining_ms=%u sequence=%u feeder=%c",
        current_token,active,reason,reject,planned,sent,elapsed,remaining,seq,feeder);
}
DECL_COMMAND_FLAGS(command_inventure_load_stream,HF_IN_SHUTDOWN,
 "inventure_load_stream action=%c steps=%u rate=%u max_ms=%u lease_ms=%hu min_c=%hi max_c=%hi token=%u sequence=%u tool=%c channel=%c e_direction=%c feeder_direction=%c");
