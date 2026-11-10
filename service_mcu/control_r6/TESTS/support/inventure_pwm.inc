// Included instead of generic hard_pwm.c for CONFIG_INVENTURE_V9 only.
// SPDX-License-Identifier: GPL-3.0-or-later
#include "autoconf.h"
#include "command.h"
#include "gpio.h"
#include "internal.h"
#include "sched.h"
#include "inventure_v9.h"
#define MAX_PWM 256u
DECL_CONSTANT("PWM_MAX", MAX_PWM);
struct iv9_pwm_map { TIM_TypeDef *timer; uint8_t pin, channel, af; uint16_t psc; uint32_t cycles; };
static const struct iv9_pwm_map iv9_pwm[] = {
    {TIM5, GPIO('A',0),1,2,10,5632},
    {TIM5, GPIO('A',1),2,2,10,5632},
    {TIM5, GPIO('A',2),3,2,10,5632},
    {TIM5, GPIO('A',3),4,2,10,5632},
    {TIM9, GPIO('E',5),1,3,49,12800},
    {TIM9, GPIO('E',6),2,3,49,12800},
    {TIM8, GPIO('C',8),3,3,65534,16776960},
    {TIM8, GPIO('C',9),4,3,65534,16776960},
    {TIM4, GPIO('D',13),2,2,4094,2096640},
};
struct gpio_pwm gpio_pwm_setup(uint8_t pin,uint32_t cycle_time,uint32_t val) {
    const struct iv9_pwm_map *p=0;
    for(unsigned i=0;i<ARRAY_SIZE(iv9_pwm);i++) if(iv9_pwm[i].pin==pin) {p=&iv9_pwm[i];break;}
    if(!p) shutdown("Inventure pin has no hardware PWM");
    // One fixed timebase for each shared timer. +/-2 CPU ticks for float rounding.
    if(cycle_time < p->cycles-2 || cycle_time > p->cycles+2)
        shutdown("Inventure incompatible shared PWM cycle");
    if(!is_enabled_pclock((uint32_t)p->timer)) enable_pclock((uint32_t)p->timer);
    TIM_TypeDef *t=p->timer;
    if(t->CR1 & 1) {
        if(t->PSC!=p->psc || t->ARR!=255) shutdown("Inventure PWM timer conflict");
    } else {
        t->CR1=0; t->PSC=p->psc; t->ARR=255; t->CNT=0; t->EGR=1;
    }
    unsigned ccshift=4*(p->channel-1);
    t->CCER &= ~(15u<<ccshift);
    volatile uint32_t *mode = p->channel<=2 ? &t->CCMR1 : &t->CCMR2;
    unsigned shift=((p->channel-1)&1)*8;
    *mode=(*mode & ~(255u<<shift)) | (0x60u<<shift); // PWM1, initial preload OFF
    volatile uint32_t *ccr;
    switch(p->channel) {
        case 1: ccr=&t->CCR1; break;
        case 2: ccr=&t->CCR2; break;
        case 3: ccr=&t->CCR3; break;
        case 4: ccr=&t->CCR4; break;
        default: shutdown("Inventure invalid PWM channel");
    }
    struct gpio_pwm g={.reg=(void*)ccr,.hwpwm_ticks=256};
    gpio_pwm_write(g,val);
    // With OCxPE cleared, the first CCR write is immediate even when another
    // channel already runs. Enabling preload afterwards avoids a one-period
    // inverted-heater glitch without resetting a shared timer's phase.
    *mode |= 8u << shift;
    if(!(t->CR1&1)) t->EGR=1;
    gpio_peripheral(pin,GPIO_FUNCTION(p->af),0);
    t->CCER |= 1u<<ccshift;
    if(t==TIM8) t->BDTR |= 1u<<15;
    t->CR1 |= 1;
    return g;
}
void gpio_pwm_write(struct gpio_pwm g,uint32_t val) {
    val=inventure_filter_pwm(g.reg,val,MAX_PWM);
    *(volatile uint32_t*)g.reg=val;
}
