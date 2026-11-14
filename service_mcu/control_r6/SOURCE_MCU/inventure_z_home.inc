// SPDX-License-Identifier: GPL-3.0-or-later
// Leased T0 contact refinement, modeled on r0323 at 0x0801DC70.
#line 13000 "inventure_z_home.inc"
DECL_CONSTANT("INVENTURE_Z_HOME_ABI",1);
#define ZH_MAX_SPAN 8020u
#define ZH_MIN_INTERVAL_US 1250u
#define ZH_MAX_INTERVAL_US 25000u
#define ZH_MAX_REPEATS 101u
#define ZH_MAX_CYCLES 512u
#define ZH_HIGH_US 10u
#define ZH_MAX_TICKS (600ull*CONFIG_CLOCK_FREQ)

enum { ZH_RUNNING,ZH_DONE,ZH_NO_CONTACT,ZH_NO_RELEASE,ZH_GUARD,ZH_TIME,ZH_STOP };
enum { ZH_START_UP,ZH_SEEK,ZH_START_DOWN,ZH_RELEASE,ZH_START_SETTLE,ZH_SETTLE };
static struct zh_state {
    uint64_t elapsed_ticks;
    uint32_t owner,token,stamp,interval,up_steps,down_steps,cycles;
    int32_t net,best;
    uint16_t span,interval_us;
    uint8_t repeats,misses,phase,status,active,high,timer_added;
} zh;
static struct timer zh_timer;

static uint8_t zh_active(void) { return zh.active; }

static void zh_end(uint8_t status) {
    if(!zh.active)return;
    if(zh.timer_added)sched_del_timer(&zh_timer);
    motor_test_write(mt_drivers[2].step_port,mt_drivers[2].step_bit,0);
    zh.active=zh.high=zh.timer_added=0;
    zh.status=status;
    if(!fs.active || zh.owner!=fs.token || (status!=ZH_DONE && status!=ZH_STOP))
        motor_test_write(GPIOE,BIT(10),0);
}

static void zh_stop(void) { zh_end(ZH_STOP); }
static void zh_shutdown(void) {
    zh.timer_added=0; // Scheduler reset owns queue removal during shutdown.
    zh_stop();
}

static uint_fast8_t zh_fail(uint8_t status) {
    zh_end(status);
    control_stop_all();
    return SF_DONE;
}

static uint8_t zh_guard_ok(uint32_t now,uint32_t owner) {
    if(!control_ready(now) || control_mode!=CT_SERVICE || !fs.active || fs.token!=owner
            || fs.tool!=0 || fs.mode!=IVT_STOP || iv9_due(now,fs.deadline)
            || !transport_live || transport_selected!=0 || !transport[0].configured
            || transport[0].fault || (door_policy_enabled() && door_closed_mask()!=3)
            || cm_input_hit(BIT(2)|BIT(4),BIT(2)|BIT(4)))
        return 0;
    uint8_t valid;
    int16_t temperature=control_temp(0,now,&valid);
    return valid && temperature>=fs.min_c && temperature<fs.max_c
        && co[6].reason<3 && control_sensors_valid(control_guard_mask|31,now);
}

static void zh_guard(uint32_t now) {
    if(!zh.active)return;
    zh.elapsed_ticks+=(uint32_t)(now-zh.stamp);
    zh.stamp=now;
    if(!zh_guard_ok(now,zh.owner))zh_fail(ZH_GUARD);
    else if(zh.elapsed_ticks>=ZH_MAX_TICKS)zh_fail(ZH_TIME);
}

static uint_fast8_t zh_wait(struct timer *t,uint32_t now,uint32_t ticks) {
    t->waketime=now+ticks;
    zh.timer_added=1;
    return SF_RESCHEDULE;
}

static uint_fast8_t zh_step(struct timer *t,uint32_t now,uint8_t up) {
    motor_test_write(mt_drivers[2].step_port,mt_drivers[2].step_bit,1);
    if(up){zh.net++;zh.up_steps++;}
    else {zh.net--;zh.down_steps++;}
    zh.high=1;
    return zh_wait(t,now,timer_from_us(ZH_HIGH_US));
}

static uint_fast8_t zh_settle(struct timer *t,uint32_t now) {
    zh.phase=ZH_START_SETTLE;
    return zh_wait(t,now,zh.interval);
}

static uint_fast8_t zh_release_pair(struct timer *t,uint32_t now) {
    if(!zh.cycles || zh.net<zh.best) {
        zh.best=zh.net;
        zh.misses=0;
    } else {
        zh.misses++;
    }
    zh.cycles++;
    if(zh.misses>=zh.repeats)return zh_settle(t,now);
    if(zh.cycles>=ZH_MAX_CYCLES)return zh_fail(ZH_TIME);
    zh.phase=ZH_START_UP;
    return zh_wait(t,now,zh.interval);
}

static uint_fast8_t zh_sample_release(struct timer *t,uint32_t now,uint32_t wait) {
    if(GPIOC->IDR&BIT(14))return zh_release_pair(t,now);
    if(zh.net<=-(int32_t)zh.span)return zh_fail(ZH_NO_RELEASE);
    // Like r0323, select the next downward pulse before waiting. PC14
    // opening during this wait does not undo the committed safe retreat.
    zh.phase=ZH_RELEASE;
    return zh_wait(t,now,wait);
}

