// V9 maps only. No SPI2 MISO pin is commandeered by the OLED transmitter.
// SPDX-License-Identifier: GPL-3.0-or-later
#include "autoconf.h"
#include "board/io.h"
#include "board/misc.h"
#include "command.h"
#include "gpio.h"
#include "internal.h"
#include "sched.h"
#include "inventure_policy.h"
DECL_ENUMERATION("spi_bus","spi2_inventure_oled",0);
DECL_CONSTANT_STR("BUS_PINS_spi2_inventure_oled","PC3,PB13");
DECL_ENUMERATION("spi_bus","spi3_inventure_sd",1);
DECL_CONSTANT_STR("BUS_PINS_spi3_inventure_sd","PC11,PC12,PC10");
struct spi_config spi_setup(uint32_t bus,uint8_t mode,uint32_t rate) {
    if(bus>1 || mode>3 || !rate) shutdown("Inventure invalid SPI configuration");
    SPI_TypeDef *s=bus ? SPI3:SPI2;
    if(!is_enabled_pclock((uint32_t)s)) {
        enable_pclock((uint32_t)s); s->CR1=0; s->CR2=0;
        if(bus) {
            gpio_peripheral(GPIO('C',10),GPIO_FUNCTION(6),0);
            gpio_peripheral(GPIO('C',11),GPIO_FUNCTION(6),1);
            gpio_peripheral(GPIO('C',12),GPIO_FUNCTION(6),0);
        } else {
            gpio_peripheral(GPIO('B',13),GPIO_FUNCTION(5),0);
            gpio_peripheral(GPIO('C',3),GPIO_FUNCTION(5),0);
        }
    }
    uint32_t pclk=get_pclock_frequency((uint32_t)s), div=0;
    while((pclk>>(div+1))>rate && div<7) div++;
    uint32_t cr1=mode | (div<<3) | (1u<<6) | (1u<<2) | (1u<<9) | (1u<<8);
    if(!bus) cr1 |= (1u<<15)|(1u<<14); // one-line TX, NOT RX on PC2/PB14
    return (struct spi_config){.spi=s,.spi_cr1=cr1};
}
void spi_prepare(struct spi_config config) {
    SPI_TypeDef *s=config.spi;
    if(s->CR1==config.spi_cr1) return;
    s->CR1 &= ~(1u<<6); (void)s->CR1; s->CR1=config.spi_cr1;
}
static void iv9_spi_wait(SPI_TypeDef *s,uint32_t mask,uint32_t wanted) {
    uint32_t deadline=timer_read_time()+timer_from_us(250);
    while((s->SR&mask)!=wanted)
        if(iv9_due(timer_read_time(),deadline)) shutdown("Inventure SPI timeout");
}
void spi_transfer(struct spi_config config,uint8_t receive_data,uint8_t len,uint8_t *data) {
    SPI_TypeDef *s=config.spi;
    uint32_t div=(config.spi_cr1>>3)&7;
    // At init rate use <=8 bytes/request; after fast clock switch <=32.
    unsigned max_len=div>=5 ? 8:32;
    if(len>max_len || (s==SPI2 && receive_data)) shutdown("Inventure SPI transfer bound");
    for(unsigned i=0;i<len;i++) {
        iv9_spi_wait(s,1u<<1,1u<<1);
        writeb((void*)&s->DR,data[i]);
        if(s==SPI3) {
            iv9_spi_wait(s,1u,1u);
            uint8_t value=readb((void*)&s->DR);
            if(receive_data) data[i]=value;
        }
    }
    iv9_spi_wait(s,(1u<<1)|(1u<<7),1u<<1);
}
