// SPDX-License-Identifier: GPL-3.0-or-later
// Bounded raw-driver qualification. Never arms native motion or establishes XYZ.
#line 9000 "inventure_motor_test.inc"
DECL_CONSTANT("INVENTURE_MOTOR_TEST_ABI", 2);
DECL_CONSTANT_STR("INVENTURE_MOTOR_TEST_RELEASE", "RC8-CONTROL-R6-SMOOTH");
DECL_CONSTANT("INVENTURE_DOOR_POLICY_ABI", 1);
DECL_CONSTANT("INVENTURE_DOOR_POLICY_DISABLE_BIT", 3);
DECL_CONSTANT("INVENTURE_MOTOR_TEST_HZ", 400);
DECL_CONSTANT("INVENTURE_MOTOR_TEST_PULSE_US", 100);
DECL_CONSTANT("INVENTURE_MOTOR_ENABLE_ON", 1);
#define MT_PERIOD (CONFIG_CLOCK_FREQ / 400u)
#define MT_HIGH (CONFIG_CLOCK_FREQ / 10000u)
#define MT_TIMEOUT MS(5000)
enum { MT_IDLE, MT_FORWARD, MT_REVERSING, MT_RETURN, MT_DONE, MT_ABORT };
enum { MT_NONE, MT_COMPLETE, MT_STOP, MT_HEARTBEAT, MT_DOOR, MT_LIMIT,
       MT_SHUTDOWN, MT_TIME, MT_HEAT };
enum { MT_OK, MT_BUSY, MT_NOT_READY, MT_OPEN_DOOR, MT_ACTIVE_LIMIT,
       MT_BAD_ACTION, MT_HEATING };
static const struct {
    GPIO_TypeDef *step_port, *dir_port;
    uint16_t step_bit, dir_bit, steps;
} mt_drivers[4] = {
    {GPIOA, GPIOB, BIT(7), BIT(2), 640},
    {GPIOA, GPIOB, BIT(6), BIT(1), 640},
    {GPIOB, GPIOE, BIT(8), BIT(7), 401},
    {GPIOB, GPIOE, BIT(9), BIT(8), 96},
};
static struct {
    uint32_t run, token, started;
    uint16_t forward, returned;
    uint8_t phase, reason, motor, direction, high, running, timer_added;
} motor_test;
static struct timer motor_timer;

// OEM r0323 callbacks 0x080173E4/0x0801740C clear/set system bit 3.
// 0x08014F74 returns before door checks and lock engagement when it is set.
// Keep that polarity in our own RAM; never write OEM NVM or bootloader data.
// This diagnostic APP keeps native motion unarmed and lock coils released.
static uint8_t door_policy_flags;
static uint8_t door_policy_enabled(void) {
    return !(door_policy_flags & BIT(3));
}
static uint8_t door_closed_mask(void) {
    return (!!(GPIOE->IDR & BIT(15))) | ((!!(GPIOB->IDR & BIT(10))) << 1);
}

void command_inventure_door_policy(uint32_t *args) {
    unsigned action=args[0], enabled=args[1];
    irqstatus_t flags=irq_save();
    uint32_t now=timer_read_time();
    uint8_t reject=0;
    if (action > 1 || (action == 1 && enabled > 1)) reject=1;
    else if (action == 1 && enabled != door_policy_enabled()) {
        if (motor_test_busy() || t0_heat_busy() || fan_manual_active()
            || feeder_running[0] || feeder_running[1]
            || locks_on[0] || locks_on[1]) reject=2;
        else if (fault || !initialized || !sample_started || !heartbeat_live
                 || iv9_due(now,heartbeat_deadline)) reject=3;
        else if (enabled) door_policy_flags &= ~BIT(3);
        else door_policy_flags |= BIT(3);
    }
    uint8_t active=door_policy_enabled(), closed=door_closed_mask();
    uint8_t locks_mask=(!!locks_on[0]) | ((!!locks_on[1]) << 1);
    uint8_t busy=motor_test_busy();
    irq_restore(flags);
    sendf("inventure_door_policy_state enabled=%c disable_bit=%c closed_mask=%c lock_command_mask=%c motor_busy=%c reject=%c",
          active, !active, closed, locks_mask, busy, reject);
}
DECL_COMMAND_FLAGS(command_inventure_door_policy, HF_IN_SHUTDOWN,
    "inventure_door_policy action=%c enabled=%c");

