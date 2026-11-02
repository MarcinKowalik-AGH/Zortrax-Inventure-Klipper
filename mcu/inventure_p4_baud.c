/* InventureBoot r1/r2, v1.5-HW2, and 1.6-RC2 P4-PERSIST-r3.
 * Source: user Drive manifest and authenticated full 32KiB InventureBoot
 * 1.6-RC2 UART5 image sha256 577d1780fec0d423af54662da2580139d438b1757ff7ba68712fa73143a665ec.
 * Original seven codes and new 2M = 0x5a verified in full image code table.
 * Bootwait codes 0xc3/0x3c are separate journal settings; not baud updates.
 * Old version r1/r2/v1.5 deliberately restrict recognized speeds to five.
 * Unlike the bootloader at APB2=16 MHz, this MCU uses APB2=60 MHz,
 * so 2M uses exact USART6 DIV=30 with OVER8=0 (16x sampling).
 * Legacy implementation behavior:
 * last valid entry wins; any non-FF byte consumes a slot; invalid/torn bytes
 * do NOT erase an earlier choice; erased journal -> index 3 (250000 8N1).
 * No erase/write API is present. The scan is bounded to boot sectors 0-1.
 * SPDX-License-Identifier: GPL-3.0-or-later */
#include "inventure_p4_baud.h"

static const uint8_t p4_codes[8] = {0x0f,0x33,0x55,0xaa,0xcc,0xf0,0x66,0x5a};
static const uint32_t p4_rates[8] = {19200u,57600u,115200u,250000u,500000u,
                                   750000u,1000000u,2000000u};

uint32_t
inventure_p4_rate_at_index(unsigned index)
{
    return index < 8u ? p4_rates[index] : 0u;
}

/* The release marker is part of the verified bootloader's code region.
 * Without it, DO NOT interpret arbitrary bytes at 0x08007F00 as a journal.
 * This also permits older v1.4 builds which used a fixed 250000 baud. */
static uint8_t
boot_revision(const volatile uint8_t *image)
{
    static const uint8_t tag[] = "P4-PERSIST-r";
    const unsigned tag_len = sizeof(tag) - 1u;
    const unsigned limit = INVENTURE_P4_STORE_OFFSET - tag_len - 2u;
    for (unsigned pos = 0; pos <= limit; pos++) {
        if (image[pos] != 'P')
            continue;
        unsigned n = 0;
        while (n < tag_len && image[pos + n] == tag[n])
            n++;
        if (n != tag_len)
            continue;
        uint8_t revision = image[pos + tag_len];
        if ((revision == '1' || revision == '2') && !image[pos + tag_len + 1u])
            return revision == '1' ? 1u : 2u;
    }
    /* Canonical published InventureBoot 1.5-HW2 does not contain the older
     * P4-PERSIST-r1/r2 marker. Recognize the two independent, verified
     * version strings from the public 32768-byte image; NEVER guess an
     * unknown high-speed journal code. The journal format remains five bytes.
     * Source: InventureBoot v1.5-HW2 public STLINK image, 2026-10-29.
     */
    static const uint8_t marker[] = "RELEASE 1.5-HW2";
    static const uint8_t identity[] = "Inventure boot v1.5";
    const unsigned n=sizeof(marker)-1u,m=sizeof(identity)-1u;
    uint8_t found_marker=0,found_identity=0;
    for(unsigned pos=0;pos+n<INVENTURE_P4_STORE_OFFSET;pos++) {
        if(image[pos]==marker[0]) {
            unsigned i=1u;
            for(;i<n;i++)if(image[pos+i]!=marker[i])break;
            if(i==n)found_marker=1;
        }
        if(image[pos]==identity[0] && pos+m<INVENTURE_P4_STORE_OFFSET) {
            unsigned i=1u;
            for(;i<m;i++)if(image[pos+i]!=identity[i])break;
            if(i==m)found_identity=1;
        }
        if(found_marker && found_identity) return 15u;
    }
    /* v1.6-RC2's two independent markers and product identity were verified
     * at actual offsets 0x67c6/0x67d4/0x6752 in the SHA-pinned image.
     * Do NOT match any single generic 1.6 string alone. */
    static const uint8_t r3[] = "P4-PERSIST-r3";
    static const uint8_t build[] = "BUILD 1.6-RC2";
    static const uint8_t product[] = "Inventure boot v1.6";
    uint8_t flags=0;
    for(unsigned pos=0;pos<INVENTURE_P4_STORE_OFFSET;pos++) {
        const uint8_t *labels[3]={r3,build,product};
        for(unsigned k=0;k<3;k++) {
            unsigned len=k==0?sizeof(r3)-1u:(k==1?sizeof(build)-1u:sizeof(product)-1u);
            if(pos+len>=INVENTURE_P4_STORE_OFFSET || image[pos]!=labels[k][0])continue;
            unsigned j=1u;
            while(j<len && image[pos+j]==labels[k][j]) j++;
            if(j==len && (k!=0 || image[pos+len]==0u)) flags|=1u<<k;
        }
        if(flags==7u)return 3u;
    }
    return 0u;
}

void
inventure_p4_from_image(const volatile uint8_t *image,
                        struct inventure_p4_selection *out)
{
    out->baud = INVENTURE_P4_DEFAULT_BAUD;
    out->slots_used = 0;
    out->invalid_slots = 0;
    out->revision = boot_revision(image);
    out->saved_index = 3u;
    out->has_saved = 0;
    if (!out->revision)
        return;
    /* Scan ALL 256 bytes. Holes/torn records are handled exactly like the
     * bootloader's p4_store.c, rather than 'stop at first erased byte'. */
    for (unsigned slot = 0; slot < INVENTURE_P4_STORE_SIZE; slot++) {
        uint8_t code = image[INVENTURE_P4_STORE_OFFSET + slot];
        if (code != 0xffu)
            out->slots_used = slot + 1u;
        int matched = 0;
        for (unsigned i=0; i < (out->revision==3u?8u:5u); i++) {
            if (code == p4_codes[i]) {
                out->saved_index = (uint8_t)i;
                out->has_saved = 1u;
                matched = 1;
                break;
            }
        }
        /* r3's on-bootwait setting uses distinct codes 0xc3/0x3c. It must
         * consume a slot but never alter the last valid baud. */
        if (code != 0xffu && !matched &&
            !(out->revision==3u && (code==0xc3u || code==0x3cu)))
            out->invalid_slots++;
    }
    out->baud = inventure_p4_rate_at_index(out->saved_index);
}
