// SPDX-License-Identifier: GPL-3.0-or-later
// Nozzles: 20 s / 25% / 45C. Chamber: 180 s / constant 75% / 50C cutoff.
// Internal t0_heat hook names are
// retained so the qualified fan sampler/cancel/shutdown paths keep their owners.
#line 6000 "inventure_heat_test.inc"
DECL_CONSTANT_STR("INVENTURE_HEAT_TEST_RELEASE", "RC8-THERMAL-R3-CHAMBER75");
DECL_CONSTANT("INVENTURE_CHAMBER_HEAT_ABI", 1);
DECL_CONSTANT("INVENTURE_CHAMBER_DUTY_PCT", 75);
DECL_CONSTANT("INVENTURE_CHAMBER_DURATION_MS", 180000);
DECL_CONSTANT("INVENTURE_CHAMBER_OVERHEAT_C", 50);
#define TH_DURATION MS(20000)
#define TH_CHAMBER_MS 180000u
#define TH_PWM_PERIOD MS(100)
#define TH_PWM_ON MS(25)
#define TH_CHAMBER_PWM_ON MS(75)
#define TH_FRESH MS(1000)
#define TH_COOL_SETTLE MS(5000)
#define TH_RAW_C(c) (((uint32_t)(c) * 4095u + 659u) / 660u)
#define TH_FANS (BIT(0) | BIT(5))
// Exact inequalities of the existing first-threshold >= raw OEM LUT.
// Exhaustively checked against every raw ADC count in the native suite.
#define TH_NTC_GE50 3321u
#define TH_NTC_GE45 3421u
#define TH_NTC_LE44 3422u
#define TH_NTC_LE40 3482u
#define TH_NTC_GE_NEG30 4061u

enum { TH_IDLE, TH_HEATING, TH_COOLING, TH_DONE };
enum { TH_NONE, TH_ELAPSED, TH_STOP, TH_HEARTBEAT, TH_ADC,
       TH_LIMIT, TH_SHUTDOWN };
enum { TH_OK, TH_BUSY, TH_NOT_READY, TH_BAD_ADC, TH_TOO_HOT, TH_ACTION };
static const struct {
    GPIO_TypeDef *port;
    uint16_t bit;
    uint8_t sensor, fans, required;
} th_heaters[4] = {
    {GPIOA, BIT(0), 0, TH_FANS, 3},
    {GPIOE, BIT(5), 1, TH_FANS, 3},
    {GPIOC, BIT(9), 2, BIT(2)|BIT(3), 31},
    {GPIOC, BIT(8), 3, BIT(2)|BIT(3), 31},
};
static struct {
    uint32_t run, started, ended, elapsed_ms, last_tick, elapsed_fraction, pwm_phase;
    uint16_t peak[8], heating_peak[8], baseline[8];
    uint8_t phase, reason, on, demand, cool_settled, heater;
    uint8_t peak_seen, heating_seen;
} t0_heat;

