// R22 lifecycle regression: actual MCU code, simulated registers and time.
// No printer, electrical timing measurement or UART transport is exercised.
#include "control_test_support.inc"

static void factory_adc(unsigned hot)
{
    control_adc();
    // Independent simulated tach feedback from the four monitored fans.
    for (unsigned fan = 0; fan < 4; fan++) {
        pulses[fan]++;
        edge_time[fan] = mock_now;
    }
    if (hot) {
        inventure_adc_observe(ADC1, 10, 1427);
        inventure_adc_observe(ADC3, 6, 1427);
    }
}

static void factory_motion_tick(void)
{
    motion_tick();
    factory_adc(1);
    control_guard(mock_now);
}

static void repeated_owner_cycles(void)
{
    control_reset(0xffff0000u);
    native_hot_mask = 0;
    struct gpio_pwm heaters[4];
    for (unsigned h = 0; h < 4; h++)
        heaters[h] = gpio_pwm_setup(co_pins[h + 2], co_cycles[h + 2], 256);
    control_command(1, CT_NATIVE);
    assert(inventure_is_armed() && !control_latched);
    for (unsigned cycle = 0; cycle < 12; cycle++) {
        // Ordinary native movement must remain the sole allowed STEP owner.
        assert(inventure_filter_gpio(GPIOA, BIT(7), 1) == 1);
        assert(inventure_filter_gpio(GPIOB, BIT(9), 1) == 1);
        inventure_filter_gpio(GPIOB, BIT(2), cycle & 1);
        inventure_filter_gpio(GPIOB, BIT(1), !(cycle & 1));
        inventure_filter_gpio(GPIOE, BIT(7), 1);
        assert(native_dir_seen == 7);
        for (unsigned n = 0; n < 200; n++) {
            command_inventure_heartbeat(NULL);
            factory_adc(0);
            sample_timer.waketime = mock_now + SAMPLE_TICKS;
            mock_now = sample_timer.waketime;
            assert(sample_event(&sample_timer) == SF_RESCHEDULE);
            assert((int32_t)(sample_timer.waketime - mock_now) > 0);
        }

        // Quiescent native -> SERVICE never uses STOP or resets the MCU.
        for (unsigned h = 0; h < 4; h++)
            gpio_pwm_write(heaters[h], 256);
        GPIOE->ODR &= ~BIT(10); // native wait_moves + motor_off boundary
        control_command(1, CT_SERVICE);
        assert(control_mode == CT_SERVICE && !control_latched && !fault);
        assert(inventure_filter_gpio(GPIOA, BIT(7), 1) == 0);
        assert(inventure_filter_gpio(GPIOB, BIT(9), 1) == 0);
        unsigned owner = 1001 + cycle, token = 2001 + cycle;
        factory_adc(1);
        uint32_t session[] = {1, 0, IVT_STOP, 0, 1000, 20160, 1500,
                              205, 240, owner, 0};
        command_inventure_factory_session(session);
        assert(fs.active && !factory_reply[3]);
        output(1, 6, 64, 1500, 245, token + 100);
        assert(co[6].duty == 64);
        motion(1, 3, 20, 20, 0, 0, 1000, 10, 1000, 0, 0, 0, token);
        while (cm.active)
            factory_motion_tick();
        assert(cm.phase == CM_DONE && cm.reason == CR_COMPLETE);
        assert(cm.sent[0] == 20 && cm.sent[1] == 20);
        assert(!cm.sent[2] && !cm.sent[3]);
        GPIOE->ODR |= BIT(10);

        // Service-only state cannot leak into the return to ordinary Home.
        if (cycle % 3 == 1)
            latest_adc[4] = 4095;
        if (cycle % 3 == 2)
            control_min[1] = 300;
        uint32_t receipt[] = { owner, cm.run, cm.token };
        command_inventure_factory_native_handoff(receipt);
        assert(control_mode == CT_NATIVE && inventure_is_armed());
        assert(!fs.active && !control_latched && !fault);
        assert(native_hot_mask & 1); // SERVICE-heated T0 remains monitored
        assert(!transport_live && !TIM9->CCR2);
        for (unsigned h = 0; h < 4; h++) {
            assert(!co[h + 6].duty && !control_heater_duty[h]);
            unsigned shift = 2 * __builtin_ctz(th_heaters[h].bit);
            assert(((th_heaters[h].port->MODER >> shift) & 3u) == 2);
        }
        control_min[1] = -20;
        factory_adc(0);
        assert(native_sensors_valid(mock_now));
        assert(!(native_hot_mask & 3));
        // Reuse the PWM handles created before the first cycle.
        gpio_pwm_write(heaters[0], 192);
        assert(control_heater_duty[0] == 64);
        gpio_pwm_write(heaters[0], 256);
        assert(!control_heater_duty[0]);
        assert(inventure_filter_gpio(GPIOA, BIT(7), 1) == 1);
    }
    puts("PASS R22: 12 consecutive NATIVE/SERVICE/heat/motion/handoff/NATIVE cycles, no reset or latch");
}

