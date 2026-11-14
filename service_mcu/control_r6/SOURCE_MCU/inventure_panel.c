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
#ifdef DMA1_Stream4
#include "board/armcm_boot.h"
#endif

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
DECL_CONSTANT_STR("INVENTURE_MCU_RELEASE", "RC8-CONTROL-R7-Z-NATIVE");

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

#ifdef DMA1_Stream4
#define OLED_MAX_SPI 3750000u
static uint8_t oled_fast_pending(void);
static void oled_fast_cancel(void);
static void oled_fast_setup(void);
static uint8_t oled_fast_alarm_active;
#else
#define OLED_MAX_SPI 2000000u
static uint8_t oled_fast_pending(void) { return 0; }
#endif

static void oled_raw_blank(void) {
    RCC->AHB1ENR |= 1u<<3;(void)RCC->AHB1ENR;
    GPIOD->BSRR=(1u<<1)|(1u<<(4+16)); // CS HIGH, RESET LOW before output mode
    GPIOD->OTYPER &= ~((1u<<1)|(1u<<4));
    GPIOD->PUPDR &= ~((3u<<2)|(3u<<8));
    GPIOD->MODER=(GPIOD->MODER & ~((3u<<2)|(3u<<8))) | (1u<<2)|(1u<<8);
    oled_reset_value=0;oled_active=0;
#ifdef DMA1_Stream4
    oled_fast_cancel();
#endif
}
#include "inventure_oled_local.inc"
void inventure_panel_shutdown(void) {
    local_oled_cancel();
    oled_latched=1;oled_fault=3;oled_raw_blank();
#ifdef DMA1_Stream4
    oled_fast_alarm_active=0; // Klipper already reset the timer list.
#endif
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
#ifdef DMA1_Stream4
// STM32F207 SPI2_TX uses DMA1 Stream 4, channel 0. SPI2 is OLED-only.
// Four fixed slots hold decoded bytes until the last bit has left SPI.
#define OLED_FAST_QUEUE 4
#define OLED_FAST_WIRE 256
#define OLED_DMA_ERRORS (DMA_HISR_TEIF4 | DMA_HISR_DMEIF4 | DMA_HISR_FEIF4)
#define OLED_DMA_FLAGS (OLED_DMA_ERRORS | DMA_HISR_TCIF4 | DMA_HISR_HTIF4)
#define OLED_DMA_CR (DMA_SxCR_MINC | DMA_SxCR_DIR_0 | DMA_SxCR_TCIE \
                    | DMA_SxCR_TEIE | DMA_SxCR_DMEIE)
DECL_CONSTANT("INVENTURE_OLED_FAST_ABI", 1);
DECL_CONSTANT("INVENTURE_OLED_FAST_QUEUE", OLED_FAST_QUEUE);
DECL_CONSTANT("INVENTURE_OLED_FAST_WIRE", OLED_FAST_WIRE);

static struct oled_fast_slot {
    uint32_t token;
    uint16_t length;
    uint8_t dc;
    uint8_t data[OLED_FAST_WIRE];
} oled_fast_slots[OLED_FAST_QUEUE];
static uint8_t oled_fast_head, oled_fast_count, oled_fast_running;
static uint8_t oled_dma_initialized, oled_fast_peak;
static uint32_t oled_fast_deadline, oled_spi_hz;
static uint32_t oled_fast_blocks, oled_fast_bytes, oled_fast_errors;
static volatile uint32_t oled_dma_status;
static struct task_wake oled_fast_wake;
static struct timer oled_fast_timer;

static uint8_t oled_fast_pending(void) { return oled_fast_count; }

// Called with interrupts masked, with CS already high. A scheduled one-shot
// may still wake the task; it sees the empty queue and does nothing.
static void
oled_fast_cancel(void)
{
    if (oled_dma_initialized) {
        SPI2->CR2 &= ~SPI_CR2_TXDMAEN;
        DMA1_Stream4->CR = 0;
        DMA1->HIFCR = OLED_DMA_FLAGS;
    }
    oled_fast_count = oled_fast_running = 0;
    oled_dma_status = 0;
}

static uint_fast8_t
oled_fast_alarm(struct timer *timer)
{
    (void)timer;
    oled_fast_alarm_active = 0;
    sched_wake_task(&oled_fast_wake);
    return SF_DONE;
}

static void
oled_fast_set_alarm(uint32_t when)
{
    if (oled_fast_alarm_active)
        sched_del_timer(&oled_fast_timer);
    // Keep short follow-up timers clear of the native scheduler's guard.
    uint32_t minimum = timer_read_time() + timer_from_us(50);
    oled_fast_timer.waketime = timer_is_before(when, minimum) ? minimum : when;
    oled_fast_timer.func = oled_fast_alarm;
    oled_fast_alarm_active = 1;
    sched_add_timer(&oled_fast_timer);
}

