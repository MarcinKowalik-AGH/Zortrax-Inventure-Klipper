// Register-mock tests for the actual R7 MCU fan bench source.
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#include "internal.h"
#include "gpio.h"
#include "sched.h"
#include "board/irq.h"
#include "inventure_v9.h"
GPIO_TypeDef mock_gpio[7]; TIM_TypeDef mock_tim[10]; USART_TypeDef mock_usart[7];
SPI_TypeDef mock_spi[4]; ADC_TypeDef mock_adc[4]; RCC_TypeDef mock_rcc;
static uint32_t mock_now;static jmp_buf failure;static int expect_failure, shutdown_count;
static const char *last_failure;
static uint8_t outputs[112];
static int32_t pulls[112];
static int input_init_count, input_poll_count, fault_led_count;
void inventure_inputs_init(void){input_init_count++;}
void inventure_inputs_poll(void){input_poll_count++;}
void inventure_fault_led(void){fault_led_count++;}
static int panel_shutdown_count;
static unsigned task_wake_count;
void sched_wake_tasks(void){task_wake_count++;}
uint8_t inventure_panel_needs_service(void){return 0;}
void inventure_panel_shutdown(void){panel_shutdown_count++;}
static struct timer *scheduled[32];static unsigned scheduled_count;
uint32_t timer_read_time(void){return mock_now;}
uint32_t timer_from_us(uint32_t us){return us*120;}
irqstatus_t irq_save(void){return 0;} void irq_restore(irqstatus_t x){(void)x;}
void mock_shutdown(const char*s){last_failure=s;shutdown_count++;if(expect_failure)longjmp(failure,1);fprintf(stderr,"Unexpected shutdown: %s\n",s);assert(0);__builtin_unreachable();}
void sendf(const char*f,...){(void)f;}
void*command_decode_ptr(uint32_t value){return (void*)(uintptr_t)value;}
GPIO_TypeDef *gpio_pin_to_regs(uint32_t pin) {
    assert(pin < 112);
    return &mock_gpio[pin / 16];
}
void gpio_clock_enable(GPIO_TypeDef *regs) { (void)regs; }
#define CONFIG_MACH_STM32F0 0
#include "gpioperiph.c"
void enable_pclock(uint32_t a){(void)a;}
uint8_t is_enabled_pclock(uint32_t a){(void)a;return 0;}
uint32_t get_pclock_frequency(uint32_t a){return a==(uint32_t)(uintptr_t)USART1?60000000:30000000;}
struct gpio_out gpio_out_setup(uint32_t pin,uint32_t value){struct gpio_out g={&mock_gpio[pin/16],1u<<(pin%16)};gpio_out_write(g,value);return g;}
void gpio_out_write(struct gpio_out g,uint32_t value){GPIO_TypeDef *r=g.regs;value=inventure_filter_gpio(r,g.bit,value);unsigned p=(r-mock_gpio)*16;for(unsigned n=0;n<16;n++)if(g.bit==(1u<<n)){outputs[p+n]=value;return;}assert(0);}
struct gpio_in gpio_in_setup(uint32_t pin,int32_t pull){pulls[pin]=pull;return (struct gpio_in){&mock_gpio[pin/16],1u<<(pin%16)};}
void sched_add_timer(struct timer*t){assert(t->func);for(unsigned i=0;i<scheduled_count;i++)assert(scheduled[i]!=t);assert(scheduled_count<32);scheduled[scheduled_count++]=t;}
void sched_del_timer(struct timer*t){for(unsigned i=0;i<scheduled_count;i++)if(scheduled[i]==t){scheduled[i]=scheduled[--scheduled_count];return;}assert(0);}
void sched_wake_task(struct task_wake*w){w->wake=1;}
uint8_t sched_check_wake(struct task_wake*w){uint8_t v=w->wake;w->wake=0;return v;}
#include "inventure_board.c"
#include "inventure_pwm.inc"
static unsigned adc_reads;
struct gpio_adc gpio_adc_setup(uint32_t pin) {
    inventure_adc_native_begin();
    for(unsigned i=0;i<8;i++)if(pin==cool_pins[i]) {
        static const uint8_t channels[8]={10,6,11,12,4,9,14,14};
        return (struct gpio_adc){i==1||i==5||i==6?ADC3:ADC1,channels[i]};
    }
    assert(!"unknown ADC pin");return (struct gpio_adc){0,0};
}
uint32_t gpio_adc_sample(struct gpio_adc g) {
    ADC_TypeDef *adc=g.adc;
    if(adc->SR&16u)return (adc->SR&2u) && adc->SQR3==g.chan?0:2400;
    adc->SQR3=g.chan;adc->SR=16u;return 2400;
}
uint16_t gpio_adc_read(struct gpio_adc g) {
    ADC_TypeDef *adc=g.adc;uint16_t raw=adc->DR;
    adc->SR=0;adc_reads++;inventure_adc_observe(adc,g.chan,raw);return raw;
}

