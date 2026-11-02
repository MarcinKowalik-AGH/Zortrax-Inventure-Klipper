// Zortrax Inventure V9 board services, ABI 3. Experimental, not hardware-qualified.
// SPDX-License-Identifier: GPL-3.0-or-later
#include "autoconf.h"
#include "board/irq.h"
#include "board/misc.h"
#include "command.h"
#include "gpio.h"
#include "internal.h"
#include "sched.h"
#include "inventure_v9.h"
#include "inventure_policy.h"
#include "inventure_lut.h"

#define MS(n) ((uint32_t)(CONFIG_CLOCK_FREQ / 1000) * (n))
#define BIT(n) (1u << (n))
#define SAMPLE_TICKS (CONFIG_CLOCK_FREQ / 20000u)
#define GUARD_DIV 200u
#define HEARTBEAT_TICKS MS(1000)
#define SENSOR_TICKS MS(1000)
#define TACH_TICKS MS(10000)

DECL_CONSTANT("INVENTURE_ABI", 3);
DECL_CONSTANT("INVENTURE_RC7_ADC_SNAPSHOT_ABI", 1);
DECL_CONSTANT("INVENTURE_BOARD_LAB", 1);
DECL_CONSTANT_STR("INVENTURE_BOARD", "V9-0.3.16-LAB_FILAMENT_P13");
DECL_CONSTANT_STR("RESERVE_PINS_inventure_aux", "PA2,PA3,PA8,PB7,PC5,PG11,PE6,PG5,PG6,PG7,PD11,PE9,PE11,PE13,PE14,PB12,PD14");

static volatile uint8_t armed, fault, heartbeat_live;
static uint8_t initialized, input_previous, guard_div, sample_started;
static volatile uint8_t sensor_seen;
static volatile uint32_t heartbeat_deadline;
static volatile uint32_t pulses[6], edge_time[6];
static uint16_t quiet_ticks[2];
static volatile uint16_t latest_adc[8];
static volatile uint32_t adc_time[8];
static volatile int16_t latest_temperature[8];
static uint8_t thermal_bad[8], cable_bad;
static volatile uint16_t heater_duty[4];
static uint8_t fan_watch_active[4];
static struct timer sample_timer, feeder_timer[2], lock_timer[2];
static struct gpio_out feeder_run[2], feeder_dir[2];
static volatile uint8_t feeder_running[2], locks_on[2], locks_pulling[2];
static uint32_t feeder_until[2], lock_until[2];
static struct gpio_pwm locks[2], feeder_clock;
static uint8_t auxiliary_ready;
static void trip(uint8_t reason);
static void transport_sample(uint32_t now);
static void transport_gpio(void *r,uint32_t bit,uint32_t value);
static void transport_shutdown(void);
static void transport_legacy_stop(unsigned i,unsigned duration);
#include "inventure_fans.inc"

/* Raw GPIO helper: establish output latch before changing mode. SWD untouched. */
static void raw_output(GPIO_TypeDef *g, unsigned int n, uint32_t value) {
    uint32_t shift = n*2;
    g->BSRR = BIT(n) << (value ? 0 : 16);
    g->OTYPER &= ~BIT(n);
    g->PUPDR &= ~(3u << shift);
    g->MODER = (g->MODER & ~(3u << shift)) | (1u << shift);
}

/* Called after SystemInit/peripheral clock reset and BEFORE clock_setup. */
static void raw_safe_outputs(void) {
    RCC->AHB1ENR |= 0x7f;
    (void)RCC->AHB1ENR;
    raw_output(GPIOE, 10, 0); // active-HIGH enable: OFF
    raw_output(GPIOA, 0, 0); raw_output(GPIOE, 5, 0);
    raw_output(GPIOC, 8, 1); raw_output(GPIOC, 9, 1); // chamber logical OFF
    raw_output(GPIOC, 5, 0); raw_output(GPIOG, 11, 0);
    raw_output(GPIOA, 2, 0); raw_output(GPIOA, 3, 0);
    raw_output(GPIOE, 6, 0);
    raw_output(GPIOA, 6, 0); raw_output(GPIOA, 7, 0);
    raw_output(GPIOB, 8, 0); raw_output(GPIOB, 9, 0);
    raw_output(GPIOG, 5, 1); raw_output(GPIOG, 6, 0); raw_output(GPIOG, 7, 0);
    // Keep needed cooling ON before a host connects, including after hot reset.
    // This is a deliberate LAB policy, not a claim of the stock shutdown policy.
    raw_output(GPIOB, 15, 1); raw_output(GPIOB, 14, 0);
    raw_output(GPIOC, 6, 1); raw_output(GPIOC, 7, 1);
    raw_output(GPIOA, 1, 1); raw_output(GPIOD, 13, 1);
    fan_safe();
    raw_output(GPIOD, 4, 0); // OLED RESET held at boot / local fail-safe
    raw_output(GPIOD, 0, 1); raw_output(GPIOD, 1, 1); // deassert SD/OLED CS
    // Do not guess the electrical purpose of PG4, PG13, PD3.
}

