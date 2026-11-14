// SPDX-License-Identifier: GPL-3.0-or-later
// Persistent generic capabilities: tests and geometry are host parameters.
#line 12000 "inventure_control.inc"
DECL_CONSTANT("INVENTURE_CONTROL_ABI",1);
DECL_CONSTANT("INVENTURE_MOTION_ABI",1);
DECL_CONSTANT("INVENTURE_OUTPUT_ABI",1);
DECL_CONSTANT("INVENTURE_LOAD_STREAM_ABI",1);
DECL_CONSTANT("INVENTURE_FACTORY_ABI",1);
DECL_CONSTANT("INVENTURE_FACTORY_FEED_CLOCK",1);
DECL_CONSTANT("INVENTURE_NATIVE_Z_GUARD_ABI",1);
#define CM_SERVICE_MAX_HZ 20000u
#define CM_FACTORY_MAX_HZ 125000u
#define CM_FACTORY_TOOL_MASK 3u
#define CM_FACTORY_PULSE_US 2u
#define CM_FACTORY_MIN_INTERVAL_US 6u
#define CM_FACTORY_CALLBACK_RESERVE_US 3u
#define CM_FACTORY_CALLBACK_MAX_US 20u
#define CM_AXIS_Z_MAX_HZ 3200u
#define CM_AXIS_E_MAX_HZ 9600u
#define CM_AXIS_E_MIN_C 180
#define CM_AXIS_E_MAX_C 250
DECL_CONSTANT("INVENTURE_CONTROL_MAX_HZ",CM_SERVICE_MAX_HZ);
DECL_CONSTANT("INVENTURE_FACTORY_MOTION_ABI",1);
DECL_CONSTANT("INVENTURE_FACTORY_MOTION_MAX_HZ",CM_FACTORY_MAX_HZ);
DECL_CONSTANT("INVENTURE_FACTORY_MOTION_TOOLS",CM_FACTORY_TOOL_MASK);
DECL_CONSTANT("INVENTURE_AXIS_MOTION_ABI",1);
DECL_CONSTANT("INVENTURE_AXIS_MOTION_MAX_HZ",CM_FACTORY_MAX_HZ);
DECL_CONSTANT("INVENTURE_AXIS_MOTION_Z_MAX_HZ",CM_AXIS_Z_MAX_HZ);
DECL_CONSTANT("INVENTURE_AXIS_MOTION_E_MAX_HZ",CM_AXIS_E_MAX_HZ);
DECL_CONSTANT("INVENTURE_CONTROL_MAX_STEPS",2000000);
DECL_CONSTANT("INVENTURE_CONTROL_MAX_MS",600000);
DECL_CONSTANT("INVENTURE_CONTROL_SENSOR_MASK",127);
enum { CT_OFF,CT_NATIVE,CT_SERVICE };
enum { CM_IDLE,CM_RUNNING,CM_HOLD,CM_DONE,CM_STOPPED,CM_FAILED };
enum { CR_NONE,CR_COMPLETE,CR_INPUT,CR_STOP,CR_HEARTBEAT,CR_DOOR,CR_TIME,CR_SHUTDOWN,CR_TIMING,CR_THERMAL };
static struct {
    uint32_t run,token,planned[4],sent[4],error[4],master,done,rate,pulse;
    uint32_t elapsed,fraction,last_tick,max_ms,hold_ms,hold_start,period;
    uint32_t fast_accel_half,fast_cruise_ramp,fast_cruise_period;
    uint32_t fast_peak_ticks,fast_peak_late,fast_last_launch;
    uint16_t stop_mask,stop_value,hit;
    uint8_t phase,reason,active,high,direction,timer_added,fast_mask,fast_direct,fast_tool;
} cm;
static struct timer cm_timer;
static uint32_t native_z_guard_owner;
static void native_z_guard_clear(void) {
    native_z_guard_active=native_z_guard_lost=0;
    native_z_refine_held=native_z_refine_conflict=0;
}
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
// A hot SERVICE procedure owns the existing DDA and P13 feeder kernel.
// Host parameters describe geometry and stages; MCU owns the independent
// lease, sensor checks, actual feeder starts and actual E accounting.
static struct {
    uint32_t token,sequence,deadline,lease_ms,duration_ms,budget_steps;
    int16_t min_c,max_c;
    uint8_t active,reason,tool,mode,monitor,start_mode,start_monitor;
} fs;
static void fs_guard(uint32_t now);
static void fs_age_update(uint32_t now);
static uint8_t zh_active(void);
static void zh_guard(uint32_t now);
static void zh_stop(void);
static void zh_shutdown(void);
static uint8_t factory_transport_allowed(void) {
    return fs.active && control_mode==CT_SERVICE;
}
static uint8_t control_guard_mask=127,control_fan_phase,control_heat_seen;
static uint32_t control_cooldown_ms,control_cooldown_tick;
static uint8_t control_tach_watch[4];
static int16_t control_min[8]={-20,-20,-20,-20,-20,-20,-20,-20};
static int16_t control_max[8]={310,310,150,150,150,100,100,190};
static const uint8_t co_pins[6]={GPIO('A',1),GPIO('D',13),GPIO('A',0),GPIO('E',5),GPIO('C',9),GPIO('C',8)};
static const uint32_t co_cycles[6]={5632,2096640,5632,12800,16776960,16776960};
static uint16_t cm_input_hit(uint16_t mask,uint16_t value) {
    const uint8_t pins[16]={GPIO('B',11),GPIO('C',15),GPIO('E',12),GPIO('C',14),
      GPIO('C',13),GPIO('G',2),GPIO('B',10),GPIO('E',15),GPIO('D',10),GPIO('F',1),
      GPIO('D',15),GPIO('G',10),GPIO('D',7),GPIO('D',6),GPIO('D',5),GPIO('A',15)};
    uint16_t hit=0;
    while(mask) {
        unsigned i=__builtin_ctz((unsigned)mask);
        unsigned pin=pins[i];
        uint8_t level=!!(gpio_pin_to_regs(pin)->IDR&BIT(pin%16));
        if(level==!!(value&BIT(i)))hit|=BIT(i);
        mask&=mask-1u;
    }
    return hit;
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
static uint8_t cm_axis_e_ready(unsigned tool,uint32_t now) {
    uint8_t valid;
    int16_t t=control_temp(tool,now,&valid);
    if(co[6+tool].reason>=3 || (co[6+tool].duty
            && (uint32_t)(now-co[6+tool].last_tick)/MS(1)>=co[6+tool].remaining))
        return 0;
    return valid && t>=CM_AXIS_E_MIN_C && t<CM_AXIS_E_MAX_C
        && control_sensors_valid(control_guard_mask|31,now);
}
static uint8_t control_aux_allowed(void) {
    return control_mode==CT_SERVICE && !sl.active && !fs.active && !fault && !control_latched && heartbeat_live;
}
static uint8_t control_door_policy_busy(void) {
    if(control_mode==CT_NATIVE || cm.active || fs.active || sl.active
       || feeder_running[0] || feeder_running[1])return 1;
    for(unsigned i=0;i<10;i++)if(co[i].duty)return 1;
    return 0;
}
static uint8_t control_busy(void) {
    return control_door_policy_busy() || locks_on[0] || locks_on[1];
}
// STM32F207 RM0033, 15.5: PWM2 idles LOW at CNT=0 and OPM stops at
// the update after ARR. Only a timer-backed SERVICE run remaps these STEP
// pins. Inventure's hardware-PWM pin table uses TIM4/5/8/9 exclusively.
#define CM_PULSE_CEN 1u
#define CM_PULSE_OPM 8u
#define CM_PULSE_PWM2 (7u << 4)
static const struct cm_pulse_channel {
    TIM_TypeDef *timer;
    GPIO_TypeDef *port;
    uint8_t pin,af;
} cm_pulse_channels[] = {
    {TIM14,GPIOA,7,9}, {TIM13,GPIOA,6,9}, {TIM10,GPIOB,8,3}, {TIM11,GPIOB,9,3},
};

static uint8_t cm_fast_available(uint8_t mask) {
    for(unsigned i=0;i<ARRAY_SIZE(cm_pulse_channels);i++) {
        if(!(mask&BIT(i)))continue;
        TIM_TypeDef *hw=cm_pulse_channels[i].timer;
        if(is_enabled_pclock((uint32_t)(uintptr_t)hw)
                && (hw->CR1 || hw->CCER || hw->DIER))return 0;
    }
    return 1;
}

static void cm_fast_setup(uint8_t mask) {
    for(unsigned i=0;i<ARRAY_SIZE(cm_pulse_channels);i++) {
        if(!(mask&BIT(i)))continue;
        const struct cm_pulse_channel *ch=&cm_pulse_channels[i];
        TIM_TypeDef *hw=ch->timer;
        uint32_t address=(uint32_t)(uintptr_t)hw;
        if(!is_enabled_pclock(address))enable_pclock(address);
        // TIM13/14: APB1 30 MHz doubled; TIM10/11: APB2 60 MHz doubled.
        uint32_t counts_per_us=get_pclock_frequency(address)*2u/1000000u;
        hw->CR1=CM_PULSE_OPM;
        hw->DIER=0;
        hw->PSC=0;
        hw->ARR=counts_per_us*CM_FACTORY_PULSE_US;
        hw->CCR1=1;
        hw->CCMR1=CM_PULSE_PWM2;
        hw->EGR=1;
        hw->SR=0;
        hw->CCER=1;
        uint32_t mode_shift=ch->pin*2,af_shift=(ch->pin%8)*4;
        GPIO_TypeDef *port=ch->port;
        raw_output(port,ch->pin,0);
        port->AFR[ch->pin/8]=(port->AFR[ch->pin/8]&~(15u<<af_shift))
            | ((uint32_t)ch->af<<af_shift);
        port->OSPEEDR=(port->OSPEEDR&~(3u<<mode_shift)) | (2u<<mode_shift);
        port->MODER=(port->MODER&~(3u<<mode_shift)) | (2u<<mode_shift);
    }
    cm.fast_mask=mask;
}

static void cm_fast_release(void) {
    for(unsigned i=0;i<ARRAY_SIZE(cm_pulse_channels);i++) {
        if(!(cm.fast_mask&BIT(i)))continue;
        const struct cm_pulse_channel *ch=&cm_pulse_channels[i];
        raw_output(ch->port,ch->pin,0);
        ch->timer->CCER=0;
        ch->timer->CR1=0;
    }
    cm.fast_mask=0;
}

static void cm_clear_steps(void) {
    motor_test_write(GPIOA,BIT(6)|BIT(7),0);
    motor_test_write(GPIOB,BIT(8)|BIT(9),0);cm.high=0;
}
static void cm_end(uint8_t reason) {
    // Preserve established carriage position between factory segments.
    // A failed/safety/global STOP path always releases the driver.
    if(!(fs.active && (reason==CR_COMPLETE || reason==CR_STOP || reason==CR_INPUT)))motor_test_write(GPIOE,BIT(10),0);
    if(cm.fast_mask)cm_fast_release();
    cm_clear_steps();
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

// Four Newton steps from the next power of two give the integer root of
// a nonzero uint32_t within one. The final division rounds down exactly.
static uint32_t cm_root(uint32_t n) {
    uint32_t x=1u<<((33u-__builtin_clz(n))/2u);
    x=(x+n/x)/2u;
    x=(x+n/x)/2u;
    x=(x+n/x)/2u;
    x=(x+n/x)/2u;
    uint32_t quotient=n/x;
    return x>quotient ? x-1 : x;
}

static void cm_fast_ramp_setup(void) {
    if(cm.planned[2])cm.fast_accel_half=40100u; // 802 steps/mm × 100 mm/s² / 2
    else if(!cm.planned[0] && !cm.planned[1])cm.fast_accel_half=240000u; // E: 500 mm/s²
    else {
        uint32_t minor=cm.planned[0]<cm.planned[1]?cm.planned[0]:cm.planned[1];
        uint32_t ratio=(1024u*minor)/cm.master;
        uint32_t norm=cm_root(2u*(1024u*1024u+ratio*ratio));
        // CoreXY has 640 steps/mm. The stock mixed planner uses 1500 mm/s²
        // along the path. Q10 geometry maps that to 960000..1357790 events/s².
        cm.fast_accel_half=(960000u*1024u)/norm;
    }
    // Store one quarter of velocity squared so 125000² fits uint32_t.
    uint32_t cruise_half=(cm.rate+1u)/2u;
    cm.fast_cruise_ramp=cm.rate<=400?0:
        (cruise_half*cruise_half-40000u+cm.fast_accel_half-1u)/cm.fast_accel_half;
    cm.fast_cruise_period=(CONFIG_CLOCK_FREQ+cm.rate-1u)/cm.rate;
}

static uint_fast8_t cm_fast_finish(uint8_t reason,uint32_t now) {
    cm_elapsed(now);
    cm.timer_added=0;
    cm_end(reason);
    return SF_DONE;
}

static uint_fast8_t cm_fast_event(struct timer *timer) {
    if(!cm.active)return SF_DONE;
    uint32_t now=timer_read_time();
    uint32_t late=(int32_t)(now-timer->waketime)>0?now-timer->waketime:0;
    if(late>cm.fast_peak_late)cm.fast_peak_late=late;
    if(!control_ready(now) || control_mode!=CT_SERVICE
            || (!cm.fast_direct && (!fs.active || iv9_due(now,fs.deadline))))
        return cm_fast_finish(CR_HEARTBEAT,now);
    if(door_policy_enabled() && door_closed_mask()!=3)
        return cm_fast_finish(CR_DOOR,now);
    // The independent 10 ms guard maintains elapsed time and temperature.
    if(cm.elapsed+10u>=cm.max_ms) {
        cm_elapsed(now);
        if(cm.elapsed>=cm.max_ms)return cm_fast_finish(CR_TIME,now);
    }
    if(late>MS(1)
            || ((cm.fast_mask&BIT(0)) && (TIM14->CR1&CM_PULSE_CEN))
            || ((cm.fast_mask&BIT(1)) && (TIM13->CR1&CM_PULSE_CEN))
            || ((cm.fast_mask&BIT(2)) && (TIM10->CR1&CM_PULSE_CEN))
            || ((cm.fast_mask&BIT(3)) && (TIM11->CR1&CM_PULSE_CEN)))
        return cm_fast_finish(CR_TIMING,now);
    // An extra dispatch drains the last one-shot before restoring GPIO.
    if(cm.reason==CR_TIMING)return cm_fast_finish(CR_TIMING,now);
    if(cm.done==cm.master)return cm_fast_finish(CR_COMPLETE,now);
    uint16_t hit=cm.stop_mask ? cm_input_hit(cm.stop_mask,cm.stop_value) : 0;
    if(hit) {
        cm.hit=hit;
        return cm_fast_finish(CR_INPUT,now);
    }
    // Allow 2 us HIGH plus 4 us nominal LOW. This leaves margin for
    // the bus write skew between pulse channels during a jittered dispatch.
    uint32_t earliest=cm.fast_last_launch+timer_from_us(CM_FACTORY_MIN_INTERVAL_US);
    if(cm.done && !iv9_due(now,earliest)) {
        timer->waketime=earliest;
        return SF_RESCHEDULE;
    }

    cm.done++;
    uint8_t step=0;
    for(unsigned i=0;i<4;i++) {
        cm.error[i]+=cm.planned[i];
        if(cm.error[i]>=cm.master) {
            cm.error[i]-=cm.master;
            cm.sent[i]++;
            step|=BIT(i);
        }
    }
    cm.fast_last_launch=timer_read_time();
    if(step&BIT(0))TIM14->CR1=CM_PULSE_OPM|CM_PULSE_CEN;
    if(step&BIT(1))TIM13->CR1=CM_PULSE_OPM|CM_PULSE_CEN;
    if(step&BIT(2))TIM10->CR1=CM_PULSE_OPM|CM_PULSE_CEN;
    if(step&BIT(3)) {
        TIM11->CR1=CM_PULSE_OPM|CM_PULSE_CEN;
        if(!cm.fast_direct)
            ivt_e_step(&transport[fs.tool],!!(cm.direction&BIT(3))==fs.tool);
    }
    uint32_t ramp=cm.done<cm.master-cm.done?cm.done:cm.master-cm.done;
    uint32_t period=cm.fast_cruise_period;
    if(ramp<cm.fast_cruise_ramp) {
        uint32_t hz=2u*cm_root(40000u+cm.fast_accel_half*ramp);
        period=(CONFIG_CLOCK_FREQ+hz-1u)/hz;
    }
    uint32_t spent=timer_read_time()-now;
    if(spent>cm.fast_peak_ticks)cm.fast_peak_ticks=spent;
    // The ramp computes an integer root even though its events are farther
    // apart. Reserve 3 us of the next nominal period for dispatch and bound
    // callback time at slower rates. At 125 kHz the limit is still 5 us.
    uint32_t budget=period-timer_from_us(CM_FACTORY_CALLBACK_RESERVE_US);
    if(budget>timer_from_us(CM_FACTORY_CALLBACK_MAX_US))
        budget=timer_from_us(CM_FACTORY_CALLBACK_MAX_US);
    if(spent>budget)cm.reason=CR_TIMING;
    if(cm.reason==CR_TIMING || cm.done==cm.master)
        timer->waketime=cm.fast_last_launch+timer_from_us(4);
    else {
        // Keep the nominal period with ordinary IRQ jitter. A longer
        // delay rebases the trajectory instead of replaying overdue steps.
        uint32_t base=late>timer_from_us(1)?now:timer->waketime;
        timer->waketime=base+period;
    }
    return SF_RESCHEDULE;
}

static uint_fast8_t cm_event(struct timer *timer) {
    cm.timer_added=0;if(!cm.active)return SF_DONE;
    uint32_t now=timer_read_time();cm_elapsed(now);
    uint8_t why=!control_ready(now)?CR_HEARTBEAT:
        door_policy_enabled()&&door_closed_mask()!=3?CR_DOOR:
        cm.elapsed>=cm.max_ms?CR_TIME:0;
    uint16_t hit=cm_input_hit(cm.stop_mask,cm.stop_value);
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
        if(fs.active && (b&BIT(9)))
            ivt_e_step(&transport[fs.tool],!!(cm.direction&BIT(3))==fs.tool);
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
    uint8_t factory_active=fs.active;
    if(fs.active){fs.active=0;fs.reason=2;transport_live=0;}
    if(sl.active) {
        sl.sent=cm.sent[3];sl.elapsed=cm.elapsed;sl.active=0;sl.reason=2;
    }
    uint8_t was_native=control_mode==CT_NATIVE;
    native_z_guard_clear();
    zh_stop();control_motion_cancel();xy_seek_cancel();
    if(motor_test.running) {
        if(motor_test.timer_added)sched_del_timer(&motor_timer);
        motor_test.timer_added=0;motor_test_end(MT_STOP);
    }
    t0_heat_end(TH_STOP,timer_read_time());fan_manual_abort();
    for(unsigned i=0;i<10;i++)control_output_off(i);
    for(unsigned i=0;i<2;i++)if(auxiliary_ready) {
        if(feeder_running[i])sched_del_timer(&feeder_timer[i]);
        feeder_running[i]=0;lock_release(i);
        raw_output(i?GPIOG:GPIOC,i?11:5,0);
        transport_stop_channel(i);
    }
    if(factory_active)gpio_pwm_write(feeder_clock,0);
    motor_test_write(GPIOE,BIT(10),0);cm_clear_steps();
    if(was_native) {control_latched=1;control_mode=CT_OFF;}
    cool_mask=control_cooling_mask();cool_applied=255;cooling_apply();
}
static void control_shutdown(void) {
    native_z_guard_clear();
    zh_shutdown();
    if(fs.active) {
        fs.active=0;fs.reason=7;transport_live=0;
        gpio_pwm_write(feeder_clock,0);
    }
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
    fs_age_update(now);
    sl_guard(now);
    zh_guard(now);
    fs_guard(now);
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
        if(native_z_guard_active && (native_z_guard_lost || (GPIOC->IDR&BIT(13))))
            trip(7);
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
        uint16_t hit=cm_input_hit(cm.stop_mask,cm.stop_value);
        if(!reason && hit){cm.hit=hit;reason=CR_INPUT;}
        if(!reason && cm.fast_direct && cm.planned[3] && !cm_axis_e_ready(cm.fast_tool,now))
            reason=CR_THERMAL;
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
    // Heater expiry and a failed DDA segment must abort the same owner on
    // this sampler pass, not after another host renewal opportunity.
    zh_guard(now);
    fs_guard(now);
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
        else if(motor_test_busy() || t0_heat_busy() || fan_manual_active() || fs.active
                || (cm.active && cm.fast_direct))reject=2;
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

void command_inventure_native_z_guard(uint32_t *args) {
    unsigned action=args[0];
    uint32_t owner=args[1];
    irqstatus_t flags=irq_save();
    uint32_t now=timer_read_time();
    zh_guard(now);
    uint8_t reject=0;
    if(action>2 || (action && !owner))reject=1;
    else if(native_z_guard_active && (native_z_guard_lost || (GPIOC->IDR&BIT(13))))
        trip(7);
    else if(action==1) {
        if(control_mode!=CT_NATIVE || !control_ready(now) || (GPIOC->IDR&BIT(13)))
            reject=3;
        else if(owner==native_z_guard_owner){} // Retries never rearm a stopped guard.
        else if(native_z_guard_active)reject=2;
        else {
            native_z_guard_owner=owner;
            native_z_guard_active=1;
        }
    } else if(owner && owner!=native_z_guard_owner)reject=4;
    else if(action==2) {
        if(native_z_refine_held && zh_active())reject=2;
        else native_z_guard_clear();
    }
    uint8_t active=native_z_guard_active;
    owner=native_z_guard_owner;
    irq_restore(flags);
    sendf("inventure_native_z_guard_state owner=%u active=%c reject=%c",owner,active,reject);
}
DECL_COMMAND_FLAGS(command_inventure_native_z_guard,HF_IN_SHUTDOWN,
    "inventure_native_z_guard action=%c owner=%u");

static uint8_t cm_axis_limits(const uint32_t *args,uint32_t *mask,uint32_t *value) {
    int32_t a=args[1]&BIT(0)?(int32_t)args[2]:-(int32_t)args[2];
    int32_t b=args[1]&BIT(1)?(int32_t)args[3]:-(int32_t)args[3];
    // CoreXY direction in the RELEASED coordinate frame; signs alone
    // determine which active-high XY endstop is ahead of the carriage.
    uint32_t required=(a+b<0?BIT(0):0) | (a-b<0?BIT(1):0),level=required;
    if(args[4]) {
        uint8_t up=!!(args[1]&BIT(2));
        required=up?BIT(3):BIT(2);
        level=up?0:BIT(2);
    }
    if((*mask&required)&(*value^level))return 0;
    *mask|=required;
    *value|=level;
    return 1;
}
static uint8_t cm_request(uint32_t *args,uint32_t now,uint8_t direct,unsigned tool) {
    uint8_t reject=0;uint32_t token=args[12],master=0;
    uint32_t stop_mask=args[9],stop_value=args[10];
    if(direct) {
        uint8_t xy=!!(args[2] || args[3]);
        if(tool>1 || args[1]>15 || args[2]>2000000u || args[3]>2000000u
                || args[4]>2000000u || args[5]>2000000u
                || (args[4] && (xy || args[5])) || (args[5] && xy)
                || (args[4] && args[6]>CM_AXIS_Z_MAX_HZ)
                || (args[5] && args[6]>CM_AXIS_E_MAX_HZ)
                || args[7]!=CM_FACTORY_PULSE_US || args[11]
                || stop_mask>65535 || (stop_value&~stop_mask)
                || !cm_axis_limits(args,&stop_mask,&stop_value))
            return 1;
    }
    if(token==cm.token) {
        for(unsigned i=0;i<4;i++)if(args[i+2]!=cm.planned[i])reject=1;
        if(args[1]!=cm.direction)reject=1;
        if(args[6]!=cm.rate || timer_from_us(args[7])!=cm.pulse || args[8]!=cm.max_ms
           || stop_mask!=cm.stop_mask || stop_value!=cm.stop_value || args[11]!=cm.hold_ms
           || direct!=cm.fast_direct || (direct && tool!=cm.fast_tool))reject=1;
    } else {
        for(unsigned i=0;i<4;i++){if(args[i+2]>2000000u)reject=1;if(args[i+2]>master)master=args[i+2];}
        uint8_t fast=direct || args[6]>CM_SERVICE_MAX_HZ;
        if(args[1]>15 || !master || args[6]<1 || args[6]>CM_FACTORY_MAX_HZ || args[7]<2 || args[7]>100
           || args[7]+2u>1000000u/args[6] || !args[8] || args[8]>600000
           || args[11]>60000 || !token)reject=1;
        if(!reject && fast && !direct && (args[4] || (args[2]!=master && args[3]!=master)
                || args[7]!=CM_FACTORY_PULSE_US || args[9] || args[10] || args[11]))reject=1;
        if(!reject && (control_mode!=CT_SERVICE || !control_ready(now)))reject=3;
        if(!reject && fast && ((door_policy_enabled() && door_closed_mask()!=3)
                || (!direct && (!fs.active || !(CM_FACTORY_TOOL_MASK&BIT(fs.tool))
                               || iv9_due(now,fs.deadline)))))reject=3;
        if(!reject && direct && (fs.active || feeder_running[0] || feeder_running[1]
                || locks_on[0] || locks_on[1]))reject=2;
        if(!reject && direct && args[5] && !cm_axis_e_ready(tool,now))reject=5;
        if(!reject && fs.active && args[5]) {
            uint8_t valid;int16_t t=control_temp(fs.tool,now,&valid);
            if(!valid || t<fs.min_c || t>=fs.max_c)reject=3;
        }
        if(!reject && (zh_active() || sl.active || cm.active || motor_test.running || xy_seek_busy()
                || t0_heat_busy() || fan_manual_active()))reject=2;
        uint8_t fast_mask=(args[2]?BIT(0):0) | (args[3]?BIT(1):0)
            | (args[4]?BIT(2):0) | (args[5]?BIT(3):0);
        if(!reject && fast && !cm_fast_available(fast_mask))reject=2;
        if(!reject) {
            cm.run++;cm.token=token;cm.master=master;cm.done=0;cm.direction=args[1];
            for(unsigned i=0;i<4;i++){cm.planned[i]=args[i+2];cm.sent[i]=cm.error[i]=0;}
            cm.rate=args[6];cm.pulse=timer_from_us(args[7]);cm.max_ms=args[8];
            cm.stop_mask=stop_mask;cm.stop_value=stop_value;cm.hold_ms=args[11];
            cm.fast_direct=direct;cm.fast_tool=tool;
            cm.elapsed=cm.fraction=cm.hit=cm.high=cm.reason=0;cm.last_tick=now;
            cm.phase=CM_RUNNING;cm.active=1;
            for(unsigned i=0;i<4;i++)raw_output(mt_drivers[i].dir_port,
                __builtin_ctz(mt_drivers[i].dir_bit),!!(cm.direction&BIT(i)));
            cm_clear_steps();
            if(fast)cm.fast_peak_ticks=cm.fast_peak_late=cm.fast_last_launch=0;
            cm.hit=cm_input_hit(cm.stop_mask,cm.stop_value);
            if(cm.hit)cm_end(CR_INPUT);
            else {
                if(fast) {
                    cm_fast_setup(fast_mask);
                    cm_fast_ramp_setup();
                }
                motor_test_write(GPIOE,BIT(10),1);
                cm_timer.func=fast?cm_fast_event:cm_event;
                cm_timer.waketime=now+MS(20);sched_add_timer(&cm_timer);cm.timer_added=1;
            }
        }
    }
    return reject;
}
static void cm_command(uint32_t *args,uint8_t direct,unsigned tool) {
    unsigned action=args[0];irqstatus_t flags=irq_save();uint8_t reject=0;
    uint32_t now=timer_read_time();
    if(action==2){zh_stop();control_motion_cancel();}
    else if(action>2)reject=1;
    else if(action==1)reject=cm_request(args,now,direct,tool);
    uint32_t run=cm.run,last_token=cm.token,elapsed=cm.elapsed,done=cm.done;
    uint8_t phase=cm.phase,reason=cm.reason,active=cm.active;uint16_t hit=cm.hit;
    irq_restore(flags);
    sendf("inventure_motion_state run=%u token=%u phase=%c reason=%c reject=%c active=%c events=%u elapsed_ms=%u hit=%hu",
          run,last_token,phase,reason,reject,active,done,elapsed,hit);
}
void command_inventure_motion(uint32_t *args) {cm_command(args,0,0);}
DECL_COMMAND_FLAGS(command_inventure_motion,HF_IN_SHUTDOWN,
 "inventure_motion action=%c directions=%c a=%u b=%u z=%u e=%u rate=%u pulse_us=%hu max_ms=%u stop_mask=%hu stop_value=%hu hold_ms=%u token=%u");
void command_inventure_axis_motion(uint32_t *args) {cm_command(args,1,args[13]);}
DECL_COMMAND_FLAGS(command_inventure_axis_motion,HF_IN_SHUTDOWN,
 "inventure_axis_motion action=%c directions=%c a=%u b=%u z=%u e=%u rate=%u pulse_us=%hu max_ms=%u stop_mask=%hu stop_value=%hu hold_ms=%u token=%u tool=%c");
void command_inventure_motion_driver(uint32_t *args) {
    unsigned i=args[0];if(i>3)return;
    irqstatus_t flags=irq_save();uint32_t run=cm.run,p=cm.planned[i],s=cm.sent[i];
    uint8_t d=!!(cm.direction&BIT(i));irq_restore(flags);
    sendf("inventure_motion_driver run=%u channel=%c planned=%u sent=%u direction=%c",run,i,p,s,d);
}
DECL_COMMAND_FLAGS(command_inventure_motion_driver,HF_IN_SHUTDOWN,"inventure_motion_driver_query channel=%c");
void command_inventure_factory_motion_timing(uint32_t *args) {
    (void)args;
    irqstatus_t flags=irq_save();
    uint32_t callback=cm.fast_peak_ticks,late=cm.fast_peak_late;
    irq_restore(flags);
    sendf("inventure_factory_motion_timing peak_callback_ticks=%u peak_late_ticks=%u",callback,late);
}
DECL_COMMAND_FLAGS(command_inventure_factory_motion_timing,HF_IN_SHUTDOWN,"inventure_factory_motion_timing_query");
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
        else if((fs.active && i!=6+fs.tool) || (sl.active && i!=6+sl.tool)
                || (cm.active && !(sl.active && i==6+sl.tool && cm.token==sl.token)
                    && !(fs.active && i==6+fs.tool)
                    && !(cm.fast_direct && cm.planned[3] && i==6u+cm.fast_tool))
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
        if(!reject && (sl.active || fs.active || cm.active || feeder_running[0] || feeder_running[1]
                || locks_on[0] || locks_on[1]))reject=2;
        for(unsigned i=0;i<10;i++)if(!reject && i!=6+tool && co[i].duty)reject=2;
        uint8_t valid;int16_t t=control_temp(tool<2?tool:0,now,&valid);
        if(!reject && (!valid || t<minimum_c || t>=maximum_c
                || !control_sensors_valid(control_guard_mask|31,now) || co[6+tool].reason>=3))reject=3;
        if(!reject) {
            uint32_t motion[]={1,e_direction?8:0,0,0,0,steps,rate,10,maximum,0,0,0,token};
            reject=cm_request(motion,now,0,0);
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

// Accumulate accepted-edge age in milliseconds even while no session exists.
// CLOCK_FREQ wraps every ~36s; a cold warm-up must not make an old edge fresh.
static struct {
    uint32_t last_tick,fraction,age[2],count[2],stamp[2];uint8_t initialized;
} fs_age;
static void fs_age_update(uint32_t now) {
    if(!fs_age.initialized) {
        fs_age.initialized=1;fs_age.last_tick=now;
        for(unsigned i=0;i<2;i++) {
            fs_age.age[i]=pulses[i+4] || edge_time[i+4]?(uint32_t)(now-edge_time[i+4])/MS(1):0xffffffffu;
            fs_age.count[i]=pulses[i+4];fs_age.stamp[i]=edge_time[i+4];
        }
        return;
    }
    uint32_t dt=now-fs_age.last_tick,f=fs_age.fraction+dt%MS(1);
    uint32_t elapsed=dt/MS(1)+f/MS(1);fs_age.fraction=f%MS(1);fs_age.last_tick=now;
    for(unsigned i=0;i<2;i++) {
        if(pulses[i+4]!=fs_age.count[i] || edge_time[i+4]!=fs_age.stamp[i]) {
            fs_age.age[i]=(uint32_t)(now-edge_time[i+4])/MS(1);
            fs_age.count[i]=pulses[i+4];fs_age.stamp[i]=edge_time[i+4];
        } else fs_age.age[i]=elapsed>0xffffffffu-fs_age.age[i]?0xffffffffu:fs_age.age[i]+elapsed;
    }
}
static void fs_set_mode(unsigned mode,unsigned monitor,uint32_t now) {
    struct ivt_state *s=&transport[fs.tool];
    s->monitor=monitor;
    if(mode!=s->mode) {
        ivt_mode(s,mode);
        if(mode==IVT_FORWARD || mode==IVT_REVERSE)ivt_start(s,now,mode==IVT_FORWARD,1);
        if(mode!=IVT_AUTO)ivt_tick(s,now,transport_a(fs.tool),transport_b(fs.tool));
        transport_output(fs.tool);
    }
    fs.mode=mode;fs.monitor=monitor;
}
static void fs_normal_stop(void) {
    fs.active=0;fs.reason=2;transport_live=0;transport_selected=255;
    zh_stop();control_motion_cancel();motor_test_write(GPIOE,BIT(10),0);
    for(unsigned i=0;i<2;i++)transport_stop_channel(i);
    gpio_pwm_write(feeder_clock,0);
    // The selected heater's independent short lease remains owned by the
    // host PID. Emergency/global STOP and faults always cut all outputs.
}
static void factory_external_stop(unsigned i) {
    if(fs.active && i==fs.tool)control_stop_all();
}
static void fs_guard(uint32_t now) {
    if(!fs.active)return;
    uint8_t why=0,valid;int16_t t=control_temp(fs.tool,now,&valid);
    if(!control_ready(now))why=6;
    else if(iv9_due(now,fs.deadline))why=3;
    else if(!valid || ((fs.mode!=IVT_STOP || (cm.active && cm.planned[3])) && t<fs.min_c) || t>=fs.max_c
            || !control_sensors_valid(control_guard_mask|31,now) || co[6+fs.tool].reason>=3)why=4;
    else if((door_policy_enabled()&&door_closed_mask()!=3) || !transport_live
            || transport_selected!=fs.tool || transport[fs.tool].fault
            || !transport[fs.tool].configured || cm.phase==CM_FAILED)why=5;
    if(why){control_stop_all();fs.reason=why;}
}
#include "inventure_z_home.inc"
void command_inventure_factory_session(uint32_t *args) {
    unsigned action=args[0],tool=args[1],mode=args[2],monitor=args[3],duration=args[4];
    uint32_t budget=args[5],lease=args[6],token=args[9],sequence=args[10];
    int16_t minimum=args[7],maximum=args[8];
    irqstatus_t irq=irq_save();uint32_t now=timer_read_time();uint8_t reject=0;
    fs_age_update(now);zh_guard(now);fs_guard(now);
    if(action>3)reject=1;
    else if(action==3) {
        if(!token || token!=fs.token)reject=4;
        else if(fs.active)fs_normal_stop();
    } else if(action==1 && token && token==fs.token) {
        // An old START never renews or revives the previous session.
        if(tool!=fs.tool || mode!=fs.start_mode || monitor!=fs.start_monitor
                || duration!=fs.duration_ms || budget!=fs.budget_steps || lease!=fs.lease_ms
                || minimum!=fs.min_c || maximum!=fs.max_c || sequence)reject=1;
    } else if(action==1) {
        if(tool>1 || mode>IVT_GATED || monitor>1 || !duration || duration>1500
                || !budget || budget>960000 || lease<250 || lease>1500 || !token || sequence
                || minimum<180 || minimum>230 || maximum<=minimum+5 || maximum>250)reject=1;
        if(!reject && (fs.active || sl.active || cm.active || motor_test.running || xy_seek_busy()
                || t0_heat_busy() || fan_manual_active() || transport_live || assist_pending[0] || assist_pending[1]
                || feeder_running[0] || feeder_running[1] || locks_on[0] || locks_on[1]))reject=2;
        for(unsigned i=0;i<10;i++)if(!reject && i!=6+tool && co[i].duty)reject=2;
        uint8_t valid;int16_t t=control_temp(tool<2?tool:0,now,&valid);
        if(!reject && (control_mode!=CT_SERVICE || !control_ready(now) || !auxiliary_ready
                || !valid || (mode!=IVT_STOP && t<minimum) || t>=maximum || co[6+tool].reason>=3
                || !control_sensors_valid(control_guard_mask|31,now)
                || (door_policy_enabled()&&door_closed_mask()!=3)))reject=3;
        if(!reject) {
            for(unsigned i=0;i<2;i++)transport_stop_channel(i);
            gpio_pwm_write(feeder_clock,0);
            fs.token=token;fs.sequence=0;fs.deadline=now+MS(lease);fs.lease_ms=lease;
            fs.duration_ms=duration;fs.budget_steps=budget;fs.min_c=minimum;fs.max_c=maximum;
            fs.tool=tool;fs.mode=IVT_STOP;fs.monitor=monitor;fs.reason=0;fs.active=1;
            fs.start_mode=mode;fs.start_monitor=monitor;
            transport_selected=tool;transport_live=1;
            struct ivt_state *s=&transport[tool];
            s->configured=s->cartridge=1;s->duration=MS(duration);
            s->assigned_budget=budget;s->budget=budget;s->retry=s->fault=0;
            cm.phase=CM_IDLE;cm.reason=CR_NONE;
            fs_set_mode(mode,monitor,now);
        }
    } else if(action==2) {
        uint8_t valid;int16_t t=control_temp(fs.tool,now,&valid);
        if(!fs.active || token!=fs.token || !sequence || sequence<fs.sequence)reject=4;
        else if(tool!=fs.tool || mode>IVT_GATED || monitor>1 || duration!=fs.duration_ms
                || budget!=fs.budget_steps || lease!=fs.lease_ms || minimum!=fs.min_c || maximum!=fs.max_c)reject=1;
        else if(zh_active() && (mode!=IVT_STOP || monitor!=fs.monitor))reject=2;
        else if(mode!=IVT_STOP && (!valid || t<fs.min_c || t>=fs.max_c
                || !control_sensors_valid(control_guard_mask|31,now)))reject=3;
        else if(sequence==fs.sequence) {
            if(mode!=fs.mode || monitor!=fs.monitor)reject=1;
        } else {
            fs.sequence=sequence;fs.deadline=now+MS(lease);fs_set_mode(mode,monitor,now);
        }
    }
    unsigned i=fs.tool;struct ivt_state s=transport[i];
    uint32_t remaining=fs.active?(uint32_t)(fs.deadline-now)/MS(1):0;
    uint32_t current_token=fs.token,seq=fs.sequence,p=pulses[i+4],age=fs_age.age[i];
    uint8_t active=fs.active,reason=fs.reason,a=transport_a(i),b=transport_b(i);
    irq_restore(irq);
    sendf("inventure_factory_state token=%u active=%c reason=%c reject=%c tool=%c mode=%c running=%c a=%c b=%c pulses=%u pulse_age_ms=%u starts=%u fault=%c sequence=%u remaining_ms=%u",
        current_token,active,reason,reject,i,s.mode,s.running,a,b,p,age,s.starts,s.fault,seq,remaining);
}
DECL_COMMAND_FLAGS(command_inventure_factory_session,HF_IN_SHUTDOWN,
 "inventure_factory_session action=%c tool=%c mode=%c monitor=%c duration_ms=%hu budget_steps=%u lease_ms=%hu min_c=%hi max_c=%hi token=%u sequence=%u");
