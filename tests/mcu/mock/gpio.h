#pragma once
#include <stdint.h>
struct gpio_out {void *regs;uint32_t bit;};
struct gpio_in {void *regs;uint32_t bit;};
struct gpio_pwm {void *reg;uint32_t hwpwm_ticks;};
struct spi_config {void *spi;uint32_t spi_cr1;};
struct gpio_out gpio_out_setup(uint32_t,uint32_t);
void gpio_out_write(struct gpio_out,uint32_t);
struct gpio_in gpio_in_setup(uint32_t,int32_t);
struct gpio_pwm gpio_pwm_setup(uint8_t,uint32_t,uint32_t);
void gpio_pwm_write(struct gpio_pwm,uint32_t);
struct spi_config spi_setup(uint32_t,uint8_t,uint32_t);
void spi_prepare(struct spi_config);
void spi_transfer(struct spi_config,uint8_t,uint8_t,uint8_t*);

struct gpio_adc {void *adc;uint32_t chan;};
struct gpio_adc gpio_adc_setup(uint32_t);
uint32_t gpio_adc_sample(struct gpio_adc);
uint16_t gpio_adc_read(struct gpio_adc);
