// SPDX-License-Identifier: GPL-3.0-or-later
// Included in the sole board owner; GPIO, ADC and TIM9 are not allocated again.
#include "inventure_transport.h"
#line 2000 "inventure_transport.inc"
DECL_CONSTANT("INVENTURE_TRANSPORT_ABI",1);
static struct ivt_state transport[2];
static struct timer assist_timer[2];
static uint64_t assist_remaining[2];
static uint32_t assist_slice[2],transport_until;
static uint8_t assist_pending[2],assist_started[2],transport_live,transport_selected=255;
static uint8_t transport_e_high,transport_e_dir;
static uint8_t transport_a(unsigned i) {return !!(GPIOD->IDR & BIT(i?15:10));}
static uint8_t transport_b(unsigned i) {return !!((i?GPIOG:GPIOF)->IDR & BIT(i?10:1));}
static void transport_output(unsigned i) {
    struct ivt_state *s=&transport[i];
    gpio_out_write(feeder_run[i],0);
    gpio_out_write(feeder_dir[i],s->direction);
    if(s->running && inventure_is_armed()) {gpio_pwm_write(feeder_clock,1);gpio_out_write(feeder_run[i],1);}
}
static void transport_stop_channel(unsigned i) {
    if(assist_pending[i])sched_del_timer(&assist_timer[i]);
    assist_pending[i]=assist_started[i]=0;assist_remaining[i]=0;
    ivt_mode(&transport[i],IVT_STOP);transport_output(i);
}
static void transport_shutdown(void) {
    transport_live=0;transport_selected=255;transport_e_high=0;
    for(unsigned i=0;i<2;i++) {
        // Scheduler shutdown removes pending timers. Do not delete twice here.
        assist_pending[i]=assist_started[i]=0;assist_remaining[i]=0;
        ivt_mode(&transport[i],IVT_STOP);
    }
}
static uint_fast8_t transport_assist_event(struct timer *t) {
    unsigned i=t==&assist_timer[0]?0:1;struct ivt_state *s=&transport[i];
    uint32_t now=timer_read_time();
    if(!transport_live || !inventure_is_armed() || ivt_due(now,transport_until)) {
        assist_pending[i]=assist_started[i]=0;assist_remaining[i]=0;ivt_mode(s,IVT_STOP);transport_output(i);return SF_DONE;
    }
    if(assist_started[i])assist_remaining[i]-=assist_slice[i];
    if(assist_started[i] && !assist_remaining[i]) {
        assist_pending[i]=assist_started[i]=0;ivt_mode(s,s->cartridge?IVT_AUTO:IVT_STOP);transport_output(i);return SF_DONE;
    }
    assist_started[i]=1;ivt_mode(s,IVT_ASSIST);ivt_tick(s,now,transport_a(i),transport_b(i));transport_output(i);
    assist_slice[i]=assist_remaining[i]>CONFIG_CLOCK_FREQ?CONFIG_CLOCK_FREQ:(uint32_t)assist_remaining[i];
    t->waketime+=assist_slice[i];return SF_RESCHEDULE;
}
static void transport_sample(uint32_t now) {
    if(!transport_live)return;
    if(!inventure_is_armed() || ivt_due(now,transport_until)) {
        transport_live=0;
        for(unsigned i=0;i<2;i++) {transport_stop_channel(i);transport[i].fault=4;}
        return;
    }
    for(unsigned i=0;i<2;i++) {
        struct ivt_state *s=&transport[i];uint8_t run=s->running,dir=s->direction;
        ivt_tick(s,now,transport_a(i),transport_b(i));
        if(run!=s->running || dir!=s->direction)transport_output(i);
    }
}
static void transport_gpio(void *r,uint32_t bit,uint32_t value) {
    if(r==GPIOE && bit==BIT(8))transport_e_dir=!!value;
    if(r==GPIOB && bit==BIT(9)) {
        uint8_t high=!!value;
        if(high && !transport_e_high && transport_live && inventure_is_armed() && transport_selected<2)
            ivt_e_step(&transport[transport_selected],transport_e_dir==transport_selected);
        transport_e_high=high;
    }
}
static void transport_legacy_stop(unsigned i,unsigned duration) {
    if(!duration)transport_stop_channel(i);
    else if(transport_live)shutdown("P13 owns feeder requests");
}
void command_inventure_transport_profile(uint32_t *args) {
    unsigned i=args[0],cart=args[1],ms=args[2],budget=args[3];
    if(i>1 || cart>1 || !ms || ms>1500 || !budget || budget>960000 || !auxiliary_ready)
        shutdown("P13 invalid transport profile");
    irqstatus_t irq=irq_save();transport_stop_channel(i);
    struct ivt_state *s=&transport[i];s->configured=1;s->cartridge=cart;s->duration=MS(ms);
    s->assigned_budget=budget;s->budget=budget;s->retry=s->fault=0;
    assist_timer[i].func=transport_assist_event;irq_restore(irq);
}
DECL_COMMAND(command_inventure_transport_profile,"inventure_transport_profile channel=%c cartridge=%c duration_ms=%hu budget_steps=%u");
void command_inventure_transport_lease(uint32_t *args) {
    if(args[0]!=0x5031334c || !inventure_is_armed())shutdown("P13 transport not armed");
    irqstatus_t irq=irq_save();transport_until=timer_read_time()+MS(500);transport_live=1;irq_restore(irq);
}
DECL_COMMAND(command_inventure_transport_lease,"inventure_transport_lease acknowledge=%u");
void command_inventure_transport_mode(uint32_t *args) {
    unsigned i=args[0],mode=args[1],monitor=args[2];
    if(i>1 || mode>IVT_GATED || monitor>1 || !transport[i].configured)shutdown("P13 invalid transport mode");
    if(mode && (!transport_live || !inventure_is_armed() || transport[i].fault))shutdown("P13 inactive transport mode");
    irqstatus_t irq=irq_save();transport_stop_channel(i);struct ivt_state *s=&transport[i];
    ivt_mode(s,mode);s->monitor=monitor;
    if(mode==IVT_FORWARD || mode==IVT_REVERSE)ivt_start(s,timer_read_time(),mode==IVT_FORWARD,1);
    // Setting AUTO stops first; the next periodic sample owns the restart.
    if(mode!=IVT_AUTO)ivt_tick(s,timer_read_time(),transport_a(i),transport_b(i));
    transport_output(i);irq_restore(irq);
}
DECL_COMMAND(command_inventure_transport_mode,"inventure_transport_mode channel=%c mode=%c monitor=%c");
void command_inventure_transport_select(uint32_t *args) {
    if(args[0]>1)shutdown("P13 invalid selected E channel");transport_selected=args[0];
}
DECL_COMMAND(command_inventure_transport_select,"inventure_transport_select channel=%c");
void command_inventure_transport_assist(uint32_t *args) {
    unsigned i=args[0];uint32_t start=args[1];uint64_t duration=((uint64_t)args[2]<<32)|args[3];
    if(i>1 || !transport[i].configured || !transport[i].cartridge || !transport_live || !inventure_is_armed()
       || transport[i].fault || assist_pending[i] || !duration || duration>(uint64_t)CONFIG_CLOCK_FREQ*3600
       || (int32_t)(start-timer_read_time())<=0)shutdown("P13 invalid scheduled assist");
    irqstatus_t irq=irq_save();transport_stop_channel(i);assist_remaining[i]=duration;
    assist_pending[i]=1;assist_started[i]=0;assist_timer[i].waketime=start;sched_add_timer(&assist_timer[i]);irq_restore(irq);
}
DECL_COMMAND(command_inventure_transport_assist,"inventure_transport_assist channel=%c clock=%u duration_hi=%u duration_lo=%u");
void command_inventure_transport_query(uint32_t *args) {
    (void)args;
    for(unsigned i=0;i<2;i++) {
        irqstatus_t irq=irq_save();struct ivt_state s=transport[i];uint32_t now=timer_read_time(),p=pulses[i+4],last=edge_time[i+4];
        uint8_t a=transport_a(i),b=transport_b(i),live=transport_live,assist=assist_pending[i],lock=locks_on[0]|(locks_on[1]<<1);irq_restore(irq);
        sendf("inventure_transport_status clock=%u channel=%c live=%c a=%c b=%c pulses=%u last_pulse_clock=%u starts=%u mode=%c running=%c direction=%c retry=%c fault=%c budget=%i forward=%u reverse=%u assist=%c locks=%c",
              now,i,live,a,b,p,last,s.starts,s.mode,s.running,s.direction,s.retry,s.fault,s.budget,s.forward,s.reverse,assist,lock);
    }
}
DECL_COMMAND_FLAGS(command_inventure_transport_query,HF_IN_SHUTDOWN,"inventure_transport_query");
