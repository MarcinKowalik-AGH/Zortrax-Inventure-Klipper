// SPDX-License-Identifier: GPL-3.0-or-later
// M300 finite pulse trains and the r0323 Inventure one-shot outputs.
#include "inventure_xy_step.h"
#include "board/armcm_boot.h"
#include "generic/armcm_timer.h"

_Static_assert(CONFIG_CLOCK_FREQ == 120000000, "Inventure STEP timer clock");
_Static_assert(INVENTURE_XY_ONESHOT_START == (TIM_CR1_OPM | TIM_CR1_CEN),
               "STM32 one-shot mode");
_Static_assert(INVENTURE_XY_MIN_PERIOD > INVENTURE_XY_ONESHOT_GAP + INVENTURE_XY_APB_MARGIN,
               "Minimum period must allow the PA6 one-shot to drain");

// RM0033 tables 22 and 60. PA7 uses TIM3_CH2/PWM with a TIM2 falling-edge
// counter. TIM6_UP triggers DMA1 stream 1 channel 7 to start TIM13_CH1 on
// PA6. PA6 pulse width is independent of DMA latency. OLED keeps stream 4.
enum {
    XY_PA7_BIT = 1u << 7, XY_PA6_BIT = 1u << 6,
    XY_PWM1 = 6u << 4, XY_PWM2 = 7u << 4,
    XY_DMA_ERRORS = DMA_LISR_TEIF1 | DMA_LISR_DMEIF1 | DMA_LISR_FEIF1,
    XY_DMA_CLEAR = DMA_LIFCR_CTCIF1 | DMA_LIFCR_CHTIF1 | DMA_LIFCR_CTEIF1
                 | DMA_LIFCR_CDMEIF1 | DMA_LIFCR_CFEIF1,
    XY_DMA_CONFIG = (7u << DMA_SxCR_CHSEL_Pos) | DMA_SxCR_DIR_0
                  | DMA_SxCR_MSIZE_1 | DMA_SxCR_PSIZE_1 | DMA_SxCR_PL,
};

struct xy_step_train {
    uint32_t blocks, steps, one_steps;
    uint16_t total, stopped_remaining;
};

static struct xy_step_train xy_trains[2];
static uint32_t xy_dma_start = INVENTURE_XY_ONESHOT_START;
static uint32_t xy_step_aborts, xy_step_errors, xy_step_peak_late;
static uint8_t xy_step_verified, xy_step_active, xy_step_host, xy_one_active;

uint8_t inventure_native_queue_allowed(void);

static TIM_TypeDef *
xy_one_timer(uint8_t axis)
{
    return axis == INVENTURE_XY_PA7 ? TIM14 : TIM13;
}

static uint8_t
xy_pin(uint8_t axis)
{
    return axis == INVENTURE_XY_PA7 ? 7 : 6;
}

static void
xy_pin_af(uint8_t pin, uint8_t af)
{
    unsigned mode = 2 * pin, alternate = 4 * pin;
    GPIOA->AFR[0] = (GPIOA->AFR[0] & ~(15u << alternate)) | ((uint32_t)af << alternate);
    GPIOA->MODER = (GPIOA->MODER & ~(3u << mode)) | (2u << mode);
}

static void
xy_prepare_one(TIM_TypeDef *timer)
{
    timer->CR1 = TIM_CR1_OPM;
    timer->CCER = timer->DIER = timer->PSC = 0;
    timer->ARR = INVENTURE_XY_FAST_HIGH / 2;
    timer->CCR1 = 1;
    timer->CCMR1 = XY_PWM2;
    timer->EGR = TIM_EGR_UG;
    timer->SR = 0;
}

static void
xy_attach_one(uint8_t axis)
{
    TIM_TypeDef *timer = xy_one_timer(axis);
    xy_prepare_one(timer);
    timer->CCER = TIM_CCER_CC1E;
    xy_pin_af(xy_pin(axis), 9);
}

static void
xy_detach_one(uint8_t axis)
{
    TIM_TypeDef *timer = xy_one_timer(axis);
    if (timer->CR1 & TIM_CR1_CEN)
        udelay(3);
    raw_output(GPIOA, xy_pin(axis), 0);
    timer->CCER = timer->CR1 = 0;
}

static void
xy_prepare_pwm(uint32_t period, uint16_t count)
{
    TIM3->CCER = 0;
    TIM3->ARR = period - 1;
    TIM3->EGR = TIM_EGR_UG;
    TIM3->CNT = period - 1;
    TIM2->CR1 = 0;
    TIM2->CCR1 = count;
    TIM2->EGR = TIM_EGR_UG;
    TIM2->CR1 = TIM_CR1_CEN;
}

static void
xy_prepare_dma(uint32_t period, uint16_t count, uint32_t interrupts)
{
    TIM6->DIER = 0;
    TIM6->CR1 = TIM_CR1_URS;
    TIM6->ARR = period - 1;
    TIM6->EGR = TIM_EGR_UG;
    TIM6->SR = 0;
    DMA1->LIFCR = XY_DMA_CLEAR;
    DMA1_Stream1->NDTR = count;
    DMA1_Stream1->CR = XY_DMA_CONFIG | interrupts | DMA_SxCR_EN;
    TIM6->DIER = TIM_DIER_UDE;
}

