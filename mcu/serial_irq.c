// Generic interrupt based serial uart helper code
//
// Copyright (C) 2016-2018  Kevin O'Connor <kevin@koconnor.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <string.h> // memmove
#include "autoconf.h" // CONFIG_SERIAL_BAUD
#include "board/io.h" // readb
#include "board/irq.h" // irq_save
#include "board/misc.h" // console_sendf
#include "board/pgm.h" // READP
#include "command.h" // DECL_CONSTANT
#include "sched.h" // sched_wake_tasks
#include "serial_irq.h" // serial_enable_tx_irq

#define RX_BUFFER_SIZE 240
#define TX_RING_SIZE 1024u
#define TX_RING_MASK (TX_RING_SIZE - 1u)

static uint8_t receive_buf[RX_BUFFER_SIZE], receive_pos;
// Producer: console task, consumer: USART TXE IRQ. Head/tail are monotonic
// modulo-16bit counters. A 1024-byte queue retains at most 1024 bytes.
static uint8_t transmit_buf[TX_RING_SIZE];
static volatile uint16_t transmit_head, transmit_tail, transmit_highwater;
static volatile uint32_t transmit_dropped;


DECL_CONSTANT("SERIAL_BAUD", CONFIG_SERIAL_BAUD);
DECL_CONSTANT("RECEIVE_WINDOW", RX_BUFFER_SIZE);
// The paired host requires these constants before using batched P06 + OLED
// ACK replay. Do not use this UART variant for an unrelated Klipper machine.
DECL_CONSTANT("INVENTURE_TX_RING_ABI", 1);
DECL_CONSTANT("INVENTURE_UART_TX_BYTES", TX_RING_SIZE);
DECL_CONSTANT("INVENTURE_UART_RX_BYTES", RX_BUFFER_SIZE);

// Rx interrupt - store read data
void
serial_rx_byte(uint_fast8_t data)
{
    if (data == MESSAGE_SYNC)
        sched_wake_tasks();
    if (receive_pos >= sizeof(receive_buf))
        // Serial overflow - ignore it as crc error will force retransmit
        return;
    receive_buf[receive_pos++] = data;
}

// Tx interrupt - get next byte to transmit
int
serial_get_tx_byte(uint8_t *pdata)
{
    uint16_t tail = transmit_tail;
    if (tail == transmit_head)
        return -1;
    *pdata = transmit_buf[tail & TX_RING_MASK];
    transmit_tail = tail + 1;
    return 0;
}

// Remove from the receive buffer the given number of bytes
static void
console_pop_input(uint_fast8_t len)
{
    uint_fast8_t copied = 0;
    for (;;) {
        uint_fast8_t rpos = readb(&receive_pos);
        uint_fast8_t needcopy = rpos - len;
        if (needcopy) {
            memmove(&receive_buf[copied], &receive_buf[copied + len]
                    , needcopy - copied);
            copied = needcopy;
            sched_wake_tasks();
        }
        irqstatus_t flag = irq_save();
        if (rpos != readb(&receive_pos)) {
            // Raced with irq handler - retry
            irq_restore(flag);
            continue;
        }
        receive_pos = needcopy;
        irq_restore(flag);
        break;
    }
}

// Process any incoming commands
void
console_task(void)
{
    uint_fast8_t rpos = readb(&receive_pos), pop_count;
    int_fast8_t ret = command_find_block(receive_buf, rpos, &pop_count);
    if (ret > 0)
        command_dispatch(receive_buf, pop_count);
    if (ret) {
        if (CONFIG_HAVE_BOOTLOADER_REQUEST && ret < 0 && pop_count == 32
            && !memcmp(receive_buf, " \x1c Request Serial Bootloader!! ~", 32))
            bootloader_request();
        console_pop_input(pop_count);
        if (ret > 0)
            command_send_ack();
    }
}
DECL_TASK(console_task);

// Encode a complete Klipper frame before entering the short IRQ critical
// section. Publication is all-or-nothing and never overwrites a live frame.
// MESSAGE_MAX=64 in this pinned Klipper; queue space is measured in uint16_t.
void
console_sendf(const struct command_encoder *ce, va_list args)
{
    uint8_t frame[MESSAGE_MAX];
    uint_fast8_t len = command_encode_and_frame(frame, sizeof(frame), ce, args);
    if (!len)
        return;
    irqstatus_t flag = irq_save();
    uint16_t used = (uint16_t)(transmit_head - transmit_tail);
    if (used >= TX_RING_SIZE || len > TX_RING_SIZE - used) {
        transmit_dropped++;
        irq_restore(flag);
        return;
    }
    uint16_t head = transmit_head;
    for (uint_fast8_t i=0; i<len; i++)
        transmit_buf[(head+i) & TX_RING_MASK] = frame[i];
    transmit_head = head + len;
    used += len;
    if (used > transmit_highwater)
        transmit_highwater = used;
    // Enable TX IRQ before unmasking CPU interrupts; ISR drains only
    // complete frames and never waits for an OLED/fan producer.
    serial_enable_tx_irq();
    irq_restore(flag);
}

void
command_inventure_uart_buffers(uint32_t *args)
{
    (void)args;
    irqstatus_t flag = irq_save();
    uint16_t pending = (uint16_t)(transmit_head - transmit_tail);
    uint16_t highwater = transmit_highwater;
    uint32_t dropped = transmit_dropped;
    uint8_t rxpending = receive_pos;
    irq_restore(flag);
    sendf("inventure_uart_buffers pending=%hu highwater=%hu dropped=%u rxpending=%c",
          pending, highwater, dropped, rxpending);
}
DECL_COMMAND_FLAGS(command_inventure_uart_buffers, HF_IN_SHUTDOWN,
                   "inventure_uart_buffers_query");
