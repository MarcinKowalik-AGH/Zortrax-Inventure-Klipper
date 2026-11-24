#line 1000 "inventure_oled_local.inc"
/* RC8: autonomous SSD1322 waiting/shutdown page on V9.
 * Zero motion/heater writes. Only the OLED pins and SPI2 are touched.
 * Runs a bounded amount of work per Klipper task iteration; no HAL delay.
 * New host generations may take ownership; a global fault remains latched.
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "board/io.h"
#include "inventure_p4_baud.h"
#include <string.h>
#include "inventure_oled_stock_glyphs.inc"
#include "inventure_oled_activity.inc"
#include "inventure_oled_error_icons.inc"
#include "inventure_oled_startup_logo.inc"
static const uint16_t local_glyph_offsets[95]={
    0,1,3,6,15,23,32,39,41,47,53,58,61,64,67,70,
    75,81,85,91,97,103,109,115,121,127,133,136,140,143,146,149,
    155,168,176,183,190,197,204,211,219,226,228,234,241,248,256,263,
    271,278,286,293,300,308,316,324,332,340,348,355,359,364,368,371,
    374,377,382,388,393,399,404,410,416,422,424,428,433,435,442,447,
    452,458,464,468,473,478,483,488,495,500,506,511,517,520,526,
};

#define LOCAL_WIRE_BYTES 8192u
#define LOCAL_TICKS_MS(ms) ((uint32_t)(CONFIG_CLOCK_FREQ / 1000u) * (ms))
#define LOCAL_TICKS_US(us) ((uint32_t)(CONFIG_CLOCK_FREQ / 1000000u) * (us))
#define LOCAL_TICK_DUE(now, end) ((int32_t)((now)-(end)) >= 0)
enum {
    LOCAL_OFF=0, LOCAL_WAIT_POWER=1, LOCAL_WAIT_LOW=2,
    LOCAL_WAIT_HIGH=3, LOCAL_INIT_CMDS=4, LOCAL_WINDOW=5,
    LOCAL_FRAME=6, LOCAL_DISPLAY_ON=7, LOCAL_READY=8,
    LOCAL_FAILED=9, LOCAL_HANDOVER=10, LOCAL_ICON_WINDOW=11,
    LOCAL_ICON_FRAME=12, LOCAL_SPLASH_HOLD=13
};
static uint8_t local_state, local_reset_released;
static uint32_t local_due;
static uint8_t local_epoch_started, local_timer_active, local_startup_splash;
static uint8_t local_animation_frame, local_icon_row;
static struct task_wake local_wake;
static struct timer local_timer;
static struct inventure_display_error local_error;
static unsigned local_init_pos, local_frame_pos;
static uint8_t local_frame[128]; /* one row; no 8 KiB MCU framebuffer */
static unsigned local_row;
enum { LOCAL_BOOT, LOCAL_RELEASE, LOCAL_EXPIRED, LOCAL_SHUTDOWN, LOCAL_TRANSFER, LOCAL_PANEL_SPI };
static uint8_t local_start_pending, local_reason, local_last_incident;
static uint8_t local_brightness = 16;
/* Native 4bpp M300 icons above replace the old thresholded 1bpp glyphs. */
static uint32_t local_epoch;
static struct gpio_out local_cs, local_dc, local_reset, local_gate;
static struct spi_config local_spi;
static struct inventure_p4_selection local_boot;
static const uint8_t local_oled_init_bytes[] = {
    0xfd,1,0x12,0xae,0,0xb3,1,0x91,0xca,1,0x3f,0xa2,1,0,0xa1,1,0,
    0xa0,2,0x14,0x11,0xb5,1,0,0xab,1,1,0xb4,2,0xa0,0xfd,
    0xc1,1,0x9f,0xc7,1,0x0f,0xb8,15,
    0x0c,0x18,0x24,0x30,0x3c,0x48,0x54,0x60,0x6c,0x78,0x84,0x90,0x9c,0xa8,0xb4,0x00,0,
    0xb1,1,0xe2,0xd1,2,0xa2,0x20,0xbb,1,0x1f,0xb6,1,8,0xbe,1,7,
    0xa6,0,0xa9,0
};
static int local_has_claim(void) {
    return (local_state >= LOCAL_WAIT_POWER && local_state <= LOCAL_READY)
        || local_state==LOCAL_ICON_WINDOW || local_state==LOCAL_ICON_FRAME
        || local_state==LOCAL_SPLASH_HOLD;
}
static void local_oled_cancel(void) {
    oled_restore_light();
    if(local_timer_active){sched_del_timer(&local_timer);local_timer_active=0;}
    local_startup_splash=0;
    local_epoch++;local_start_pending=0;
    local_state=LOCAL_HANDOVER;
}
static void local_oled_request(uint8_t reason) {
    oled_restore_light();
    if (reason >= LOCAL_EXPIRED) local_last_incident=reason;
    local_startup_splash=0;
    local_epoch++;local_reason=reason;local_start_pending=1;
    sched_wake_task(&local_wake);
    local_state=LOCAL_HANDOVER;
}
uint8_t inventure_panel_needs_service(void) {
    return local_start_pending || (local_has_claim() && local_state!=LOCAL_READY);
}
static void local_oled_failed(void) {
    if(oled_active || !local_has_claim())return;
    local_last_incident=LOCAL_PANEL_SPI;
    oled_display_failed=1;
    local_state=LOCAL_FAILED;local_start_pending=0;
    if(local_timer_active){sched_del_timer(&local_timer);local_timer_active=0;}
    local_reset_released=0;
    oled_raw_blank(); /* only the panel: no global shutdown or heater mutation */
}
/* Local SPI: nonfatal, bounded polling. A missing OLED must NOT issue shutdown().
 * A board-level SPI failure is reported by local status and cannot arm anything. */