void inventure_early_init(void) {
    raw_safe_outputs();
    // Reset the used peripheral blocks, preserving GPIO latches.
    RCC->APB1RSTR |= BIT(2)|BIT(3)|BIT(14)|BIT(15)|BIT(18);
    RCC->APB1RSTR &= ~(BIT(2)|BIT(3)|BIT(14)|BIT(15)|BIT(18));
    RCC->APB2RSTR |= BIT(1)|BIT(4)|BIT(5)|BIT(8)|BIT(16);
    RCC->APB2RSTR &= ~(BIT(1)|BIT(4)|BIT(5)|BIT(8)|BIT(16));
}

uint8_t inventure_is_armed(void) { return armed && !fault; }

/* Standard GPIO/software-PWM requests remain unable to energize while unarmed. */
uint32_t inventure_filter_gpio(void *r, uint32_t bit, uint32_t value) {
    /* STEP is the frequent path. Keep E accounting exactly once; never run
     * fan classification or heater/resource branches for these four pins. */
    if((r==GPIOA && (bit==BIT(6) || bit==BIT(7))) ||
       (r==GPIOB && (bit==BIT(8) || bit==BIT(9)))) {
        if(r==GPIOB && bit==BIT(9))transport_gpio(r,bit,value);
        return inventure_is_armed()?!!value:0;
    }
    transport_gpio(r,bit,value);
    uint8_t allow = inventure_is_armed();
    int fi=fan_index(r,bit);
    if(!allow && fi>=0)value=!!(cool_mask&BIT(fi));
    fan_gpio_observe(r,bit,value);
    // Heaters must use the guarded fixed hardware-PWM path. A digital ON
    // would bypass duty/tach bookkeeping; reject it instead of silently allowing.
    if (allow && (((r == GPIOA && bit == BIT(0)) ||
                    (r == GPIOE && bit == BIT(5))) ? !!value :
                   (r == GPIOC && (bit == BIT(8) || bit == BIT(9)) && !value)))
        shutdown("Inventure heaters require hardware PWM");
    if ((r == GPIOE && (bit & (BIT(10)|BIT(5)|BIT(6)))) ||
        (r == GPIOA && (bit & (BIT(0)|BIT(2)|BIT(3)))) ||
        (r == GPIOC && (bit & BIT(5))) || (r == GPIOG && (bit & BIT(11))))
        return iv9_safe_value(allow, !!value, 0);
    if (r == GPIOC && (bit & (BIT(8)|BIT(9))))
        return iv9_safe_value(allow, !!value, 1);
    if (!allow && ((r == GPIOB && bit == BIT(15)) ||
                  (r == GPIOC && (bit == BIT(6) || bit == BIT(7))) ||
                  (r == GPIOA && bit == BIT(1)) ||
                  (r == GPIOD && bit == BIT(13))))
        return !!(cool_mask & BIT(r==GPIOA?4:r==GPIOD?5:r==GPIOB?0:bit==BIT(7)?2:3));
    return value;
}

uint32_t inventure_filter_pwm(void *reg, uint32_t value, uint32_t maximum) {
    value = iv9_clamp(value, maximum);
    int index = -1;
    if (reg == (void*)&TIM5->CCR1) index=0;
    else if (reg == (void*)&TIM9->CCR1) index=1;
    else if (reg == (void*)&TIM8->CCR4) index=2;
    else if (reg == (void*)&TIM8->CCR3) index=3;
    if (index >= 0) {
        uint32_t effective = inventure_is_armed() ? value : (index >= 2 ? maximum : 0);
        uint32_t duty = index >= 2 ? maximum-effective : effective;
        heater_duty[index] = duty;
        return effective;
    }
    if(reg==(void*)&TIM5->CCR2 || reg==(void*)&TIM4->CCR2) {
        uint32_t duty=inventure_is_armed()?value:
            ((cool_mask&BIT(reg==(void*)&TIM5->CCR2?4:5))?maximum:0);
        fan_set_duty(reg==(void*)&TIM5->CCR2?4:5,(duty*255+maximum/2)/maximum);
    }
    if (!inventure_is_armed()) {
        if (reg == (void*)&TIM5->CCR2 || reg == (void*)&TIM4->CCR2)
            return (cool_mask&BIT(reg==(void*)&TIM5->CCR2?4:5))?maximum:0;
        if (reg == (void*)&TIM5->CCR3 || reg == (void*)&TIM5->CCR4 ||
            reg == (void*)&TIM9->CCR2) return 0;
    }
    return value;
}

