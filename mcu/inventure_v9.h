#ifndef INVENTURE_V9_H
#define INVENTURE_V9_H
#include <stdint.h>
void inventure_early_init(void);
uint32_t inventure_filter_gpio(void *regs, uint32_t bit, uint32_t value);
uint32_t inventure_filter_pwm(void *reg, uint32_t value, uint32_t maximum);
void inventure_fan_observe(void *regs, uint32_t bit, uint32_t on_ticks, uint32_t cycle_ticks);
void inventure_adc_observe(void *adc, uint8_t channel, uint16_t value);
void inventure_adc_native_begin(void);
uint8_t inventure_panel_needs_service(void);
uint8_t inventure_is_armed(void);
void inventure_inputs_init(void);
void inventure_inputs_poll(void);
void inventure_fault_led(void);
void inventure_panel_shutdown(void);
void inventure_oled_watchdog(void);
#endif