static uint8_t motor_test_busy(void) { return motor_test.running || xy_seek_busy() || control_busy(); }
static void motor_test_write(GPIO_TypeDef *port, uint16_t bit, uint8_t on) {
    port->BSRR = (uint32_t)bit << (on ? 0 : 16);
}
static void motor_test_end(uint8_t reason) {
    // PE10 active HIGH follows the existing V9 RC8 board driver, not the old
    // unqualified active-LOW draft. Release the shared enable on every exit.
    motor_test_write(GPIOE, BIT(10), 0);
    const unsigned m = motor_test.motor;
    motor_test_write(mt_drivers[m].step_port, mt_drivers[m].step_bit, 0);
    motor_test.high = motor_test.running = 0;
    motor_test.reason = reason;
    motor_test.phase = reason == MT_COMPLETE ? MT_DONE : MT_ABORT;
}
static uint8_t motor_test_limits(unsigned motor) {
    if (motor < 2)
        return !!((GPIOB->IDR & BIT(11)) || (GPIOC->IDR & BIT(15)));
    if (motor == 2)
        return !!(GPIOE->IDR & BIT(12)) || !(GPIOC->IDR & BIT(14));
    return 0;
}
static uint8_t motor_test_guard(uint32_t now, unsigned motor) {
    if (fault || !initialized || !sample_started || !heartbeat_live
        || iv9_due(now, heartbeat_deadline)) return MT_HEARTBEAT;
    if (t0_heat_busy()) return MT_HEAT;
    if (door_policy_enabled() && door_closed_mask() != 3) return MT_DOOR;
    if (motor_test_limits(motor)) return MT_LIMIT;
    return MT_NONE;
}
static uint_fast8_t motor_test_event(struct timer *t) {
    uint32_t now = timer_read_time();
    motor_test.timer_added = 0;
    if (!motor_test.running) return SF_DONE;
    unsigned m = motor_test.motor;
    uint8_t reason = motor_test_guard(now, m);
    if (!reason && (uint32_t)(now-motor_test.started) >= MT_TIMEOUT) reason=MT_TIME;
    if (reason) { motor_test_end(reason); return SF_DONE; }
    if (motor_test.high) {
        motor_test_write(mt_drivers[m].step_port, mt_drivers[m].step_bit, 0);
        motor_test.high = 0;
        unsigned count = motor_test.phase == MT_FORWARD
            ? motor_test.forward : motor_test.returned;
        if (count == mt_drivers[m].steps) {
            if (motor_test.phase == MT_RETURN) {
                motor_test_end(MT_COMPLETE); return SF_DONE;
            }
            motor_test.phase = MT_REVERSING;
            // DIR is changed only with STEP low, then held 100 ms before
            // the first return pulse. No queued native movement is involved.
            motor_test_write(mt_drivers[m].dir_port, mt_drivers[m].dir_bit,
                             !motor_test.direction);
            t->waketime = now + MS(100);
        } else t->waketime = now + MT_PERIOD-MT_HIGH;
    } else {
        if (motor_test.phase == MT_REVERSING) motor_test.phase = MT_RETURN;
        motor_test_write(mt_drivers[m].step_port, mt_drivers[m].step_bit, 1);
        motor_test.high = 1;
        if (motor_test.phase == MT_FORWARD) motor_test.forward++;
        else motor_test.returned++;
        t->waketime = now + MT_HIGH;
    }
    motor_test.timer_added = 1;
    return SF_RESCHEDULE;
}
void command_inventure_motor_test(uint32_t *args) {
    unsigned action=args[0], m=args[1], direction=args[2];
    uint32_t token=args[3];
    irqstatus_t flags=irq_save();
    uint32_t now=timer_read_time();
    uint8_t reject=MT_OK;
    if (action == 2) {
        control_motion_cancel();
        xy_seek_cancel();
        if (motor_test.running) {
            if (motor_test.timer_added) sched_del_timer(&motor_timer);
            motor_test.timer_added=0; motor_test_end(MT_STOP);
        }
    } else if (action > 2 || (action == 1 && (m >= 4 || direction > 1 || !token))) {
        reject=MT_BAD_ACTION;
    } else if (action == 1 && token == motor_test.token) {
        // A query retry of START only reports the original run, including
        // after completion. It can never initiate a second round trip.
        if (m != motor_test.motor || direction != motor_test.direction) reject=MT_BAD_ACTION;
    } else if (action == 1) {
        uint8_t guard=motor_test_guard(now,m);
        if (motor_test_busy() || fan_manual_active()) reject=MT_BUSY;
        else if (guard == MT_HEAT) reject=MT_HEATING;
        else if (guard == MT_HEARTBEAT) reject=MT_NOT_READY;
        else if (guard == MT_DOOR) reject=MT_OPEN_DOOR;
        else if (guard == MT_LIMIT) reject=MT_ACTIVE_LIMIT;
        else {
            motor_test.run++; motor_test.token=token; motor_test.started=now;
            motor_test.motor=m; motor_test.direction=direction;
            motor_test.forward=motor_test.returned=0;
            motor_test.high=motor_test.reason=0;
            motor_test.phase=MT_FORWARD;
            // Native STEP stays locked by inventure_is_armed()==0. Only
            // this timer can emit a fixed number of pulses on one driver.
            raw_output(GPIOB,2,0); raw_output(GPIOB,1,0);
            raw_output(GPIOE,7,0); raw_output(GPIOE,8,0);
            motor_test_write(mt_drivers[m].dir_port,mt_drivers[m].dir_bit,direction);
            motor_test_write(mt_drivers[m].step_port,mt_drivers[m].step_bit,0);
            motor_test_write(GPIOE,BIT(10),1);
            motor_test.running=1;
            motor_timer.func=motor_test_event;
            motor_timer.waketime=now+MS(20);
            sched_add_timer(&motor_timer); motor_test.timer_added=1;
        }
    }
    unsigned selected=motor_test.motor;
    uint32_t run=motor_test.run, last_token=motor_test.token;
    uint16_t forward=motor_test.forward, returned=motor_test.returned;
    uint8_t phase=motor_test.phase, reason=motor_test.reason;
    uint8_t running=motor_test.running, dir=motor_test.direction;
    uint8_t door_enabled=door_policy_enabled(), closed=door_closed_mask();
    irq_restore(flags);
    sendf("inventure_motor_state run=%u token=%u motor=%c direction=%c phase=%c reason=%c reject=%c forward=%hu returned=%hu planned=%hu enabled=%c door_lock_enabled=%c door_closed_mask=%c",
          run,last_token,selected,dir,phase,reason,reject,forward,returned,
          mt_drivers[selected].steps,running,door_enabled,closed);
}
DECL_COMMAND_FLAGS(command_inventure_motor_test, HF_IN_SHUTDOWN,
    "inventure_motor_test action=%c motor=%c direction=%c token=%u");
