// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __INVENTURE_XY_STEP_H
#define __INVENTURE_XY_STEP_H

#include <stdint.h>

enum {
    INVENTURE_XY_PA7 = 1, INVENTURE_XY_PA6 = 2,
    INVENTURE_XY_PULSE = 480,       // 4 us at 120 MHz
    INVENTURE_XY_MIN_PERIOD = 1200, // 100,000 steps/s per motor
    INVENTURE_XY_MAX_PERIOD = 9600, // 12,500 steps/s per motor
    INVENTURE_XY_SETUP = 480,       // Begin setup before the first rising edge.
    INVENTURE_XY_FINISH = 120,      // Observe the last fall and gate/DMA state.
    INVENTURE_XY_DMA_MARGIN = 60,   // Allow for AHB service on the PA6 edges.
};

uint8_t inventure_xy_step_axis(uint32_t step_pin, uint32_t dir_pin);
uint8_t inventure_xy_step_allowed(void);
uint32_t inventure_xy_step_start(uint8_t axis, uint32_t period,
                                 uint16_t count, uint32_t first);
uint16_t inventure_xy_step_remaining(uint8_t axis);
uint16_t inventure_xy_step_stop(uint8_t axis);
void inventure_xy_step_finish(uint8_t axis);

static inline uint32_t inventure_xy_step_period(uint8_t axis, uint32_t period)
{
    return axis == INVENTURE_XY_PA7 ? period & ~1u : period;
}

static inline uint32_t inventure_xy_step_high(uint8_t axis)
{
    return INVENTURE_XY_PULSE
        + (axis == INVENTURE_XY_PA6 ? INVENTURE_XY_DMA_MARGIN : 0);
}

static inline uint16_t
inventure_xy_step_chunk(uint8_t axis, uint32_t period, uint16_t count)
{
    // TIM3 has half the resolution of the DWT clock. Re-anchor an odd
    // period at least every 64 pulses, bounding its rounding to 63 DWT ticks.
    return axis == INVENTURE_XY_PA7 && (period & 1) && count > 64 ? 64 : count;
}

#endif
