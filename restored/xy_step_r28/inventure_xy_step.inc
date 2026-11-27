// SPDX-License-Identifier: GPL-3.0-or-later
// Finite native CoreXY trains derived from the Classic M300 step timer.
#include "inventure_xy_step.h"
#include "board/armcm_boot.h" // DECL_ARMCM_IRQ
#include "generic/armcm_timer.h" // udelay

_Static_assert(CONFIG_CLOCK_FREQ == 120000000, "Inventure STEP timer clock");

// RM0033 table 60: TIM3 ITR1 <- TIM2, TIM2 ITR2 <- TIM3.
// PA7 is TIM3_CH2 (AF2). TIM3_CH1 clocks TIM2 at each falling edge.
// RM0033 table 23: TIM1_UP -> DMA2/S5/C6, TIM1_CH3 -> DMA2/S6/C6.
// PA6 stays GPIO. DMA2 can write its BSRR over AHB1 while SPI2 uses DMA1.
// TIM4/5/8/9 remain reserved by the existing board PWM owner.
enum {
    XY_PA7_BIT = 1u << 7, XY_PA6_BIT = 1u << 6,
    XY_TIM_GATED = 5, XY_TIM_EXTERNAL = 7,
    XY_TIM_COMPARE_TRGO = 3u << 4, XY_TIM_OC1_TRGO = 4u << 4,
    XY_TIM_PWM1_CH1 = 6u << 4, XY_TIM_PWM1_CH2 = 6u << 12,
    XY_DMA_ERRORS = DMA_HISR_TEIF5 | DMA_HISR_DMEIF5 | DMA_HISR_FEIF5
                  | DMA_HISR_TEIF6 | DMA_HISR_DMEIF6 | DMA_HISR_FEIF6,
    XY_DMA_CLEAR = DMA_HIFCR_CTCIF5 | DMA_HIFCR_CHTIF5 | DMA_HIFCR_CTEIF5
                 | DMA_HIFCR_CDMEIF5 | DMA_HIFCR_CFEIF5
                 | DMA_HIFCR_CTCIF6 | DMA_HIFCR_CHTIF6 | DMA_HIFCR_CTEIF6
                 | DMA_HIFCR_CDMEIF6 | DMA_HIFCR_CFEIF6,
    XY_DMA_CONFIG = (6u << DMA_SxCR_CHSEL_Pos)
                  | DMA_SxCR_DIR_0 | DMA_SxCR_MSIZE_1 | DMA_SxCR_PSIZE_1
                  | DMA_SxCR_PL_1,
};

struct xy_step_train {
    uint32_t blocks, steps;
    uint16_t total, stopped_remaining;
};

static struct xy_step_train xy_trains[2];
static uint32_t xy_dma_set = XY_PA6_BIT, xy_dma_clear = XY_PA6_BIT << 16;
static uint32_t xy_step_aborts, xy_step_errors, xy_step_peak_late;
static uint8_t xy_step_verified, xy_step_active, xy_step_host;

uint8_t inventure_native_queue_allowed(void);

static void
xy_prepare_pwm(uint32_t period, uint16_t count)
{
    TIM3->CCER = 0;
    TIM3->ARR = period - 1;
    TIM3->EGR = TIM_EGR_UG;
    TIM3->CNT = period - 1; // Stay low until the first rollover.
    TIM2->CR1 = 0;
    TIM2->CCR1 = count;
    TIM2->EGR = TIM_EGR_UG;
    TIM2->CNT = 0;
    TIM2->CR1 = TIM_CR1_CEN;
}

static void
xy_prepare_dma(uint32_t period, uint32_t high, uint16_t count,
               uint32_t interrupts)
{
    // URS and DIER=0 prevent the setup update from producing a STEP.
    TIM1->DIER = 0;
    TIM1->ARR = period - 1;
    TIM1->CCR3 = high;
    TIM1->EGR = TIM_EGR_UG;
    TIM1->SR = 0;
    TIM1->CNT = period - 1;
    DMA2->HIFCR = XY_DMA_CLEAR;
    DMA2_Stream5->NDTR = count;
    DMA2_Stream6->NDTR = count;
    DMA2_Stream5->CR = XY_DMA_CONFIG | interrupts | DMA_SxCR_EN;
    DMA2_Stream6->CR = XY_DMA_CONFIG | interrupts | DMA_SxCR_EN;
    TIM1->DIER = TIM_DIER_UDE | TIM_DIER_CC3DE;
}