static int local_spi_write(uint8_t dc, const uint8_t *data, unsigned len) {
    if (!local_has_claim() || oled_active || len>32) return 0;
    uint32_t epoch=local_epoch;
    gpio_out_write(local_dc, dc);
    spi_prepare(local_spi);
    irqstatus_t flags=irq_save();
    uint8_t live=local_has_claim() && !oled_active && epoch==local_epoch;
    if(live)gpio_out_write(local_cs,0);
    irq_restore(flags);
    if(!live)return 0;
    uint32_t deadline=timer_read_time()+LOCAL_TICKS_US(1000);
    for(unsigned i=0;i<len;i++) {
        if(epoch!=local_epoch)goto cancelled;
        while (!(SPI2->SR & (1u<<1))) {
            if(epoch!=local_epoch)goto cancelled;
            if (LOCAL_TICK_DUE(timer_read_time(),deadline)) goto fail;
        }
        writeb((void*)&SPI2->DR,data[i]);
    }
    while ((SPI2->SR & ((1u<<1)|(1u<<7))) != (1u<<1)) {
        if(epoch!=local_epoch)goto cancelled;
        if (LOCAL_TICK_DUE(timer_read_time(),deadline)) goto fail;
    }
    gpio_out_write(local_cs,1);
    return epoch==local_epoch;
cancelled:
    gpio_out_write(local_cs,1);return 0;
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
    if(x>=256u || y<0 || (unsigned)y!=local_row)return;
    unsigned offset=x>>1, shift=(x&1u)?0u:4u;
    local_frame[offset]=(uint8_t)((local_frame[offset]&~(15u<<shift))
                                    | ((level&15u)<<shift));
}
static const uint8_t *local_glyph(unsigned code,unsigned *width,unsigned *span) {
    unsigned n=(code>=32u && code<=126u) ? code-32u : '?' - 32u;
    const uint8_t *p=glyph_packed+local_glyph_offsets[n];
    uint8_t header=*p++;*width=header>>4u;
    unsigned s=header&15u;*span=s==15u?*p++:glyph_span[s];
    return p;
}
static void local_text(unsigned x,unsigned y,const char *str) {
    if(local_row+3u<y || local_row>y+25u)return;
    while (*str && x<250u) {
        unsigned width,span,code=(uint8_t)*str++;
        const uint8_t *bits=local_glyph(code,&width,&span);
        if(x+width>192u)break;
        int row=(int)local_row-(int)y-(int)(span>>4u)+3;
        if(row>=0 && (unsigned)row<(span&15u)) {
            unsigned r=row;
            for(unsigned c=0;c<width;c++) {
                unsigned b=r*width+c;
                if (bits[b>>3u] & (1u<<(b&7u)))
                    local_pixel(x+c,(int)y+(int)(span>>4u)+(int)r-3,15u);
            }
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
static void local_render_icon(unsigned row) {
    if(row<7 || row>=57)return;
    unsigned y=row-7;
    const uint8_t *pixels;
    if(local_error.kind) {
        unsigned icon=display_catalog[local_error.kind].icon;
        pixels=local_error_rle+local_error_frames[icon][local_animation_frame][y];
    } else pixels=local_activity_rle+local_activity_frames[local_reason>=LOCAL_EXPIRED][local_animation_frame][y];
    unsigned x=0;
    while(x<50) {
        unsigned token=*pixels++,end=x+(token>>4)+1,level=token&15;
        if(end>50)end=50;
        while(x<end){if(level)local_pixel(200+x,row,level);x++;}
    }
}
static void local_render(unsigned row) {
    local_row=row;memset(local_frame,0,sizeof(local_frame));
    if(local_startup_splash && !local_error.kind) {
        if(row>=12 && row<52)memcpy(local_frame+24,m300_startup_logo[row-12],80);
        return;
    }
    const struct inventure_display_text *error=&display_catalog[local_error.kind];
    if(local_error.kind) {
        char title[]="Error #000";
        title[7]='0'+error->code/100;title[8]='0'+(error->code/10)%10;title[9]='0'+error->code%10;
        local_text(4,2,error->code ? title : "INVENTURE - Error");
        local_text(4,18,error->title);local_text(4,34,error->hint1);local_text(4,50,error->hint2);
    } else {
        local_text(4,2,"ZORTRAX INVENTURE");
        local_text(4,18,"Waiting for Klipper...");
        local_text(4,34,local_boot.revision==17 ? "MCU: R23  BOOT: 1.7" :
                   local_boot.revision==15 ? "MCU: R23  BOOT: 1.5" :
                   local_boot.revision==3 ? "MCU: R23  BOOT: 1.6" :
                   (local_boot.revision==1 || local_boot.revision==2) ? "MCU: R23  BOOT: 1.4" :
                   "MCU: R23  BOOT: unknown");
        char line[24];local_baud_line(line,local_boot.baud);local_text(4,50,line);
    }
    if(row==12)for(unsigned x=4;x<192;x++)local_pixel(x,12,5);
    local_render_icon(row);
}
/* No repeated UART animation frames. A private wake timer advances a bounded
 * local task even before host configuration and after the scheduler reset. */
static uint_fast8_t local_event(struct timer *timer) {
    if(!local_has_claim()){local_timer_active=0;return SF_DONE;}
    sched_wake_task(&local_wake);
    uint32_t now=timer_read_time(),next=now+LOCAL_TICKS_MS(1);
    if(local_state==LOCAL_READY && !LOCAL_TICK_DUE(now,local_due))next=local_due;
    else if(local_state==LOCAL_SPLASH_HOLD && !LOCAL_TICK_DUE(now,local_due)) {
        uint32_t poll=now+LOCAL_TICKS_MS(100);
        next=LOCAL_TICK_DUE(poll,local_due)?local_due:poll;
    }
    timer->waketime=next;return SF_RESCHEDULE;
}
/* Minimal init: no waiting or SPI writes in init callback, only GPIO/PERIPH.
 * Called after clock_setup by Klipper DECL_INIT; before any config command. */
void inventure_local_oled_init(void) {
    inventure_p4_from_image((const volatile uint8_t*)INVENTURE_P4_BOOT_BASE,&local_boot);
    local_cs=gpio_out_setup(GPIO('D',1),1);
    local_dc=gpio_out_setup(GPIO('D',2),0);
    local_gate=gpio_out_setup(GPIO('D',3),1);
    local_reset=gpio_out_setup(GPIO('D',4),0);
    local_reset_released=0;
    local_spi=spi_setup(0,0,2000000);
    local_init_pos=local_frame_pos=0;
    local_state=LOCAL_WAIT_POWER;
    local_due=0;local_epoch_started=0;
    local_start_pending=0;local_reason=0;local_epoch++;
    local_startup_splash=1;local_animation_frame=0;
    local_timer_active=0;sched_wake_task(&local_wake);
}
DECL_INIT(inventure_local_oled_init);
/* One bounded 32-byte SPI block per task run; does not steal motion timers. */
void inventure_local_oled_task(void) {
    if(local_start_pending && !oled_active) {
        if(local_timer_active){sched_del_timer(&local_timer);local_timer_active=0;}
        local_start_pending=0;local_state=LOCAL_WAIT_POWER;
        local_animation_frame=0;
        local_reset_released=0;local_init_pos=local_frame_pos=0;
        gpio_out_write(local_cs,1);gpio_out_write(local_reset,0);
        local_due=timer_read_time()+LOCAL_TICKS_MS(20);local_epoch_started=1;
    }
    if(!local_has_claim() || oled_active)return;
    uint32_t now=timer_read_time();
    if(!local_timer_active) {
        local_timer.func=local_event;local_timer.waketime=now+LOCAL_TICKS_MS(1);
        local_timer_active=1;sched_add_timer(&local_timer);
        sched_wake_task(&local_wake);
    }
    if(!sched_check_wake(&local_wake))return;
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
        local_init_pos=0;local_state=LOCAL_INIT_CMDS;
    } else if(local_state==LOCAL_INIT_CMDS) {
        if(local_init_pos>=sizeof(local_oled_init_bytes)) {
            local_state=LOCAL_WINDOW;return;
        }
        unsigned idx=local_init_pos;
        if(idx+2u>sizeof(local_oled_init_bytes)) {local_oled_failed();return;}
        uint8_t cmd=local_oled_init_bytes[idx++],count=local_oled_init_bytes[idx++];
        uint8_t contrast=(uint8_t)((159u*local_brightness+8u)/16u);
        const uint8_t *params = cmd==0xc1 ? &contrast : &local_oled_init_bytes[idx];
        if(idx+count>sizeof(local_oled_init_bytes) || !local_cmd(cmd,params,count)) {
            local_oled_failed();return;
        }
        local_init_pos=idx+count;
    } else if(local_state==LOCAL_WINDOW) {
        local_error=display_snapshot();
        if(!local_error.fatal && local_reason>=LOCAL_EXPIRED) {
            local_error.kind=local_reason==LOCAL_EXPIRED ? DISPLAY_LINK :
                local_reason==LOCAL_SHUTDOWN ? DISPLAY_SHUTDOWN : DISPLAY_SPI;
        }
        if(local_error.kind || local_reason!=LOCAL_BOOT)local_startup_splash=0;
        const uint8_t col[2]={0x1c,0x5b},row[2]={0,63};
        if(!local_cmd(0x15,col,2) || !local_cmd(0x75,row,2) ||
           !local_cmd(0x5c,NULL,0)) {
            local_oled_failed();return;
        }
        local_frame_pos=0;local_state=LOCAL_FRAME;
    } else if(local_state==LOCAL_FRAME) {
        if(local_frame_pos<LOCAL_WIRE_BYTES) {
            unsigned offset=local_frame_pos%128u;
            if(!offset)local_render(local_frame_pos/128u);
            if(!local_spi_write(1,local_frame+offset,32)) {
                local_oled_failed();return;
            }
            local_frame_pos+=32u;
        }
        if(local_frame_pos==LOCAL_WIRE_BYTES)local_state=LOCAL_DISPLAY_ON;
    } else if(local_state==LOCAL_DISPLAY_ON) {
        if(!local_cmd(0xaf,NULL,0)) {local_oled_failed();return;}
        local_due=now+LOCAL_TICKS_MS(local_startup_splash?2500:500);
        local_state=local_startup_splash?LOCAL_SPLASH_HOLD:LOCAL_READY;
    } else if(local_state==LOCAL_SPLASH_HOLD) {
        struct inventure_display_error current=display_snapshot();
        if(LOCAL_TICK_DUE(now,local_due) || current.kind) {
            local_startup_splash=0;local_animation_frame=0;local_state=LOCAL_WINDOW;
        }
    } else if(local_state==LOCAL_READY && LOCAL_TICK_DUE(now,local_due)) {
        struct inventure_display_error current=display_snapshot();
        local_animation_frame=(local_animation_frame+1)&3;
        local_due=now+LOCAL_TICKS_MS(500);
        local_state=current.revision!=local_error.revision ? LOCAL_WINDOW : LOCAL_ICON_WINDOW;
    } else if(local_state==LOCAL_ICON_WINDOW) {
        const uint8_t col[]={0x4e,0x5a},row[]={7,56};
        if(!local_cmd(0x15,col,2)||!local_cmd(0x75,row,2)||!local_cmd(0x5c,NULL,0))return;
        local_icon_row=0;local_state=LOCAL_ICON_FRAME;
    } else if(local_state==LOCAL_ICON_FRAME) {
        local_row=7+local_icon_row;memset(local_frame,0,sizeof(local_frame));
        local_render_icon(local_row);
        if(!local_spi_write(1,local_frame+100,26))return;
        if(++local_icon_row==50)local_state=LOCAL_READY;
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

/* Diagnostic reads work in shutdown. Preferences are display-only, in RAM.
 * Report the most recent incident even after a new host owns the panel. */
static void local_send_diag(void) {
    sendf("inventure_local_oled_diag state=%c boot_revision=%c baud=%u reason=%c last_incident=%c board_fault=%c guard_input=%c display_fault=%c brightness=%c",
          local_state,local_boot.revision,local_boot.baud,local_reason,local_last_incident,inventure_board_fault(),
          inventure_board_guard_input(),local_state==LOCAL_FAILED,local_brightness);
}
void command_inventure_local_oled_diag_query(uint32_t *args) {
    (void)args;
    local_send_diag();
}
DECL_COMMAND_FLAGS(command_inventure_local_oled_diag_query,HF_IN_SHUTDOWN,"inventure_local_oled_diag_query");

void command_inventure_local_oled_pref(uint32_t *args) {
    if (args[0]>=1 && args[0]<=16) local_brightness=args[0];
    local_send_diag();
}
DECL_COMMAND(command_inventure_local_oled_pref,"inventure_local_oled_pref level=%c");
