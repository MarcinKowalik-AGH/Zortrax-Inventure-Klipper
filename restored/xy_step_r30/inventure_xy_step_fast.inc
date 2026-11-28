// SPDX-License-Identifier: GPL-3.0-or-later
#include "board/internal.h" // DWT, TIM_CR1_CEN

enum { INVENTURE_XY_ONE_WAKE_ADVANCE = 76 };

static inline uint32_t
stepper_xy_shortest_period(uint32_t interval, int32_t add, uint32_t pulses)
{
    // The first gap positions the first event. Examine subsequent gaps.
    return add < 0 && pulses > 2 ? interval + add * (int32_t)(pulses - 2) : interval;
}

// Load the next short block while the last hardware pulse is still draining.
// A direction or timer ownership change must use the ordinary drain path.
static uint8_t
stepper_xy_can_chain_one(struct stepper *s)
{
    struct move_node *node = move_queue_first(&s->mq);
    if (!node || s->stop_signal.func)
        return 0;
    struct stepper_move *next = container_of(node, struct stepper_move, node);
    uint32_t period = next->interval;
    return !(next->flags & MF_DIR)
        && (next->add || (period & 1) || next->count < 32)
        && stepper_xy_shortest_period(period + next->add, next->add, next->count)
            <= INVENTURE_XY_MAX_PERIOD
        && inventure_xy_step_allowed();
}

// Klipper may split a single cruising cadence into several queue_step
// commands. Combine a bounded prefix of those identical runs while the
// queue is owned by this callback. A change of direction or cadence ends it.
static void
stepper_xy_join_constant_moves(struct stepper *s)
{
    uint32_t pulses = s->count / 2;
    uint32_t joined = pulses;
    for (unsigned i = 0; i < 4; i++) {
        struct move_node *node = move_queue_first(&s->mq);
        if (!node)
            break;
        struct stepper_move *next = container_of(node, struct stepper_move, node);
        if (next->flags & MF_DIR || next->add || next->interval != s->interval
            || joined + next->count > UINT16_MAX)
            break;
        joined += next->count;
        move_free(move_queue_pop(&s->mq));
    }
    s->position += joined - pulses;
    s->count = 2u * joined;
}

// Wake slightly ahead of the next rising edge and finish the bounded wait
// here, so scheduler dispatch time does not lengthen every pulse interval.
// Read the same APB register after starting the timer before sampling DWT.
static inline uint32_t
stepper_xy_one_emit(volatile uint32_t *timer, uint32_t due, uint32_t host_clock)
{
    uint32_t now, scratch;
    asm goto (
        "1: ldr %[now], [%[clock]]\n\t"
        "subs %[scratch], %[now], %[due]\n\t"
        "bmi 1b\n\t"
        "subs %[scratch], %[now], %[host_clock]\n\t"
        "cmp %[scratch], %[late_limit]\n\t"
        "bgt %l[missed]\n\t"
        "ldr %[scratch], [%[timer]]\n\t"
        "tst %[scratch], %[running]\n\t"
        "bne %l[missed]\n\t"
        "str %[start], [%[timer]]\n\t"
        "ldr %[scratch], [%[timer]]\n\t"
        "ldr %[now], [%[clock]]"
        : [now] "=&r" (now), [scratch] "=&r" (scratch)
        : [clock] "r" (&DWT->CYCCNT), [timer] "r" (timer),
          [due] "r" (due), [host_clock] "r" (host_clock), [running] "I" (TIM_CR1_CEN),
          [start] "r" (INVENTURE_XY_ONESHOT_START), [late_limit] "r" (timer_from_us(1000))
        : "cc", "memory"
        : missed);
    return now;
missed:
    shutdown("Inventure XY one-shot missed its deadline");
}
