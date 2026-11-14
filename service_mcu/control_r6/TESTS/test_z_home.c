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

static void edge(unsigned action,unsigned owner,unsigned token,unsigned operation,
                 unsigned steps,unsigned interval)
{
    uint32_t args[]={action,owner,token,operation,steps,interval};
    command_inventure_z_edge(args);
}

static void edge_terminal(unsigned token,unsigned operation,unsigned status,unsigned sent,
                          unsigned raw)
{
    edge(0,OWNER,token,0,0,0);
    assert(!z_edge_reply.reject && !z_edge_reply.active);
    assert(z_edge_reply.owner==OWNER && z_edge_reply.token==token);
    assert(z_edge_reply.operation==operation && z_edge_reply.status==status);
    assert(z_edge_reply.sent==sent && z_edge_reply.end_raw==raw);
    assert(!zh.high && !zh.timer_added && !scheduled_count);
}

static void host_edge_bounce_and_reversal(void)
{
    const unsigned interval=4000;
    reset(0,0xffff0000u);
    edge(1,OWNER,TOKEN,ZH_EDGE_CLOSE,ZH_EDGE_MAX_STEPS,interval);
    assert(!z_edge_reply.reject && z_edge_reply.active);
    step_timer(); // Set DIR up, sample open and wait one interval.
    assert(zh.phase==ZH_SEEK && zh_timer.waketime-mock_now==timer_from_us(interval));
    step_timer(); // PC14 stays open and the first rising STEP is issued.
    assert(zh.high && zh.up_steps==1);
    step_timer();
    assert(!zh.high && zh_timer.waketime-mock_now==timer_from_us(interval-ZH_HIGH_US));
    contact(1);step_timer(); // A late closure must stop before another rising STEP.
    contact(0); // Contact bounces open after the local stop and before the host reads.
    edge_terminal(TOKEN,ZH_EDGE_CLOSE,ZH_DONE,1,0);
    assert(fs.active && GPIOE->BSRR!=(BIT(10)<<16));

    edge(1,OWNER,TOKEN+1,ZH_EDGE_OPEN,1,interval);
    step_timer(); // Fresh open sample as DIR goes down returns zero-step release.
    contact(1); // Reclosure does not change the result.
    edge_terminal(TOKEN+1,ZH_EDGE_OPEN,ZH_DONE,0,1);
    assert(fs.active && GPIOE->BSRR!=(BIT(10)<<16));

    edge(1,OWNER,TOKEN+2,ZH_EDGE_OPEN,1,interval);
    step_timer(); // Closed sample commits a down pulse before delay.
    assert(zh.phase==ZH_RELEASE && !zh.high && !zh.down_steps);
    assert(zh_timer.waketime-mock_now==timer_from_us(interval));
    contact(0);
    step_timer(); // Even with PC14 open now, execute the committed retreat.
    assert(zh.high && zh.down_steps==1);
    step_timer();
    contact(1);
    edge_terminal(TOKEN+2,ZH_EDGE_OPEN,ZH_DONE,1,1);
    assert(fs.active && GPIOE->BSRR!=(BIT(10)<<16));

    edge(1,OWNER,TOKEN+3,ZH_EDGE_SETTLE,2,interval);
    while(zh.active) {
        contact(zh.down_steps<2);
        step_timer();
    }
    edge_terminal(TOKEN+3,ZH_EDGE_SETTLE,ZH_DONE,2,1);
}