static void assert_fans(uint8_t expected) {
    assert(cool_mask == expected && fan_high == (expected & 15));
    for (unsigned channel = 0; channel < 6; ++channel)
        assert(fan_pwm[channel] == ((expected & BIT(channel)) ? 255 : 0));
}

static void tick(uint32_t delta) {
    mock_now += delta;
    command_inventure_heartbeat(NULL);
    cooling_update();
}

static void command(unsigned action, unsigned channel) {
    uint32_t args[] = {action, channel};
    command_inventure_fan_manual(args);
}

static void test_heater_lock(void) {
    static const uint8_t heater_pins[] = {
        GPIO('A',0), GPIO('E',5), GPIO('C',8), GPIO('C',9), GPIO('E',10)
    };
    for (unsigned i = 0; i < ARRAY_SIZE(heater_pins); ++i) {
        uint32_t pin = heater_pins[i], shift = (pin % 16) * 2;
        uint32_t bit = BIT(pin % 16), off = i != 4;
        GPIO_TypeDef *regs = gpio_pin_to_regs(pin);
        assert(((regs->MODER >> shift) & 3u) == 1);
        gpio_peripheral(pin, 2 | (3 << 4), 1); // attempted PWM alternate function
        gpio_peripheral(pin, 3, 0);            // attempted analog mode
        gpio_peripheral(pin, 0, -1);           // attempted input with pull-down
        assert(((regs->MODER >> shift) & 3u) == 1);
        assert(inventure_filter_gpio(regs, bit, !off) == off);
        assert(inventure_filter_gpio(regs, bit, off) == off);
    }

    volatile uint32_t *ccr[] = {&TIM5->CCR1, &TIM9->CCR1, &TIM8->CCR3, &TIM8->CCR4};
    for (unsigned i = 0; i < ARRAY_SIZE(ccr); ++i) {
        struct gpio_pwm h = {.reg = (void *)ccr[i], .hwpwm_ticks = 256};
        gpio_pwm_write(h, 0);
        assert(*ccr[i] == 256);
        gpio_pwm_write(h, 127);
        assert(*ccr[i] == 256);
    }
    uint32_t arm_key[] = {0x56394c42};
    command_inventure_arm(arm_key);
    assert(!inventure_is_armed());
    assert(inventure_filter_gpio(GPIOA, BIT(6), 1) == 0);
}

