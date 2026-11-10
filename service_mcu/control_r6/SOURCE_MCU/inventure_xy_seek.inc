// SPDX-License-Identifier: GPL-3.0-or-later
// One bounded coordinated CoreXY approach; never sets Klipper homed position.
#line 10000 "inventure_xy_seek.inc"
DECL_CONSTANT("INVENTURE_XY_SEEK_ABI", 1);
DECL_CONSTANT("INVENTURE_XY_SEEK_MAX_STEPS", 102400);
DECL_CONSTANT("INVENTURE_XY_SEEK_MAX_HZ", 3200);
DECL_CONSTANT("INVENTURE_XY_SEEK_MAX_MS", 40000);
DECL_CONSTANT("INVENTURE_XY_SEEK_X_DIR_MASK", 0);
DECL_CONSTANT("INVENTURE_XY_SEEK_Y_DIR_MASK", 2);
DECL_CONSTANT("INVENTURE_XY_REPEAT_ABI", 1);
DECL_CONSTANT("INVENTURE_XY_REPEAT_BACKOFF_STEPS", 1280);
DECL_CONSTANT("INVENTURE_XY_REPEAT_BACKOFF_HZ", 1280);
DECL_CONSTANT("INVENTURE_XY_REPEAT_SLOW_STEPS", 2560);
DECL_CONSTANT("INVENTURE_XY_REPEAT_SLOW_HZ", 640);
#define XS_STEPS 102400u
#define XS_HZ 3200u
#define XS_MS 40000u
#define XS_STEP_BITS (BIT(6)|BIT(7))
#define XR_BACKOFF_STEPS 1280u
#define XR_BACKOFF_HZ 1280u
#define XR_SLOW_STEPS 2560u
#define XR_SLOW_HZ 640u
enum { XR_FIRST, XR_BACKOFF, XR_SLOW };
enum { XS_IDLE, XS_SEEKING, XS_HIT, XS_STOPPED, XS_FAILED };
enum { XS_NONE, XS_ENDSTOP, XS_ALREADY, XS_STOP, XS_HEARTBEAT,
       XS_SHUTDOWN, XS_DOOR, XS_HEAT, XS_TIME, XS_DISTANCE, XS_OTHER_LIMIT,
       XS_STUCK, XS_EARLY_CONTACT };