void
inventure_oled_dma_irq(void)
{
    uint32_t status = DMA1->HISR & OLED_DMA_FLAGS;
    DMA1->HIFCR = status;
    if (status & (OLED_DMA_ERRORS | DMA_HISR_TCIF4)) {
        oled_dma_status |= status;
        sched_wake_task(&oled_fast_wake);
    }
}

static void
oled_fast_setup(void)
{
    oled_spi_hz = get_pclock_frequency((uint32_t)(uintptr_t)SPI2)
                 >> (((oled_spi.spi_cr1 >> 3) & 7) + 1);
    RCC->AHB1ENR |= RCC_AHB1ENR_DMA1EN;
    (void)RCC->AHB1ENR;
    DMA1_Stream4->CR = 0;
    DMA1->HIFCR = OLED_DMA_FLAGS;
    armcm_enable_irq(inventure_oled_dma_irq, DMA1_Stream4_IRQn, 4);
    oled_dma_initialized = 1;
}

// This is the M300 byte PackBits variant: low 7 bits store length minus 1.
// Decode into the uncommitted tail and verify both input and output bounds.
static uint8_t
oled_fast_decode(uint8_t *out, unsigned length, unsigned encoding,
                 const uint8_t *data, unsigned size)
{
    if (!length || length > OLED_FAST_WIRE || !size || size > 32)
        return 0;
    if (!encoding) {
        if (length != size)
            return 0;
        memcpy(out, data, size);
        return 1;
    }
    if (encoding != 1)
        return 0;
    unsigned i = 0, written = 0;
    while (i < size) {
        unsigned code = data[i++], count = (code & 127) + 1;
        if (count > length - written)
            return 0;
        if (code & 128) {
            if (i == size)
                return 0;
            memset(out + written, data[i++], count);
        } else {
            if (count > size - i)
                return 0;
            memcpy(out + written, data + i, count);
            i += count;
        }
        written += count;
    }
    return written == length;
}

static void
oled_fast_fail(void)
{
    oled_fast_errors++;
    oled_fault = 2;
    oled_raw_blank();
    local_oled_request(2);
}

void
command_inventure_oled_burst(uint32_t *args)
{
    uint32_t hi = args[0], lo = args[1], token = args[2];
    unsigned dc = args[3], encoding = args[4], length = args[5];
    irqstatus_t flag = irq_save();
    if (!oled_active || hi != oled_generation_hi || lo != oled_generation_lo
        || token <= oled_last_token) {
        uint8_t result = oled_latched ? 3 : 1;
        irq_restore(flag);
        oled_ack(hi, lo, token, result);
        return;
    }

    // A command and the DMA task run on the same thread. Interrupts can
    // revoke the session, but cannot publish a new slot while decoding.
    unsigned tail = (oled_fast_head + oled_fast_count) % OLED_FAST_QUEUE;
    struct oled_fast_slot *slot = &oled_fast_slots[tail];
    uint8_t room = oled_fast_count < OLED_FAST_QUEUE;
    irq_restore(flag);
    uint8_t valid = room && dc <= 1 && oled_fast_decode(
        slot->data, length, encoding, command_decode_ptr(args[7]), args[6]);
    flag = irq_save();
    if (!oled_active) {
        irq_restore(flag);
        oled_ack(hi, lo, token, 1);
        return;
    }
    if (!valid || !oled_reset_value) {
        if (oled_fast_count)
            token = oled_fast_slots[oled_fast_head].token;
        oled_fast_fail();
        irq_restore(flag);
        oled_ack(hi, lo, token, 2);
        return;
    }
    slot->token = token;
    slot->length = length;
    slot->dc = dc;
    oled_last_token = token;
    oled_last_kind = 4;
    oled_deadline = timer_read_time() + OLED_LEASE_TICKS;
    oled_fast_count++;
    if (oled_fast_count > oled_fast_peak)
        oled_fast_peak = oled_fast_count;
    sched_wake_task(&oled_fast_wake);
    irq_restore(flag);
}
DECL_COMMAND(command_inventure_oled_burst,
             "inventure_oled_burst generation_hi=%u generation_lo=%u token=%u"
             " dc=%c encoding=%c length=%hu data=%*s");

