#define TEST_Z_HOME_WIRE
#include "control_test_support.inc"

enum { OWNER = 501, TOKEN = 601, INTERVAL_US = 3000 };

static void z_home_adc(void)
{
    control_adc();
    inventure_adc_observe(ADC1, 10, 1427);
}

static void factory_lease(unsigned action, unsigned tool, unsigned mode, unsigned seq)
{
    // 1427 maps to 229°C, inside the temporary owner's heater band.
    uint32_t args[] = { action, tool, mode, 0, 100, 20160, 1500,
                        190, 245, OWNER, seq };
    command_inventure_factory_session(args);
}

static void reset(unsigned tool, uint32_t now)
{
    memset(&zh, 0, sizeof(zh));
    memset(&sl, 0, sizeof(sl));
    control_reset(now);
    z_home_adc();
    factory_lease(1, tool, IVT_STOP, 0);
    assert(fs.active && !factory_reply[3]);
    memset(&z_home_reply, 0, sizeof(z_home_reply));
}

static void probe(unsigned action, unsigned owner, unsigned token,
                  unsigned span, unsigned interval, unsigned repeats)
{
    uint32_t args[] = { action, owner, token, span, interval, repeats };
    command_inventure_z_home(args);
}

static void contact(unsigned closed)
{
    if (closed)
        GPIOC->IDR &= ~BIT(14);
    else
        GPIOC->IDR |= BIT(14);
}

static void step_timer(void)
{
    assert(zh.active && zh.timer_added && zh_timer.func == zh_event);
    sched_del_timer(&zh_timer);
    mock_now = zh_timer.waketime;
    command_inventure_heartbeat(NULL);
    z_home_adc();
    unsigned ret = zh_timer.func(&zh_timer);
    if (ret == SF_RESCHEDULE)
        sched_add_timer(&zh_timer);
    else
        assert(!zh.active && !zh.timer_added);
}

static void check_terminal(unsigned status)
{
    probe(0, OWNER, TOKEN, 0, 0, 0);
    assert(z_home_reply.owner == OWNER && z_home_reply.token == TOKEN);
    assert(!z_home_reply.active && !z_home_reply.reject && z_home_reply.status == status);
    assert(!zh.high && !zh.timer_added && !scheduled_count);
    assert(z_home_reply.net == (int32_t)z_home_reply.up_steps - (int32_t)z_home_reply.down_steps);
}

static void minimum_and_final_settle(unsigned fail_endpoint)
{
    const int32_t release[] = { 4, 3, 5, 4 };
    reset(0, 0xffff0000u);
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 2);
    assert(z_home_reply.active && z_home_reply.status == ZH_RUNNING && !z_home_reply.reject);
    unsigned dispatches = 0;
    while (zh.active) {
        switch (zh.phase) {
        case ZH_START_UP:
        case ZH_SEEK:
            contact(zh.net >= 6);
            break;
        case ZH_START_DOWN:
        case ZH_RELEASE:
            assert(zh.cycles < ARRAY_SIZE(release));
            contact(zh.net > release[zh.cycles]);
            break;
        case ZH_START_SETTLE:
        case ZH_SETTLE:
            // Settlement steps down independently of PC14 until the minimum.
            contact(zh.net != 3 || fail_endpoint);
            break;
        default:
            assert(!"unrecognized probe phase");
        }
        step_timer();
        assert(++dispatches < 120);
    }
    check_terminal(fail_endpoint ? ZH_GUARD : ZH_DONE);
    assert(z_home_reply.cycles == 4 && z_home_reply.misses == 2);
    assert(z_home_reply.best == 3 && z_home_reply.net == 3);
    assert(z_home_reply.up_steps == 12 && z_home_reply.down_steps == 9);
    if (fail_endpoint)
        assert(!fs.active && GPIOE->BSRR == (BIT(10) << 16));
    else
        assert(fs.active && GPIOE->BSRR != (BIT(10) << 16));
}

static void raw_alternation_zero_step_releases(void)
{
    reset(0, 0);
    // Protocol passes the required miss count. r0323 internally uses 100
    // and exits on strictly greater, so its normal count is 101 misses.
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 101);
    assert(z_home_reply.active && !z_home_reply.reject);
    // The up-side read sees active-low closure and the subsequent down-side
    // read sees high immediately, all while the platform stays at one point.
    // No host polls may be needed to sample either level or count repeats.
    unsigned dispatches = 0;
    while (zh.active) {
        contact(zh.phase == ZH_START_UP || zh.phase == ZH_SEEK);
        step_timer();
        // The real host refreshes its leases even without polling probe state.
        if (dispatches == 100 || dispatches == 200)
            factory_lease(2, 0, IVT_STOP, dispatches / 100);
        assert(++dispatches < 400);
    }
    check_terminal(ZH_DONE);
    assert(z_home_reply.best == 0 && z_home_reply.net == 0);
    assert(z_home_reply.cycles == 102 && z_home_reply.misses == 101);
    assert(!z_home_reply.up_steps && !z_home_reply.down_steps);
}

