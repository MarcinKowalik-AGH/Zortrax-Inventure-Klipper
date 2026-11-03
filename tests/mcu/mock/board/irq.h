#pragma once
#include <stdint.h>
typedef uint32_t irqstatus_t;
irqstatus_t irq_save(void);void irq_restore(irqstatus_t);
