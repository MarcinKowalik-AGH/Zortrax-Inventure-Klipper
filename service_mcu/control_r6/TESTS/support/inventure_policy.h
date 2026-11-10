#ifndef INVENTURE_POLICY_H
#define INVENTURE_POLICY_H
#include <stdint.h>
/* Pure functions shared by MCU and native tests. Time intervals < 2^31. */
static inline int iv9_due(uint32_t now, uint32_t deadline) {
    return (int32_t)(now-deadline) >= 0;
}
static inline uint32_t iv9_clamp(uint32_t value, uint32_t maximum) {
    return value > maximum ? maximum : value;
}
static inline uint32_t iv9_safe_value(uint8_t armed, uint32_t value,
                                      uint32_t off_value) {
    return armed ? value : off_value;
}
static inline uint8_t iv9_frame_fits(uint16_t used, uint16_t length,
                                     uint16_t size) {
    return used < size && length <= size-1-used;
}
#endif