static uint32_t t0_heat_duration_ms(void) {
    return t0_heat.heater < 2 ? 20000u : TH_CHAMBER_MS;
}
static void t0_heat_update_elapsed(uint32_t now) {
    if (t0_heat.phase != TH_HEATING) return;
    // Each ISR delta is bounded, but a full chamber trial crosses the
    // 120MHz timer's uint32 wrap repeatedly. Accumulate milliseconds and
    // a sub-millisecond remainder instead of subtracting the start clock.
    uint32_t delta = now-t0_heat.last_tick;
    t0_heat.last_tick = now;
    uint32_t fraction = t0_heat.elapsed_fraction + delta % MS(1);
    t0_heat.elapsed_ms += delta / MS(1) + fraction / MS(1);
    t0_heat.elapsed_fraction = fraction % MS(1);
    t0_heat.pwm_phase = (t0_heat.pwm_phase + delta % TH_PWM_PERIOD) % TH_PWM_PERIOD;
}
static uint8_t t0_heat_fans(void) { return th_heaters[t0_heat.heater].fans; }
static uint32_t t0_heat_gpio_level(unsigned heater) {
    return !(t0_heat.on && t0_heat.heater == heater);
}
static uint8_t t0_heat_busy(void) {
    return t0_heat.phase == TH_HEATING || t0_heat.phase == TH_COOLING;
}
static uint8_t t0_heat_raw_valid(unsigned i, uint16_t raw) {
    return raw > (i < 2 ? 8 : 0) && raw < 4095;
}
static void t0_heat_write(uint8_t on) {
    if (t0_heat.on != on) {
        // The previous proven OFF latch remains HIGH on all nonselected pins.
        th_heaters[t0_heat.heater].port->BSRR =
            (uint32_t)th_heaters[t0_heat.heater].bit << (on ? 16 : 0);
        t0_heat.on = on;
    }
}
static void t0_heat_observe(unsigned i, uint16_t raw) {
    // Called at acquisition, including cooling. No LUT or extra sampling in ISR.
    if (!t0_heat_busy() || !t0_heat_raw_valid(i, raw)) return;
    uint8_t bit = BIT(i);
    if (!(t0_heat.peak_seen & bit)
        || (i < 2 ? raw > t0_heat.peak[i] : raw < t0_heat.peak[i])) {
        t0_heat.peak[i] = raw;
        t0_heat.peak_seen |= bit;
    }
    if (t0_heat.phase == TH_HEATING && (!(t0_heat.heating_seen & bit)
        || (i < 2 ? raw > t0_heat.heating_peak[i] : raw < t0_heat.heating_peak[i]))) {
        t0_heat.heating_peak[i] = raw;
        t0_heat.heating_seen |= bit;
    }
}
static uint8_t t0_heat_read(uint32_t now, uint16_t raw[8]) {
    uint8_t seen = cool_native_claimed ? cool_native_seen : cool_local_seen;
    uint8_t result = TH_OK, required = th_heaters[t0_heat.heater].required;
    for (unsigned i=0; i<8; i++) {
        raw[i] = cool_native_claimed ? latest_adc[i] : cool_local_raw[i];
        if (!(required & BIT(i))) continue;
        uint32_t stamp = cool_native_claimed ? adc_time[i] : cool_local_time[i];
        if (!(seen & BIT(i)) || (uint32_t)(now-stamp) >= TH_FRESH
            || !t0_heat_raw_valid(i, raw[i])
            || (i >= 2 && raw[i] > TH_NTC_GE_NEG30))
            result = TH_BAD_ADC;
        else if ((i < 2 ? raw[i] >= TH_RAW_C(50) : raw[i] <= TH_NTC_GE50)
                 && result == TH_OK)
            result = TH_TOO_HOT;
    }
    return result;
}
static void t0_heat_end(uint8_t reason, uint32_t now) {
    t0_heat_write(0);
    t0_heat.demand = 0;
    if (t0_heat.phase != TH_HEATING) return;
    t0_heat_update_elapsed(now);
    if (t0_heat.elapsed_ms > t0_heat_duration_ms())
        t0_heat.elapsed_ms = t0_heat_duration_ms();
    t0_heat.ended = now;
    t0_heat.cool_settled = 0;
    t0_heat.reason = reason;
    t0_heat.phase = TH_COOLING;
}
static void t0_heat_manual_cancel(void) { t0_heat_end(TH_STOP, timer_read_time()); }
static void t0_heat_sample(uint32_t now) {
    if (t0_heat.phase != TH_HEATING) return;
    t0_heat_update_elapsed(now);
    if (t0_heat.elapsed_ms >= t0_heat_duration_ms()) {
        t0_heat_end(TH_ELAPSED, now); return;
    }
    if (fault || !initialized || !sample_started || !heartbeat_live
        || iv9_due(now, heartbeat_deadline)) {
        t0_heat_end(TH_HEARTBEAT, now); return;
    }
    uint16_t raw[8];
    uint8_t result = t0_heat_read(now, raw);
    if (result != TH_OK) {
        t0_heat_end(result == TH_TOO_HOT ? TH_LIMIT : TH_ADC, now); return;
    }
    unsigned sensor = th_heaters[t0_heat.heater].sensor;
    if (sensor < 2) {
        if (raw[sensor] >= TH_RAW_C(45)) t0_heat.demand = 0;
        else if (raw[sensor] <= TH_RAW_C(44)) t0_heat.demand = 1;
    } else {
        // Air transport to the thermistor is delayed. This is a constant
        // power diagnostic; 45C is not a setpoint for a chamber heater.
        t0_heat.demand = 1;
    }
    uint32_t on_ticks = sensor < 2 ? TH_PWM_ON : TH_CHAMBER_PWM_ON;
    t0_heat_write(t0_heat.demand && t0_heat.pwm_phase < on_ticks);
}
static void t0_heat_cooldown(uint32_t now) {
    if (t0_heat.phase != TH_COOLING) return;
    if (!t0_heat.cool_settled
        && (uint32_t)(now-t0_heat.ended) >= TH_COOL_SETTLE)
        t0_heat.cool_settled = 1;
    uint16_t raw[8];
    if (!t0_heat.cool_settled || t0_heat_read(now, raw) != TH_OK) return;
    uint8_t required = th_heaters[t0_heat.heater].required;
    for (unsigned i=0; i<8; i++)
        if ((required & BIT(i))
            && (i < 2 ? raw[i] > TH_RAW_C(40) : raw[i] < TH_NTC_LE40)) return;
    t0_heat.phase = TH_DONE;
}
void command_inventure_heat_test(uint32_t *args) {
    unsigned action = args[0], heater = args[1];
    cooling_update();
    irqstatus_t flags = irq_save();
    uint32_t now = timer_read_time();
    uint8_t reject = TH_OK;
    // ABI3 chamber START uses action 3. An older host's action 1 may only
    // start a nozzle, so it cannot silently run the new 75% power profile.
    if (action > 3 || (action == 1 && heater >= 2)
        || (action == 3 && (heater < 2 || heater >= 4))) reject = TH_ACTION;
    else if (action == 2) t0_heat_end(TH_STOP, now);
    else if (action == 1 || action == 3) {
        if (t0_heat_busy() || fan_manual_active()) reject = TH_BUSY;
        else if (fault || !initialized || !sample_started || !heartbeat_live
                 || iv9_due(now, heartbeat_deadline)) reject = TH_NOT_READY;
        else {
            // Validate the requested zone without erasing the previous run.
            unsigned previous_heater = t0_heat.heater;
            t0_heat.heater = heater;
            uint16_t raw[8];
            reject = t0_heat_read(now, raw);
            if (reject) t0_heat.heater = previous_heater;
            else {
                t0_heat.run++;
                t0_heat.started = now;
                t0_heat.ended = t0_heat.elapsed_ms = 0;
                t0_heat.last_tick = now;
                t0_heat.elapsed_fraction = t0_heat.pwm_phase = 0;
                t0_heat.peak_seen = t0_heat.heating_seen = 0;
                uint8_t seen = cool_native_claimed ? cool_native_seen : cool_local_seen;
                for (unsigned i=0; i<8; i++) {
                    uint32_t stamp = cool_native_claimed ? adc_time[i] : cool_local_time[i];
                    uint16_t value = (seen & BIT(i)) && (uint32_t)(now-stamp) < TH_FRESH
                        && t0_heat_raw_valid(i, raw[i]) ? raw[i] : 0;
                    t0_heat.baseline[i] = t0_heat.peak[i] = t0_heat.heating_peak[i] = value;
                    if (value) t0_heat.peak_seen |= BIT(i), t0_heat.heating_seen |= BIT(i);
                }
                t0_heat.reason = TH_NONE; t0_heat.cool_settled = 0;
                t0_heat.demand = 1;
                // Start both verified chamber blowers for either chamber heater.
                cool_mask = t0_heat_fans(); cooling_apply();
                t0_heat.phase = TH_HEATING;
            }
        }
    }
    irq_restore(flags);
    cooling_update();
    flags = irq_save();
    now = timer_read_time();
    t0_heat_update_elapsed(now);
    uint32_t duration = t0_heat_duration_ms(), elapsed = t0_heat.elapsed_ms;
    if (elapsed > duration) elapsed = duration;
    uint32_t run=t0_heat.run;
    uint8_t selected=t0_heat.heater, phase=t0_heat.phase, reason=t0_heat.reason;
    uint8_t duty=phase == TH_HEATING && t0_heat.demand ? (selected < 2 ? 25 : 75) : 0;
    uint8_t on=t0_heat.on, mask=cool_mask;
    irq_restore(flags);
    sendf("inventure_heat_state run=%u heater=%c phase=%c reason=%c reject=%c elapsed_ms=%u remaining_ms=%u target_c=%hi duty=%c applied=%c cooling_mask=%c",
          run, selected, phase, reason, reject, elapsed,
          phase == TH_HEATING ? duration-elapsed : 0, selected < 2 ? 45 : 0,
          duty, on, mask);
}
DECL_COMMAND_FLAGS(command_inventure_heat_test, HF_IN_SHUTDOWN,
                   "inventure_heat_test action=%c heater=%c");