enum { XS_OK, XS_BUSY, XS_NOT_READY, XS_OPEN_DOOR, XS_HEATING, XS_BAD_ACTION };
static struct {
    uint32_t run, token, steps, elapsed_ms, fraction, last_tick, period;
    uint8_t axis, phase, reason, running, high, timer_added, initial_mask, hit_mask;
    uint32_t first_steps, backoff_steps, clear_clock;
    uint8_t repeat, stage, released, clear_live;
} xy_seek;
static struct timer xy_seek_timer;
static uint8_t xy_seek_busy(void) { return xy_seek.running; }
static uint8_t xy_seek_inputs(void) {
    return (!!(GPIOB->IDR&BIT(11))) | ((!!(GPIOC->IDR&BIT(15)))<<1);
}
static void xy_seek_elapsed(uint32_t now) {
    uint32_t delta=now-xy_seek.last_tick;
    xy_seek.last_tick=now;
    uint32_t fraction=xy_seek.fraction+delta%MS(1);
    xy_seek.elapsed_ms+=delta/MS(1)+fraction/MS(1);
    xy_seek.fraction=fraction%MS(1);
}
static void xy_seek_end(uint8_t reason) {
    motor_test_write(GPIOE,BIT(10),0);
    motor_test_write(GPIOA,XS_STEP_BITS,0);
    xy_seek.high=xy_seek.running=0;
    xy_seek.reason=reason;
    xy_seek.phase=(reason==XS_ENDSTOP || reason==XS_ALREADY) ? XS_HIT
        : reason==XS_STOP ? XS_STOPPED : XS_FAILED;
}
static void xy_seek_cancel(void) {
    if (!xy_seek.running) return;
    if (xy_seek.timer_added) sched_del_timer(&xy_seek_timer);
    xy_seek.timer_added=0;
    xy_seek_elapsed(timer_read_time());
    xy_seek_end(XS_STOP);
}
static uint8_t xy_seek_guard(uint32_t now) {
    if (fault || !initialized || !sample_started || !heartbeat_live
        || iv9_due(now,heartbeat_deadline)) return XS_HEARTBEAT;
    if (t0_heat_busy()) return XS_HEAT;
    if (door_policy_enabled() && door_closed_mask()!=3) return XS_DOOR;
    return XS_NONE;
}
static void xy_repeat_stage(uint8_t stage,uint32_t now) {
    motor_test_write(GPIOA,XS_STEP_BITS,0);xy_seek.high=0;
    xy_seek.stage=stage;xy_seek.steps=0;
    uint8_t away=stage==XR_BACKOFF;
    raw_output(GPIOB,2,away);
    raw_output(GPIOB,1,away ? xy_seek.axis==0 : xy_seek.axis==1);
    xy_seek.clear_live=0;
    // DIR reversal happens with STEP low and the shared ENABLE retained.
    xy_seek_timer.waketime=now+MS(100);
}
static uint_fast8_t xy_seek_event(struct timer *t) {
    xy_seek.timer_added=0;
    if (!xy_seek.running) return SF_DONE;
    uint32_t now=timer_read_time();
    xy_seek_elapsed(now);
    uint8_t reason=xy_seek_guard(now), inputs=xy_seek_inputs();
    uint8_t target=BIT(xy_seek.axis), other=3u^target;
    // A previous X hit may remain asserted throughout a pure Y approach.
    // A newly asserted unrelated switch aborts rather than declaring success.
    if (!reason && (inputs&other&~xy_seek.initial_mask)) reason=XS_OTHER_LIMIT;
    if (!reason && xy_seek.elapsed_ms>=XS_MS) reason=XS_TIME;
    uint8_t backoff=xy_seek.repeat && xy_seek.stage==XR_BACKOFF;
    if (backoff) {
        if (inputs&target) {
            xy_seek.clear_live=xy_seek.released=0;
        } else if (!xy_seek.clear_live) {
            xy_seek.clear_live=1;xy_seek.clear_clock=now;
        } else if ((uint32_t)(now-xy_seek.clear_clock)>=MS(5)) xy_seek.released=1;
    } else if (!reason && (inputs&target)) {
        xy_seek.hit_mask=inputs;
        if (xy_seek.repeat && xy_seek.stage==XR_FIRST) {
            xy_seek.first_steps=xy_seek.steps;
            xy_repeat_stage(XR_BACKOFF,now);
            xy_seek.timer_added=1;return SF_RESCHEDULE;
        }
        reason=xy_seek.repeat && xy_seek.steps<64 ? XS_EARLY_CONTACT : XS_ENDSTOP;
    }
    if (reason) {xy_seek_end(reason);return SF_DONE;}
    if (xy_seek.high) {
        motor_test_write(GPIOA,XS_STEP_BITS,0);xy_seek.high=0;
        if (backoff && xy_seek.steps==XR_BACKOFF_STEPS) {
            if (!xy_seek.released || (inputs&target)) {
                xy_seek_end(XS_STUCK);return SF_DONE;
            }
            xy_seek.backoff_steps=xy_seek.steps;
            xy_repeat_stage(XR_SLOW,now);
        } else t->waketime=now+xy_seek.period-MT_HIGH;
    } else {
        uint32_t budget=backoff ? XR_BACKOFF_STEPS
            : xy_seek.repeat && xy_seek.stage==XR_SLOW ? XR_SLOW_STEPS : XS_STEPS;
        if (xy_seek.steps>=budget) {xy_seek_end(XS_DISTANCE);return SF_DONE;}
        // Same-port BSRR makes the two STEP edges simultaneous. Fixed
        // 100us HIGH is the already tested MOTOR-R1/R2 pulse width.
        motor_test_write(GPIOA,XS_STEP_BITS,1);xy_seek.high=1;
        xy_seek.steps++;
        uint32_t hz=400u+16u*(xy_seek.steps-1u);
        uint32_t cap=backoff ? XR_BACKOFF_HZ
            : xy_seek.repeat && xy_seek.stage==XR_SLOW ? XR_SLOW_HZ : XS_HZ;
        if (hz>cap) hz=cap;
        xy_seek.period=CONFIG_CLOCK_FREQ/hz;
        t->waketime=now+MT_HIGH;
    }
    xy_seek.timer_added=1;return SF_RESCHEDULE;
}
static uint8_t xy_seek_request(uint32_t *args,uint8_t repeat) {
    unsigned action=args[0], axis=args[1];uint32_t token=args[2];
    irqstatus_t flags=irq_save();uint32_t now=timer_read_time();uint8_t reject=XS_OK;
    if (action==2) {
        control_motion_cancel();
        xy_seek_cancel();
        if (motor_test.running) {
            if (motor_test.timer_added) sched_del_timer(&motor_timer);
            motor_test.timer_added=0;motor_test_end(MT_STOP);
        }
    } else if (action>2 || (action==1 && (axis>1 || !token))) reject=XS_BAD_ACTION;
    else if (action==1 && token==xy_seek.token) {
        if (axis!=xy_seek.axis || repeat!=xy_seek.repeat) reject=XS_BAD_ACTION;
    } else if (action==1) {
        uint8_t guard=xy_seek_guard(now);
        if (motor_test_busy() || fan_manual_active()) reject=XS_BUSY;
        else if (guard==XS_HEARTBEAT) reject=XS_NOT_READY;
        else if (guard==XS_DOOR) reject=XS_OPEN_DOOR;
        else if (guard==XS_HEAT) reject=XS_HEATING;
        else {
            xy_seek.run++;xy_seek.token=token;xy_seek.axis=axis;
            xy_seek.steps=xy_seek.elapsed_ms=xy_seek.fraction=0;
            xy_seek.last_tick=now;xy_seek.initial_mask=xy_seek_inputs();
            xy_seek.hit_mask=xy_seek.high=xy_seek.reason=0;
            xy_seek.repeat=repeat;xy_seek.stage=XR_FIRST;
            xy_seek.first_steps=xy_seek.backoff_steps=0;
            xy_seek.released=xy_seek.clear_live=0;
            xy_seek.phase=XS_SEEKING;
            if (!repeat && (xy_seek.initial_mask&BIT(axis))) {
                xy_seek.hit_mask=xy_seek.initial_mask;
                xy_seek_end(XS_ALREADY);
            } else {
                // OEM X approach 0x08013F24, normal sign: PB2=0/PB1=0.
                // OEM Y approach 0x08013F74: PB2=0/PB1=1.
                // These are coordinated axes, unlike the A/B driver test.
                raw_output(GPIOB,2,0);raw_output(GPIOB,1,axis==1);
                motor_test_write(GPIOA,XS_STEP_BITS,0);
                motor_test_write(GPIOE,BIT(10),1);
                xy_seek.running=1;xy_seek_timer.func=xy_seek_event;
                xy_seek_timer.waketime=now+MS(20);
                if (repeat && (xy_seek.initial_mask&BIT(axis))) {
                    xy_seek.hit_mask=xy_seek.initial_mask;
                    xy_repeat_stage(XR_BACKOFF,now);
                }
                sched_add_timer(&xy_seek_timer);xy_seek.timer_added=1;
            }
        }
    }
    irq_restore(flags);return reject;
}
void command_inventure_xy_seek(uint32_t *args) {
    uint8_t reject=xy_seek_request(args,0);
    irqstatus_t flags=irq_save();
    uint32_t run=xy_seek.run,last_token=xy_seek.token,steps=xy_seek.steps,elapsed=xy_seek.elapsed_ms;
    uint8_t selected=xy_seek.axis,phase=xy_seek.phase,reason=xy_seek.reason;
    uint8_t enabled=xy_seek.running,inputs=xy_seek_inputs(),hit=xy_seek.hit_mask;
    uint8_t door_enabled=door_policy_enabled();
    irq_restore(flags);
    sendf("inventure_xy_seek_state run=%u token=%u axis=%c phase=%c reason=%c reject=%c steps=%u elapsed_ms=%u enabled=%c endstop_mask=%c hit_mask=%c door_lock_enabled=%c",
          run,last_token,selected,phase,reason,reject,steps,elapsed,enabled,inputs,hit,door_enabled);
}
DECL_COMMAND_FLAGS(command_inventure_xy_seek,HF_IN_SHUTDOWN,
    "inventure_xy_seek action=%c axis=%c token=%u");

