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
static uint32_t factory_reply[15];
void sendf(const char*f,...){
    if(strncmp(f,"inventure_factory_state ",24))return;
    va_list ap;va_start(ap,f);
    for(unsigned i=0;i<15;i++)factory_reply[i]=va_arg(ap,unsigned int);
    va_end(ap);
}
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
        gpio_peripheral(pin, 2 | (3 << 4), 1); // exact heater AF allowed only at safe CCR=256
        uint32_t expected_mode=(regs->MODER>>shift)&3u;
        assert(expected_mode==1 || (i!=0 && i!=4 && expected_mode==2));
        gpio_peripheral(pin, 3, 0);            // attempted analog mode
        gpio_peripheral(pin, 0, -1);           // attempted input with pull-down
        assert(((regs->MODER >> shift) & 3u) == expected_mode);
        assert(inventure_filter_gpio(regs, bit, !off) == off);
        assert(inventure_filter_gpio(regs, bit, off) == off);
        raw_output(regs,pin%16,off);
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

static void test_cold_fan_regression(void) {
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

static void heat_adc(unsigned a, unsigned b) {
    inventure_adc_observe(ADC1,10,a);
    inventure_adc_observe(ADC3,6,b);
}
static void heat_reset(uint32_t now) {
    control_mode=control_latched=control_pwm_bypass=0;
    memset(&cm,0,sizeof(cm));memset(co,0,sizeof(co));
    memset((void*)control_heater_duty,0,sizeof(control_heater_duty));
    control_heat_seen=0;control_cooldown_ms=0;
    memset(&t0_heat,0,sizeof(t0_heat));
    fault=0;initialized=sample_started=1;heartbeat_live=0;
    fan_manual.phase=FM_IDLE;cool_native_claimed=1;cool_native_seen=0;
    mock_now=now;cool_mask=0;cool_applied=255;
    raw_safe_outputs();
    command_inventure_heartbeat(NULL);
    heat_adc(163,166);
    inventure_adc_observe(ADC1,14,1054); // BAD PC4 does NOT block this test
    cooling_update();
    assert(!cool_valid && cool_pcb_temp==160);
    assert_fans(0);
}
static void heat_command(unsigned action) {
    uint32_t args[]={action,0};command_inventure_heat_test(args);
}
static void other_actuators_locked(void) {
    assert(!inventure_is_armed());
    for (unsigned i=0;i<4;i++) {
        uint32_t expected=t0_heat_gpio_level(i);
        assert(inventure_filter_gpio(th_heaters[i].port,th_heaters[i].bit,0)==expected);
        assert(inventure_filter_gpio(th_heaters[i].port,th_heaters[i].bit,1)==expected);
        if (i != t0_heat.heater) assert(expected==1);
    }
    assert(inventure_filter_gpio(GPIOE,BIT(10),1)==0);
    assert(inventure_filter_gpio(GPIOA,BIT(6),1)==0);
    assert(inventure_filter_gpio(GPIOA,BIT(7),1)==0);
    assert(inventure_filter_pwm((void*)&TIM5->CCR1,0,256)==256);
}
static void all_heat_adc(void) {
    heat_adc(163,166);
    inventure_adc_observe(ADC1,11,3700);
    inventure_adc_observe(ADC1,12,3670);
    inventure_adc_observe(ADC1,4,3750);
}
static void exact_twenty_seconds(uint32_t start, unsigned heater) {
    heat_reset(start);all_heat_adc();
    uint32_t args[]={1,heater};command_inventure_heat_test(args);
    assert(t0_heat.phase==TH_HEATING && t0_heat.on);
    assert_fans(th_heaters[heater].fans);
    assert(th_heaters[heater].port->BSRR==(uint32_t)th_heaters[heater].bit<<16); // active-LOW pulse, no PE10 change
    uint32_t until=t0_heat.started, run=t0_heat.run;
    heat_command(1); // cannot renew or extend an active pulse
    assert(t0_heat.started==until && t0_heat.run==run);
    unsigned on_samples=1;
    for(unsigned tickno=1;tickno<400000;tickno++) {
        mock_now+=SAMPLE_TICKS;
        if(tickno%2000==0) {
            command_inventure_heartbeat(NULL);
            all_heat_adc();
        }
        sample_event(&sample_timer);
        assert(t0_heat.phase==TH_HEATING);
        on_samples+=t0_heat.on;
        if(tickno%400==0) cooling_update();
        if(tickno%10000==0) {
            other_actuators_locked();
            heat_command(0); // query never extends the run
            assert_fans(th_heaters[heater].fans);
        }
    }
    assert(mock_now-start==TH_DURATION-SAMPLE_TICKS);
    mock_now+=SAMPLE_TICKS;
    sample_event(&sample_timer); // ISR alone must cut at exactly 20 s
    assert(t0_heat.phase==TH_COOLING && !t0_heat.on);
    assert(t0_heat.reason==TH_ELAPSED && t0_heat.elapsed_ms==20000);
    assert(on_samples==100000); // 25% * 400000 sampler intervals
    assert(t0_heat.started==until && t0_heat.run==run);
    assert_fans(th_heaters[heater].fans);
    heat_command(1);assert(t0_heat.phase==TH_COOLING);
    mock_now+=TH_COOL_SETTLE-1;all_heat_adc();cooling_update();
    assert(t0_heat.phase==TH_COOLING);
    mock_now++;all_heat_adc();cooling_update();
    assert(t0_heat.phase==TH_DONE && !t0_heat.on);
    assert_fans(0);other_actuators_locked();
}
static void test_heat_probe(void) {
    for(unsigned heater=0;heater<2;heater++) exact_twenty_seconds(0,heater); // duration > INT32_MAX ticks at 120 MHz
    for(unsigned heater=0;heater<2;heater++) exact_twenty_seconds(0xffff0000u,heater); // duration across uint32 wrap
    heat_reset(12345);heat_command(255);assert(t0_heat.phase==TH_IDLE);
    heat_command(2);assert(t0_heat.phase==TH_IDLE && !t0_heat.on);
    command(FM_START,2);heat_command(1);assert(t0_heat.phase==TH_IDLE);
    command(FM_CANCEL,0);heat_command(1);assert(t0_heat.phase==TH_HEATING);
    command(FM_START,1);assert_fans(TH_FANS); // fan test cannot steal cooling
    command(FM_CANCEL,0);assert(t0_heat.reason==TH_STOP && !t0_heat.on);
    assert_fans(TH_FANS);
    heat_reset(0);heat_command(1);
    heat_adc(TH_RAW_C(45),166);sample_event(&sample_timer);
    assert(t0_heat.phase==TH_HEATING && !t0_heat.on && !t0_heat.demand);
    heat_adc(TH_RAW_C(44),166);sample_event(&sample_timer);
    assert(t0_heat.phase==TH_HEATING && t0_heat.on);
    heat_adc(163,TH_RAW_C(50));sample_event(&sample_timer);
    assert(t0_heat.phase==TH_COOLING && t0_heat.reason==TH_LIMIT && !t0_heat.on);
    assert(t0_heat.peak[1]==TH_RAW_C(50));
    assert(t0_heat.baseline[0]==163 && t0_heat.baseline[1]==166);
    heat_reset(0);heat_command(1);mock_now+=TH_FRESH;
    command_inventure_heartbeat(NULL);sample_event(&sample_timer);
    assert(t0_heat.reason==TH_ADC && !t0_heat.on);
    heat_reset(0);heat_command(1);mock_now+=HEARTBEAT_TICKS;
    heat_adc(163,166);sample_event(&sample_timer);
    assert(t0_heat.reason==TH_HEARTBEAT && !t0_heat.on);
    command_inventure_heartbeat(NULL);cooling_update();
    assert(t0_heat.phase==TH_COOLING && !t0_heat.on); // no automatic restart
    heat_reset(0);heat_command(1);inventure_board_shutdown();
    assert(fault==255 && t0_heat.reason==TH_SHUTDOWN && !t0_heat.on);
    assert_fans(TH_FANS);
    assert(GPIOG->BSRR==BIT(4)); // cooling path restored AFTER raw safe outputs
    other_actuators_locked();
    heat_command(1);assert(t0_heat.phase==TH_COOLING);
    mock_now+=TH_COOL_SETTLE; // stale nozzles cannot release cooling
    cooling_update();assert_fans(TH_FANS);
    heat_adc(TH_RAW_C(45),166);cooling_update();assert_fans(TH_FANS);
    mock_now+=MS(100);heat_adc(163,166);cooling_update();assert_fans(0);
    assert(t0_heat.phase==TH_DONE && !t0_heat.on);
    heat_reset(0);heat_adc(8,166);heat_command(1);
    assert(t0_heat.phase==TH_IDLE && !t0_heat.on);
    heat_reset(0);heat_adc(163,4095);heat_command(1);
    assert(t0_heat.phase==TH_IDLE && !t0_heat.on);
    heat_reset(0);heat_adc(TH_RAW_C(50),166);heat_command(1);
    assert(t0_heat.phase==TH_IDLE && !t0_heat.on);
    puts("PASS T0 HEAT: exact ISR 20s and 25% duty, wrap, target, both nozzle limits, ADC freshness, heartbeat, stop/cancel, shutdown cooling, no restart, PC4-independent and other outputs locked");
}
static void test_chamber_sensors_and_acquisition_peaks(void) {
    for (unsigned raw=1;raw<4095;raw++) {
        int value=lut_temperature(raw);
        assert((value>=50)==(raw<=TH_NTC_GE50));
        assert((value>=45)==(raw<=TH_NTC_GE45));
        assert((value<=44)==(raw>=TH_NTC_LE44));
        assert((value<=40)==(raw>=TH_NTC_LE40));
        assert((value>=-30)==(raw<=TH_NTC_GE_NEG30));
    }
    for(unsigned h=2;h<4;h++) {
        heat_reset(0);all_heat_adc();
        uint32_t args[]={3,h};command_inventure_heat_test(args);
        assert(t0_heat.heater==h && t0_heat.phase==TH_HEATING);
        assert_fans(12);other_actuators_locked();
        unsigned control=th_heaters[h].sensor;
        inventure_adc_observe(ADC1,control==2?11:12,TH_NTC_GE45);
        sample_event(&sample_timer);assert(t0_heat.on && t0_heat.demand);
        inventure_adc_observe(ADC1,control==2?11:12,TH_NTC_LE44);
        sample_event(&sample_timer);assert(t0_heat.on && t0_heat.demand);
        // Any chamber sensor can stop either heater; not only the nominal pair.
        inventure_adc_observe(ADC1,4,TH_NTC_GE50);
        sample_event(&sample_timer);
        assert(t0_heat.phase==TH_COOLING && t0_heat.reason==TH_LIMIT && !t0_heat.on);
        assert_fans(12);
    }
    heat_reset(0);all_heat_adc();
    uint32_t args[]={3,2};command_inventure_heat_test(args);
    inventure_adc_observe(ADC1,11,3500);
    assert(t0_heat.peak[2]==3500 && t0_heat.heating_peak[2]==3500);
    args[0]=2;command_inventure_heat_test(args);
    inventure_adc_observe(ADC1,11,3400);
    assert(t0_heat.peak[2]==3400 && t0_heat.heating_peak[2]==3500);
    assert(t0_heat.baseline[2]==3700);
    // A failed request preserves selection and the previous completed record.
    mock_now+=TH_COOL_SETTLE;all_heat_adc();cooling_update();
    assert(t0_heat.phase==TH_DONE);
    uint32_t run=t0_heat.run;args[0]=1;args[1]=255;
    command_inventure_heat_test(args);
    assert(t0_heat.run==run && t0_heat.heater==2 && !t0_heat.on);
    // Stale/noncontrol chamber input blocks starting a chamber trial.
    heat_reset(0);all_heat_adc();mock_now+=TH_FRESH;
    command_inventure_heartbeat(NULL);heat_adc(163,166);
    args[1]=2;command_inventure_heat_test(args);
    assert(t0_heat.phase==TH_IDLE && !t0_heat.on);
    // Shutdown must switch off each physical selection and retain its own fans.
    for (unsigned h=0;h<4;h++) {
        heat_reset(0);all_heat_adc();
        uint32_t selection[]={h<2?1:3,h};command_inventure_heat_test(selection);
        assert(t0_heat.on);
        inventure_board_shutdown();
        assert(!t0_heat.on && t0_heat.reason==TH_SHUTDOWN);
        assert_fans(th_heaters[h].fans);
        for(unsigned pin=0;pin<4;pin++) assert(t0_heat_gpio_level(pin)==1);
        other_actuators_locked();
        mock_now+=TH_COOL_SETTLE;all_heat_adc();cooling_update();
        assert(t0_heat.phase==TH_DONE);assert_fans(0);
        command_inventure_heartbeat(NULL);cooling_update();
        assert(!t0_heat.on && t0_heat.phase==TH_DONE);
    }
    // A cooling peak is retained in the MCU, independently of host polling.
    heat_reset(0);heat_command(1);heat_adc(263,166);heat_command(2);
    heat_adc(280,177);
    assert(t0_heat.peak[0]==280 && t0_heat.heating_peak[0]==263);
    assert(t0_heat.peak[1]==177 && t0_heat.heating_peak[1]==166);
    // Cross-channel heating cannot grant a second heater while one is active.
    heat_reset(0);all_heat_adc();heat_command(1);
    args[0]=1;args[1]=1;command_inventure_heat_test(args);
    assert(t0_heat.heater==0 && t0_heat.run==1);
    other_actuators_locked();
    puts("PASS R3: separate nozzle/chamber profiles, no 45C chamber regulation, OEM LUT bounds, chamber cross-sensor cutoff, stale ADC, acquisition peaks through cooling, shutdown and no cross-channel activation");
}
static void exact_chamber_three_minutes(uint32_t start, unsigned heater) {
    heat_reset(start);all_heat_adc();
    uint32_t old_start[]={1,heater};command_inventure_heat_test(old_start);
    assert(t0_heat.phase==TH_IDLE && !t0_heat.on); // old host cannot start 75%
    uint32_t args[]={3,heater};command_inventure_heat_test(args);
    assert(t0_heat.phase==TH_HEATING && t0_heat.on);assert_fans(12);
    uint32_t run=t0_heat.run,started=t0_heat.started;
    unsigned on_samples=1,wraps=0;
    for(unsigned tickno=1;tickno<3600000;tickno++) {
        uint32_t previous=mock_now;mock_now+=SAMPLE_TICKS;
        if(mock_now<previous)wraps++;
        if(tickno%2000==0){command_inventure_heartbeat(NULL);all_heat_adc();}
        sample_event(&sample_timer);
        assert(t0_heat.phase==TH_HEATING && t0_heat.demand);
        on_samples+=t0_heat.on;
        if(tickno%20000==0){args[0]=0;command_inventure_heat_test(args);other_actuators_locked();assert_fans(12);}
        if(tickno==1000000){args[0]=3;command_inventure_heat_test(args);assert(t0_heat.run==run && t0_heat.started==started);}
    }
    mock_now+=SAMPLE_TICKS;sample_event(&sample_timer);
    assert(wraps>=5 && on_samples==2700000);
    assert(t0_heat.phase==TH_COOLING && t0_heat.reason==TH_ELAPSED);
    assert(t0_heat.elapsed_ms==180000 && !t0_heat.on);assert_fans(12);
    mock_now+=TH_COOL_SETTLE;all_heat_adc();cooling_update();
    assert(t0_heat.phase==TH_DONE);assert_fans(0);
}
static void chamber_abort_cases(void) {
    for(unsigned h=2;h<4;h++) {
        for(unsigned sensor=0;sensor<5;sensor++) {
            heat_reset(0);all_heat_adc();uint32_t args[]={3,h};command_inventure_heat_test(args);
            if(sensor<2)heat_adc(sensor==0?TH_RAW_C(50):163,sensor==1?TH_RAW_C(50):166);
            else inventure_adc_observe(ADC1,sensor==2?11:(sensor==3?12:4),TH_NTC_GE50);
            sample_event(&sample_timer);
            assert(t0_heat.reason==TH_LIMIT && !t0_heat.on);assert_fans(12);
        }
        heat_reset(0);all_heat_adc();uint32_t args[]={3,h};command_inventure_heat_test(args);
        args[0]=2;command_inventure_heat_test(args);assert(t0_heat.reason==TH_STOP && !t0_heat.on);assert_fans(12);
        heat_reset(0);all_heat_adc();args[0]=3;command_inventure_heat_test(args);
        mock_now+=HEARTBEAT_TICKS;all_heat_adc();sample_event(&sample_timer);
        assert(t0_heat.reason==TH_HEARTBEAT && !t0_heat.on);assert_fans(12);
        heat_reset(0);all_heat_adc();command_inventure_heat_test(args);
        mock_now+=TH_FRESH;command_inventure_heartbeat(NULL);sample_event(&sample_timer);
        assert(t0_heat.reason==TH_ADC && !t0_heat.on);assert_fans(12);
    }
    heat_reset(0);all_heat_adc();uint32_t invalid[]={3,0};command_inventure_heat_test(invalid);
    assert(t0_heat.phase==TH_IDLE && !t0_heat.on);
}
int main(void) {
    test_cold_fan_regression();
    test_heat_probe();
    test_chamber_sensors_and_acquisition_peaks();
    for(unsigned h=2;h<4;h++) {exact_chamber_three_minutes(0,h);exact_chamber_three_minutes(0xffff0000u,h);}
    chamber_abort_cases();
    puts("PASS CHAMBER75: exact 180s and 75% across repeated timer wraps, old START refused, all five overheat inputs, STOP, stale ADC, lost heartbeat and retained cooling");
    return 0;
}
