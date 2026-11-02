#line 1000 "inventure_oled_local.inc"
/* RC6: autonomous, pre-host SSD1322 start page on V9.
 * Zero motion/heater writes. Only the OLED pins and SPI2 are touched.
 * Runs a bounded amount of work per Klipper task iteration; no HAL delay.
 * The host's first valid OLED session always takes ownership permanently.
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "board/io.h"
#include "inventure_p4_baud.h"
#include <string.h>
#include "inventure_oled_stock_glyphs.inc"

#define LOCAL_WIRE_BYTES 8192u
#define LOCAL_TICKS_MS(ms) ((uint32_t)(CONFIG_CLOCK_FREQ / 1000u) * (ms))
#define LOCAL_TICKS_US(us) ((uint32_t)(CONFIG_CLOCK_FREQ / 1000000u) * (us))
#define LOCAL_TICK_DUE(now, end) ((int32_t)((now)-(end)) >= 0)
enum {
    LOCAL_OFF=0, LOCAL_WAIT_POWER=1, LOCAL_WAIT_LOW=2,
    LOCAL_WAIT_HIGH=3, LOCAL_INIT_CMDS=4, LOCAL_WINDOW=5,
    LOCAL_FRAME=6, LOCAL_DISPLAY_ON=7, LOCAL_READY=8,
    LOCAL_FAILED=9, LOCAL_HANDOVER=10
};
static uint8_t local_state, local_reset_released;
static uint32_t local_due;
static uint8_t local_epoch_started;
static unsigned local_init_pos, local_frame_pos;
static uint8_t local_frame[LOCAL_WIRE_BYTES]; /* 8 KiB local only; host owns its own frame */
static struct gpio_out local_cs, local_dc, local_reset, local_gate;
static struct spi_config local_spi;
static struct inventure_p4_selection local_boot;
static const uint8_t local_oled_init_bytes[] = {
    0xfd,1,0x12,0xae,0,0xb3,1,0x91,0xca,1,0x3f,0xa2,1,0,0xa1,1,0,
    0xa0,2,0x14,0x11,0xb5,1,0,0xab,1,1,0xb4,2,0xa0,0xfd,
    0xc1,1,0x9f,0xc7,1,0x0f,0xb9,0,
    0xb1,1,0xe2,0xd1,2,0xa2,0x20,0xbb,1,0x1f,0xb6,1,8,0xbe,1,7,
    0xa6,0,0xa9,0
};
static int local_has_claim(void) {
    return local_state >= LOCAL_WAIT_POWER && local_state <= LOCAL_READY;
}
static void local_oled_cancel(void) {
    local_state=LOCAL_HANDOVER;
}
static void local_oled_failed(void) {
    local_state=LOCAL_FAILED;
    local_reset_released=0;
    oled_raw_blank(); /* only the panel: no global shutdown or heater mutation */
}
/* Local SPI: nonfatal, bounded polling. A missing OLED must NOT issue shutdown().
 * A board-level SPI failure is reported by local status and cannot arm anything. */
