// SPDX-License-Identifier: GPL-3.0-or-later
// Klipper owns the queue and position. The hardware owns STEP high time.
enum { XY_SOFTWARE, XY_PLANNED, XY_RUNNING, XY_ONE_PLANNED, XY_ONE_RUNNING, XY_ONE_DRAIN };

static void
stepper_xy_release_one(struct stepper *s)
{
    if (s->xy_oneshot) {
        inventure_xy_one_release(s->xy_axis);
        s->xy_oneshot = 0;
    }
}

static void
stepper_xy_plan(struct stepper *s)
{
    if (!s->xy_axis || s->stop_signal.func) {
        stepper_xy_release_one(s);
        return;
    }
    uint32_t pulses = s->count / 2;
    // Klipper's first gap was used to position waketime. Look at the
    // shortest subsequent gap when an accelerating block crosses the threshold.
    uint32_t shortest = s->interval;
    if (s->add < 0 && pulses > 2)
        shortest += s->add * (int32_t)(pulses - 2);
    if (shortest > INVENTURE_XY_MAX_PERIOD || !inventure_xy_step_allowed()) {
        stepper_xy_release_one(s);
        return;
    }
    s->xy_mode = XY_ONE_PLANNED;
    if (!s->add && pulses >= 32 && s->interval >= INVENTURE_XY_MIN_PERIOD) {
        stepper_xy_release_one(s);
        s->xy_mode = XY_PLANNED;
    }
    s->xy_first = s->time.waketime;
    if ((s->xy_mode == XY_PLANNED || !s->xy_oneshot)
        && (int32_t)(s->xy_first - timer_read_time()) > 2 * INVENTURE_XY_SETUP)
        s->time.waketime -= INVENTURE_XY_SETUP;
}

static uint_fast8_t
stepper_xy_start(struct stepper *s)
{
    uint32_t period = s->interval & ~1u;
    uint16_t count = inventure_xy_step_chunk(s->interval, s->count / 2);
    uint32_t actual = inventure_xy_step_start(s->xy_axis, period, count, s->xy_first);
    s->xy_mode = XY_RUNNING;
    s->xy_count = count;
    s->next_step_time += (count - 1u) * s->interval;
    s->time.waketime = actual + (count - 1u) * period + inventure_xy_step_drain(s->xy_axis);
    return SF_RESCHEDULE;
}

static uint_fast8_t
stepper_xy_finish(struct stepper *s)
{
    inventure_xy_step_finish(s->xy_axis);
    s->xy_mode = XY_SOFTWARE;
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

static uint_fast8_t
stepper_xy_one_step(struct stepper *s)
{
    uint32_t now = timer_read_time();
    if (*s->xy_oneshot & 1u || (int32_t)(now - s->next_step_time) > (int32_t)timer_from_us(1000))
        shutdown("Inventure XY one-shot missed its deadline");
    *s->xy_oneshot = INVENTURE_XY_ONESHOT_START;
    uint32_t earliest = timer_read_time() + INVENTURE_XY_ONESHOT_GAP;
    uint32_t remaining = s->count - 2;
    if (unlikely(!remaining)) {
        // Keep the queue busy until the last pulse has completed and its
        // required low interval has passed before changing DIR or owner.
        s->xy_mode = XY_ONE_DRAIN;
        s->time.waketime = earliest;
        return SF_RESCHEDULE;
    }
    s->count = remaining;
    s->next_step_time += s->interval;
    s->interval += s->add;
    s->time.waketime = timer_is_before(s->next_step_time, earliest)
        ? earliest : s->next_step_time;
    return SF_RESCHEDULE;
}

static uint_fast8_t
stepper_xy_one_finish(struct stepper *s)
{
    if (*s->xy_oneshot & 1u)
        shutdown("Inventure XY one-shot failed to drain");
    inventure_xy_one_record(s->xy_axis, s->xy_count);
    s->xy_mode = XY_SOFTWARE;
    return stepper_load_next(s);
}

static uint32_t
stepper_xy_remaining(struct stepper *s, uint16_t hardware_remaining)
{
    return s->count / 2 - s->xy_count + hardware_remaining;
}