static uint32_t
xy_launch(TIM_TypeDef *timer, uint32_t period, uint32_t scale, uint32_t first)
{
    uint32_t now = timer_read_time();
    int32_t delay = first - now;
    uint32_t lead = delay > 0 ? DIV_ROUND_UP((uint32_t)delay, scale) : 1;
    if (lead > INVENTURE_XY_SETUP / scale)
        shutdown("Inventure XY STEP setup outside the low phase");
    timer->CNT = period - lead;
    timer->CR1 |= TIM_CR1_CEN;
    // Read back the APB write before estimating the first hardware edge.
    (void)timer->CR1;
    uint32_t estimate = timer_read_time() + lead * scale;
    if ((int32_t)(estimate - first) > (int32_t)xy_step_peak_late)
        xy_step_peak_late = estimate - first;
    return estimate;
}

static uint16_t
xy_freeze_dma(void)
{
    TIM1->DIER = 0;
    TIM1->CR1 = TIM_CR1_URS;
    (void)TIM1->CR1;
    DMA2_Stream5->CR &= ~DMA_SxCR_EN;
    DMA2_Stream6->CR &= ~DMA_SxCR_EN;
    uint32_t until = timer_read_time() + INVENTURE_XY_FINISH;
    while ((DMA2_Stream5->CR | DMA2_Stream6->CR) & DMA_SxCR_EN) {
        if (!timer_is_before(timer_read_time(), until)) {
            // DMA2 has no other owner. A stalled stream must be reset before
            // another GPIO owner can use this pin. Position now needs Home.
            uint16_t left = DMA2_Stream5->NDTR;
            RCC->AHB1RSTR |= RCC_AHB1RSTR_DMA2RST;
            (void)RCC->AHB1RSTR;
            RCC->AHB1RSTR &= ~RCC_AHB1RSTR_DMA2RST;
            xy_step_verified &= ~INVENTURE_XY_PA6;
            xy_step_errors++;
            return left;
        }
    }
    uint16_t left = DMA2_Stream5->NDTR;
    DMA2->HIFCR = XY_DMA_CLEAR;
    return left;
}

static void
xy_detach_pwm(void)
{
    GPIOA->BSRR = XY_PA7_BIT << 16;
    GPIOA->MODER = (GPIOA->MODER & ~(3u << 14)) | (1u << 14);
    TIM3->CCER = 0;
    TIM2->CR1 = 0;
}

static void
inventure_xy_step_init(void)
{
    if (GPIOE->ODR & BIT(10))
        return;
    enable_pclock((uint32_t)(uintptr_t)TIM1);
    enable_pclock((uint32_t)(uintptr_t)TIM2);
    enable_pclock((uint32_t)(uintptr_t)TIM3);
    RCC->AHB1ENR |= RCC_AHB1ENR_DMA2EN;
    (void)RCC->AHB1ENR;

    TIM2->CR2 = XY_TIM_OC1_TRGO;
    TIM2->SMCR = (2u << 4) | XY_TIM_EXTERNAL;
    TIM2->CCMR1 = XY_TIM_PWM1_CH1;
    TIM2->ARR = 65535;
    TIM3->CR2 = XY_TIM_COMPARE_TRGO;
    TIM3->SMCR = (1u << 4) | XY_TIM_GATED;
    TIM3->CCMR1 = XY_TIM_PWM1_CH2;
    TIM3->CCR1 = TIM3->CCR2 = INVENTURE_XY_PULSE / 2;

    // Test the complete trigger loop with PA7 still GPIO and CC2E clear.
    xy_prepare_pwm(INVENTURE_XY_MIN_PERIOD / 2, 4);
    TIM3->CR1 = TIM_CR1_CEN;
    udelay(55);
    uint32_t stopped = TIM3->CNT;
    udelay(10);
    if (TIM2->CNT == 4 && TIM3->CNT == stopped && stopped >= TIM3->CCR2
        && stopped < TIM3->CCR2 + 32)
        xy_step_verified |= INVENTURE_XY_PA7;
    TIM3->CR1 = TIM2->CR1 = 0;

    // Exercise both real DMA requests and the GPIO bus destination. PA6
    // stays pulled low as an input while only its output latch is tested.
    // The board's general GPIO API reserves STEP as an output. Only this
    // board-owned boot check may temporarily isolate its output latch.
    GPIOA->PUPDR = (GPIOA->PUPDR & ~(3u << 12)) | (2u << 12);
    GPIOA->MODER &= ~(3u << 12);
    (void)GPIOA->MODER;
    DMA2_Stream5->PAR = DMA2_Stream6->PAR = (uint32_t)(uintptr_t)&GPIOA->BSRR;
    DMA2_Stream5->M0AR = (uint32_t)(uintptr_t)&xy_dma_set;
    DMA2_Stream6->M0AR = (uint32_t)(uintptr_t)&xy_dma_clear;
    DMA2_Stream5->FCR = DMA2_Stream6->FCR = 0;
    TIM1->CR1 = TIM_CR1_URS;
    xy_prepare_dma(timer_from_us(100), timer_from_us(30), 1, 0);
    TIM1->CR1 = TIM_CR1_URS | TIM_CR1_CEN;
    udelay(12);
    uint8_t rose = GPIOA->ODR & XY_PA6_BIT;
    udelay(38);
    if (rose && !(GPIOA->ODR & XY_PA6_BIT) && !DMA2_Stream5->NDTR
        && !DMA2_Stream6->NDTR && !(DMA2->HISR & XY_DMA_ERRORS))
        xy_step_verified |= INVENTURE_XY_PA6;
    xy_freeze_dma();
    raw_output(GPIOA, 6, 0);
    NVIC_SetPriority(DMA2_Stream5_IRQn, 3);
    NVIC_SetPriority(DMA2_Stream6_IRQn, 3);
    NVIC_EnableIRQ(DMA2_Stream5_IRQn);
    NVIC_EnableIRQ(DMA2_Stream6_IRQn);
}

