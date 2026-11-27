/* M300 DMA task discipline adapted to F207 Stream4 and Inventure generations.
 * SPI/DMA preparation and ACK encoding run with IRQs enabled. Only timer-list
 * edits and the final hardware/queue publication are atomic. No motion owner,
 * safety deadline, pin assignment or wire frame format is changed. */
static uint32_t oled_irq_peak_ticks;
static irqstatus_t oled_irq_begin(uint32_t *started) {
    irqstatus_t flags=irq_save();*started=timer_read_time();return flags;
}
static void oled_irq_end(irqstatus_t flags,uint32_t started) {
    uint32_t elapsed=timer_read_time()-started;
    if(elapsed>oled_irq_peak_ticks)oled_irq_peak_ticks=elapsed;
    irq_restore(flags);
}
static void oled_alarm_cancel(void) {
    uint32_t started;irqstatus_t flags=oled_irq_begin(&started);
    if(oled_fast_alarm_active){sched_del_timer(&oled_fast_timer);oled_fast_alarm_active=0;}
    oled_irq_end(flags,started);
}
static void oled_task_alarm_at(uint32_t when) {
    uint32_t started;irqstatus_t flags=oled_irq_begin(&started);
    if(oled_fast_alarm_active){sched_del_timer(&oled_fast_timer);oled_fast_alarm_active=0;}
    if(oled_active && oled_fast_count) {
        uint32_t minimum=timer_read_time()+timer_from_us(50);
        oled_fast_timer.waketime=timer_is_before(when,minimum)?minimum:when;
        oled_fast_timer.func=oled_fast_alarm;oled_fast_alarm_active=1;
        sched_add_timer(&oled_fast_timer);
    }
    oled_irq_end(flags,started);
}
static uint8_t oled_task_live(unsigned head,uint32_t hi,uint32_t lo) {
    return oled_active && oled_fast_count && oled_fast_head==head
        && oled_generation_hi==hi && oled_generation_lo==lo;
}
void inventure_oled_fast_task(void) {
    if(!sched_check_wake(&oled_fast_wake) || !oled_active || !oled_fast_count)return;
    unsigned head=oled_fast_head;
    uint32_t hi=oled_generation_hi,lo=oled_generation_lo,now=timer_read_time();
    struct oled_fast_slot *slot=&oled_fast_slots[head];
    if(oled_fast_running) {
        uint32_t status=oled_dma_status;
        uint8_t done=(status&DMA_HISR_TCIF4)
            && (SPI2->SR&(SPI_SR_TXE|SPI_SR_BSY))==SPI_SR_TXE
            && !(DMA1_Stream4->CR&DMA_SxCR_EN);
        if((status&OLED_DMA_ERRORS) || SPI2->CR1!=oled_spi.spi_cr1
                || (!done && !timer_is_before(now,oled_fast_deadline))) {
            uint32_t token=0,started;irqstatus_t flags=oled_irq_begin(&started);
            if(oled_task_live(head,hi,lo) && oled_fast_running) {
                token=slot->token;oled_fast_fail();
            }
            oled_irq_end(flags,started);
            if(token)oled_ack(hi,lo,token,2);
            return;
        }
        if(!done) {
            if(status&DMA_HISR_TCIF4)oled_task_alarm_at(now+timer_from_us(10));
            return;
        }
        uint32_t token,started;irqstatus_t flags=oled_irq_begin(&started);
        if(!oled_task_live(head,hi,lo) || !oled_fast_running) {
            oled_irq_end(flags,started);return;
        }
        SPI2->CR2&=~SPI_CR2_TXDMAEN;
        gpio_out_write(oled_cs,1);
        oled_fast_blocks++;oled_fast_bytes+=slot->length;token=slot->token;
        oled_fast_head=(head+1)%OLED_FAST_QUEUE;
        oled_fast_count--;oled_fast_running=0;
        oled_irq_end(flags,started);
        oled_alarm_cancel();
        // As on M300, ACK only a completed SPI transfer, outside the lock.
        oled_ack(hi,lo,token,0);
    }
    if(!oled_active || !oled_fast_count){oled_alarm_cancel();return;}
    head=oled_fast_head;slot=&oled_fast_slots[head];
    hi=oled_generation_hi;lo=oled_generation_lo;
    // UART and native step/endstop IRQs may preempt all of this preparation.
    // A watchdog cancellation can clear the session; the final gate below
    // then refuses to enable DMA or pull CS low for the abandoned operation.
    spi_prepare(oled_spi);
    DMA1->HIFCR=OLED_DMA_FLAGS;
    DMA1_Stream4->FCR=0;
    DMA1_Stream4->PAR=(uint32_t)(uintptr_t)&SPI2->DR;
    DMA1_Stream4->M0AR=(uint32_t)(uintptr_t)slot->data;
    DMA1_Stream4->NDTR=slot->length;
    uint32_t duration=timer_from_us(2000u+slot->length*8000000u/oled_spi_hz);
    uint32_t started;irqstatus_t flags=oled_irq_begin(&started);
    if(!oled_task_live(head,hi,lo) || oled_fast_running) {
        oled_irq_end(flags,started);return;
    }
    oled_dma_status=0;oled_fast_running=1;
    oled_fast_deadline=timer_read_time()+duration;
    gpio_out_write(oled_dc,slot->dc);gpio_out_write(oled_cs,0);
    __DMB();
    DMA1_Stream4->CR=OLED_DMA_CR|DMA_SxCR_EN;
    SPI2->CR2|=SPI_CR2_TXDMAEN;
    oled_irq_end(flags,started);
    oled_task_alarm_at(oled_fast_deadline);
}
DECL_TASK(inventure_oled_fast_task);

extern void inventure_serial_errors(uint32_t *overrun,uint32_t *noise,uint32_t *framing);
extern uint32_t inventure_uart_rx_overflows(void);
void command_inventure_oled_coexist_query(uint32_t *args) {
    (void)args;uint32_t overrun,noise,framing;
    inventure_serial_errors(&overrun,&noise,&framing);
    sendf("inventure_oled_coexist_state irq_ticks=%u blocks=%u wire_bytes=%u errors=%u peak=%c uart_overruns=%u uart_noise=%u uart_framing=%u uart_rx_overflows=%u",
        oled_irq_peak_ticks,oled_fast_blocks,oled_fast_bytes,oled_fast_errors,
        oled_fast_peak,overrun,noise,framing,inventure_uart_rx_overflows());
}
DECL_COMMAND_FLAGS(command_inventure_oled_coexist_query,HF_IN_SHUTDOWN,"inventure_oled_coexist_query");
