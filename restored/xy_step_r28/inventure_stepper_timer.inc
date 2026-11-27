// SPDX-License-Identifier: GPL-3.0-or-later
// The ordinary Klipper queue and position remain the authority for each block.
enum { XY_SOFTWARE, XY_PLANNED, XY_RUNNING };

static void
stepper_xy_plan(struct stepper *s)
{
    if (!s->xy_axis || s->add || s->stop_signal.func || s->count < 64
        || s->interval < INVENTURE_XY_MIN_PERIOD
        || s->interval > INVENTURE_XY_MAX_PERIOD)
        return;
    s->xy_mode = XY_PLANNED;
    s->xy_first = s->time.waketime;
    if ((int32_t)(s->xy_first - timer_read_time()) > 2 * INVENTURE_XY_SETUP)
        s->time.waketime -= INVENTURE_XY_SETUP;
}

static uint_fast8_t
stepper_xy_start(struct stepper *s)
{
    uint32_t period = inventure_xy_step_period(s->xy_axis, s->interval);
    uint16_t count = inventure_xy_step_chunk(s->xy_axis, s->interval, s->count / 2);
    uint32_t actual = inventure_xy_step_start(s->xy_axis, period, count, s->xy_first);
    s->xy_mode = XY_RUNNING;
    s->xy_count = count;
    s->next_step_time += (count - 1u) * s->interval;
    s->time.waketime = actual + (count - 1u) * period
        + inventure_xy_step_high(s->xy_axis) + INVENTURE_XY_FINISH;
    return SF_RESCHEDULE;
}

static uint_fast8_t
stepper_xy_finish(struct stepper *s)
{
    inventure_xy_step_finish(s->xy_axis);
    s->xy_mode = XY_SOFTWARE;
    // Keep the normal minimum interval after the falling edge, including
    // when a following block changes the direction or returns to software.
    s->time.waketime += s->step_pulse_ticks - INVENTURE_XY_FINISH;
    uint32_t remaining = s->count - 2u * s->xy_count;
    if (!remaining)
        return stepper_load_next(s);
    s->count = remaining;
    s->next_step_time += s->interval;
    if (timer_is_before(s->time.waketime, s->next_step_time))
        s->time.waketime = s->next_step_time;
    stepper_xy_plan(s);
    return SF_RESCHEDULE;
}

static uint32_t
stepper_xy_remaining(struct stepper *s, uint16_t hardware_remaining)
{
    return s->count / 2 - s->xy_count + hardware_remaining;
}