static void elapsed_clock_endurance(void)
{
    // A 500-second simulated run crosses > 10 full STM32 clock wraps.
    // Independent 1 ms gaps model delayed callback delivery, not measured
    // CPU work or proof that the real step scheduler cannot report an error.
    // Keep the simulated native fan outputs high and toggle their actual
    // tach GPIOs. Updating the cumulative pulse counters alone would miss
    // the independent fan_sample()/fan_evaluate() feedback path.
    for (unsigned fan = 0; fan < 4; fan++) {
        GPIO_TypeDef *port = fan < 2 ? GPIOB : GPIOC;
        unsigned pin = fan < 2 ? 15 - fan : fan == 2 ? 7 : 6;
        fan_gpio_observe(port, BIT(pin), 1);
        inventure_fan_observe(port, BIT(pin), 255, 255);
    }
    uint64_t elapsed = 0;
    unsigned wraps = 0;
    uint32_t skipped = sample_skipped_slots;
    for (unsigned n = 0; n < 500000; n++) {
        uint32_t previous = mock_now;
        sample_timer.waketime = mock_now + SAMPLE_TICKS;
        mock_now += MS(1);
        elapsed += (uint32_t)(mock_now - previous);
        if (mock_now < previous)
            wraps++;
        command_inventure_heartbeat(NULL);
        factory_adc(0);
        GPIOE->IDR ^= BIT(14) | BIT(13) | BIT(9) | BIT(11);
        assert(sample_event(&sample_timer) == SF_RESCHEDULE);
        assert((int32_t)(sample_timer.waketime - mock_now) > 0);
        assert(control_mode == CT_NATIVE && inventure_is_armed() && !fault);
    }
    assert(elapsed == (uint64_t)MS(1) * 500000 && wraps > 10);
    assert(sample_skipped_slots > skipped);
    printf("PASS R22 sampler endurance: 500 simulated seconds, %u clock wraps, no past reschedule\n", wraps);

    // A physical interlock at the end of a long healthy session still wins.
    native_z_guard_active = 1;
    GPIOC->IDR |= BIT(13);
    expect_failure = 1;
    if (!setjmp(failure)) {
        control_guard(mock_now);
        assert(!"PC13 interlock failed after repeated cycles");
    }
    expect_failure = 0;
    assert(fault == 7 && control_latched && control_mode == CT_OFF);
    uint32_t replay[] = { native_xy_handoff_owner, native_xy_handoff_run,
                          native_xy_handoff_token };
    command_inventure_factory_native_handoff(replay);
    assert(!inventure_is_armed() && control_mode == CT_OFF);
    assert(inventure_filter_gpio(GPIOA, BIT(7), 1) == 0);
    puts("PASS R22: PC13 latches OFF after endurance; old handoff cannot rearm STEP");
}

int main(void)
{
    repeated_owner_cycles();
    elapsed_clock_endurance();
    return 0;
}
