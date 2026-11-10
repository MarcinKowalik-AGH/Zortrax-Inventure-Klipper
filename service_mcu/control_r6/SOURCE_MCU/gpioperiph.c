// Code to setup gpio on stm32 chip (except for stm32f1)
//
// Copyright (C) 2019-2021  Kevin O'Connor <kevin@koconnor.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "internal.h" // gpio_peripheral

// Set the mode, extended function and speed of a pin
void
gpio_peripheral(uint32_t gpio, uint32_t mode, int pullup)
{
    // Actuator inputs/analog remaps remain blocked. Native output setup is
    // allowed; GPIO writes are gated until explicit native control enable.
    // Prevent alternate function, analog mode or input remapping from
    // bypassing its output levels. Board init and fan test use raw GPIO.
    switch (gpio) {
    case GPIO('A',0): case GPIO('E',5): case GPIO('C',8):
    case GPIO('C',9): case GPIO('E',10):
    case GPIO('B',15): case GPIO('B',14): case GPIO('C',7):
    case GPIO('C',6): case GPIO('A',1): case GPIO('D',13):
    case GPIO('A',7): case GPIO('A',6): case GPIO('B',8): case GPIO('B',9):
    case GPIO('B',2): case GPIO('B',1): case GPIO('E',7): case GPIO('E',8):
    case GPIO('G',4):
        if((mode&15)==1)break;
        // Permit only the exact heater/fan timer AF after its first safe CCR
        // was loaded by the board's fixed hardware-PWM implementation.
        if(mode==(2u|(2u<<4)) && gpio==GPIO('A',0) && TIM5->CCR1==256)break;
        if(mode==(2u|(3u<<4)) && gpio==GPIO('E',5) && TIM9->CCR1==256)break;
        if(mode==(2u|(3u<<4)) && gpio==GPIO('C',8) && TIM8->CCR3==256)break;
        if(mode==(2u|(3u<<4)) && gpio==GPIO('C',9) && TIM8->CCR4==256)break;
        if(mode==(2u|(2u<<4)) && gpio==GPIO('A',1))break;
        if(mode==(2u|(2u<<4)) && gpio==GPIO('D',13))break;
        return;
    }

    GPIO_TypeDef *regs = gpio_pin_to_regs(gpio);

    // Enable GPIO clock
    gpio_clock_enable(regs);

    // Configure GPIO
    uint32_t mode_bits = mode & 0xf, func = (mode >> 4) & 0xf;
    uint32_t od = (mode >> 8) & 0x1, hs = (mode >> 9) & 0x1;
    uint32_t pup = pullup ? (pullup > 0 ? 1 : 2) : 0;
    uint32_t pos = gpio % 16, af_reg = pos / 8;
    uint32_t af_shift = (pos % 8) * 4, af_msk = 0x0f << af_shift;
    uint32_t m_shift = pos * 2, m_msk = 0x03 << m_shift;

    regs->AFR[af_reg] = (regs->AFR[af_reg] & ~af_msk) | (func << af_shift);
    regs->MODER = (regs->MODER & ~m_msk) | (mode_bits << m_shift);
    regs->PUPDR = (regs->PUPDR & ~m_msk) | (pup << m_shift);
    regs->OTYPER = (regs->OTYPER & ~(1 << pos)) | (od << pos);

    // Setup OSPEEDR:
    // stm32f0 is ~10Mhz at 50pF
    // stm32f2 is ~25Mhz at 40pF
    // stm32f4 is ~50Mhz at 40pF
    // stm32f7 is ~50Mhz at 40pF
    // stm32g0 is ~30Mhz at 50pF
    // stm32h7 is ~85Mhz at 50pF
    uint32_t ospeed = hs ? 0x03 : (CONFIG_MACH_STM32F0 ? 0x01 : 0x02);
    regs->OSPEEDR = (regs->OSPEEDR & ~m_msk) | (ospeed << m_shift);
}