static int local_spi_write(uint8_t dc, const uint8_t *data, unsigned len) {
    if (!local_has_claim() || oled_active || oled_latched || len>32) return 0;
    gpio_out_write(local_dc, dc);
    spi_prepare(local_spi);
    gpio_out_write(local_cs, 0);
    for(unsigned i=0;i<len;i++) {
        uint32_t deadline=timer_read_time()+LOCAL_TICKS_US(1000);
        while (!(SPI2->SR & (1u<<1))) {
            if (LOCAL_TICK_DUE(timer_read_time(),deadline)) goto fail;
        }
        writeb((void*)&SPI2->DR,data[i]);
    }
    uint32_t deadline=timer_read_time()+LOCAL_TICKS_US(1000);
    while ((SPI2->SR & ((1u<<1)|(1u<<7))) != (1u<<1)) {
        if (LOCAL_TICK_DUE(timer_read_time(),deadline)) goto fail;
    }
    gpio_out_write(local_cs,1);
    return 1;
fail:
    gpio_out_write(local_cs,1);
    local_oled_failed();
    return 0;
}
static int local_cmd(uint8_t cmd, const uint8_t *params, unsigned count) {
    if(!local_spi_write(0,&cmd,1))return 0;
    return !count || local_spi_write(1,params,count);
}
static void local_pixel(unsigned x,int y,unsigned level) {
    if(x>=256u || y<0 || y>=64)return;
    unsigned offset=(unsigned)y*128u+(x>>1), shift=(x&1u)?0u:4u;
    local_frame[offset]=(uint8_t)((local_frame[offset]&~(15u<<shift))
                                    | ((level&15u)<<shift));
}
static const uint8_t *local_glyph(unsigned code,unsigned *width,unsigned *span) {
    unsigned n=(code>=32u && code<=126u) ? code-32u : '?' - 32u;
    const uint8_t *p=glyph_packed;
    for(unsigned count=0;count<95u;count++) {
        uint8_t header=*p++;
        unsigned w=header>>4u,s=header&15u;
        s=s==15u?*p++:glyph_span[s];
        if(n==0u) {
            *width=w;*span=s;return p;
        }
        p+=(w*(s&15u)+7u)>>3u;
        n--;
    }
    *width=0;*span=0;return glyph_packed;
}
static void local_text(unsigned x,unsigned y,const char *str) {
    while (*str && x<250u) {
        unsigned width,span,code=(uint8_t)*str++;
        const uint8_t *bits=local_glyph(code,&width,&span);
        if(x+width>252u)break;
        for(unsigned r=0;r<(span&15u);r++)
            for(unsigned c=0;c<width;c++) {
                unsigned b=r*width+c;
                if (bits[b>>3u] & (1u<<(b&7u)))
                    local_pixel(x+c,(int)y+(int)(span>>4u)+(int)r-3,15u);
            }
        x+=width+1u;
    }
}
static void local_baud_line(char out[24],uint32_t baud) {
    const char prefix[]="P4: ";
    unsigned n=0;
    for(unsigned i=0;i<sizeof(prefix)-1u;i++)out[n++]=prefix[i];
    char digits[10];unsigned k=0;
    do { digits[k++]=(char)('0'+baud%10u);baud/=10u; } while(baud && k<sizeof(digits));
    while(k)out[n++]=digits[--k];
    out[n++]=' ';out[n++]='8';out[n++]='N';out[n++]='1';out[n]=0;
}
static void local_render(void) {
    memset(local_frame,0,sizeof(local_frame));
    local_text(4,2,"ZORTRAX INVENTURE");
    for(unsigned x=4;x<252u;x++)local_pixel(x,12,5u);
    local_text(4,18,"Waiting for Klipper...");
    if(local_boot.revision==1) local_text(4,34,"MCU: RC7.1  BOOT: 1.4 r1");
    else if(local_boot.revision==2) local_text(4,34,"MCU: RC7.1  BOOT: 1.4 r2");
    else if(local_boot.revision==3) local_text(4,34,"MCU: RC7.1  BOOT: 1.6 RC2");
    else if(local_boot.revision==15) local_text(4,34,"MCU: RC7.1  BOOT: 1.5 HW2");
    else local_text(4,34,"MCU: RC7.1  BOOT: unknown");
    char baud[24];
    local_baud_line(baud,local_boot.baud);
    local_text(4,50,baud);
}
/* Minimal init: no waiting or SPI writes in init callback, only GPIO/PERIPH.
 * Called after clock_setup by Klipper DECL_INIT; before any config command. */
