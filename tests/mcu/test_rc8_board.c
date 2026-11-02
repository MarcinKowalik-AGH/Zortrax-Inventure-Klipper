// OFFLINE REGISTER MOCKS. Not MCU emulation, not upstream linkage, not V9 tests.
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
static uint8_t outputs[112], functions[112];
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
void gpio_peripheral(uint32_t pin,uint32_t function,int pull){(void)pull;assert(pin<112);functions[pin]=function;}
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
#define ASSERT_SHUTDOWN(statement) do{expect_failure=1;if(!setjmp(failure)){statement;assert(!"Expected shutdown");}expect_failure=0;}while(0)
static void fresh_sensors(void){sensor_seen=255;for(unsigned i=0;i<8;i++){latest_temperature[i]=25;adc_time[i]=mock_now;thermal_bad[i]=0;}heartbeat_deadline=mock_now+MS(1000);heartbeat_live=1;GPIOD->IDR&=~BIT(11);}
static void reset_guard(void){armed=fault=cable_bad=0;memset(fan_watch_active,0,sizeof(fan_watch_active));for(unsigned i=0;i<4;i++)heater_duty[i]=0;fresh_sensors();}
static int run_legacy(void){
    assert(iv9_due(10,10));assert(!iv9_due(9,10));assert(iv9_due(0x20,0xfffffff0));
    assert(iv9_frame_fits(254,1,256));assert(!iv9_frame_fits(254,2,256));
    assert(lut_temperature(1)==28048);assert(lut_temperature(4095)==-46);
    assert(inventure_filter_gpio(GPIOE,BIT(10),1)==0);
    assert(inventure_filter_gpio(GPIOC,BIT(8),0)==1);
    assert(inventure_filter_gpio(GPIOB,BIT(15),0)==1);
    assert(inventure_filter_pwm((void*)&TIM8->CCR3,0,256)==256);
    inventure_early_init(); assert((GPIOE->MODER&(3u<<20))==(1u<<20));
    assert((GPIOC->MODER&(3u<<16))==(1u<<16));
    mock_now=1000;inventure_board_init();fresh_sensors();
    assert(input_init_count==1);
    assert(pulls[GPIO('D',11)]==0);
    assert(pulls[GPIO('E',14)]==-1 && pulls[GPIO('E',13)]==-1);
    assert(pulls[GPIO('E',9)]==-1 && pulls[GPIO('E',11)]==-1);
    assert(pulls[GPIO('B',12)]==1 && pulls[GPIO('D',14)]==1);
    uint32_t ack[]={0x56394c42};command_inventure_arm(ack);assert(armed);
    ASSERT_SHUTDOWN((void)inventure_filter_gpio(GPIOA,BIT(0),1));
    ASSERT_SHUTDOWN((void)inventure_filter_gpio(GPIOC,BIT(8),0));
    assert(inventure_filter_gpio(GPIOA,BIT(0),0)==0);
    assert(inventure_filter_gpio(GPIOC,BIT(8),1)==1);
    reset_guard();command_inventure_aux_config(0);
    assert(TIM5->PSC==10&&TIM5->ARR==255&&TIM9->PSC==49&&TIM9->ARR==255);
    assert(TIM5->CCR3==0&&TIM5->CCR4==0&&TIM9->CCR2==0);
    ASSERT_SHUTDOWN((void)gpio_pwm_setup(GPIO('E',5),15360,0));
    ASSERT_SHUTDOWN((void)gpio_pwm_setup(GPIO('E',5),0xffffffffu,0));
    struct gpio_pwm ch= gpio_pwm_setup(GPIO('C',8),16776960,256);
    assert(TIM8->CCR3==256);assert(ch.hwpwm_ticks==256);
    uint32_t previous_egr=TIM8->EGR;TIM8->CNT=77;
    (void)gpio_pwm_setup(GPIO('C',9),16776960,256);
    assert(TIM8->CCR4==256&&TIM8->CNT==77&&TIM8->EGR==previous_egr);
    fresh_sensors();command_inventure_arm(ack);assert(armed);
    uint32_t feeder[]={0,1,1000};command_inventure_feeder(feeder);
    assert(feeder_running[0]&&outputs[GPIO('C',5)]==1&&TIM9->CCR2==1);
    command_inventure_feeder(feeder);assert(feeder_running[0]);
    sched_del_timer(&feeder_timer[0]);feeder_end(&feeder_timer[0]);assert(!feeder_running[0]&&outputs[GPIO('C',5)]==0);
    // ABI3 STOP: duration zero removes independent feeder timers immediately.
    for(unsigned i=0;i<2;i++) {
        feeder[0]=i;feeder[2]=1000;command_inventure_feeder(feeder);
        assert(feeder_running[i]);
        unsigned before=scheduled_count;feeder[2]=0;command_inventure_feeder(feeder);
        assert(!feeder_running[i]&&scheduled_count+1==before);
        assert(outputs[i?GPIO('G',11):GPIO('C',5)]==0);
    }
    feeder[0]=0;feeder[2]=1000;
    uint32_t bad_feed[]={0,1,1501};ASSERT_SHUTDOWN(command_inventure_feeder(bad_feed));
    uint32_t lock[]={0,1};command_inventure_lock(lock);assert(TIM5->CCR4==256);
    uint32_t stop_at=lock_timer[0].waketime;command_inventure_lock(lock);assert(lock_timer[0].waketime==stop_at);
    sched_del_timer(&lock_timer[0]);lock_hold(&lock_timer[0]);assert(TIM5->CCR4==60&&!locks_pulling[0]);
    lock[1]=0;command_inventure_lock(lock);assert(TIM5->CCR4==0&&!locks_on[0]);
    // ADC routing: actual board callback, not a simulated conversion.
    sensor_seen=0;inventure_adc_observe(ADC1,10,1000);inventure_adc_observe(ADC3,6,1200);
    inventure_adc_observe(ADC1,11,2000);inventure_adc_observe(ADC1,12,2001);
    inventure_adc_observe(ADC1,4,2002);inventure_adc_observe(ADC3,9,2003);
    inventure_adc_observe(ADC3,14,2004);inventure_adc_observe(ADC1,14,2005);
    assert(sensor_seen==255&&latest_adc[1]==1200&&latest_adc[6]==2004&&latest_adc[7]==2005);
    assert(latest_temperature[0]==(1000*660/4095));
    // Heartbeat timeout, cable debounce, stale ADC, three bad temperature checks.
    reset_guard();armed=1;
    command_inventure_feeder(feeder);feeder[0]=1;command_inventure_feeder(feeder);feeder[0]=0;
    assert(feeder_running[0]&&feeder_running[1]);
    heartbeat_deadline=mock_now;ASSERT_SHUTDOWN(guard_check(mock_now));assert(fault==1&&!armed);
    assert(!feeder_running[0]&&!feeder_running[1]&&!locks_on[0]&&!locks_on[1]);
    for(unsigned i=0;i<4;i++)assert(heater_duty[i]==0);
    assert(inventure_filter_gpio(GPIOE,BIT(10),1)==0);
    assert(inventure_filter_pwm((void*)&TIM8->CCR3,0,256)==256);
    reset_guard();armed=1;GPIOD->IDR|=BIT(11);for(int i=0;i<5;i++)guard_check(mock_now);
    ASSERT_SHUTDOWN(guard_check(mock_now));assert(fault==2&&!armed);
    reset_guard();armed=1;adc_time[0]=mock_now-MS(1000);ASSERT_SHUTDOWN(guard_check(mock_now));assert(fault==3);
    reset_guard();armed=1;latest_temperature[1]=310;guard_check(mock_now);guard_check(mock_now);
    ASSERT_SHUTDOWN(guard_check(mock_now));assert(fault==4);
    // No tach: stays monitored through output=0 when a nozzle remains hot.
    reset_guard();armed=1;latest_temperature[0]=70;guard_check(mock_now);
    mock_now+=TACH_TICKS;fresh_sensors();latest_temperature[0]=70;
    ASSERT_SHUTDOWN(guard_check(mock_now));assert(fault==5);
    // Time wrap: valid recent data and heartbeat must not spuriously trip.
    reset_guard();mock_now=0xfffffff0;fresh_sensors();armed=1;guard_check(mock_now);
    mock_now=0x20;guard_check(mock_now);assert(armed&&!fault);
    // PWM-aware tach normalization and default detector: actual board functions.
    reset_guard();armed=1;fan_fault_mask=0;
    for(unsigned i=0;i<4;i++){fan_bad[i]=fan_peak[i]=0;fan_normalized[i]=1;}
    inventure_fan_observe(GPIOB,BIT(15),4517647,12000000);
    assert(fan_pwm[0]==96);
    inventure_filter_gpio(GPIOB,BIT(15),1);assert(fan_high&1);
    inventure_filter_gpio(GPIOB,BIT(15),0);assert(!(fan_high&1));
    fan_sample_ticks=0;fan_detector_ticks=0;
    for(unsigned i=0;i<4;i++){fan_pwm[i]=96;fan_edges[i]=0;}
    fan_high=5; // FAN0/2 powered, FAN1/3 OFF: rising edges only counted when ON.
    for(unsigned tick=0;tick<20000;tick++)fan_sample(tick%100==0?15:0,mock_now);
    assert(fan_window_edges[0]==200&&fan_window_edges[1]==0&&fan_window_edges[2]==200&&fan_window_edges[3]==0);
    assert(fan_normalized[0]==200*255/96/4&&fan_sequence==1);
    fan_pwm[0]=0;fan_edges[0]=17;fan_sample_ticks=19999;fan_sample(0,mock_now);
    assert(fan_normalized[0]==0); // zero denominator explicitly safe
    for(unsigned i=0;i<4;i++){fan_pwm[i]=255;fan_normalized[i]=1;fan_bad[i]=fan_peak[i]=0;}
    fan_normalized[1]=0;
    for(unsigned n=0;n<99;n++)fan_evaluate();
    assert(fan_bad[1]==99&&!fan_fault_mask&&armed);
    fan_evaluate();assert(fan_fault_mask==2&&armed&&!fault);
    fan_normalized[1]=1;fan_evaluate();assert(!fan_bad[1]&&fan_peak[1]==100&&fan_fault_mask==2);
    fan_pwm[1]=95;fan_normalized[1]=0;fan_evaluate();assert(!fan_bad[1]&&fan_fault_mask==2);
    for(unsigned i=0;i<4;i++)if(i!=1) {
        reset_guard();armed=1;fan_fault_mask=0;
        for(unsigned j=0;j<4;j++){fan_pwm[j]=255;fan_normalized[j]=1;fan_bad[j]=fan_peak[j]=0;}
        fan_normalized[i]=0;
        for(unsigned n=0;n<99;n++)fan_evaluate();
        ASSERT_SHUTDOWN(fan_evaluate());assert(fan_fault_mask==(1u<<i)&&fault==5&&!armed);
    }
    // Actual sample-count divider: 2020 x 50us per evaluation, 100th latch.
    reset_guard();armed=1;fan_fault_mask=0;fan_detector_ticks=0;fan_sample_ticks=0;
    for(unsigned i=0;i<4;i++){fan_pwm[i]=i==1?96:0;fan_normalized[i]=0;fan_bad[i]=fan_peak[i]=fan_edges[i]=0;}
    for(unsigned n=0;n<2020*99;n++)fan_sample(0,n*SAMPLE_TICKS);
    assert(fan_bad[1]==99&&!fan_fault_mask&&armed);
    for(unsigned n=0;n<2020;n++)fan_sample(0,n*SAMPLE_TICKS);
    assert(fan_bad[1]==100&&fan_fault_mask==2&&armed);
    armed=0;
    // OFF/unarmed FAN1 cannot run; cooling remains ON and latches survive.
    assert(inventure_filter_gpio(GPIOB,BIT(14),1)==0);
    assert(inventure_filter_gpio(GPIOB,BIT(15),0)==1);
    fault=0;inventure_board_shutdown();assert(fault==255&&!armed);
    fresh_sensors();command_inventure_arm(ack);assert(!armed); // fault cannot be ACK-cleared
    assert(panel_shutdown_count>0);
    printf("PASS: board/PWM policy and register-mock scenarios; shutdowns=%d\n",shutdown_count);
    return 0;
}

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
static uint16_t raw_for(int temp) {
    for(unsigned raw=1;raw<4095;raw++)if(lut_temperature(raw)==temp)return raw;
    assert(!"temperature absent from LUT");return 0;
}
static void local_step(const uint16_t raw[8]) {
    mock_now+=MS(2);
    if(cool_pending) {
        ADC_TypeDef *adc=cool_adc[cool_channel].adc;
        adc->DR=raw[cool_channel];adc->SR=18u;
    }
    inventure_local_cooling_task();
}
static void local_cycle(const uint16_t raw[8]) {
    for(unsigned n=0;n<100;n++)local_step(raw);
}
static void native_samples(const uint16_t raw[8]) {
    static const uint8_t channels[8]={10,6,11,12,4,9,14,14};
    for(unsigned i=0;i<8;i++)inventure_adc_observe(i==1||i==5||i==6?ADC3:ADC1,channels[i],raw[i]);
    cool_policy_started=0;inventure_local_cooling_task();
}
int main(void) {
    run_legacy();
    /* Simulated cold reboot: all unknown outputs stay protective until all
     * eight locally measured channels are fresh and in their own envelope. */
    armed=fault=sensor_seen=cool_native_seen=cool_local_seen=0;
    cool_native_claimed=cool_adc_ready=cool_pending=cool_timer_started=0;
    cool_policy_started=0;cool_hot=1;cool_mask=COOL_MASK;scheduled_count=0;
    for(unsigned i=0;i<8;i++)latest_adc[i]=0;
    uint16_t raw[8]={155,155};for(unsigned i=2;i<8;i++)raw[i]=raw_for(25);
    inventure_local_cooling_task();assert(!cool_valid && cool_mask==COOL_MASK);
    local_cycle(raw);assert(cool_valid && cool_mode==COOL_LOCAL && cool_mask==0);
    assert(!sensor_seen && !cool_native_seen && !armed); // no local ARM samples
    raw[0]=311;local_cycle(raw);assert(cool_mask==COOL_MASK);
    raw[0]=300;local_cycle(raw);assert(cool_mask==COOL_MASK); // 48.35 C retains ON
    raw[0]=280;local_cycle(raw);assert(cool_mask==COOL_MASK); // 45.13 C still ON
    raw[0]=279;local_cycle(raw);assert(cool_mask==0);
    raw[0]=310;local_cycle(raw);assert(cool_mask==0); // 49.96 C retains OFF
    raw[7]=raw_for(70);local_cycle(raw);assert(cool_mask==COOL_MASK);
    raw[7]=raw_for(25);raw[0]=155;local_cycle(raw);assert(cool_mask==0);
    /* O(1) stable policy: no repeated GPIO mode writes on a static screen. */
    uint32_t before_updates=cool_gpio_updates;local_cycle(raw);
    assert(cool_gpio_updates==before_updates);
    raw[0]=0;local_cycle(raw);assert(!cool_valid && cool_mask==COOL_MASK);
    raw[0]=4095;local_cycle(raw);assert(!cool_valid && cool_mask==COOL_MASK);
    raw[0]=155;local_cycle(raw);assert(cool_mask==0);
    /* A conversion cannot extend freshness by reporting a delayed EOC. */
    cool_pending=1;cool_conversion_start=mock_now-MS(101);cool_policy_started=0;
    inventure_local_cooling_task();assert(cool_adc_failures && cool_mask==COOL_MASK);
    local_cycle(raw);assert(cool_mask==0);
    /* Exact first native setup cancels any local conversion permanently. */
    inventure_adc_native_begin();assert(cool_native_claimed && !cool_pending && !cool_local_seen);
    unsigned before=adc_reads;ADC1->CR2=1234;ADC3->CR2=5678;
    local_cycle(raw);assert(adc_reads==before && ADC1->CR2==1234 && ADC3->CR2==5678);
    assert(cool_mask==COOL_MASK); // not allowed to borrow pre-config samples
    native_samples(raw);assert(cool_valid && cool_mask==0);
    raw[1]=311;native_samples(raw);assert(cool_mask==COOL_MASK);
    raw[1]=279;native_samples(raw);assert(cool_mask==0);
    for(unsigned reason=1;reason<=5;reason++) {
        fault=reason;native_samples(raw);
        assert(cool_mask==(reason==1?0:COOL_MASK));assert(!armed && fault==reason);
    }
    fault=255;native_samples(raw);assert(cool_mask==0 && fault==255 && !armed);
    /* Native ADC can continue after shutdown; all actuation remains latched. */
    inventure_board_shutdown();assert(!cool_timer_started && !armed);
    scheduled_count=0;inventure_local_cooling_task();assert(cool_timer_started);
    raw[0]=311;native_samples(raw);assert(cool_mask==COOL_MASK && !armed && fault==255);
    raw[0]=155;native_samples(raw);assert(cool_mask==0 && !armed && fault==255);
    mock_now+=MS(1100);inventure_local_cooling_task();assert(!cool_valid && cool_mask==COOL_MASK);
    mock_now=adc_time[0]+10;cool_policy_started=0;inventure_local_cooling_task();
    assert(!cool_valid); // expired tags cannot revive across a full 32-bit wrap
    /* A delayed soft-service callback always schedules into the future. */
    mock_now=0xfffffff0u;cool_pending=0;
    cooling_event(&cool_timer);assert((uint32_t)(cool_timer.waketime-mock_now)==MS(20));
    /* Fast path and quiet periods preserve edge-count semantics for all masks. */
    for(unsigned high=0;high<16;high++)for(unsigned rising=0;rising<16;rising++) {
        uint32_t old[4];for(unsigned i=0;i<4;i++)old[i]=fan_edges[i];
        fan_high=high;fan_sample_ticks=fan_detector_ticks=0;fan_sample(rising,mock_now);
        for(unsigned i=0;i<4;i++)assert(fan_edges[i]==old[i]+!!((high&rising)&BIT(i)));
    }
    fault=0;armed=1;
    for(unsigned v=0;v<2;v++) {
        assert(inventure_filter_gpio(GPIOA,BIT(6),v)==v);
        assert(inventure_filter_gpio(GPIOA,BIT(7),v)==v);
        assert(inventure_filter_gpio(GPIOB,BIT(8),v)==v);
        assert(inventure_filter_gpio(GPIOB,BIT(9),v)==v);
    }
    armed=0;assert(inventure_filter_gpio(GPIOA,BIT(6),1)==0);
    assert(inventure_filter_gpio(GPIOB,BIT(9),1)==0);
    /* Full independent hotend ADC oracle, including both hysteresis states. */
    fault=0;mock_now=MS(1000);raw[0]=raw[1]=155;
    for(unsigned sensor=0;sensor<2;sensor++)for(unsigned prior=0;prior<2;prior++)
        for(unsigned value=0;value<4096;value++) {
            raw[sensor]=value;cool_hot=prior;native_samples(raw);
            double temp=value*660.0/4095.0;
            unsigned valid=value>0 && value<4095 && (int)temp<310;
            unsigned expected=!valid || temp>=50.0 || (temp>45.0 && prior);
            assert(cool_mask==(expected?COOL_MASK:0));
            raw[sensor]=155;
        }
    /* Sampling epoch starts after the final initialization callback. */
    sample_started=0;scheduled_count=0;mock_now=0xfffffff0u;
    inventure_board_start_task();assert(sample_started && scheduled_count==1);
    assert((uint32_t)(sample_timer.waketime-mock_now)==SAMPLE_TICKS);
    inventure_board_start_task();assert(scheduled_count==1);
    /* First requirement after long idle must get a full tach grace period. */
    mock_now=MS(20000);reset_guard();armed=1;latest_temperature[0]=70;edge_time[0]=0;
    guard_check(mock_now);assert(armed && !fault && edge_time[0]==mock_now);
    mock_now+=TACH_TICKS;fresh_sensors();latest_temperature[0]=70;
    ASSERT_SHUTDOWN(guard_check(mock_now));assert(fault==5 && !armed);
    TIM5->CR1=TIM4->CR1=1;TIM5->CCER=TIM4->CCER=1u<<4;
    cooling_native_handover();
    assert(functions[GPIO('A',1)]==GPIO_FUNCTION(2));
    assert(functions[GPIO('D',13)]==GPIO_FUNCTION(2));
    puts("PASS RC8: eight-channel local/native ownership, 50/45 hysteresis, ADC staleness/wrap, shutdown, GPIO cache, STEP/tach fast paths");
    return 0;
}
