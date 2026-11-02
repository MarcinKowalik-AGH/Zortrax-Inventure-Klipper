// Inventure V9 panel and read-only input snapshot. ABI3, LAB, no flash writes.
// SPDX-License-Identifier: GPL-3.0-or-later
#include "autoconf.h"
#include <string.h> // memcmp / memcpy: OLED retry payload identity
#include "board/irq.h"
#include "board/misc.h"
#include "command.h"
#include "gpio.h"
#include "internal.h"
#include "sched.h"
#include "inventure_v9.h"

DECL_CONSTANT_STR("RESERVE_PINS_inventure_oled", "PB13,PC3,PD1,PD2,PD3,PD4");
DECL_CONSTANT_STR("RESERVE_PINS_inventure_rgb", "PE3,PE2,PE4");
DECL_CONSTANT("INVENTURE_OLED_MAX_CHUNK", 32);
DECL_CONSTANT("INVENTURE_OLED_ABI", 2);
DECL_CONSTANT("INVENTURE_OLED_REPLAY_ABI", 1);
// RC4 retains OLED_ABI2, but identifies the verified contiguous SPI burst.
DECL_CONSTANT("INVENTURE_OLED_SPI_BURST_ABI", 1);
DECL_CONSTANT("INVENTURE_LOCAL_OLED_ABI", 1);
DECL_CONSTANT("INVENTURE_LOCAL_OLED_RECOVERY_ABI", 1);
DECL_CONSTANT("INVENTURE_LOCAL_OLED_BUFFER_BYTES",128);
DECL_CONSTANT_STR("INVENTURE_MCU_RELEASE", "P01-P17-RC8.0-LOCAL");

static const uint8_t input_pins[16] = {
    GPIO('B',11), GPIO('C',15), GPIO('E',12), GPIO('C',14),
    GPIO('C',13), GPIO('G',2), GPIO('B',10), GPIO('E',15),
    GPIO('D',10), GPIO('F',1), GPIO('D',15), GPIO('G',10),
    GPIO('D',7), GPIO('D',6), GPIO('D',5), GPIO('A',15)
};
static GPIO_TypeDef *const input_ports[7] = {GPIOA,GPIOB,GPIOC,GPIOD,GPIOE,GPIOF,GPIOG};
static struct task_wake input_wake;
static uint16_t input_last;
static uint8_t panel_candidate, panel_stable, panel_count;
static uint32_t panel_sequence;

static uint16_t read_inputs(void) {
    uint16_t value=0;
    for (unsigned i=0;i<16;i++) {
        uint8_t pin=input_pins[i];
        if (input_ports[pin/16]->IDR & (1u<<(pin%16))) value |= 1u<<i;
    }
    return value;
}
void inventure_inputs_init(void) {
    for (unsigned i=0;i<16;i++) gpio_in_setup(input_pins[i], i<12 ? 1 : -1);
    // A later endstop object can take over its input. The monitor never
    // changes its mode or pulls after this initialization.
    input_last=read_inputs();
    panel_candidate=panel_stable=(input_last>>12)&15;
    panel_count=0; panel_sequence=0;
}
void inventure_inputs_poll(void) {
    inventure_oled_watchdog();
    input_last=read_inputs();
    uint8_t panel=(input_last>>12)&15;
    if (panel!=panel_candidate) {panel_candidate=panel;panel_count=0;}
    else if (panel_count<3) panel_count++;
    if (panel_count>=3 && panel_stable!=panel_candidate) {
        panel_stable=panel_candidate; panel_sequence++;
        sched_wake_task(&input_wake);
    }
}
static void send_inputs(void) {
    sendf("inventure_inputs clock=%u raw=%hu buttons=%c sequence=%u",
          timer_read_time(),read_inputs(),panel_stable,panel_sequence);
}
void command_inventure_inputs(uint32_t *args) {(void)args;send_inputs();}
DECL_COMMAND_FLAGS(command_inventure_inputs,HF_IN_SHUTDOWN,"inventure_inputs_query");
void inventure_inputs_task(void) {if(sched_check_wake(&input_wake)) send_inputs();}
DECL_TASK(inventure_inputs_task);

// OLED_ABI2: bounded operations, 64-bit host generation + nonwrapping token.
// Shutdown policy is RESET held LOW, CS HIGH. PD3 is not toggled for safety.
#define OLED_LEASE_TICKS (CONFIG_CLOCK_FREQ * 2u)
static struct gpio_out oled_cs,oled_dc,oled_reset,oled_gate;
static struct spi_config oled_spi;
static volatile uint8_t oled_configured,oled_active,oled_latched,oled_fault;
static volatile uint8_t oled_reset_value;
static volatile uint32_t oled_generation_hi,oled_generation_lo,oled_last_token;
static volatile uint32_t oled_deadline;
// Only the immediately preceding operation can be retried, with identical
// generation, token, kind and bytes. A lost ACK must NOT repeat SPI writes.
static uint8_t oled_last_kind, oled_last_result, oled_last_meta, oled_last_len;
static uint8_t oled_last_data[32];

