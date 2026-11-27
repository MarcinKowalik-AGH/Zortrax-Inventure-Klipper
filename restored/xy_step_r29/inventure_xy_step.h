// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __INVENTURE_XY_STEP_H
#define __INVENTURE_XY_STEP_H

#include <stdint.h>

enum {
    INVENTURE_XY_PA7 = 1, INVENTURE_XY_PA6 = 2,
    INVENTURE_XY_PULSE = 480,       // Existing 4 us software pulse for Home.
    INVENTURE_XY_FAST_HIGH = 294,   // 2.45 us, rounded up from M300's 176/72 us.
    INVENTURE_XY_MIN_LOW = 294,     // Same minimum low interval as the M300.
    INVENTURE_XY_MIN_PERIOD = 660, // r0323 TIM6: (10 + 1) / 2 MHz = 5.5 us.
    INVENTURE_XY_MAX_PERIOD = 9600,
    INVENTURE_XY_SETUP = 240,
    INVENTURE_XY_APB_MARGIN = 60,  // TIM6 DMA/OPM transfer allowance.
    INVENTURE_XY_ONESHOT_START = 9, // TIM_CR1_OPM | TIM_CR1_CEN.
    INVENTURE_XY_ONESHOT_GAP = INVENTURE_XY_FAST_HIGH + INVENTURE_XY_MIN_LOW + 2,
};

uint8_t inventure_xy_step_axis(uint32_t step_pin, uint32_t dir_pin);
uint8_t inventure_xy_step_allowed(void);
uint32_t inventure_xy_step_start(uint8_t axis, uint32_t period,
                                 uint16_t count, uint32_t first);
uint16_t inventure_xy_step_remaining(uint8_t axis);
uint16_t inventure_xy_step_stop(uint8_t axis);
void inventure_xy_step_finish(uint8_t axis);
volatile uint32_t *inventure_xy_one_arm(uint8_t axis);
void inventure_xy_one_release(uint8_t axis);
void inventure_xy_one_record(uint8_t axis, uint16_t count);

static inline uint16_t
inventure_xy_step_chunk(uint32_t period, uint16_t count)
{
    // Re-anchor odd periods on the half-rate timers every 64 pulses.
    return (period & 1) && count > 64 ? 64 : count;
}

static inline uint32_t
inventure_xy_step_drain(uint8_t axis)
{
    return INVENTURE_XY_ONESHOT_GAP
        + (axis == INVENTURE_XY_PA6 ? INVENTURE_XY_APB_MARGIN : 0);
}

#endif
