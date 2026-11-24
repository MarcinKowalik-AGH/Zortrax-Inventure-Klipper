#pragma once
#include <stdint.h>
uint32_t timer_read_time(void);uint32_t timer_from_us(uint32_t);
static inline int timer_is_before(uint32_t a,uint32_t b){return (int32_t)(a-b)<0;}
