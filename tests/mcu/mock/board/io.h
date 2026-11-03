#pragma once
#include <stdint.h>
static inline uint8_t readb(void*p){return *(volatile uint8_t*)p;}
void test_writeb(void*,uint8_t);
#define writeb test_writeb