void
inventure_oled_fast_task(void)
{
    if (!sched_check_wake(&oled_fast_wake))
        return;
    irqstatus_t flag = irq_save();
    if (!oled_active || !oled_fast_count) {
        irq_restore(flag);
        return;
    }
    uint32_t now = timer_read_time(), token = 0;
    uint32_t hi = oled_generation_hi, lo = oled_generation_lo;
    struct oled_fast_slot *slot = &oled_fast_slots[oled_fast_head];
    if (oled_fast_running) {
        uint32_t status = oled_dma_status;
        uint8_t done = (status & DMA_HISR_TCIF4)
            && (SPI2->SR & (SPI_SR_TXE | SPI_SR_BSY)) == SPI_SR_TXE
            && !(DMA1_Stream4->CR & DMA_SxCR_EN);
        if ((status & OLED_DMA_ERRORS)
            || SPI2->CR1 != oled_spi.spi_cr1
            || (!done && !timer_is_before(now, oled_fast_deadline))) {
            token = slot->token;
            oled_fast_fail();
            irq_restore(flag);
            oled_ack(hi, lo, token, 2);
            return;
        }
        if (!done) {
            if (status & DMA_HISR_TCIF4)
                oled_fast_set_alarm(now + timer_from_us(10));
            irq_restore(flag);
            return;
        }
        SPI2->CR2 &= ~SPI_CR2_TXDMAEN;
        gpio_out_write(oled_cs, 1);
        oled_fast_blocks++;
        oled_fast_bytes += slot->length;
        token = slot->token;
        oled_fast_head = (oled_fast_head + 1) % OLED_FAST_QUEUE;
        oled_fast_count--;
        oled_fast_running = 0;
    }
    if (oled_fast_count) {
        slot = &oled_fast_slots[oled_fast_head];
        spi_prepare(oled_spi);
        DMA1->HIFCR = OLED_DMA_FLAGS;
        DMA1_Stream4->FCR = 0; // direct mode, one byte per peripheral request
        DMA1_Stream4->PAR = (uint32_t)(uintptr_t)&SPI2->DR;
        DMA1_Stream4->M0AR = (uint32_t)(uintptr_t)slot->data;
        DMA1_Stream4->NDTR = slot->length;
        oled_dma_status = 0;
        oled_fast_running = 1;
        oled_fast_deadline = timer_read_time() + timer_from_us(
            2000u + slot->length * 8000000u / oled_spi_hz);
        oled_fast_set_alarm(oled_fast_deadline);
        gpio_out_write(oled_dc, slot->dc);
        gpio_out_write(oled_cs, 0);
        __DMB();
        DMA1_Stream4->CR = OLED_DMA_CR | DMA_SxCR_EN;
        SPI2->CR2 |= SPI_CR2_TXDMAEN;
    } else if (oled_fast_alarm_active) {
        sched_del_timer(&oled_fast_timer);
        oled_fast_alarm_active = 0;
    }
    irq_restore(flag);
    if (token)
        oled_ack(hi, lo, token, 0);
}
DECL_TASK(inventure_oled_fast_task);

void
command_inventure_oled_fast_query(uint32_t *args)
{
    (void)args;
    sendf("inventure_oled_fast_state spi_hz=%u blocks=%u wire_bytes=%u errors=%u peak=%c",
          oled_spi_hz, oled_fast_blocks, oled_fast_bytes, oled_fast_errors, oled_fast_peak);
}
DECL_COMMAND_FLAGS(command_inventure_oled_fast_query, HF_IN_SHUTDOWN,
                   "inventure_oled_fast_query");
#endif

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
    if(oled_fast_pending())return 2;
    oled_last_token=args[2];oled_deadline=timer_read_time()+OLED_LEASE_TICKS;
    oled_last_kind=kind;oled_last_meta=meta;oled_last_len=count;
    if (count)memcpy(oled_last_data,data,count);
    oled_last_result=2; // No successful acknowledgement until execution ends.
    return 0;
}
void command_inventure_oled_config(uint32_t *args) {
    uint32_t rate=args[0];
    if(oled_configured || rate<500000 || rate>OLED_MAX_SPI)
        shutdown("Inventure invalid OLED config");
    oled_cs=gpio_out_setup(GPIO('D',1),1);
    oled_dc=gpio_out_setup(GPIO('D',2),0);
    // Preserve an already visible local "Waiting for Klipper" screen while
    // the host reads its dictionary/configures the MCU. Only BEGIN hands over.
    oled_reset=gpio_out_setup(GPIO('D',4),local_reset_released ? 1u : 0u);
    oled_gate=gpio_out_setup(GPIO('D',3),1);
    oled_spi=spi_setup(0,0,rate);
#ifdef DMA1_Stream4
    oled_fast_setup();
#endif
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
