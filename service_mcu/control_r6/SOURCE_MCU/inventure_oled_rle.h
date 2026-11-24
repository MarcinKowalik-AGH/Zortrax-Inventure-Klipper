// Marcin Kowalik <mkowalik@agh.edu.pl> | m300_oled_rle.h - Wypełnia bufor pikseli powtórzeniami bajtu przy rozpakowywaniu ramek RLE dla SSD1322.
// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef M300_OLED_RLE_H
#define M300_OLED_RLE_H

#include <stdint.h>
#include <string.h>

#if defined(__ARM_ARCH_7M__)
// Keep the registers clobbered by the wide store off the short-run path.
static void __attribute__((__noinline__))
oled_fill_long_run(uint8_t *dest, uint8_t value, unsigned count)
{
    // The DMA slot starts at a byte offset. Align before using word stores.
    while ((uintptr_t)dest & 3) {
        *dest++ = value;
        count--;
    }
    uint32_t word = value * 0x01010101u;
    unsigned blocks = count / 16;
    unsigned tail = count % 16;
    // STMIA stores four copies of the same word in one instruction.
    __asm__ volatile(
        "mov r1, %[word]\n\t"
        "mov r2, %[word]\n\t"
        "mov r3, %[word]\n\t"
        "mov r12, %[word]\n\t"
        "1: stmia %[dest]!, {r1, r2, r3, r12}\n\t"
        "subs %[blocks], %[blocks], #1\n\t"
        "bne 1b\n\t"
        "cmp %[tail], #0\n\t"
        "beq 2f\n\t"
        "tst %[tail], #8\n\t"
        "it ne\n\t"
        "stmiane %[dest]!, {r1, r2}\n\t"
        "tst %[tail], #4\n\t"
        "it ne\n\t"
        "strne r1, [%[dest]], #4\n\t"
        "tst %[tail], #2\n\t"
        "it ne\n\t"
        "strhne r1, [%[dest]], #2\n\t"
        "tst %[tail], #1\n\t"
        "it ne\n\t"
        "strbne r1, [%[dest]]\n"
        "2:"
        : [dest] "+&r" (dest), [blocks] "+&r" (blocks)
        : [word] "r" (word), [tail] "r" (tail)
        : "r1", "r2", "r3", "r12", "cc", "memory");
}
#endif

static inline void
oled_fill_run(uint8_t *dest, uint8_t value, unsigned count)
{
#if defined(__ARM_ARCH_7M__)
    if (count >= 32) {
        oled_fill_long_run(dest, value, count);
        return;
    }
#endif
    memset(dest, value, count);
}

#endif // M300_OLED_RLE_H
