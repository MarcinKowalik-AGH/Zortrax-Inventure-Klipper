#line 4000 "inventure_cooling.inc"
/* RC8 F207-only local cooling. Before native ADC configuration, use the SAME
 * bounded ADC driver and fixed eight sensor pins. The first native setup
 * permanently ends local acquisition; native analog timers continue after
 * shutdown. Local readings never authorize ARM or overwrite native samples.
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
DECL_CONSTANT("INVENTURE_LOCAL_COOLING_ABI",1);
DECL_CONSTANT("INVENTURE_LOCAL_COOLING_ON_C",50);
DECL_CONSTANT("INVENTURE_LOCAL_COOLING_OFF_C",45);
#define COOL_MASK 0x3du /* all five cooling fans; model fan (bit 1) OFF */
#define COOL_FRESH MS(1000)
enum {COOL_UNKNOWN,COOL_NATIVE,COOL_LOCAL,COOL_EMERGENCY};
static uint8_t cool_mode, cool_reason, cool_hot=1, cool_valid;
static uint8_t cool_native_claimed, cool_local_setup, cool_local_read;
static uint8_t cool_native_seen, cool_local_seen, cool_adc_ready, cool_pending;
static uint8_t cool_channel, cool_timer_started, cool_applied=255;
static uint16_t cool_local_raw[8];
static uint32_t cool_local_time[8],cool_conversion_start,cool_cycle;
static uint32_t cool_adc_failures,cool_gpio_updates;
static uint32_t cool_policy_clock;
static uint8_t cool_policy_started;
static int16_t cool_maximum;
static struct gpio_adc cool_adc[8];
static struct timer cool_timer;
static const uint8_t cool_pins[8]={
    GPIO('C',0),GPIO('F',8),GPIO('C',1),GPIO('C',2),
    GPIO('A',4),GPIO('F',3),GPIO('F',4),GPIO('C',4)};

void inventure_adc_native_begin(void) {
    if(cool_local_setup || cool_native_claimed)return;
    irqstatus_t flags=irq_save();
    /* This runs before any native analog timer can be registered. Abort only
     * conversions owned by this local reader; preserve sample times/clocks. */
    if(cool_adc_ready) {
        ADC1->CR2=1u;ADC1->SR=0;
        ADC3->CR2=1u;ADC3->SR=0;
    }
    cool_native_claimed=1;cool_local_seen=0;cool_pending=0;cool_policy_started=0;
    irq_restore(flags);
}

static void cooling_local_observe(unsigned i,uint16_t raw) {
    cool_local_raw[i]=raw;cool_local_time[i]=cool_conversion_start;
    cool_local_seen|=BIT(i);
}

static uint8_t cooling_critical_fault(void) {
    return fault && fault!=1 && fault!=255;
}

static void cooling_policy(void) {
    uint16_t raw[8];uint32_t stamp[8],now;
    uint8_t seen,source;
    irqstatus_t flags=irq_save();
    now=timer_read_time();source=cool_native_claimed;
    seen=source?cool_native_seen:cool_local_seen;
    for(unsigned i=0;i<8;i++) {
        raw[i]=source?latest_adc[i]:cool_local_raw[i];
        stamp[i]=source?adc_time[i]:cool_local_time[i];
        if((uint32_t)(now-stamp[i])>=COOL_FRESH)seen&=~BIT(i);
    }
    if(source)cool_native_seen=seen;else cool_local_seen=seen;
    irq_restore(flags);
    cool_valid=seen==255;cool_maximum=-32768;
    uint8_t hot=0,cold=1;
    static const int16_t ceiling[8]={310,310,150,150,150,100,100,100};
    for(unsigned i=0;i<8 && cool_valid;i++) {
        int16_t temp=i<2?(int16_t)((uint32_t)raw[i]*660u/4095u):lut_temperature(raw[i]);
        if(!raw[i] || raw[i]>=4095 || temp<=-30 || temp>=ceiling[i])cool_valid=0;
        if(temp>cool_maximum)cool_maximum=temp;
        if(i<2) {
            if((uint32_t)raw[i]*660u>=50u*4095u)hot=1;
            if((uint32_t)raw[i]*660u>45u*4095u)cold=0;
        } else {if(temp>=50)hot=1;if(temp>45)cold=0;}
    }
    if(inventure_is_armed()) {
        cool_mode=COOL_NATIVE;cool_reason=0;return;
    }
    cool_mask=COOL_MASK;
    if(cooling_critical_fault()) {cool_mode=COOL_EMERGENCY;cool_reason=3;return;}
    if(!cool_valid) {cool_mode=COOL_UNKNOWN;cool_reason=seen==255?2:1;return;}
    if(hot)cool_hot=1;
    else if(cold)cool_hot=0;
    cool_mask=cool_hot?COOL_MASK:0;
    cool_mode=COOL_LOCAL;cool_reason=0;
}