static int16_t lut_temperature(uint16_t adc) {
    unsigned lo=0, hi=ARRAY_SIZE(iv9_lut);
    while (lo < hi) {
        unsigned mid=(lo+hi)/2;
        if (iv9_lut[mid].adc < adc) lo=mid+1; else hi=mid;
    }
    return lo < ARRAY_SIZE(iv9_lut) ? iv9_lut[lo].temperature : -46;
}

#include "inventure_cooling.inc"

void inventure_adc_observe(void *adc, uint8_t ch, uint16_t value) {
    int i=-1;
    if (adc == ADC1) {
        if (ch==10) i=0; else if(ch==11) i=2; else if(ch==12) i=3;
        else if(ch==4) i=4; else if(ch==14) i=7;
    } else if (adc == ADC3) {
        if (ch==6) i=1; else if(ch==9) i=5; else if(ch==14) i=6;
    }
    if (i<0) return;
    if(cool_local_read) {cooling_local_observe(i,value);return;}
    irqstatus_t saved_irq=irq_save();
    latest_adc[i]=value;
    latest_temperature[i] = i<2 ? (int16_t)(((uint32_t)value*660u)/4095u) : lut_temperature(value);
    adc_time[i]=timer_read_time();
    sensor_seen |= BIT(i);
    cool_native_seen |= BIT(i);
    irq_restore(saved_irq);
}

static uint8_t read_pulses(void) {
    uint32_t e=GPIOE->IDR, b=GPIOB->IDR, d=GPIOD->IDR;
    return (!!(e&BIT(14))) | ((!!(e&BIT(13)))<<1) |
           ((!!(e&BIT(9)))<<2) | ((!!(e&BIT(11)))<<3) |
           ((!!(b&BIT(12)))<<4) | ((!!(d&BIT(14)))<<5);
}

static void force_safe(void) {
    armed=0;
    cool_mask=COOL_MASK;cool_applied=255;cool_policy_started=0;
    transport_shutdown();
    // Use GPIO output mode so stale timer compare/preload cannot energize heaters.
    raw_safe_outputs();
    inventure_panel_shutdown();
    if (fault) inventure_fault_led();
    for (unsigned i=0; i<2; i++) {
        feeder_running[i]=0; locks_on[i]=0; locks_pulling[i]=0;
    }
    for (unsigned i=0; i<4; i++) heater_duty[i]=0;
}

static void trip(uint8_t reason) {
    if (!fault) fault=reason;
    force_safe();
    shutdown("Inventure V9 local safety fault");
}

static uint8_t sensors_valid(uint32_t now) {
    const int16_t ceiling[8]={310,310,150,150,150,100,100,100};
    if (sensor_seen != 255) return 0;
    for(unsigned i=0;i<8;i++)
        if(iv9_due(now,adc_time[i]+SENSOR_TICKS) || latest_temperature[i]<=-30 ||
           latest_temperature[i]>=ceiling[i]) return 0;
    return 1;
}

