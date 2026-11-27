// R23 actual MCU OLED code against SPI/register/time doubles. No physical test.
#define TEST_LOCAL_UI_R14
#include <stdlib.h>
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#include "autoconf.h"
#include "internal.h"
#include "gpio.h"
#include "sched.h"
#include "board/irq.h"
#include "inventure_v9.h"
#include "inventure_p4_baud.h"
void inventure_p4_from_image(const volatile uint8_t *image,
                             struct inventure_p4_selection *out) {
    (void)image;
    out->revision=2;out->baud=250000;out->has_saved=1;
    out->saved_index=3;out->slots_used=1;out->invalid_slots=0;
}
GPIO_TypeDef mock_gpio[7];TIM_TypeDef mock_tim[10];USART_TypeDef mock_usart[7];
SPI_TypeDef mock_spi[4];ADC_TypeDef mock_adc[4];RCC_TypeDef mock_rcc;
static int32_t pulls[112];static uint8_t outputs[112],payload[64],transmitted[4096];
static unsigned transferred,visible_bytes,spi_calls,cs_low,cs_high,ack_count,last_token,last_result,snapshots;
#ifndef OLED_TEST_SPI_RATE
#define OLED_TEST_SPI_RATE 2000000
#endif
static uint32_t mock_now=12345,last_hi,last_lo;static unsigned irq_depth;
static int inject_watchdog_at=-1,inject_shutdown_at=-1,guard_reset_high;
static jmp_buf failure;static int expect_failure;
void mock_shutdown(const char*s){if(expect_failure)longjmp(failure,1);fprintf(stderr,"Unexpected %s\n",s);assert(0);__builtin_unreachable();}
void *command_decode_ptr(uint32_t p){(void)p;return payload;}
static uint8_t spi_stuck_clock;
uint32_t timer_read_time(void){if(spi_stuck_clock)mock_now+=120u;return mock_now;}
irqstatus_t irq_save(void){unsigned old=irq_depth;irq_depth++;return old;}
void irq_restore(irqstatus_t f){irq_depth=f;}
void sendf(const char *fmt,...){
 assert(!irq_depth);va_list a;va_start(a,fmt);
 if(strstr(fmt,"oled_ack")){last_hi=va_arg(a,unsigned);last_lo=va_arg(a,unsigned);last_token=va_arg(a,unsigned);last_result=va_arg(a,unsigned);ack_count++;}
 else if(strstr(fmt,"inventure_inputs"))snapshots++;
 va_end(a);
}
struct gpio_in gpio_in_setup(uint32_t pin,int32_t pull){pulls[pin]=pull;return (struct gpio_in){&mock_gpio[pin/16],1u<<(pin%16)};}
void gpio_out_write(struct gpio_out g,uint32_t val){
 unsigned p=((GPIO_TypeDef*)g.regs-mock_gpio)*16;
 for(unsigned n=0;n<16;n++)if(g.bit==(1u<<n)){
  if(guard_reset_high&&p+n==GPIO('D',4)&&val)assert(irq_depth); // atomic against fatal IRQ
  outputs[p+n]=val;if(p+n==GPIO('D',1)){if(val)cs_high++;else cs_low++;}return;
 }assert(0);
}
struct gpio_out gpio_out_setup(uint32_t p,uint32_t val){struct gpio_out g={&mock_gpio[p/16],1u<<(p%16)};gpio_out_write(g,val);return g;}
struct spi_config spi_setup(uint32_t bus,uint8_t mode,uint32_t rate){
 assert(bus==0&&mode==0&&rate==OLED_TEST_SPI_RATE);
 return (struct spi_config){SPI2,(rate==500000 ? (5u<<3) : 1u)};
}
void spi_prepare(struct spi_config c){
 assert(c.spi==SPI2);
#ifdef TEST_SPI_PREPARE_HOOK
 TEST_SPI_PREPARE_HOOK();
#endif
}
void spi_transfer(struct spi_config c,uint8_t receive,uint8_t len,uint8_t *data){
 assert(!irq_depth);assert(c.spi==SPI2&&!receive&&len>0&&len<=32);
 if(OLED_TEST_SPI_RATE==500000)assert(len<=8);
 spi_calls++;
 for(unsigned i=0;i<len;i++) {
  assert(transferred<sizeof(transmitted));
  if(!outputs[GPIO('D',1)]) visible_bytes++;
  transmitted[transferred++]=data[i];
  if((int)transferred==inject_watchdog_at){mock_now+=CONFIG_CLOCK_FREQ*3u;inventure_oled_watchdog();}
  if((int)transferred==inject_shutdown_at)inventure_panel_shutdown();
 }
}