static uint16_t
xy_freeze_dma(void)
{
    TIM6->DIER = 0;
    TIM6->CR1 = TIM_CR1_URS;
    (void)TIM6->CR1;
    DMA1_Stream1->CR &= ~DMA_SxCR_EN;
    uint32_t until = timer_read_time() + INVENTURE_XY_APB_MARGIN;
    while (DMA1_Stream1->CR & DMA_SxCR_EN) {
        if (!timer_is_before(timer_read_time(), until)) {
            // The OLED owns another DMA1 stream. Isolate STEP without
            // resetting the shared controller and require a fresh boot.
            GPIOE->BSRR = BIT(10) << 16;
            raw_output(GPIOA, 6, 0);
            xy_step_verified &= ~INVENTURE_XY_PA6;
            xy_step_errors++;
            break;
        }
    }
    uint16_t left = DMA1_Stream1->NDTR;
    DMA1->LIFCR = XY_DMA_CLEAR;
    return left;
}

static uint8_t
xy_one_startup(TIM_TypeDef *timer)
{
    xy_prepare_one(timer);
    timer->CR1 = INVENTURE_XY_ONESHOT_START;
    uint8_t starts = !!(timer->CR1 & TIM_CR1_CEN);
    udelay(4);
    uint8_t stops = !(timer->CR1 & TIM_CR1_CEN) && !timer->CNT;
    timer->CR1 = 0;
    return starts && stops;
}

static void
inventure_xy_step_init(void)
{
    if (GPIOE->ODR & BIT(10))
        return;
    enable_pclock((uint32_t)(uintptr_t)TIM2);
    enable_pclock((uint32_t)(uintptr_t)TIM3);
    enable_pclock((uint32_t)(uintptr_t)TIM6);
    enable_pclock((uint32_t)(uintptr_t)TIM13);
    enable_pclock((uint32_t)(uintptr_t)TIM14);
    RCC->AHB1ENR |= RCC_AHB1ENR_DMA1EN;
    (void)RCC->AHB1ENR;

    TIM2->CR2 = 4u << 4;       // OC1REF as TRGO.
    TIM2->SMCR = (2u << 4) | 7; // TIM3 TRGO, external clock mode.
    TIM2->CCMR1 = XY_PWM1;
    TIM2->ARR = 65535;
    TIM3->CR2 = 3u << 4;       // CC1 match as TRGO.
    TIM3->SMCR = (1u << 4) | 5; // TIM2 gate.
    TIM3->CCMR1 = XY_PWM1 << 8;
    TIM3->CCR1 = TIM3->CCR2 = INVENTURE_XY_FAST_HIGH / 2;
    xy_prepare_pwm(INVENTURE_XY_MIN_PERIOD / 2, 4);
    TIM3->CR1 = TIM_CR1_CEN;
    udelay(30);
    uint32_t stopped = TIM3->CNT;
    udelay(5);
    if (TIM2->CNT == 4 && TIM3->CNT == stopped && stopped >= TIM3->CCR2
        && stopped < TIM3->CCR2 + 32 && xy_one_startup(TIM14))
        xy_step_verified |= INVENTURE_XY_PA7;
    TIM3->CR1 = TIM2->CR1 = 0;

    // PA6 remains GPIO, with CC1E clear. Test the actual TIM6 DMA route and
    // the OEM one-shot without exposing the test pulse to the driver.
    DMA1_Stream1->PAR = (uint32_t)(uintptr_t)&TIM13->CR1;
    DMA1_Stream1->M0AR = (uint32_t)(uintptr_t)&xy_dma_start;
    DMA1_Stream1->FCR = 0;
    xy_prepare_one(TIM13);
    xy_prepare_dma(6000, 1, 0);
    TIM6->CNT = 5999;
    TIM6->CR1 = TIM_CR1_URS | TIM_CR1_CEN;
    uint32_t until = timer_read_time() + INVENTURE_XY_APB_MARGIN;
    while (DMA1_Stream1->NDTR && timer_is_before(timer_read_time(), until))
        ;
    uint8_t started = !!(TIM13->CR1 & TIM_CR1_CEN);
    udelay(4);
    if (started && !DMA1_Stream1->NDTR && !(TIM13->CR1 & TIM_CR1_CEN)
        && !TIM13->CNT && !(DMA1->LISR & XY_DMA_ERRORS))
        xy_step_verified |= INVENTURE_XY_PA6;
    xy_freeze_dma();
    TIM13->CR1 = 0;
    NVIC_SetPriority(DMA1_Stream1_IRQn, 3);
    NVIC_EnableIRQ(DMA1_Stream1_IRQn);
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
        && inventure_native_queue_allowed() && (GPIOE->ODR & BIT(10));
}

static void
xy_require_free_motor(uint8_t axis)
{
    if (!inventure_xy_step_allowed() || ((xy_step_active | xy_one_active) & axis)
        || (GPIOA->IDR & BIT(xy_pin(axis))))
        shutdown("Inventure XY timer start without a free enabled motor");
}