static void host_edge_bounds_guards_and_replay(void)
{
    reset(0,0);
    edge(1,OWNER,TOKEN,ZH_EDGE_CLOSE,ZH_EDGE_MAX_STEPS+1,INTERVAL_US);
    assert(z_edge_reply.reject==1 && !zh.active);
    edge(1,OWNER,TOKEN,ZH_EDGE_CLOSE,1,ZH_MIN_INTERVAL_US-1);
    assert(z_edge_reply.reject==1 && !zh.active);
    edge(1,OWNER,TOKEN,ZH_EDGE_OPEN,0,INTERVAL_US);
    assert(z_edge_reply.reject==1 && !zh.active);
    edge(1,OWNER+1,TOKEN,ZH_EDGE_OPEN,1,INTERVAL_US);
    assert(z_edge_reply.reject==4 && !zh.active);
    edge(1,OWNER,TOKEN,ZH_EDGE_CLOSE,1,INTERVAL_US);
    assert(!z_edge_reply.reject && zh.active);
    const uint32_t wake=zh_timer.waketime;
    edge(1,OWNER,TOKEN,ZH_EDGE_CLOSE,1,INTERVAL_US);
    assert(!z_edge_reply.reject && zh.active && zh_timer.waketime==wake);
    edge(1,OWNER,TOKEN+1,ZH_EDGE_CLOSE,1,INTERVAL_US);
    assert(z_edge_reply.reject==2 && zh.active && zh_timer.waketime==wake);
    edge(2,OWNER,TOKEN+1,0,0,0);
    assert(z_edge_reply.reject==4 && zh.active);
    while(zh.active)step_timer();
    edge_terminal(TOKEN,ZH_EDGE_CLOSE,ZH_NO_CONTACT,1,1);
    assert(fs.active && GPIOE->BSRR!=(BIT(10)<<16));

    contact(1);
    edge(1,OWNER,TOKEN+1,ZH_EDGE_OPEN,1,INTERVAL_US);
    while(zh.active)step_timer();
    edge_terminal(TOKEN+1,ZH_EDGE_OPEN,ZH_NO_CONTACT,1,0);
    assert(fs.active && GPIOE->BSRR!=(BIT(10)<<16));
    edge(1,OWNER,TOKEN,ZH_EDGE_CLOSE,1,INTERVAL_US);
    assert(z_edge_reply.reject==4 && !zh.active);

    contact(0);
    edge(1,OWNER,TOKEN+2,ZH_EDGE_SETTLE,0,INTERVAL_US);
    step_timer();
    edge_terminal(TOKEN+2,ZH_EDGE_SETTLE,ZH_DONE,0,1);

    output(1,6,32,1500,250,67);
    contact(1);
    edge(1,OWNER,TOKEN+3,ZH_EDGE_SETTLE,0,INTERVAL_US);
    step_timer();
    edge_terminal(TOKEN+3,ZH_EDGE_SETTLE,ZH_GUARD,0,0);
    assert(!fs.active && !co[6].duty && GPIOE->BSRR==(BIT(10)<<16));

    reset(0,0);
    output(1,6,32,1500,250,67);
    contact(1);
    edge(1,OWNER,TOKEN,ZH_EDGE_OPEN,2,INTERVAL_US);
    step_timer(); // A down pulse is committed but physical guards still apply.
    GPIOC->IDR|=BIT(13);
    step_timer();
    edge_terminal(TOKEN,ZH_EDGE_OPEN,ZH_GUARD,0,0);
    assert(!fs.active && !co[6].duty && GPIOE->BSRR==(BIT(10)<<16));

    reset(0,0);
    edge(1,OWNER,TOKEN,ZH_EDGE_CLOSE,2,INTERVAL_US);
    step_timer();step_timer();assert(zh.high);
    edge(2,OWNER,TOKEN,0,0,0);
    edge_terminal(TOKEN,ZH_EDGE_CLOSE,ZH_STOP,1,1);
    assert(GPIOB->BSRR==(BIT(8)<<16));
    edge(1,OWNER,TOKEN,ZH_EDGE_CLOSE,2,INTERVAL_US);
    assert(!z_edge_reply.reject && !zh.active && z_edge_reply.status==ZH_STOP);
}