int main(void) {
    mock_now = 0xfffffff0;
    inventure_early_init();
    assert_fans(0);
    assert(GPIOB->BSRR == (BIT(14) << 16) && GPIOC->BSRR == (BIT(7) << 16));
    assert(GPIOA->BSRR == (BIT(1) << 16) && GPIOD->BSRR == BIT(1));
    inventure_board_init();
    inventure_board_start_task();
    assert(sample_started);
    assert_fans(0);
    test_heater_lock();
    command(FM_START, 0); // heartbeat required
    assert(fan_manual.phase == FM_IDLE && !cool_mask);

    inventure_adc_native_begin();
    inventure_adc_observe(ADC1, 14, 1131); // out-of-range PC4 seen on printer
    cooling_update();
    assert(cool_pcb_adc == 1131 && cool_pcb_temp >= 150 && !cool_valid);
    assert_fans(0);
    // A cable input and invalid temperature remain visible telemetry only.
    GPIOD->IDR |= BIT(11);
    for (unsigned channel = 0; channel < 6; ++channel) {
        tick(MS(20));
        command(FM_START, channel);
        assert(fan_manual.phase == FM_ON && fan_manual.channel == channel);
        assert_fans(BIT(channel));
        command(FM_START, (channel + 1) % 6);
        command(FM_STOP, (channel + 1) % 6);
        assert(fan_manual.phase == FM_ON && fan_manual.channel == channel);
        assert_fans(BIT(channel));
        tick(FAN_ON_SETTLE);
        if (channel < 4)
            pulses[channel] += 200;
        tick(FAN_WINDOW);
        for (unsigned sec = 0; sec < 125; ++sec) {
            tick(MS(1000));
            assert(fan_manual.phase == FM_ON);
            assert_fans(BIT(channel));
        }
        test_heater_lock();
        command(FM_STOP, channel);
        assert(fan_manual.phase == FM_OFF);
        assert_fans(0);
        command(FM_CONFIRM_PASS, channel);
        assert(fan_manual.phase == FM_OFF);
        tick(FAN_OFF_SETTLE);
        if (channel < 4)
            pulses[channel] += 27; // first OFF window still contains coast-down
        tick(FAN_WINDOW);
        if (channel < 4) {
            assert(fan_manual.off_edges == 27);
            command(FM_CONFIRM_PASS, channel);
            assert(fan_manual.phase == FM_OFF);
            pulses[channel] += 3;
            tick(FAN_WINDOW);
            assert(fan_manual.off_edges == 3);
            tick(FAN_WINDOW);
            assert(fan_manual.off_edges == 0);
            // A rotor that starts moving in the current window cannot pass
            // using the previous zero reading.
            pulses[channel]++;
            command(FM_CONFIRM_PASS, channel);
            assert(fan_manual.phase == FM_OFF);
            tick(FAN_WINDOW);
            assert(fan_manual.off_edges == 1);
            tick(FAN_WINDOW);
            assert(fan_manual.off_edges == 0);
        }
        for (unsigned sec = 0; sec < 125; ++sec) {
            tick(MS(1000));
            assert(fan_manual.phase == FM_OFF);
            assert_fans(0);
        }
        if (channel < 4) {
            assert(fan_manual.on_edges == 200);
            assert(fan_manual.off_edges == 0);
        } else {
            assert(fan_manual.on_edges == FM_NO_EDGES);
            assert(fan_manual.off_edges == FM_NO_EDGES);
        }
        command(FM_CONFIRM_PASS, channel);
        assert(fan_manual.phase == FM_PASS && (fan_manual.passed & BIT(channel)));
    }
    assert(fan_manual.passed == 63);

    command(FM_START, 0);
    assert_fans(1);
    command(FM_CANCEL, 0);
    assert(fan_manual.phase == FM_ABORT);
    assert_fans(0);

    command(FM_START, 3);
    assert_fans(8);
    mock_now += HEARTBEAT_TICKS;
    guard_check(mock_now);
    cooling_update();
    assert(fan_manual.phase == FM_ABORT && fan_manual.reason == FM_INTERLOCK);
    assert_fans(0);
    tick(MS(20));
    assert_fans(0); // no restart of the interrupted run
    command(FM_START, 5);
    assert_fans(32);
    inventure_board_shutdown();
    assert(fault == 255 && fan_manual.phase == FM_ABORT);
    assert_fans(0);
    command(FM_START, 5);
    assert_fans(0);
    test_heater_lock();
    puts("PASS R7.1: rolling OFF windows, coast-down to stop, rejection of new pulses, six channels, heater locks and heartbeat");
}
