// SPDX-License-Identifier: GPL-3.0-or-later
// Included in inventure_board.c: scheduler sampled rising edges, NOT RPM.
// No TIM1 owner. Native PWM scheduler feeds actual on_duration at load time.
DECL_CONSTANT("INVENTURE_FANS_ABI", 1);
DECL_CONSTANT("INVENTURE_FAN_BATCH_ABI", 1);
static volatile uint8_t fan_pwm[6], fan_high, fan_fault_mask, fan_required_mask, fan_mixed;
static volatile uint32_t fan_edges[4], fan_window_edges[4], fan_normalized[4];
static volatile uint32_t fan_bad[6], fan_peak[6], fan_sequence, fan_window_clock;
static uint16_t fan_sample_ticks, fan_detector_ticks;
static uint8_t fan_window_mixed, fan_window_duty[4];
static volatile uint8_t cool_mask=0x3d;

static int fan_index(void *r, uint32_t bit) {
    if (r==GPIOB && bit==BIT(15)) return 0;
    if (r==GPIOB && bit==BIT(14)) return 1;
    if (r==GPIOC && bit==BIT(7)) return 2;
    if (r==GPIOC && bit==BIT(6)) return 3;
    return -1;
}
static void fan_set_duty(unsigned i, uint8_t value) {
    if (fan_pwm[i]!=value) fan_mixed |= BIT(i);
    fan_pwm[i]=value;
}
void inventure_fan_observe(void *r, uint32_t bit, uint32_t on, uint32_t cycle) {
    int i=fan_index(r,bit);
    if(i<0) return;
    uint8_t value= !cycle || on>=cycle ? (on ? 255:0) :
        ((uint64_t)on*255+cycle/2)/cycle;
    if(!inventure_is_armed())value=(cool_mask&BIT(i))?255:0;
    fan_set_duty(i,value);
}
static void fan_gpio_observe(void *r,uint32_t bit,uint32_t value) {
    int i=fan_index(r,bit);
    if(i<0) return;
    if(value) fan_high |= BIT(i); else fan_high &= ~BIT(i);
}
static void fan_safe(void) {
    fan_high=0x0d;
    for(unsigned i=0;i<6;i++) fan_set_duty(i,i==1?0:255);
}
static void fan_evaluate(void) {
    if(!inventure_is_armed()) return;
    for(unsigned i=0;i<4;i++) {
        if(fan_pwm[i]>=96 && !fan_normalized[i]) {
            fan_bad[i]++;
            if(fan_bad[i]>fan_peak[i]) fan_peak[i]=fan_bad[i];
            if(fan_bad[i]>99) fan_fault_mask |= BIT(i);
        } else fan_bad[i]=0;
    }
    if(fan_fault_mask & 0x0d) trip(5);
}
static void fan_sample(uint8_t rising,uint32_t now) {
    uint8_t observed=rising & fan_high & 15u;
    while(observed) {
        unsigned i=__builtin_ctz((unsigned)observed);
        fan_edges[i]++;observed&=observed-1u;
    }
    if(++fan_sample_ticks>=20000) {
        fan_sample_ticks=0;
        for(unsigned i=0;i<4;i++) {
            uint32_t count=fan_edges[i]; fan_window_edges[i]=count; fan_edges[i]=0;
            fan_window_duty[i]=fan_pwm[i];
            fan_normalized[i]=fan_pwm[i] ? count*255/fan_pwm[i]/4 : 0;
        }
        fan_window_mixed=fan_mixed; fan_mixed=0;
        fan_window_clock=now; fan_sequence++;
    }
    if(++fan_detector_ticks>=2020) {fan_detector_ticks=0;fan_evaluate();}
}
void command_inventure_fan_query(uint32_t *args) {
    (void)args;
    // Copy atomically, then serialize outside the critical section.
    struct {uint32_t edges,norm,bad,peak;uint8_t duty,window_duty;} copy[6];
    irqstatus_t flag=irq_save();
    uint32_t clock=timer_read_time(), seq=fan_sequence, window=fan_window_clock;
    uint8_t mask=fan_fault_mask, mixed=fan_window_mixed, required=fan_required_mask;
    for(unsigned i=0;i<6;i++) {
        copy[i].edges=i<4?fan_window_edges[i]:0;
        copy[i].norm=i<4?fan_normalized[i]:0;
        copy[i].window_duty=i<4?fan_window_duty[i]:0;
        copy[i].bad=fan_bad[i];copy[i].peak=fan_peak[i];copy[i].duty=fan_pwm[i];
    }
    irq_restore(flag);
    for(unsigned i=0;i<6;i++)
        sendf("inventure_fan_status clock=%u window_clock=%u sequence=%u channel=%c duty=%c window_duty=%c edges=%u normalized=%u bad=%u peak=%u fault_mask=%c mixed=%c required_mask=%c",
              clock,window,seq,i,copy[i].duty,copy[i].window_duty,copy[i].edges,copy[i].norm,
              copy[i].bad,copy[i].peak,mask,mixed,required);
}
DECL_COMMAND_FLAGS(command_inventure_fan_query,HF_IN_SHUTDOWN,"inventure_fan_query");

// Added P06 readout: two channels per tick rather than six in one ISR/task
// burst. The legacy all-channel query remains for historical/debug clients;
// production paired RC3 host uses only this batched form.
void command_inventure_fan_query_batch(uint32_t *args) {
    unsigned group=args[0];
    if(group>2) {return;}
    unsigned base=group*2;
    struct {uint32_t edges,norm,bad,peak;uint8_t duty,window_duty;} copy[2];
    irqstatus_t flag=irq_save();
    uint32_t clock=timer_read_time(),seq=fan_sequence,window=fan_window_clock;
    uint8_t mask=fan_fault_mask,mixed=fan_window_mixed,required=fan_required_mask;
    for(unsigned n=0;n<2;n++) {
        unsigned i=base+n;
        copy[n].edges=i<4?fan_window_edges[i]:0;
        copy[n].norm=i<4?fan_normalized[i]:0;
        copy[n].window_duty=i<4?fan_window_duty[i]:0;
        copy[n].bad=fan_bad[i];copy[n].peak=fan_peak[i];copy[n].duty=fan_pwm[i];
    }
    irq_restore(flag);
    for(unsigned n=0;n<2;n++) {
        unsigned i=base+n;
        sendf("inventure_fan_status clock=%u window_clock=%u sequence=%u channel=%c duty=%c window_duty=%c edges=%u normalized=%u bad=%u peak=%u fault_mask=%c mixed=%c required_mask=%c",
              clock,window,seq,i,copy[n].duty,copy[n].window_duty,copy[n].edges,
              copy[n].norm,copy[n].bad,copy[n].peak,mask,mixed,required);
    }
}
DECL_COMMAND_FLAGS(command_inventure_fan_query_batch,HF_IN_SHUTDOWN,
                   "inventure_fan_query_batch group=%c");
