// Offline register/API model. Not an actual STM32 or optical SSD1322 test.
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
void spi_prepare(struct spi_config c){assert(c.spi==SPI2);}
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
#include "inventure_panel.c"

static uint8_t captured[8192],expected[8192],ram_write;
static unsigned captured_count,interrupt_at;
void test_writeb(void *reg,uint8_t value) {
    *(volatile uint8_t*)reg=value;
    if(!outputs[GPIO('D',2)]) {
        ram_write=value==0x5c;
        if(ram_write)captured_count=0;
    } else if(ram_write) {
        assert(captured_count<sizeof(captured));captured[captured_count++]=value;
        if(interrupt_at && captured_count==interrupt_at) {
            interrupt_at=0;inventure_panel_shutdown();
        }
    }
}
static void ref_pixel(unsigned x,int y,unsigned value) {
    if(x>=256 || y<0 || y>=64)return;
    unsigned off=y*128+(x>>1),shift=(x&1)?0:4;
    expected[off]=(expected[off]&~(15u<<shift))|(value<<shift);
}
/* Independent full-frame/scanning renderer retained only as an oracle. */
static void ref_text(unsigned x,int y,const char *text) {
    while(*text && x<250) {
        unsigned code=(unsigned char)*text++,index=code>=32 && code<=126?code-32:'?'-32;
        const uint8_t *p=glyph_packed;unsigned width=0,span=0;
        for(unsigned n=0;n<=index;n++) {
            unsigned h=*p++;width=h>>4;span=(h&15)==15?*p++:glyph_span[h&15];
            if(n<index)p+=(width*(span&15)+7)/8;
        }
        unsigned w,s;const uint8_t *fast=local_glyph(code,&w,&s);
        assert(w==width && s==span && fast==p);
        for(unsigned row=0;row<(span&15);row++)for(unsigned col=0;col<width;col++) {
            unsigned b=row*width+col;
            if(p[b/8]&(1u<<(b%8)))ref_pixel(x+col,y+(span>>4)+row-3,15);
        }
        x+=width+1;
    }
}
static void reference(unsigned reason) {
    memset(expected,0,sizeof(expected));ref_text(4,2,"ZORTRAX INVENTURE");
    for(unsigned x=4;x<252;x++)ref_pixel(x,12,5);
    ref_text(4,18,reason==3?"MCU stopped":"Waiting for Klipper...");
    ref_text(4,34,reason==3?"Cooling: automatic":reason?"Connection lost / released":"MCU: RC8.0  BOOT: 1.4 r2");
#if CONFIG_SERIAL_BAUD == 1500000
    ref_text(4,50,"P4: 1500000 8N1");
#else
    ref_text(4,50,"P4: 250000 8N1");
#endif
}
static void finish_local(unsigned reason) {
    for(unsigned n=0;n<1600 && (local_start_pending || local_state!=LOCAL_READY);n++) {
        mock_now+=CONFIG_CLOCK_FREQ/500u;inventure_local_oled_task();
    }
    assert(local_state==LOCAL_READY && local_has_claim());
    assert(captured_count==8192 && sizeof(local_frame)==128);
    reference(reason);assert(!memcmp(captured,expected,8192));
}
static void begin(unsigned generation,unsigned token) {
    uint32_t args[]={100,generation,token};command_inventure_oled_begin(args);
    assert(!last_result && oled_active && !oled_reset_value);
}
int main(void) {
    SPI2->SR=2;inventure_local_oled_init();finish_local(0);
    uint32_t config[]={2000000};command_inventure_oled_config(config);
    assert(local_state==LOCAL_READY && oled_reset_value);
    begin(1,1);
    uint32_t control[]={100,1,2,1,1};command_inventure_oled_control(control);
    assert(!last_result && oled_reset_value);
    mock_now+=3u*CONFIG_CLOCK_FREQ;inventure_oled_watchdog();
    assert(!oled_active && local_start_pending && !oled_latched);
    finish_local(2);
    uint32_t old[]={100,1,3,1,1,0};command_inventure_oled_write(old);
    assert(last_result==1); // expired generation cannot overwrite local status
    begin(2,4);control[1]=2;control[2]=5;command_inventure_oled_control(control);
    assert(!last_result);
    uint32_t unused[]={0};command_inventure_oled_stop(unused);finish_local(1);
    begin(3,6);inventure_panel_shutdown();
    assert(oled_latched && !oled_active && local_start_pending);
    finish_local(3);
    /* The fault status may render; no host command can release the latch. */
    unsigned prior=captured_count;
    uint32_t refused[]={100,4,7};command_inventure_oled_begin(refused);
    assert(last_result==3 && oled_latched && captured_count==prior);
    command_inventure_oled_write(old);assert(last_result==3);
    /* Shutdown in a local byte aborts that epoch, then starts a fresh frame. */
    command_inventure_oled_stop(unused);interrupt_at=17;
    for(unsigned n=0;n<1600 && interrupt_at;n++) {
        mock_now+=CONFIG_CLOCK_FREQ/500u;inventure_local_oled_task();
    }
    assert(!interrupt_at && local_start_pending && captured_count==17);
    finish_local(3);
    /* A missing panel is a bounded local error, never a new board shutdown. */
    local_oled_request(3);inventure_local_oled_task();
    local_state=LOCAL_INIT_CMDS;local_init_pos=0;SPI2->SR=0;spi_stuck_clock=1;
    uint32_t before=mock_now;inventure_local_oled_task();
    assert(local_state==LOCAL_FAILED && !oled_reset_value && oled_latched);
    assert((uint32_t)(mock_now-before)<CONFIG_CLOCK_FREQ/500u);
    assert(!irq_depth);
    puts("PASS RC8: 128-byte row renderer vs full-frame oracle, lease/release/shutdown recovery, stale host fencing, mid-byte epoch abort, bounded SPI timeout");
    return 0;
}

