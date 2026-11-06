/* InventureBoot P4-PERSIST r1/r2, 1.5-HW2, 1.6-RC2 and RELEASE 1.7 r3.
 * Vendor/unknown bootloaders always fall back to the application's 250000.
 * SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef INVENTURE_P4_BAUD_H
#define INVENTURE_P4_BAUD_H
#include <stdint.h>
#define INVENTURE_P4_BOOT_BASE UINT32_C(0x08000000)
#define INVENTURE_P4_STORE_OFFSET UINT32_C(0x7f00)
#define INVENTURE_P4_STORE_SIZE 256u
#define INVENTURE_P4_DEFAULT_BAUD UINT32_C(250000)
#define INVENTURE_P4_JOURNAL_ABI 1u
/* This application always follows BOOT. Keep the old diagnostic constant
 * at zero so a build configuration can never silently override the menu. */
#define INVENTURE_P4_FORCED_BAUD 0
#define INVENTURE_P4_BOOT17_ABI 1u

struct inventure_p4_selection {
    uint32_t baud;
    uint16_t slots_used;
    uint16_t invalid_slots;
    uint8_t revision;     /* 0=unknown, 1=r1, 2=r2, 3=1.6-RC2, 15=1.5-HW2, 17=1.7 */
    uint8_t saved_index;  /* 0..8 (1.7), 0..7 (1.6), 0..4 (older) */
    uint8_t has_saved;    /* 1 if any VALID record, even with torn entries */
};
void inventure_p4_from_image(const volatile uint8_t *boot_image,
                            struct inventure_p4_selection *out);
uint32_t inventure_p4_rate_at_index(unsigned index);
#endif