static void guard_check(uint32_t observed_now) {
    (void)observed_now;
    /* M300 R14.4-inspired fix, V9-specific: capture timer and async ADC
     * timestamps under the SAME IRQ lock. Without this, a fresh ADC IRQ
     * between timer_read_time() and adc_time[] could falsely appear stale
     * across an unsigned wrap. Keep the critical section short: only copy.
     */
    uint32_t now, last_adc[8], last_edge[6], deadline;
    int16_t temp[8];
    uint16_t duty[4];
    uint8_t live;
    irqstatus_t flags=irq_save();
    now=timer_read_time();deadline=heartbeat_deadline;live=heartbeat_live;
    for(unsigned i=0;i<8;i++){last_adc[i]=adc_time[i];temp[i]=latest_temperature[i];}
    for(unsigned i=0;i<6;i++)last_edge[i]=edge_time[i];
    for(unsigned i=0;i<4;i++)duty[i]=heater_duty[i];
    irq_restore(flags);
    if (live && iv9_due(now,deadline)) {heartbeat_live=0;live=0;}
    if (!inventure_is_armed()) return;
    if (!live) trip(1);
    if (GPIOD->IDR & BIT(11)) {
        if (++cable_bad >= 6) trip(2); // LAB: 6 x 10ms, not stock 6 x 101ms
    } else cable_bad=0;
    const int16_t ceiling[8]={310,310,150,150,150,100,100,100};
    for(unsigned i=0;i<8;i++) {
        if(iv9_due(now,last_adc[i]+SENSOR_TICKS)) trip(3);
        if(temp[i]<=-30 || temp[i]>=ceiling[i]) {
            if(++thermal_bad[i]>=3) trip(4);
        } else thermal_bad[i]=0;
    }
    // Watch required cooling through PWM zero crossings and while still hot.
    // Refresh deadlines on first requirement; never compare an indefinitely old
    // heater-start timestamp across multiple 32-bit clock wraps.
    uint8_t required[4]={
        duty[0] || duty[1] || temp[0]>40 || temp[1]>40,
        0, duty[2] || temp[2]>40,
        duty[3] || temp[3]>40};
    for(unsigned fan=0;fan<4;fan++) {
        if(!required[fan]) {fan_watch_active[fan]=0;continue;}
        if(!fan_watch_active[fan]) {
            fan_watch_active[fan]=1;edge_time[fan]=now;
            last_edge[fan]=now; // first requirement starts a FULL grace period
        }
        if(iv9_due(now,last_edge[fan]+TACH_TICKS)) {
            fan_required_mask |= BIT(fan);fan_fault_mask |= BIT(fan);trip(5);
        }
    }

}

static uint_fast8_t feeder_end(struct timer *t) {
    unsigned i = t == &feeder_timer[0] ? 0:1;
    gpio_out_write(feeder_run[i],0); feeder_running[i]=0;
    return SF_DONE;
}
static uint_fast8_t lock_hold(struct timer *t) {
    unsigned i = t == &lock_timer[0] ? 0:1;
    if(locks_on[i] && inventure_is_armed()) gpio_pwm_write(locks[i],60);
    else gpio_pwm_write(locks[i],0);
    locks_pulling[i]=0;
    return SF_DONE;
}

static uint_fast8_t sample_event(struct timer *t) {
    uint32_t now=timer_read_time();
    uint8_t value=read_pulses(), changed=value^input_previous;
    uint8_t rising=changed & value & 15u;
    while(rising) {
        unsigned i=__builtin_ctz((unsigned)rising);
        pulses[i]++;edge_time[i]=now;rising&=rising-1u;
    }
    for(unsigned i=0;i<2;i++) {
        if(quiet_ticks[i]<401) quiet_ticks[i]++;
        if(changed & BIT(i+4)) {
            if(quiet_ticks[i]>=401) {pulses[i+4]++;edge_time[i+4]=now;}
            quiet_ticks[i]=0;
        }
    }
    fan_sample(changed & value,now);
    input_previous=value;
    if(++guard_div >= GUARD_DIV) { guard_div=0; inventure_inputs_poll(); guard_check(now); transport_sample(now); }
    t->waketime += SAMPLE_TICKS;
    return SF_RESCHEDULE;
}

void inventure_board_init(void) {
    gpio_in_setup(GPIO('D',11),0); // r0323: no internal pull; LOW healthy
    const uint8_t pins[6]={GPIO('E',14),GPIO('E',13),GPIO('E',9),GPIO('E',11),GPIO('B',12),GPIO('D',14)};
    for(unsigned i=0;i<6;i++) gpio_in_setup(pins[i], i < 4 ? -1 : 1);
    // TIM1 tach pins had pull-down; filament pulse inputs had pull-up.
    inventure_inputs_init();
    input_previous=read_pulses();
    sample_timer.func=sample_event;
    initialized=1;
}
DECL_INIT(inventure_board_init);
void inventure_board_start_task(void) {
    if(sample_started || !initialized || fault)return;
    irqstatus_t flags=irq_save();
    /* All DECL_INIT callbacks, including counter initialization, have ended. */
    sample_timer.waketime=timer_read_time()+SAMPLE_TICKS;
    sched_add_timer(&sample_timer);sample_started=1;
    irq_restore(flags);
}
DECL_TASK(inventure_board_start_task);

void inventure_board_shutdown(void) {
    fault=fault?fault:255;force_safe();
    /* sched_timer_reset removed the soft-service timer. Native task wakeup
     * remains periodic and restarts it without touching motion timers. */
    cool_timer_started=0;
}
DECL_SHUTDOWN(inventure_board_shutdown);

void command_inventure_heartbeat(uint32_t *args) {
    (void)args;
    irqstatus_t flag=irq_save();
    heartbeat_deadline=timer_read_time()+HEARTBEAT_TICKS; heartbeat_live=1;
    irq_restore(flag);
}
DECL_COMMAND(command_inventure_heartbeat,"inventure_heartbeat");