static void down_pulse_committed_before_contact_opens(void)
{
    reset(0, 0);
    contact(1);
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 1);
    assert(zh.active);
    step_timer(); // Initial upward sample sees closed PC14.
    assert(zh.phase == ZH_START_DOWN && !zh.up_steps);
    step_timer(); // The downward sample is still closed and commits a pulse.
    assert(zh.phase == ZH_RELEASE && !zh.high && !zh.down_steps);
    const uint32_t wake = zh_timer.waketime;
    assert(wake - mock_now == timer_from_us(INTERVAL_US));
    contact(0);
    probe(0, OWNER, TOKEN, 0, 0, 0);
    assert(!z_home_reply.cycles && zh_timer.waketime == wake);
    step_timer();
    assert(zh.down_steps == 1 && zh.high && zh.net == -1);
    assert(zh_timer.waketime - mock_now == timer_from_us(ZH_HIGH_US));
    step_timer();
    assert(zh.cycles == 1 && zh.best == -1 && !zh.high);
    probe(2, OWNER, TOKEN, 0, 0, 0);
    check_terminal(ZH_STOP);
}

static void travel_failures(void)
{
    reset(0, 0);
    probe(1, OWNER, TOKEN, 5, INTERVAL_US, 2);
    assert(zh.active);
    unsigned dispatches = 0;
    while (zh.active) {
        // The upper bound is checked before its contact read.
        contact(zh.net == 5);
        step_timer();
        assert(++dispatches < 36);
    }
    check_terminal(ZH_NO_CONTACT);
    assert(!z_home_reply.cycles && z_home_reply.net == 5);
    assert(z_home_reply.up_steps == 5 && !z_home_reply.down_steps);
    assert(!fs.active && GPIOE->BSRR == (BIT(10) << 16));

    reset(0, 0);
    probe(1, OWNER, TOKEN, 5, INTERVAL_US, 2);
    assert(zh.active);
    dispatches = 0;
    while (zh.active) {
        contact(1);
        step_timer();
        assert(++dispatches < 36);
    }
    check_terminal(ZH_NO_RELEASE);
    assert(!z_home_reply.cycles && !z_home_reply.up_steps);
    // The MCU travel envelope prevents a pulse past its lower limit.
    assert(z_home_reply.down_steps == 5 && z_home_reply.net == -5);
    assert(!fs.active && GPIOE->BSRR == (BIT(10) << 16));
}

static void travel_limit_can_keep_a_prior_measurement(void)
{
    reset(0, 0);
    contact(1);
    probe(1, OWNER, TOKEN, 5, INTERVAL_US, 2);
    assert(zh.active);
    unsigned dispatches = 0;
    while (zh.active) {
        // First turn closes at zero and opens at -5, exactly on the allowed
        // lower limit. Later up travel reaches +5 without closing PC14.
        switch (zh.phase) {
        case ZH_START_UP:
        case ZH_SEEK:
            contact(!zh.cycles);
            break;
        case ZH_START_DOWN:
        case ZH_RELEASE:
            contact(zh.net > -5);
            break;
        case ZH_START_SETTLE:
        case ZH_SETTLE:
            contact(0);
            break;
        default:
            assert(!"unrecognized probe phase");
        }
        step_timer();
        assert(++dispatches < 70);
    }
    check_terminal(ZH_DONE);
    assert(z_home_reply.cycles == 1 && !z_home_reply.misses);
    assert(z_home_reply.best == -5 && z_home_reply.net == -5);
    assert(z_home_reply.up_steps == 10 && z_home_reply.down_steps == 15);
}

static void wall_clock_limit_survives_timer_wrap(void)
{
    reset(0, 0xffff0000u);
    probe(1, OWNER, TOKEN, 5, INTERVAL_US, 2);
    assert(zh.active);
    for (unsigned seq = 1; seq <= 600; seq++) {
        mock_now += MS(1000);
        command_inventure_heartbeat(NULL);
        z_home_adc();
        factory_lease(2, 0, IVT_STOP, seq);
        control_guard(mock_now);
        assert(zh.active == (seq < 600));
    }
    check_terminal(ZH_TIME);
    assert(!z_home_reply.up_steps && !z_home_reply.down_steps);
    assert(!fs.active && GPIOE->BSRR == (BIT(10) << 16));
}