uint8_t
inventure_xy_step_axis(uint32_t step_pin, uint32_t dir_pin)
{
    if (xy_step_verified != (INVENTURE_XY_PA7 | INVENTURE_XY_PA6))
        return 0;
    if (step_pin == GPIO('A', 7) && dir_pin == GPIO('B', 2))
        return INVENTURE_XY_PA7;
    return step_pin == GPIO('A', 6) && dir_pin == GPIO('B', 1)
        ? INVENTURE_XY_PA6 : 0;
}

uint8_t
inventure_xy_step_allowed(void)
{
    return xy_step_host && xy_step_verified == (INVENTURE_XY_PA7 | INVENTURE_XY_PA6)
        && inventure_native_queue_allowed()
        && (GPIOE->ODR & BIT(10));
}

uint32_t
inventure_xy_step_start(uint8_t axis, uint32_t period, uint16_t count,
                        uint32_t first)
{
    uint32_t pin = axis == INVENTURE_XY_PA7 ? XY_PA7_BIT : XY_PA6_BIT;
    if (!inventure_xy_step_allowed() || (xy_step_active & axis)
        || (GPIOA->IDR & pin))
        shutdown("Inventure XY timer start without a free enabled motor");
    if ((int32_t)(timer_read_time() - first) > (int32_t)timer_from_us(1000))
        shutdown("Inventure XY timer start too late");
    xy_trains[axis - 1].total = count;
    xy_step_active |= axis;
    if (axis == INVENTURE_XY_PA7) {
        xy_prepare_pwm(period / 2, count);
        TIM3->CCER = TIM_CCER_CC2E;
        GPIOA->AFR[0] = (GPIOA->AFR[0] & ~(15u << 28)) | (2u << 28);
        GPIOA->MODER = (GPIOA->MODER & ~(3u << 14)) | (2u << 14);
        return xy_launch(TIM3, period / 2, 2, first);
    }
    xy_prepare_dma(period, inventure_xy_step_high(axis), count,
                   DMA_SxCR_TEIE | DMA_SxCR_DMEIE);
    return xy_launch(TIM1, period, 1, first);
}

static uint16_t
xy_pwm_remaining(uint32_t count, uint8_t high)
{
    uint32_t emitted = count + high;
    uint16_t total = xy_trains[INVENTURE_XY_PA7 - 1].total;
    return emitted < total ? total - emitted : 0;
}

uint16_t
inventure_xy_step_remaining(uint8_t axis)
{
    if (!(xy_step_active & axis))
        return xy_trains[axis - 1].stopped_remaining;
    if (axis == INVENTURE_XY_PA6)
        return DMA2_Stream5->NDTR;
    uint32_t count;
    uint8_t high;
    do {
        count = TIM2->CNT;
        high = !!(GPIOA->IDR & XY_PA7_BIT);
    } while (count != TIM2->CNT);
    return xy_pwm_remaining(count, high);
}