volatile uint32_t *
inventure_xy_one_arm(uint8_t axis)
{
    xy_require_free_motor(axis);
    xy_attach_one(axis);
    xy_one_active |= axis;
    return &xy_one_timer(axis)->CR1;
}

void
inventure_xy_one_release(uint8_t axis)
{
    if (xy_one_active & axis) {
        xy_detach_one(axis);
        xy_one_active &= ~axis;
    }
}

void
inventure_xy_one_record(uint8_t axis, uint16_t count)
{
    xy_trains[axis - 1].one_steps += count;
}

uint32_t
inventure_xy_step_start(uint8_t axis, uint32_t period, uint16_t count,
                        uint32_t first)
{
    xy_require_free_motor(axis);
    if ((int32_t)(timer_read_time() - first) > (int32_t)timer_from_us(1000))
        shutdown("Inventure XY timer start too late");
    xy_trains[axis - 1].total = count;
    xy_step_active |= axis;
    TIM_TypeDef *timer = TIM6;
    if (axis == INVENTURE_XY_PA7) {
        timer = TIM3;
        xy_prepare_pwm(period / 2, count);
        TIM3->CCER = TIM_CCER_CC2E;
        xy_pin_af(7, 2);
    } else {
        xy_attach_one(axis);
        xy_prepare_dma(period / 2, count, DMA_SxCR_TEIE | DMA_SxCR_DMEIE);
    }
    int32_t delay = first - timer_read_time();
    uint32_t lead = delay > 0 ? DIV_ROUND_UP((uint32_t)delay, 2) : 1;
    if (lead > INVENTURE_XY_SETUP / 2)
        shutdown("Inventure XY STEP setup outside the low phase");
    timer->CNT = period / 2 - lead;
    timer->CR1 |= TIM_CR1_CEN;
    (void)timer->CR1;
    uint32_t estimate = timer_read_time() + lead * 2;
    if ((int32_t)(estimate - first) > (int32_t)xy_step_peak_late)
        xy_step_peak_late = estimate - first;
    return estimate;
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
        return DMA1_Stream1->NDTR;
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
        udelay(high ? 3 : 1);
        left = xy_pwm_remaining(TIM2->CNT, high);
        raw_output(GPIOA, 7, 0);
        TIM3->CCER = TIM2->CR1 = 0;
    } else {
        left = xy_freeze_dma();
        xy_detach_one(axis);
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
        incomplete |= DMA1_Stream1->NDTR || (DMA1->LISR & XY_DMA_ERRORS)
                    || (TIM13->CR1 & TIM_CR1_CEN) || (GPIOA->IDR & XY_PA6_BIT);
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
    uint8_t active = xy_step_active | xy_one_active;
    if (active) {
        GPIOE->BSRR = BIT(10) << 16;
        TIM3->CR1 = 0;
        TIM6->DIER = 0;
        TIM6->CR1 = TIM_CR1_URS;
        if (xy_step_active & INVENTURE_XY_PA7)
            inventure_xy_step_stop(INVENTURE_XY_PA7);
        if (xy_step_active & INVENTURE_XY_PA6)
            inventure_xy_step_stop(INVENTURE_XY_PA6);
        inventure_xy_one_release(INVENTURE_XY_PA7);
        inventure_xy_one_release(INVENTURE_XY_PA6);
    }
    irq_restore(flag);
    return active;
}

void
inventure_xy_dma_error(void)
{
    if (!(DMA1->LISR & XY_DMA_ERRORS))
        return;
    xy_step_errors++;
    inventure_xy_step_quiesce();
    shutdown("Inventure XY STEP DMA error");
}
DECL_ARMCM_IRQ(inventure_xy_dma_error, DMA1_Stream1_IRQn);

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
    struct xy_step_train a = xy_trains[0], b = xy_trains[1];
    uint32_t aborted = xy_step_aborts, errors = xy_step_errors, late = xy_step_peak_late;
    uint8_t verified = xy_step_verified, active = xy_step_active | xy_one_active;
    uint8_t host = xy_step_host;
    irq_restore(flag);
    sendf("inventure_xy_step_state verified=%c enabled=%c active=%c blocks_a=%u blocks_b=%u"
          " steps_a=%u steps_b=%u aborted=%u errors=%u peak_late=%u",
          verified, host, active, a.blocks, b.blocks, a.steps + a.one_steps,
          b.steps + b.one_steps, aborted, errors, late);
}
DECL_COMMAND_FLAGS(command_inventure_xy_step_query, HF_IN_SHUTDOWN,
                   "inventure_xy_step_query");
DECL_CONSTANT("INVENTURE_XY_STEP_TIMER_ABI", 2);
DECL_CONSTANT("INVENTURE_XY_STEP_MIN_TICKS", INVENTURE_XY_MIN_PERIOD);
DECL_CONSTANT("INVENTURE_XY_STEP_HIGH_TICKS", INVENTURE_XY_FAST_HIGH);