void command_inventure_xy_repeat(uint32_t *args) {
    uint8_t reject=xy_seek_request(args,1);
    irqstatus_t flags=irq_save();
    uint32_t run=xy_seek.run,token=xy_seek.token,elapsed=xy_seek.elapsed_ms;
    uint32_t first=xy_seek.stage==XR_FIRST ? xy_seek.steps : xy_seek.first_steps;
    uint32_t away=xy_seek.stage==XR_BACKOFF ? xy_seek.steps : xy_seek.backoff_steps;
    uint32_t slow=xy_seek.stage==XR_SLOW ? xy_seek.steps : 0;
    uint8_t axis=xy_seek.axis,phase=xy_seek.phase,stage=xy_seek.stage,reason=xy_seek.reason;
    uint8_t enabled=xy_seek.running,inputs=xy_seek_inputs(),hit=xy_seek.hit_mask;
    uint8_t released=xy_seek.released,mode=xy_seek.repeat;
    irq_restore(flags);
    sendf("inventure_xy_repeat_state run=%u token=%u axis=%c phase=%c stage=%c reason=%c reject=%c first_steps=%u backoff_steps=%hu slow_steps=%hu elapsed_ms=%u enabled=%c endstop_mask=%c hit_mask=%c released=%c repeat=%c",
          run,token,axis,phase,stage,reason,reject,first,away,slow,elapsed,enabled,inputs,hit,released,mode);
}
DECL_COMMAND_FLAGS(command_inventure_xy_repeat,HF_IN_SHUTDOWN,
    "inventure_xy_repeat action=%c axis=%c token=%u");