static void native_tray_guard(unsigned block_pulse)
{
    control_reset(0);
    uint32_t args[]={1,OWNER};
    command_inventure_native_z_guard(args);
    assert(native_z_guard_reply[2]==3 && !native_z_guard_active);
    control_command(1,CT_NATIVE);
    assert(inventure_is_armed());
    GPIOC->IDR|=BIT(13);
    assert(inventure_filter_gpio(GPIOB,BIT(8),1)==1); // unrelated native operation
    command_inventure_native_z_guard(args);
    assert(native_z_guard_reply[2]==3 && !native_z_guard_active);
    GPIOC->IDR&=~BIT(13);
    command_inventure_native_z_guard(args);
    assert(native_z_guard_reply[0]==OWNER && native_z_guard_reply[1]==1
        && !native_z_guard_reply[2] && native_z_guard_active);
    assert(inventure_filter_gpio(GPIOB,BIT(8),1)==1);
    args[1]=OWNER+1;
    command_inventure_native_z_guard(args);
    assert(native_z_guard_reply[2]==2 && native_z_guard_active);
    args[0]=2;
    command_inventure_native_z_guard(args);
    assert(native_z_guard_reply[2]==4 && native_z_guard_active);
    args[1]=OWNER;
    command_inventure_native_z_guard(args);
    assert(!native_z_guard_reply[2] && !native_z_guard_active);
    args[0]=1;
    command_inventure_native_z_guard(args);
    assert(!native_z_guard_reply[2] && !native_z_guard_active);
    args[1]=OWNER+1;
    command_inventure_native_z_guard(args);
    assert(native_z_guard_active);
    GPIOC->IDR|=BIT(13);
    if(block_pulse) {
        assert(inventure_filter_gpio(GPIOB,BIT(8),1)==0 && native_z_guard_lost);
        GPIOC->IDR&=~BIT(13);
        assert(inventure_filter_gpio(GPIOB,BIT(8),1)==0);
        assert(inventure_filter_gpio(GPIOB,BIT(8),0)==0);
    }
    expect_failure=1;
    if(!setjmp(failure)){control_guard(mock_now);assert(!"tray guard failed to trip");}
    expect_failure=0;
    assert(fault==7 && !native_z_guard_active && !native_z_guard_lost);
    assert(!inventure_is_armed() && control_latched && !control_heater_duty[0]);
    assert(inventure_filter_gpio(GPIOB,BIT(8),1)==0);
}

static void native_tray_guard_stop(void)
{
    control_reset(0);
    control_command(1,CT_NATIVE);
    uint32_t args[]={1,OWNER+2};
    command_inventure_native_z_guard(args);
    assert(native_z_guard_active);
    control_command(2,0);
    assert(!native_z_guard_active && !native_z_guard_lost && !inventure_is_armed());
    args[0]=0;
    command_inventure_native_z_guard(args);
    assert(native_z_guard_reply[0]==OWNER+2 && !native_z_guard_reply[1] && !native_z_guard_reply[2]);
}

static void refine(unsigned action,unsigned owner,unsigned token,unsigned span,
                   unsigned interval,unsigned lower,unsigned upper)
{
    uint32_t args[]={action,owner,token,span,interval,lower,upper};
    command_inventure_native_z_refine(args);
}

static void native_refine_reset(unsigned enable_guard)
{
    memset(&zh,0,sizeof(zh));
    native_z_guard_owner=0;
    native_z_guard_clear();
    control_reset(0xffff0000u);
    control_command(1,CT_NATIVE);
    assert(inventure_is_armed());
    z_home_adc();
    GPIOE->ODR=BIT(7)|BIT(10);
    if(enable_guard) {
        uint32_t args[]={1,OWNER};
        command_inventure_native_z_guard(args);
        assert(!native_z_guard_reply[2] && native_z_guard_active);
    }
}