void command_inventure_heat_sensor_query(uint32_t *args) {
    unsigned sensor = args[1];
    irqstatus_t flags = irq_save();
    uint32_t run=t0_heat.run, age=0xffffffffu;
    uint16_t raw=0, start=0, peak=0, heating_peak=0;
    if (sensor < 8) {
        uint32_t now=timer_read_time();
        uint8_t seen=cool_native_claimed ? cool_native_seen : cool_local_seen;
        uint32_t stamp=cool_native_claimed ? adc_time[sensor] : cool_local_time[sensor];
        if (seen & BIT(sensor)) age=(uint32_t)(now-stamp)/MS(1);
        raw=cool_native_claimed ? latest_adc[sensor] : cool_local_raw[sensor];
        start=t0_heat.baseline[sensor];peak=t0_heat.peak[sensor];
        heating_peak=t0_heat.heating_peak[sensor];
    }
    irq_restore(flags);
    // Echo the actual run so stale or interleaved host reads cannot mix trials.
    sendf("inventure_heat_sensor run=%u sensor=%c adc=%hu start_adc=%hu peak_adc=%hu heating_peak_adc=%hu age_ms=%u",
          run, sensor, raw, start, peak, heating_peak, age);
}
DECL_COMMAND_FLAGS(command_inventure_heat_sensor_query, HF_IN_SHUTDOWN,
                   "inventure_heat_sensor_query run=%u sensor=%c");