void inventure_local_oled_init(void) {
    inventure_p4_from_image((const volatile uint8_t*)INVENTURE_P4_BOOT_BASE,&local_boot);
#if CONFIG_SERIAL_BAUD == 1000000 || CONFIG_SERIAL_BAUD == 2000000
    /* Local splash must report the REAL USART baud, not the stored baud. */
    local_boot.baud=CONFIG_SERIAL_BAUD;
#endif
    local_cs=gpio_out_setup(GPIO('D',1),1);
    local_dc=gpio_out_setup(GPIO('D',2),0);
    local_gate=gpio_out_setup(GPIO('D',3),1);
    local_reset=gpio_out_setup(GPIO('D',4),0);
    local_reset_released=0;
    local_spi=spi_setup(0,0,2000000);
    local_init_pos=local_frame_pos=0;
    local_state=LOCAL_WAIT_POWER;
    local_due=0;local_epoch_started=0;
}
DECL_INIT(inventure_local_oled_init);
/* One bounded 32-byte SPI block per task run; does not steal motion timers. */
void inventure_local_oled_task(void) {
    if(!local_has_claim() || oled_latched || oled_active)return;
    uint32_t now=timer_read_time();
    // M300 timer-epoch transfer: first task iteration occurs after all
    // DECL_INIT callbacks, including the core cycle counter reset. No stale
    // pre-timer-init deadline or busy catch-up loop is possible.
    if (!local_epoch_started) {
        local_epoch_started=1;
        local_due=now+LOCAL_TICKS_MS(500);
        return;
    }
    if(local_state==LOCAL_WAIT_POWER && LOCAL_TICK_DUE(now,local_due)) {
        gpio_out_write(local_reset,0);
        local_due=now+LOCAL_TICKS_MS(10);
        local_state=LOCAL_WAIT_LOW;
    } else if(local_state==LOCAL_WAIT_LOW && LOCAL_TICK_DUE(now,local_due)) {
        gpio_out_write(local_reset,1);
        local_reset_released=1;
        oled_reset_value=1;
        local_due=now+LOCAL_TICKS_MS(20);
        local_state=LOCAL_WAIT_HIGH;
    } else if(local_state==LOCAL_WAIT_HIGH && LOCAL_TICK_DUE(now,local_due)) {
        local_render();local_init_pos=0;local_state=LOCAL_INIT_CMDS;
    } else if(local_state==LOCAL_INIT_CMDS) {
        if(local_init_pos>=sizeof(local_oled_init_bytes)) {
            local_state=LOCAL_WINDOW;return;
        }
        unsigned idx=local_init_pos;
        if(idx+2u>sizeof(local_oled_init_bytes)) {local_oled_failed();return;}
        uint8_t cmd=local_oled_init_bytes[idx++],count=local_oled_init_bytes[idx++];
        if(idx+count>sizeof(local_oled_init_bytes) || !local_cmd(cmd,&local_oled_init_bytes[idx],count)) {
            local_oled_failed();return;
        }
        local_init_pos=idx+count;
    } else if(local_state==LOCAL_WINDOW) {
        const uint8_t col[2]={0x1c,0x5b},row[2]={0,63};
        if(!local_cmd(0x15,col,2) || !local_cmd(0x75,row,2) ||
           !local_cmd(0x5c,NULL,0)) {
            local_oled_failed();return;
        }
        local_frame_pos=0;local_state=LOCAL_FRAME;
    } else if(local_state==LOCAL_FRAME) {
        if(local_frame_pos<sizeof(local_frame)) {
            if(!local_spi_write(1,local_frame+local_frame_pos,32)) {
                local_oled_failed();return;
            }
            local_frame_pos+=32u;
        }
        if(local_frame_pos==sizeof(local_frame))local_state=LOCAL_DISPLAY_ON;
    } else if(local_state==LOCAL_DISPLAY_ON) {
        if(!local_cmd(0xaf,NULL,0)) {local_oled_failed();return;}
        local_state=LOCAL_READY;
    }
}
DECL_TASK(inventure_local_oled_task);
/* The host may query before taking over. No write-back to boot Flash. */
void command_inventure_local_oled_query(uint32_t *args) {
    (void)args;
    sendf("inventure_local_oled_status state=%c boot_revision=%c baud=%u",
          local_state,local_boot.revision,local_boot.baud);
}
DECL_COMMAND_FLAGS(command_inventure_local_oled_query,HF_IN_SHUTDOWN,
                   "inventure_local_oled_query");
