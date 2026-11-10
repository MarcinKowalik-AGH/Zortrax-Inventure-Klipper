#pragma once
#include <stdint.h>
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define GPIO(p,n) (((p)-'A')*16+(n))
#define GPIO_FUNCTION(n) (2u | ((n) << 4))
typedef struct { volatile uint32_t MODER,OTYPER,OSPEEDR,PUPDR,IDR,ODR,BSRR,LCKR,AFR[2]; } GPIO_TypeDef;
typedef struct { volatile uint32_t CR1,CR2,SMCR,DIER,SR,EGR,CCMR1,CCMR2,CCER,CNT,PSC,ARR,RCR,CCR1,CCR2,CCR3,CCR4,BDTR; } TIM_TypeDef;
typedef struct { volatile uint32_t SR,DR,BRR,CR1,CR2,CR3; } USART_TypeDef;
typedef struct { volatile uint32_t CR1,CR2,SR,DR; } SPI_TypeDef;
typedef struct { volatile uint32_t SR,CR1,CR2,DR,SQR1,SQR2,SQR3,SMPR1,SMPR2; } ADC_TypeDef;
typedef struct { volatile uint32_t AHB1ENR,APB1RSTR,APB2RSTR; } RCC_TypeDef;
extern GPIO_TypeDef mock_gpio[7];
extern TIM_TypeDef mock_tim[10];
extern USART_TypeDef mock_usart[7];
extern SPI_TypeDef mock_spi[4];
extern ADC_TypeDef mock_adc[4];
extern RCC_TypeDef mock_rcc;
#define GPIOA (&mock_gpio[0])
#define GPIOB (&mock_gpio[1])
#define GPIOC (&mock_gpio[2])
#define GPIOD (&mock_gpio[3])
#define GPIOE (&mock_gpio[4])
#define GPIOF (&mock_gpio[5])
#define GPIOG (&mock_gpio[6])
#define TIM4 (&mock_tim[4])
#define TIM5 (&mock_tim[5])
#define TIM8 (&mock_tim[8])
#define TIM9 (&mock_tim[9])
#define USART1 (&mock_usart[1])
#define USART3 (&mock_usart[3])
#define SPI2 (&mock_spi[2])
#define SPI3 (&mock_spi[3])
#define ADC1 (&mock_adc[1])
#define ADC3 (&mock_adc[3])
#define RCC (&mock_rcc)
#define USART1_IRQn 37
#define USART3_IRQn 39
void gpio_peripheral(uint32_t pin,uint32_t function,int pull);
void enable_pclock(uint32_t address);
uint32_t get_pclock_frequency(uint32_t address);
uint8_t is_enabled_pclock(uint32_t address);