static void cooling_apply(void) {
    if(inventure_is_armed()) {cool_applied=255;return;}
    irqstatus_t flags=irq_save();
    uint8_t mask=cool_mask;
    if(mask!=cool_applied) {
        raw_output(GPIOB,15,!!(mask&1));raw_output(GPIOB,14,0);
        raw_output(GPIOC,7,!!(mask&4));raw_output(GPIOC,6,!!(mask&8));
        raw_output(GPIOA,1,!!(mask&16));raw_output(GPIOD,13,!!(mask&32));
        fan_high=mask&15;
        for(unsigned i=0;i<6;i++)fan_set_duty(i,(mask&BIT(i))?255:0);
        cool_applied=mask;cool_gpio_updates++;
    }
    irq_restore(flags);
}

static void cooling_native_handover(void) {
    /* Local fail-safe GPIO mode must not strand a configured hardware fan
     * when the qualified host arms again after an unarmed cold period. */
    if((TIM5->CR1&1u) && (TIM5->CCER&(1u<<4)))
        gpio_peripheral(GPIO('A',1),GPIO_FUNCTION(2),0);
    if((TIM4->CR1&1u) && (TIM4->CCER&(1u<<4)))
        gpio_peripheral(GPIO('D',13),GPIO_FUNCTION(2),0);
    cool_applied=255;
}

static uint_fast8_t cooling_event(struct timer *timer) {
    /* Only a wakeup in IRQ context. Expensive ADC/LUT/SPI work stays in tasks.
     * These soft service deadlines never modify the 20 kHz edge sampler. */
    sched_wake_tasks();
    timer->waketime=timer_read_time()+MS(cool_pending || inventure_panel_needs_service()?2:20);
    return SF_RESCHEDULE;
}

void inventure_local_cooling_task(void) {
    uint32_t now=timer_read_time();
    if(!cool_timer_started) {
        irqstatus_t flags=irq_save();
        cool_timer.func=cooling_event;cool_timer.waketime=now+MS(2);
        sched_add_timer(&cool_timer);cool_timer_started=1;
        irq_restore(flags);
    }
    if(!cool_native_claimed) {
        if(!cool_adc_ready) {
            cool_local_setup=1;
            for(unsigned i=0;i<8;i++)cool_adc[i]=gpio_adc_setup(cool_pins[i]);
            cool_local_setup=0;cool_adc_ready=1;cool_cycle=now;
        }
        if(cool_pending) {
            if((uint32_t)(now-cool_conversion_start)>=MS(100)) {
                ADC1->CR2=1u;ADC1->SR=0;ADC3->CR2=1u;ADC3->SR=0;
                cool_local_seen=0;cool_pending=0;cool_channel=0;
                cool_adc_failures++;cool_cycle=now;cool_policy_started=0;
            } else if(!gpio_adc_sample(cool_adc[cool_channel])) {
                cool_local_read=1;(void)gpio_adc_read(cool_adc[cool_channel]);cool_local_read=0;
                cool_pending=0;
                if(++cool_channel==8) {cool_channel=0;cool_cycle=now;}
            }
        }
        if(!cool_pending && (cool_channel || (uint32_t)(now-cool_cycle)>=MS(100))) {
            cool_conversion_start=now;
            (void)gpio_adc_sample(cool_adc[cool_channel]);cool_pending=1;
        }
    }
    if(!cool_policy_started || (uint32_t)(now-cool_policy_clock)>=MS(20)) {
        cool_policy_started=1;cool_policy_clock=now;
        cooling_policy();cooling_apply();
    }
}
DECL_TASK(inventure_local_cooling_task);

void command_inventure_cooling_query(uint32_t *args) {
    (void)args;irqstatus_t flags=irq_save();
    uint8_t mode=cool_mode,mask=cool_mask,valid=cool_valid,reason=cool_reason,owner=cool_native_claimed;
    uint32_t clock=timer_read_time(),failures=cool_adc_failures,updates=cool_gpio_updates;
    int16_t maximum=cool_maximum;
    irq_restore(flags);
    sendf("inventure_cooling_state clock=%u mode=%c reason=%c valid=%c mask=%c max_temp=%hi native_owner=%c adc_failures=%u gpio_updates=%u",
          clock,mode,reason,valid,mask,maximum,owner,failures,updates);
}
DECL_COMMAND_FLAGS(command_inventure_cooling_query,HF_IN_SHUTDOWN,"inventure_cooling_query");