static uint_fast8_t zh_event(struct timer *t) {
    zh.timer_added=0;
    uint32_t now=timer_read_time();
    zh_guard(now);
    if(!zh.active)return SF_DONE;
    if(zh.high) {
        motor_test_write(mt_drivers[2].step_port,mt_drivers[2].step_bit,0);
        zh.high=0;
        uint32_t wait=zh.interval-timer_from_us(ZH_HIGH_US);
        if(zh.phase==ZH_RELEASE)return zh_sample_release(t,now,wait);
        return zh_wait(t,now,wait);
    }

    switch(zh.phase) {
    case ZH_START_UP:
        motor_test_write(mt_drivers[2].dir_port,mt_drivers[2].dir_bit,1);
        zh.phase=ZH_SEEK;
        if(zh.net<zh.span && (GPIOC->IDR&BIT(14)))return zh_wait(t,now,zh.interval);
        // fall through
    case ZH_SEEK:
        if(zh.net>=zh.span) {
            if(zh.cycles)return zh_settle(t,now);
            return zh_fail(ZH_NO_CONTACT);
        }
        // Check again just before moving toward the nozzle. A late
        // closure must not commit an additional upward pulse.
        if(GPIOC->IDR&BIT(14))return zh_step(t,now,1);
        zh.phase=ZH_START_DOWN;
        return zh_wait(t,now,zh.interval);
    case ZH_START_DOWN:
        motor_test_write(mt_drivers[2].dir_port,mt_drivers[2].dir_bit,0);
        return zh_sample_release(t,now,zh.interval);
    case ZH_RELEASE:
        return zh_step(t,now,0);
    case ZH_START_SETTLE:
        motor_test_write(mt_drivers[2].dir_port,mt_drivers[2].dir_bit,0);
        zh.phase=ZH_SETTLE;
        if(zh.net>zh.best)return zh_wait(t,now,zh.interval);
        // fall through
    case ZH_SETTLE:
        if(zh.net>zh.best)return zh_step(t,now,0);
        if(zh.net!=zh.best || !(GPIOC->IDR&BIT(14)))return zh_fail(ZH_GUARD);
        zh_end(ZH_DONE);
        return SF_DONE;
    }
    return zh_fail(ZH_GUARD);
}

static uint8_t zh_start(uint32_t now,uint32_t owner,uint32_t token,uint32_t span,
                        uint32_t interval_us,uint32_t repeats) {
    if(!owner || !token || !span || span>ZH_MAX_SPAN || interval_us<ZH_MIN_INTERVAL_US
            || interval_us>ZH_MAX_INTERVAL_US || !repeats || repeats>ZH_MAX_REPEATS)
        return 1;
    if(owner==zh.owner && token==zh.token) {
        // Retries describe the last run and never repeat physical movement.
        return span!=zh.span || interval_us!=zh.interval_us || repeats!=zh.repeats;
    }
    if(!fs.active || owner!=fs.token || owner==zh.owner)return 4;
    if(zh.active || sl.active || cm.active || motor_test.running || xy_seek_busy()
            || t0_heat_busy() || fan_manual_active() || feeder_running[0] || feeder_running[1]
            || locks_on[0] || locks_on[1])
        return 2;
    if(!zh_guard_ok(now,owner))return 3;
    zh=(struct zh_state){.owner=owner,.token=token,.stamp=now,.span=span,.interval_us=interval_us,
        .interval=timer_from_us(interval_us),.repeats=repeats,.active=1,.timer_added=1};
    motor_test_write(mt_drivers[2].step_port,mt_drivers[2].step_bit,0);
    motor_test_write(GPIOE,BIT(10),1);
    zh_timer.func=zh_event;
    zh_timer.waketime=now+zh.interval;
    sched_add_timer(&zh_timer);
    return 0;
}

void command_inventure_z_home(uint32_t *args) {
    unsigned action=args[0];
    uint32_t owner=args[1],token=args[2];
    irqstatus_t irq=irq_save();
    uint32_t now=timer_read_time();
    zh_guard(now);
    uint8_t reject=0;
    if(action==1)reject=zh_start(now,owner,token,args[3],args[4],args[5]);
    else if(action>2)reject=1;
    else if(action==0 && !owner && !token){} // Read-only diagnostics.
    else if(!owner || !token || owner!=zh.owner || token!=zh.token)reject=4;
    else if(action==2)zh_stop();
    struct zh_state state=zh;
    irq_restore(irq);
    sendf("inventure_z_home_state owner=%u token=%u active=%c status=%c reject=%c net_steps=%i best_steps=%i cycles=%u non_improvements=%c up_steps=%u down_steps=%u",
        state.owner,state.token,state.active,state.status,reject,state.net,state.best,
        state.cycles,state.misses,state.up_steps,state.down_steps);
}
DECL_COMMAND_FLAGS(command_inventure_z_home,HF_IN_SHUTDOWN,
    "inventure_z_home action=%c owner=%u token=%u span=%hu interval_us=%hu repeats=%c");
