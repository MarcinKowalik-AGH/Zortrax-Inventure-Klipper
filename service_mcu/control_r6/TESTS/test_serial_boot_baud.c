/* Production serial.c, mock STM32 registers; P4 selector is stubbed because
 * the real selector is independently exercised against factory binary images. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "internal.h"
#include "inventure_p4_baud.h"
GPIO_TypeDef mock_gpio[7];TIM_TypeDef mock_tim[10];USART_TypeDef mock_usart[7];
SPI_TypeDef mock_spi[4];ADC_TypeDef mock_adc[4];RCC_TypeDef mock_rcc;
static uint32_t selected=250000, saw_flash=0,pclk_checked=0;
static unsigned calls=0,pins=0;
#define CHECK(c) do {calls++; if(!(c)){fprintf(stderr,"FAIL serial test line=%d: %s\n",__LINE__,#c);exit(1);}}while(0)
void inventure_p4_from_image(const volatile uint8_t *image, struct inventure_p4_selection *o){
    CHECK((uintptr_t)image==INVENTURE_P4_BOOT_BASE);saw_flash++;
    o->baud=selected;o->revision=2;o->has_saved=1;o->saved_index=3;
    o->slots_used=1;o->invalid_slots=0;
}
void serial_rx_byte(uint_fast8_t b){(void)b;}
int serial_get_tx_byte(uint8_t *b){(void)b;return -1;}
void enable_pclock(uint32_t addr){CHECK(addr==(uint32_t)(uintptr_t)USART6);}
uint32_t get_pclock_frequency(uint32_t addr){CHECK(addr==(uint32_t)(uintptr_t)USART6);pclk_checked++;return 60000000u;}
void gpio_peripheral(uint32_t pin,uint32_t function,int pull){(void)pull;CHECK(function==8);CHECK(pin==GPIO('G',9)||pin==GPIO('G',14));pins++;}
void sendf(const char *fmt,...){(void)fmt;}
void mock_shutdown(const char *s){fprintf(stderr,"shutdown %s\n",s);exit(2);}
/* serial_init has external linkage and is linked from unmodified serial.c */
void serial_init(void);
void serial_enable_tx_irq(void);
void command_inventure_p4_baud_query(uint32_t*);
int main(void){const uint32_t options[]={19200u,57600u,115200u,250000u,500000u,750000u,1000000u,1500000u,2000000u};
  for(unsigned i=0;i<9;i++){
    selected=options[i]; memset(mock_usart,0,sizeof(mock_usart));
    serial_init();
    uint32_t div=(60000000u+selected/2u)/selected;
    CHECK(USART6->BRR==div);
    CHECK(USART6->CR1==((1u<<13)|(1u<<5)|(1u<<3)|(1u<<2)));
    serial_enable_tx_irq();CHECK(USART6->CR1&USART_CR1_TXEIE);
    command_inventure_p4_baud_query(NULL);
  }
  CHECK(saw_flash==9);CHECK(pclk_checked==9);CHECK(pins==18);
  printf("PASS p4_serial_init_mock checks=%u\n",calls);return 0;
}