static void native_refine_run(void)
{
    native_refine_reset(0);
    refine(1,OWNER,TOKEN,10,4000,200,240);
    assert(z_home_reply.reject==4 && !zh.active);
    uint32_t args[]={1,OWNER};
    command_inventure_native_z_guard(args);
    GPIOE->ODR&=~BIT(10);
    refine(1,OWNER,TOKEN,10,4000,200,240);
    assert(z_home_reply.reject==3 && !zh.active);
    GPIOE->ODR|=BIT(10);
    GPIOB->ODR|=BIT(8);
    refine(1,OWNER,TOKEN,10,4000,200,240);
    assert(z_home_reply.reject==3 && !zh.active);
    GPIOB->ODR&=~BIT(8);
    contact(1);
    refine(1,OWNER,TOKEN,10,4000,200,240);
    assert(z_home_reply.reject==3 && !zh.active);
    contact(0);
    refine(1,OWNER,TOKEN,10,4000,235,240);
    assert(z_home_reply.reject==3 && !zh.active);
    refine(1,OWNER,TOKEN,10,4000,200,240);
    assert(!z_home_reply.reject && zh.active && zh.native && native_z_refine_held);
    uint32_t wake=zh_timer.waketime;
    refine(1,OWNER,TOKEN,10,4000,200,240);
    assert(!z_home_reply.reject && zh.active && zh_timer.waketime==wake);
    refine(1,OWNER,TOKEN,10,4000,199,240);
    assert(z_home_reply.reject==1 && zh.active && zh_timer.waketime==wake);
    args[0]=2;command_inventure_native_z_guard(args);
    assert(native_z_guard_reply[2]==2 && native_z_refine_held);

    unsigned dispatches=0;
    while(zh.active) {
        switch(zh.phase) {
        case ZH_START_UP: case ZH_SEEK:
            contact(zh.net>=4);
            break;
        case ZH_START_DOWN: case ZH_RELEASE:
            contact(zh.net>(zh.cycles?1:0));
            break;
        case ZH_START_SETTLE: case ZH_SETTLE:
            contact(zh.net!=0);
            break;
        default:assert(!"unexpected native refinement phase");
        }
        step_timer();
        assert(++dispatches<2000);
    }
    refine(0,OWNER,TOKEN,0,0,0,0);
    assert(!z_home_reply.reject && z_home_reply.status==ZH_DONE && !zh.active);
    assert(z_home_reply.cycles==102 && z_home_reply.misses==101);
    assert(z_home_reply.net==0 && z_home_reply.best==0);
    assert(z_home_reply.up_steps>0 && z_home_reply.down_steps==z_home_reply.up_steps);
    assert(inventure_is_armed() && native_z_refine_held && native_z_guard_active);
    assert(GPIOE->BSRR==BIT(7)); // saved native DIR restored while queue remains fenced
    command_inventure_native_z_guard(args);
    assert(!native_z_guard_reply[2] && !native_z_guard_active && !native_z_refine_held);
    assert(inventure_filter_gpio(GPIOB,BIT(8),1)==1 && inventure_is_armed());
    refine(1,OWNER,TOKEN,10,4000,200,240);
    assert(!z_home_reply.reject && z_home_reply.status==ZH_DONE && !zh.active);
}

static void native_refine_faults(void)
{
    for(unsigned kind=0;kind<8;kind++) {
        native_refine_reset(1);
        refine(1,OWNER,TOKEN,10,4000,200,240);
        assert(zh.active && native_z_refine_held);
        if(kind==0)assert(inventure_filter_gpio(GPIOB,BIT(8),1)==0);
        if(kind==1)assert(inventure_filter_gpio(GPIOE,BIT(7),0)==1); // hold prior DIR
        if(kind==2)assert(inventure_filter_gpio(GPIOE,BIT(10),0)==0); // disable is honored
        if(kind==3)GPIOC->IDR|=BIT(13);
        if(kind==4)GPIOE->IDR|=BIT(12);
        if(kind==5)inventure_adc_observe(ADC1,10,1024);
        if(kind==6)heartbeat_live=0;
        if(kind==7) {
            zh_end(ZH_DONE); // simulate terminal hold awaiting the host rebase
            mock_now+=MS(ZH_HANDOFF_MS);
            command_inventure_heartbeat(NULL);
            z_home_adc();
        }
        expect_failure=1;
        if(!setjmp(failure)){control_guard(mock_now);assert(!"native probe guard failed");}
        expect_failure=0;
        assert(fault==8 && !inventure_is_armed() && !native_z_refine_held && !zh.active);
        assert(zh.status==ZH_GUARD && !zh.up_steps && !zh.down_steps);
        assert(!control_heater_duty[0] && !zh.high && !zh.timer_added);
    }
    native_refine_reset(1);
    refine(1,OWNER,TOKEN,10,4000,200,240);
    refine(2,OWNER,TOKEN+1,0,0,0,0);
    assert(z_home_reply.reject==4 && zh.active);
    refine(2,OWNER,TOKEN,0,0,0,0);
    assert(!z_home_reply.reject && z_home_reply.status==ZH_STOP);
    assert(!inventure_is_armed() && control_latched && !native_z_refine_held && !zh.active);
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
    host_edge_bounce_and_reversal();
    host_edge_bounds_guards_and_replay();
    puts("PASS host-owned Z-edge: MCU cadence, local contact/release latch, bounded settle, guards and replay");
    native_tray_guard(1);
    native_tray_guard(0);
    native_tray_guard_stop();
    puts("PASS native Z tray guard: token ownership, PB8 interlock, latched PC13 fault and global STOP");
    native_refine_run();
    native_refine_faults();
    puts("PASS native Z refine: exact 101 misses, saved DIR, exclusive queue gate, handoff and fault stops");
    return 0;
}