uint16_t
inventure_xy_step_stop(uint8_t axis)
{
    struct xy_step_train *train = &xy_trains[axis - 1];
    if (!(xy_step_active & axis))
        return train->stopped_remaining;
    uint16_t left;
    if (axis == INVENTURE_XY_PA7) {
        TIM3->CR1 = 0;
        (void)TIM3->CR1;
        uint8_t high = !!(GPIOA->IDR & XY_PA7_BIT);
        // Allow a just emitted fall to clock the gate, or finish an
        // in-progress high pulse before returning the pin to GPIO.
        udelay(high ? 4 : 1);
        left = xy_pwm_remaining(TIM2->CNT, high);
        xy_detach_pwm();
    } else {
        uint8_t ready = xy_step_verified;
        left = xy_freeze_dma();
        if (GPIOA->IDR & XY_PA6_BIT)
            udelay(5);
        GPIOA->BSRR = XY_PA6_BIT << 16;
        if (ready != xy_step_verified)
            GPIOE->BSRR = BIT(10) << 16;
    }
    xy_step_active &= ~axis;
    train->stopped_remaining = left;
    if (left)
        xy_step_aborts++;
    return left;
}

void
inventure_xy_step_finish(uint8_t axis)
{
    struct xy_step_train *train = &xy_trains[axis - 1];
    uint8_t incomplete = !(xy_step_active & axis);
    if (axis == INVENTURE_XY_PA7)
        incomplete |= TIM2->CNT != train->total || (GPIOA->IDR & XY_PA7_BIT);
    else
        incomplete |= DMA2_Stream5->NDTR || DMA2_Stream6->NDTR
                   || (DMA2->HISR & XY_DMA_ERRORS) || (GPIOA->IDR & XY_PA6_BIT);
    if (incomplete) {
        xy_step_errors++;
        shutdown("Inventure XY timer incomplete pulse train");
    }
    inventure_xy_step_stop(axis);
    train->blocks++;
    train->steps += train->total;
}

static uint8_t
inventure_xy_step_quiesce(void)
{
    irqstatus_t flag = irq_save();
    uint8_t active = xy_step_active;
    if (active) {
        GPIOE->BSRR = BIT(10) << 16;
        TIM3->CR1 = 0;
        TIM1->DIER = 0;
        TIM1->CR1 = TIM_CR1_URS;
        if (active & INVENTURE_XY_PA7)
            inventure_xy_step_stop(INVENTURE_XY_PA7);
        if (active & INVENTURE_XY_PA6)
            inventure_xy_step_stop(INVENTURE_XY_PA6);
    }
    irq_restore(flag);
    return active;
}

void
inventure_xy_dma_error(void)
{
    if (!(DMA2->HISR & XY_DMA_ERRORS))
        return;
    xy_step_errors++;
    inventure_xy_step_quiesce();
    shutdown("Inventure XY STEP DMA error");
}
DECL_ARMCM_IRQ(inventure_xy_dma_error, DMA2_Stream5_IRQn);
DECL_ARMCM_IRQ(inventure_xy_dma_error, DMA2_Stream6_IRQn);

void
command_inventure_xy_step_config(uint32_t *args)
{
    (void)args;
    if (!inventure_is_armed() && !(GPIOE->ODR & BIT(10)))
        xy_step_host = xy_step_verified == (INVENTURE_XY_PA7 | INVENTURE_XY_PA6);
}
DECL_COMMAND(command_inventure_xy_step_config, "inventure_xy_step_config");

void
command_inventure_xy_step_query(uint32_t *args)
{
    (void)args;
    irqstatus_t flag = irq_save();
    uint32_t blocks_a = xy_trains[0].blocks, blocks_b = xy_trains[1].blocks;
    uint32_t steps_a = xy_trains[0].steps, steps_b = xy_trains[1].steps;
    uint32_t aborted = xy_step_aborts, errors = xy_step_errors, late = xy_step_peak_late;
    uint8_t verified = xy_step_verified, active = xy_step_active, host = xy_step_host;
    irq_restore(flag);
    sendf("inventure_xy_step_state verified=%c enabled=%c active=%c blocks_a=%u blocks_b=%u"
          " steps_a=%u steps_b=%u aborted=%u errors=%u peak_late=%u",
          verified, host, active, blocks_a, blocks_b, steps_a, steps_b, aborted,
          errors, late);
}
DECL_COMMAND_FLAGS(command_inventure_xy_step_query, HF_IN_SHUTDOWN,
                   "inventure_xy_step_query");
DECL_CONSTANT("INVENTURE_XY_STEP_TIMER_ABI", 1);
DECL_CONSTANT("INVENTURE_XY_STEP_MIN_TICKS", INVENTURE_XY_MIN_PERIOD);
