#line 38000 "inventure_sampler_uart.inc"
/* Keep the timer list and motion callbacks non-reentrant while USART6,
 * priority 0, can drain its single-byte receiver during the board sampler.
 * The sampler body, deadlines, guards and GPIO policy are unchanged. */
static volatile uint32_t uart_sample_windows,uart_sample_peak,uart_sample_rx,uart_sample_resets;
static volatile uint8_t uart_sample_active;

void noinline inventure_sampler_uart_init(void) {
    irqstatus_t flags=irq_save();
    /* Do not inherit a bootloader's subpriority grouping: UART 0, SysTick 2
     * and DMA 4 must be distinct preemption priorities on every F207 board. */
    NVIC_SetPriorityGrouping(0);__DSB();__ISB();
    irq_restore(flags);
}
DECL_INIT(inventure_sampler_uart_init);

void inventure_sampler_uart_irq(uint32_t status) {
    if(uart_sample_active && (status&(USART_SR_RXNE|USART_SR_ORE)))uart_sample_rx++;
}

static uint_fast8_t sample_event(struct timer *timer) {
    irqstatus_t flags=irq_save();
    uint32_t old_priority=__get_BASEPRI();
    uint32_t started=timer_read_time();
    uart_sample_windows++;uart_sample_active=1;
    /* Mask all configurable priorities >=1; SysTick is 2 and OLED DMA is 4.
     * USART6 at priority 0 only consumes/produces serial bytes and wakes
     * tasks. It never dispatches commands, edits timers or writes motors. */
    __set_BASEPRI_MAX(1u<<(8-__NVIC_PRIO_BITS));
    __ISB();
    irq_enable();
    uint_fast8_t result=sample_event_body(timer);
    irq_disable();
    uart_sample_active=0;
    uint32_t elapsed=timer_read_time()-started;
    if(elapsed>uart_sample_peak)uart_sample_peak=elapsed;
    __set_BASEPRI(old_priority);
    __ISB();
    irq_restore(flags);
    return result;
}

void noinline inventure_sampler_uart_shutdown(void) {
    /* A guard may longjmp out of sample_event_body. Scheduler shutdown runs
     * with PRIMASK set; release our priority ceiling before any later reset. */
    uart_sample_active=0;uart_sample_resets++;
    __set_BASEPRI(0);__ISB();
}
DECL_SHUTDOWN(inventure_sampler_uart_shutdown);

void command_inventure_uart_window_query(uint32_t *args) {
    (void)args;irqstatus_t flags=irq_save();
    uint32_t windows=uart_sample_windows,peak=uart_sample_peak,rx=uart_sample_rx,resets=uart_sample_resets;
    uint8_t active=uart_sample_active;
    uint8_t group=NVIC_GetPriorityGrouping();
    irq_restore(flags);
    sendf("inventure_uart_window_state windows=%u peak_ticks=%u rx=%u shutdowns=%u active=%c group=%c",
        windows,peak,rx,resets,active,group);
}
DECL_COMMAND_FLAGS(command_inventure_uart_window_query,HF_IN_SHUTDOWN,"inventure_uart_window_query");
DECL_CONSTANT("INVENTURE_UART_TIMER_WINDOW_ABI",1);