static void oled_raw_blank(void) {
    RCC->AHB1ENR |= 1u<<3;(void)RCC->AHB1ENR;
    GPIOD->BSRR=(1u<<1)|(1u<<(4+16)); // CS HIGH, RESET LOW before output mode
    GPIOD->OTYPER &= ~((1u<<1)|(1u<<4));
    GPIOD->PUPDR &= ~((3u<<2)|(3u<<8));
    GPIOD->MODER=(GPIOD->MODER & ~((3u<<2)|(3u<<8))) | (1u<<2)|(1u<<8);
    oled_reset_value=0;oled_active=0;
}
#include "inventure_oled_local.inc"
void inventure_panel_shutdown(void) {
    local_oled_cancel();
    oled_latched=1;oled_fault=3;oled_raw_blank();
    local_oled_request(3);
}
DECL_SHUTDOWN(inventure_panel_shutdown);
void inventure_oled_watchdog(void) {
    irqstatus_t flag=irq_save();
    if(oled_active && (int32_t)(timer_read_time()-oled_deadline)>=0) {
        oled_fault=1;oled_raw_blank();
        local_oled_request(2);
    }
    irq_restore(flag);
}
static void oled_ack(uint32_t hi,uint32_t lo,uint32_t token,uint8_t result) {
    sendf("inventure_oled_ack generation_hi=%u generation_lo=%u token=%u result=%c",hi,lo,token,result);
}
// 'replayed' is set only if this exact operation has already completed;
// retries do not touch the display, advance its column or extend the lease.
static uint8_t oled_accept(uint32_t *args, uint8_t kind, uint8_t meta,
                           unsigned count, const uint8_t *data,
                           uint8_t *replayed) {
    *replayed=0;
    if(oled_latched)return 3;
    if(!oled_active || args[0]!=oled_generation_hi || args[1]!=oled_generation_lo)
        return 1;
    if(args[2] == oled_last_token) {
        if (oled_last_kind!=kind || oled_last_meta!=meta ||
            oled_last_len!=count ||
            (count && memcmp(oled_last_data,data,count))) return 1;
        *replayed=1;
        return oled_last_result;
    }
    if(args[2]<oled_last_token)return 1;
    oled_last_token=args[2];oled_deadline=timer_read_time()+OLED_LEASE_TICKS;
    oled_last_kind=kind;oled_last_meta=meta;oled_last_len=count;
    if (count)memcpy(oled_last_data,data,count);
    oled_last_result=2; // No successful acknowledgement until execution ends.
    return 0;
}
void command_inventure_oled_config(uint32_t *args) {
    uint32_t rate=args[0];
    if(oled_configured || rate<500000 || rate>2000000)
        shutdown("Inventure invalid OLED config");
    oled_cs=gpio_out_setup(GPIO('D',1),1);
    oled_dc=gpio_out_setup(GPIO('D',2),0);
    // Preserve an already visible local "Waiting for Klipper" screen while
    // the host reads its dictionary/configures the MCU. Only BEGIN hands over.
    oled_reset=gpio_out_setup(GPIO('D',4),local_reset_released ? 1u : 0u);
    oled_gate=gpio_out_setup(GPIO('D',3),1);
    oled_spi=spi_setup(0,0,rate);
    oled_configured=1;
    if (!local_has_claim()) oled_raw_blank();
}
DECL_COMMAND(command_inventure_oled_config,"inventure_oled_config rate=%u");
void command_inventure_oled_begin(uint32_t *args) {
    if(!oled_configured || (!args[0] && !args[1]) || !args[2])
        shutdown("Inventure invalid OLED session");
    irqstatus_t flag=irq_save();uint8_t result=0;
    if(oled_latched)result=3;
    else if(args[0]==oled_generation_hi && args[1]==oled_generation_lo) {
        result = oled_active && args[2]==oled_last_token &&
                 oled_last_kind==1 ? oled_last_result : 1;
    } else {
        // A NEW generation may take over a local waiting screen. Global
        // shutdown remains latched and cannot be cleared by BEGIN.
        local_oled_cancel();
        oled_raw_blank();
        oled_generation_hi=args[0];oled_generation_lo=args[1];oled_last_token=args[2];
        oled_last_kind=1;oled_last_result=0;oled_last_meta=0;oled_last_len=0;
        oled_fault=0;oled_deadline=timer_read_time()+OLED_LEASE_TICKS;oled_active=1;
    }
    irq_restore(flag);oled_ack(args[0],args[1],args[2],result);
}
DECL_COMMAND(command_inventure_oled_begin,"inventure_oled_begin generation_hi=%u generation_lo=%u token=%u");
void command_inventure_oled_control(uint32_t *args) {
    if(!oled_configured || args[3]>1 || args[4]>1)
        shutdown("Inventure invalid OLED control");
    irqstatus_t flag=irq_save();uint8_t replayed=0;
    uint8_t result=oled_accept(args,2,(args[3]<<1)|args[4],0,NULL,&replayed);
    if(!result && !replayed) {
        gpio_out_write(oled_cs,1);gpio_out_write(oled_gate,args[4]);
        gpio_out_write(oled_reset,args[3]);oled_reset_value=args[3];
        oled_last_result=0;
    }
    irq_restore(flag);oled_ack(args[0],args[1],args[2],result);
}
DECL_COMMAND(command_inventure_oled_control,"inventure_oled_control generation_hi=%u generation_lo=%u token=%u reset=%c gate=%c");
void command_inventure_oled_write(uint32_t *args) {
    unsigned dc=args[3],count=args[4];uint8_t *data=command_decode_ptr(args[5]);
    if(!oled_configured || dc>1 || !count || count>32)
        shutdown("Inventure invalid OLED chunk");
    irqstatus_t flag=irq_save();uint8_t replayed=0;
    uint8_t result=oled_accept(args,3,dc,count,data,&replayed);
    irq_restore(flag);
    if(result || replayed) {oled_ack(args[0],args[1],args[2],result);return;}
    if(!oled_reset_value) {oled_ack(args[0],args[1],args[2],2);return;}
    // SSD1322 accepts contiguous RAM-write bytes while CS is LOW. Avoid a
    // separate GPIO transition and SPI call for every data byte. The V9 SPI
    // driver supports max 8 bytes for low SPI rates, otherwise max 32.
    // Interrupts remain enabled while SPI is shifting data, so Klipper's
    // planner and safety watchdog are NOT masked for an entire OLED chunk.
    gpio_out_write(oled_dc,dc);
    spi_prepare(oled_spi);
    unsigned burst_max=((oled_spi.spi_cr1>>3)&7)>=5 ? 8u : 32u;
    // The last liveliness check AND CS assertion must be atomic against
    // an asynchronous OLED watchdog/fatal reset (which forces CS HIGH).
    flag=irq_save();
    uint8_t live=oled_active&&!oled_latched;
    if(live)gpio_out_write(oled_cs,0);
    irq_restore(flag);
    if (!live) {oled_ack(args[0],args[1],args[2],2);return;}
    for(unsigned off=0;off<count;) {
        unsigned n=count-off;
        if(n>burst_max)n=burst_max;
        spi_transfer(oled_spi,0,n,&data[off]);
        off+=n;
    }
    gpio_out_write(oled_cs,1);
    flag=irq_save();
    result=oled_active&&!oled_latched ? 0:2;
    oled_last_result=result;
    irq_restore(flag);
    oled_ack(args[0],args[1],args[2],result);
}
DECL_COMMAND(command_inventure_oled_write,"inventure_oled_write generation_hi=%u generation_lo=%u token=%u dc=%c data=%*s");
void command_inventure_oled_ping(uint32_t *args) {
    if(!oled_latched && oled_active && args[0]==oled_generation_hi && args[1]==oled_generation_lo)
        oled_deadline=timer_read_time()+OLED_LEASE_TICKS;
}
DECL_COMMAND(command_inventure_oled_ping,"inventure_oled_ping generation_hi=%u generation_lo=%u");
void command_inventure_oled_stop(uint32_t *args) {
    (void)args;irqstatus_t flag=irq_save();
    local_oled_cancel();
    if(!oled_latched)oled_fault=2;
    oled_raw_blank();local_oled_request(oled_latched?3:1);irq_restore(flag);
}
DECL_COMMAND_FLAGS(command_inventure_oled_stop,HF_IN_SHUTDOWN,"inventure_oled_stop");
void command_inventure_oled_query(uint32_t *args) {
    (void)args;
    irqstatus_t flag=irq_save();
    uint32_t hi=oled_generation_hi,lo=oled_generation_lo,clock=timer_read_time();
    uint8_t configured=oled_configured,active=oled_active,latched=oled_latched;
    uint8_t fault=oled_fault,reset=oled_reset_value;
    irq_restore(flag);
    sendf("inventure_oled_state clock=%u generation_hi=%u generation_lo=%u configured=%c active=%c latched=%c fault=%c reset=%c",
        clock,hi,lo,configured,active,latched,fault,reset);
}
DECL_COMMAND_FLAGS(command_inventure_oled_query,HF_IN_SHUTDOWN,"inventure_oled_query");

static void rgb_raw(unsigned mask) {
    RCC->AHB1ENR |= 1u<<4;
    (void)RCC->AHB1ENR;
    const uint8_t pins[3]={3,2,4}; // R G B, latest UI setter HIGH=on
    for(unsigned i=0;i<3;i++) {
        unsigned n=pins[i];
        GPIOE->BSRR=(1u<<n)<<((mask&(1u<<i))?0:16);
        GPIOE->OTYPER &= ~(1u<<n);
        GPIOE->PUPDR &= ~(3u<<(n*2));
        GPIOE->MODER=(GPIOE->MODER & ~(3u<<(n*2))) | (1u<<(n*2));
    }
}
void inventure_fault_led(void) {rgb_raw(1);}
void command_inventure_rgb(uint32_t *args) {
    if(args[0]>7) shutdown("Inventure invalid RGB mask");
    rgb_raw(args[0]);
}
// Not allowed in shutdown: a host cannot clear the local red fault indication.
DECL_COMMAND(command_inventure_rgb,"inventure_rgb mask=%c");
