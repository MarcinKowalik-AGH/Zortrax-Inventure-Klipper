/* Run the actual F207 DMA branch, including task/IRQ cancellation races. */
#include "internal.h"
typedef struct {volatile uint32_t HISR,HIFCR;} TestDMA;
typedef struct {volatile uint32_t CR,NDTR,PAR,M0AR,FCR;} TestStream;
static TestDMA test_dma;static TestStream test_stream;
#define DMA1 (&test_dma)
#define DMA1_Stream4 (&test_stream)
#define DMA1_Stream4_IRQn 15
#define DMA_HISR_TEIF4 (1u<<3)
#define DMA_HISR_DMEIF4 (1u<<2)
#define DMA_HISR_FEIF4 (1u<<0)
#define DMA_HISR_TCIF4 (1u<<5)
#define DMA_HISR_HTIF4 (1u<<4)
#define DMA_SxCR_EN 1u
#define DMA_SxCR_MINC (1u<<10)
#define DMA_SxCR_DIR_0 (1u<<6)
#define DMA_SxCR_TCIE (1u<<4)
#define DMA_SxCR_TEIE (1u<<2)
#define DMA_SxCR_DMEIE (1u<<1)
#define SPI_SR_TXE 2u
#define SPI_SR_BSY 128u
#define SPI_CR2_TXDMAEN 2u
#define RCC_AHB1ENR_DMA1EN (1u<<21)
#define __DMB() __asm__ __volatile__("" ::: "memory")
void test_spi_prepare_hook(void);
#define TEST_SPI_PREPARE_HOOK test_spi_prepare_hook
#define main legacy_panel_main
#include "test_r23_panel.c"
#undef main

static unsigned serviced_uart,serviced_native;static unsigned cancel_on_prepare,generation;
uint32_t get_pclock_frequency(uint32_t address){(void)address;return 7500000u;}
void inventure_serial_errors(uint32_t *overrun,uint32_t *noise,uint32_t *framing){*overrun=*noise=*framing=0;}
uint32_t inventure_uart_rx_overflows(void){return 0;}
void test_spi_prepare_hook(void){
    // This assertion reproduces the old whole-task IRQ blackout. These two
    // interrupts must be serviceable during the real DMA register setup.
    assert(!irq_depth);serviced_uart++;serviced_native++;
    if(cancel_on_prepare==1){mock_now+=CONFIG_CLOCK_FREQ*3u;inventure_oled_watchdog();}
    if(cancel_on_prepare==2)inventure_panel_shutdown();
}
static void start(void){
    scheduled=NULL;cancel_on_prepare=0;oled_fast_alarm_active=0;
    memset(&test_dma,0,sizeof(test_dma));memset(&test_stream,0,sizeof(test_stream));
    reset_panel();uint32_t config[]={OLED_TEST_SPI_RATE};command_inventure_oled_config(config);
    begin(++generation,1);uint32_t control[]={100,generation,2,1,1};command_inventure_oled_control(control);
    assert(oled_reset_value && oled_active);SPI2->CR1=oled_spi.spi_cr1;
    oled_fast_blocks=oled_fast_bytes=oled_fast_errors=0;ack_count=0;
}
static void queue(unsigned token){
    memset(payload,0x55,32);uint32_t args[]={100,generation,token,1,0,32,32,0};
    command_inventure_oled_burst(args);assert(!oled_fault && oled_fast_count);
}
static void complete(void){
    test_stream.CR&=~DMA_SxCR_EN;SPI2->SR=SPI_SR_TXE;
    test_dma.HISR=DMA_HISR_TCIF4;inventure_oled_dma_irq();
    inventure_oled_fast_task();test_dma.HISR=0;
}
int main(void){
    start();for(unsigned token=3;token<7;token++)queue(token);
    inventure_oled_fast_task();assert(oled_fast_running && !ack_count);
    for(unsigned i=0;i<4;i++)complete();
    assert(oled_fast_blocks==4 && oled_fast_bytes==128 && !oled_fast_count);
    assert(ack_count==4 && last_token==6 && last_result==0);
    assert(!scheduled && serviced_uart==4 && serviced_native==4);
    // RX and native IRQs run during every new frame in a long FIFO sequence.
    for(unsigned token=7;token<2007;token++){queue(token);inventure_oled_fast_task();complete();}
    assert(oled_fast_blocks==2004 && !oled_fast_count && !irq_depth);
    // DMA TC alone is insufficient: never ACK bytes still shifting on SPI.
    start();queue(3);inventure_oled_fast_task();test_stream.CR&=~DMA_SxCR_EN;
    SPI2->SR=SPI_SR_TXE|SPI_SR_BSY;test_dma.HISR=DMA_HISR_TCIF4;
    inventure_oled_dma_irq();inventure_oled_fast_task();assert(!ack_count && scheduled);
    complete();assert(ack_count==1 && last_token==3);
    // Expiry or shutdown between prepare and enable cannot restart old DMA.
    for(unsigned kind=1;kind<=2;kind++){
        start();queue(3);cancel_on_prepare=kind;unsigned low_before=cs_low;
        inventure_oled_fast_task();assert(!oled_active && !oled_fast_count);
        assert(!(test_stream.CR&DMA_SxCR_EN) && !(SPI2->CR2&SPI_CR2_TXDMAEN));
        assert(cs_low==low_before && !ack_count && !irq_depth);
    }
    // The display error is isolated and never issues a printer shutdown.
    start();queue(3);inventure_oled_fast_task();test_dma.HISR=DMA_HISR_TEIF4;
    inventure_oled_dma_irq();inventure_oled_fast_task();
    assert(oled_display_failed && oled_fast_errors==1 && last_result==2);
    assert(!oled_active && !irq_depth);
    puts("PASS_R26_F207_DMA_2004_BLOCKS_IRQ_PREEMPTION_CANCELLATION_SPI_BUSY");
}