void command_inventure_arm(uint32_t *args) {
    irqstatus_t saved_irq=irq_save();
    uint32_t now=timer_read_time();
    uint8_t ok = args[0] == 0x56394c42 && initialized && !fault && heartbeat_live &&
        sensors_valid(now) && !(GPIOD->IDR&BIT(11)) &&
        !iv9_due(now,heartbeat_deadline);
    if(ok) {armed=1;cooling_native_handover();}
    irq_restore(saved_irq);
    sendf("inventure_arm_result armed=%c fault=%c",armed,fault);
}
DECL_COMMAND(command_inventure_arm,"inventure_arm acknowledge=%u");

void command_inventure_status(uint32_t *args) {
    (void)args;
    sendf("inventure_status armed=%c fault=%c seen=%c cable=%c p0=%u p1=%u p2=%u p3=%u p4=%u p5=%u",
          armed,fault,sensor_seen,!!(GPIOD->IDR&BIT(11)),
          pulses[0],pulses[1],pulses[2],pulses[3],pulses[4],pulses[5]);
}
DECL_COMMAND_FLAGS(command_inventure_status, HF_IN_SHUTDOWN, "inventure_query");

void command_inventure_aux_config(uint32_t *args) {
    (void)args;
    if(auxiliary_ready) shutdown("Inventure auxiliary already configured");
    feeder_run[0]=gpio_out_setup(GPIO('C',5),0);
    feeder_run[1]=gpio_out_setup(GPIO('G',11),0);
    feeder_dir[0]=gpio_out_setup(GPIO('A',8),0);
    feeder_dir[1]=gpio_out_setup(GPIO('B',7),0);
    locks[0]=gpio_pwm_setup(GPIO('A',3),5632,0);
    locks[1]=gpio_pwm_setup(GPIO('A',2),5632,0);
    feeder_clock=gpio_pwm_setup(GPIO('E',6),12800,0);
    for(unsigned i=0;i<2;i++) {feeder_timer[i].func=feeder_end;lock_timer[i].func=lock_hold;}
    auxiliary_ready=1;
}
DECL_COMMAND(command_inventure_aux_config,"inventure_aux_config");

void command_inventure_feeder(uint32_t *args) {
    unsigned i=args[0], direction=args[1], duration=args[2];
    if(i>1 || direction>1 || duration>1500 || !auxiliary_ready)
        shutdown("Inventure invalid feeder request");
    if(duration && !inventure_is_armed()) shutdown("Inventure feeder not armed");
    transport_legacy_stop(i,duration);
    irqstatus_t flag=irq_save();
    gpio_out_write(feeder_run[i],0); // stop before changing DIR
    if(feeder_running[i]) sched_del_timer(&feeder_timer[i]);
    feeder_running[i]=0;
    if(!duration) {irq_restore(flag);return;}
    gpio_out_write(feeder_dir[i],direction);
    gpio_pwm_write(feeder_clock,1); // shared timebase, fixed 1/256 duty
    feeder_until[i]=timer_read_time()+MS(duration);
    feeder_timer[i].waketime=feeder_until[i];
    feeder_running[i]=1;
    sched_add_timer(&feeder_timer[i]);
    gpio_out_write(feeder_run[i],1);
    irq_restore(flag);
}
DECL_COMMAND(command_inventure_feeder,"inventure_feeder channel=%c direction=%c duration_ms=%hu");

void command_inventure_lock(uint32_t *args) {
    unsigned i=args[0], on=args[1];
    if(i>1 || on>1 || !auxiliary_ready) shutdown("Inventure invalid lock request");
    if(on && !inventure_is_armed()) shutdown("Inventure lock not armed");
    irqstatus_t flag=irq_save();
    if(!on) {
        gpio_pwm_write(locks[i],0);
        if(locks_pulling[i]) sched_del_timer(&lock_timer[i]);
        locks_on[i]=locks_pulling[i]=0;
        irq_restore(flag);return;
    }
    if(locks_on[i]) {irq_restore(flag);return;} // duplicate never extends pull
    locks_on[i]=locks_pulling[i]=1;
    lock_until[i]=timer_read_time()+MS(100);
    lock_timer[i].waketime=lock_until[i];
    sched_add_timer(&lock_timer[i]);
    gpio_pwm_write(locks[i],256);
    irq_restore(flag);
}
DECL_COMMAND(command_inventure_lock,"inventure_lock channel=%c value=%c");
#include "inventure_transport.inc"