void sched_wake_task(struct task_wake *t){t->wake=1;}
uint8_t sched_check_wake(struct task_wake *t){uint8_t v=t->wake;t->wake=0;return v;}
#ifdef TEST_LOCAL_UI_R14
static uint8_t test_board_fault, test_guard_input;
uint8_t inventure_board_fault(void) { return test_board_fault; }
uint8_t inventure_board_guard_input(void) { return test_guard_input; }
#endif
#include "inventure_panel.c"

uint32_t timer_from_us(uint32_t us){return us*(CONFIG_CLOCK_FREQ/1000000u);}

static struct timer *scheduled;
void sched_add_timer(struct timer *timer) {
    assert(!scheduled);scheduled=timer;
    assert((int32_t)(timer->waketime-mock_now)>0);
}
void sched_del_timer(struct timer *timer) {assert(scheduled==timer);scheduled=NULL;}
static uint8_t screen[8192], last_command, parameter_index;
static uint8_t columns[2]={0x1c,0x5b}, rows[2]={0,63};
static unsigned window_offset, window_bytes, spi_bytes;
void test_writeb(void *reg,uint8_t value) {
    *(volatile uint8_t*)reg=value;spi_bytes++;
    if(!outputs[GPIO('D',2)]) {
        last_command=value;parameter_index=0;
        if(value==0x5c){window_offset=0;window_bytes=0;}
        return;
    }
    if(last_command==0x15 && parameter_index<2)columns[parameter_index++]=value;
    else if(last_command==0x75 && parameter_index<2)rows[parameter_index++]=value;
    else if(last_command==0x5c) {
        unsigned width=2u*(columns[1]-columns[0]+1u);
        unsigned y=rows[0]+window_offset/width;
        unsigned x=2u*(columns[0]-0x1c)+window_offset%width;
        assert(x<128 && y<64 && y<=rows[1]);
        screen[y*128+x]=value;window_offset++;window_bytes++;
    }
}
static void tick(void) {
    mock_now+=CONFIG_CLOCK_FREQ/500u;
    if(scheduled && (int32_t)(mock_now-scheduled->waketime)>=0) {
        struct timer *t=scheduled;
        if(t->func(t)==SF_DONE && scheduled==t)scheduled=NULL;
    }
    inventure_local_oled_task();
}
static void finish(unsigned state) {
    unsigned count=0;
    do {tick();assert(++count<6000);}while(local_start_pending || local_state!=state);
}
static void save_screen(const char *name) {
    const char *directory=getenv("INVENTURE_DISPLAY_CAPTURE_DIR");
    if(!directory)return;
    char path[4096];int n=snprintf(path,sizeof(path),"%s/%s.bin",directory,name);
    assert(n>0 && (unsigned)n<sizeof(path));
    FILE *file=fopen(path,"wb");assert(file);
    assert(fwrite(screen,1,sizeof(screen),file)==sizeof(screen));assert(!fclose(file));
}
static void begin(unsigned generation,unsigned token) {
    uint32_t args[]={100,generation,token};command_inventure_oled_begin(args);
    assert(last_result==0 && oled_active && !local_timer_active && !scheduled);
}
static void reset_panel(void) {
    scheduled=NULL;oled_latched=oled_active=oled_configured=oled_fault=oled_display_failed=0;
    local_timer_active=local_start_pending=0;
    memset(&display_error,0,sizeof(display_error));
    test_board_fault=test_guard_input=0;SPI2->SR=2;spi_stuck_clock=0;
    inventure_local_oled_init();
}
static void check_icon(unsigned icon) {
    for(unsigned y=0;y<50;y++) {
        const uint8_t *p=local_error_rle+local_error_frames[icon][local_animation_frame][y];
        unsigned x=0;
        while(x<50) {
            assert(p<local_error_rle+sizeof(local_error_rle));
            unsigned token=*p++,n=(token>>4)+1,level=token&15;
            assert(x+n<=50);
            while(n--) {
                unsigned px=200+x++,value=screen[(7+y)*128+px/2];
                assert(((px&1)?value&15:value>>4)==level);
            }
        }
    }
}
int main(void) {
    /* Start across a clock wrap, with no host or panel configuration. */
    mock_now=0xffff0000u;reset_panel();finish(LOCAL_SPLASH_HOLD);
    assert(window_bytes==8192 && local_startup_splash);
    for(unsigned row=12;row<52;row++)
        assert(!memcmp(screen+row*128+24,m300_startup_logo[row-12],80));
    save_screen("startup");finish(LOCAL_READY);save_screen("waiting");
    assert(!local_startup_splash && sizeof(local_frame)==128 && ack_count==0);
    uint8_t before[8192];memcpy(before,screen,sizeof(before));
    unsigned old_bytes=spi_bytes;
    do{tick();}while(local_state==LOCAL_READY);
    finish(LOCAL_READY);
    assert(local_animation_frame==1 && window_bytes==1300);
    assert(spi_bytes-old_bytes==1307); // 52x50 pixels + seven window bytes
    for(unsigned y=0;y<64;y++)for(unsigned x=0;x<128;x++)
        if(y<7 || y>=57 || x<100 || x>=126)assert(screen[y*128+x]==before[y*128+x]);
    assert(ack_count==0); // animation is MCU-local, never a UART frame
    uint32_t config[]={2000000};command_inventure_oled_config(config);begin(1,1);
    old_bytes=spi_bytes;for(unsigned n=0;n<50;n++)tick();
    assert(spi_bytes==old_bytes); // local renderer cannot overwrite host ownership
    mock_now+=CONFIG_CLOCK_FREQ*3u;inventure_oled_watchdog();finish(LOCAL_READY);
    assert(local_reason==LOCAL_EXPIRED && local_error.kind==DISPLAY_LINK && !oled_latched);
    check_icon(11);save_screen("disconnected");
    uint32_t ping[]={100,1};command_inventure_oled_ping(ping);
    assert(!oled_active);begin(2,2); // only a NEW generation reclaims the display
    uint32_t stop[]={0};command_inventure_oled_stop(stop);finish(LOCAL_READY);
    assert(local_reason==LOCAL_RELEASE && !local_startup_splash);

    /* Core shutdown metadata arrives before scheduler-reset callbacks. */
    inventure_oled_capture_shutdown(_DECL_STATIC_STR("Timer too close"));
    test_board_fault=255; // board shutdown must not mask the native first cause
    scheduled=NULL;inventure_panel_after_shutdown();finish(LOCAL_READY);
    assert(local_error.kind==DISPLAY_TIMER && local_error.fatal && oled_latched);
    check_icon(10);save_screen("timer_error");
    inventure_oled_capture_shutdown(_DECL_STATIC_STR("ADC out of range"));
    assert(display_error.kind==DISPLAY_TIMER); // preserve the first cause
    uint32_t stale[]={100,3,3};old_bytes=spi_bytes;
    command_inventure_oled_begin(stale);assert(last_result==3 && !oled_active && spi_bytes==old_bytes);
    command_inventure_display_error_query(stop);
    const unsigned faults[]={1,4,5,6,7,8};
    const unsigned kinds[]={DISPLAY_CABLE,DISPLAY_SENSOR,DISPLAY_FAN,DISPLAY_DOOR,DISPLAY_TRAY,DISPLAY_Z};
    for(unsigned n=0;n<6;n++) {
        reset_panel();test_board_fault=faults[n];test_guard_input=1;
        inventure_panel_shutdown();finish(LOCAL_READY);
        assert(local_error.kind==kinds[n] && local_error.fatal);
        check_icon(display_catalog[kinds[n]].icon);
        if(n==0) {assert(display_catalog[kinds[n]].code==138);save_screen("cable_error");}
        if(n==3)save_screen("door_error");
        if(n==4)save_screen("tray_error");
    }
    /* A disconnected SPI panel is bounded and cannot introduce another
     * global shutdown, heater command or movement. */
    reset_panel();inventure_local_oled_task();
    local_state=LOCAL_INIT_CMDS;local_init_pos=0;SPI2->SR=0;spi_stuck_clock=1;
    uint32_t started=mock_now;sched_wake_task(&local_wake);inventure_local_oled_task();
    assert(local_state==LOCAL_FAILED && !local_timer_active && !scheduled);
    assert((uint32_t)(mock_now-started)<CONFIG_CLOCK_FREQ/500u);
    assert(!oled_latched && !test_board_fault && !irq_depth);
    assert(oled_display_failed && display_snapshot().kind==DISPLAY_SPI);
    /* Legacy transport has the same optional, nonfatal timeout as DMA. */
    reset_panel();command_inventure_oled_config(config);begin(10,1);
    uint32_t control[]={100,10,2,1,1};command_inventure_oled_control(control);
    payload[0]=0xae;
    uint32_t write[]={100,10,3,0,1,0};command_inventure_oled_write(write);
    assert(!last_result);old_bytes=spi_bytes;command_inventure_oled_write(write);
    assert(!last_result && spi_bytes==old_bytes); // duplicate ACK, no repeated SPI
    write[2]=4;SPI2->SR=0;spi_stuck_clock=1;started=mock_now;
    command_inventure_oled_write(write);
    assert(last_result==2 && oled_display_failed && !oled_active && !oled_latched);
    assert((uint32_t)(mock_now-started)<CONFIG_CLOCK_FREQ/500u);
    SPI2->SR=2;spi_stuck_clock=0;
    uint32_t retry[]={100,11,1};command_inventure_oled_begin(retry);
    assert(last_result==2 && !oled_active); // display failure is not cleared by recovery
    puts("PASS R23: exact M300 4bpp assets, boot preemption, local 2fps dirty icons, 1307B/frame, no UART animation, clock wrap, first shutdown cause, link fencing and bounded panel failure");
    return 0;
}