static void guard_or_stop(unsigned fault_index)
{
    reset(0, 0);
    output(1, 6, 32, 1500, 250, 67);
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 2);
    assert(zh.active && co[6].duty);
    unsigned dispatches = 0;
    while (!zh.high) {
        step_timer();
        assert(++dispatches < 10);
    }
    const unsigned up = zh.up_steps, down = zh.down_steps;
    if (fault_index == 0)
        GPIOE->IDR |= BIT(12);
    else if (fault_index == 1)
        GPIOC->IDR |= BIT(13);
    else if (fault_index == 2) {
        mock_now = fs.deadline;
        command_inventure_heartbeat(NULL);
        z_home_adc();
    } else if (fault_index == 3)
        heartbeat_live = 0;
    if (fault_index >= 4)
        control_command(2, 0);
    else
        control_guard(mock_now);
    check_terminal(fault_index >= 4 ? ZH_STOP : ZH_GUARD);
    assert(z_home_reply.up_steps == up && z_home_reply.down_steps == down);
    assert(!co[6].duty && !fs.active && GPIOE->BSRR == (BIT(10) << 16));
}

static void downward_commit_does_not_override_guards(void)
{
    reset(0, 0);
    contact(1);
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 1);
    step_timer();
    step_timer();
    assert(zh.phase == ZH_RELEASE && !zh.high && !zh.down_steps);
    GPIOC->IDR |= BIT(13);
    step_timer();
    check_terminal(ZH_GUARD);
    assert(!z_home_reply.down_steps && !fs.active);
}

static void ownership_and_exclusion(void)
{
    reset(1, 0);
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 2);
    assert(!zh.active && z_home_reply.reject);
    reset(0, 0);
    motion(1, 1, 10, 0, 0, 0, 400, 10, 1000, 0, 0, 0, 631);
    assert(cm.active);
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 2);
    assert(!zh.active && z_home_reply.reject && cm.active);
    while (cm.active)
        motion_tick();
    z_home_adc();

    probe(1, OWNER + 1, TOKEN, 20, INTERVAL_US, 2);
    assert(!zh.active && z_home_reply.reject);
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 2);
    assert(zh.active && !z_home_reply.reject);
    const uint32_t wake = zh_timer.waketime;
    const unsigned timers = scheduled_count;
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 2);
    assert(!z_home_reply.reject && zh.active && zh_timer.waketime == wake && scheduled_count == timers);
    probe(1, OWNER, TOKEN, 20, INTERVAL_US + 1, 2);
    assert(z_home_reply.reject && zh.active);
    probe(2, OWNER + 1, TOKEN, 0, 0, 0);
    assert(z_home_reply.reject && zh.active);
    probe(2, OWNER, TOKEN + 1, 0, 0, 0);
    assert(z_home_reply.reject && zh.active);
    motion(1, 1, 10, 0, 0, 0, 400, 10, 1000, 0, 0, 0, 632);
    assert(!cm.active && zh.active);
    uint32_t smooth[] = { 1, 1536, 1536, 5000, 1500, 205, 240, 901, 0, 0, 0, 0, 1 };
    command_inventure_load_stream(smooth);
    assert(!sl.active && zh.active);
    while (!zh.high)
        step_timer();
    assert(GPIOB->BSRR == BIT(8));
    probe(2, OWNER, TOKEN, 0, 0, 0);
    check_terminal(ZH_STOP);
    assert(GPIOB->BSRR == (BIT(8) << 16));
    probe(1, OWNER, TOKEN, 20, INTERVAL_US, 2);
    assert(!zh.active && !scheduled_count);
}

int main(void)
{
    minimum_and_final_settle(0);
    minimum_and_final_settle(1);
    raw_alternation_zero_step_releases();
    down_pulse_committed_before_contact_opens();
    travel_failures();
    travel_limit_can_keep_a_prior_measurement();
    wall_clock_limit_survives_timer_wrap();
    for (unsigned i = 0; i < 5; i++)
        guard_or_stop(i);
    downward_commit_does_not_override_guards();
    ownership_and_exclusion();
    puts("PASS OEM Z reference: raw PC14 loop, zero-step releases, minimum settle, travel bounds, independent guards and exclusive motor ownership");
    return 0;
}
