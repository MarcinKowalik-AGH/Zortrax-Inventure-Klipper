// SPDX-License-Identifier: GPL-3.0-or-later
#include "test_factory_session.c"

uint8_t
inventure_native_stepper_idle(void)
{
    return !cm.active;
}

static void
start_feed(uint32_t owner, uint32_t run, unsigned condition, unsigned timeout)
{
    uint32_t args[] = {owner, run, condition, timeout};
    command_inventure_factory_feed_start(args);
}

static void
keep_feed(uint32_t owner, uint32_t run)
{
    uint32_t args[] = {owner, run};
    command_inventure_factory_feed_keepalive(args);
}

static void
begin_factory(unsigned tool, uint32_t clock)
{
    memset(&factory_feeder, 0, sizeof(factory_feeder));
    factory_reset(clock, 1);
    factory(1, tool, IVT_STOP, 0, 1000, 1500, 501, 0);
    assert(fs.active && fs.tool == tool);
}

static void
check_feed_phases(unsigned tool)
{
    begin_factory(tool, 0xffff0000);
    factory_input(tool, 1, 0);
    start_feed(501, 1, FEED_TO_A, 5000);
    assert(factory_feeder.state == FEED_REACHED && !transport[tool].starts && !TIM9->CCR2);
    factory(2, tool, IVT_STOP, 0, 1000, 1500, 501, 1);

    factory_input(tool, 0, 0);
    start_feed(501, 2, FEED_TO_A, 5000);
    assert(factory_feeder.state == FEED_RUNNING && TIM9->CCR2);
    assert(transport[tool].running && !transport[1-tool].running && transport[tool].direction);
    uint32_t deadline = factory_feeder.deadline;
    uint32_t starts = transport[tool].starts;
    uint32_t lease = fs.deadline;
    factory_advance(400, 1);
    keep_feed(500, 2);
    assert(fs.deadline == lease);
    keep_feed(501, 1);
    assert(fs.deadline == lease);
    keep_feed(501, 2);
    assert(fs.deadline == mock_now + MS(1500));
    assert(factory_feeder.deadline == deadline && transport[tool].starts == starts);
    start_feed(501, 2, FEED_TO_A, 5000);
    assert(factory_feeder.deadline == deadline && transport[tool].starts == starts);
    factory_input(tool, 1, 0);
    factory_advance(10, 1);
    assert(factory_feeder.state == FEED_REACHED && !transport[tool].running && !TIM9->CCR2);
    factory(2, tool, IVT_FORWARD, 0, 1000, 1500, 501, 2);
    start_feed(501, 2, FEED_TO_A, 5000);
    assert(!TIM9->CCR2 && transport[tool].starts == starts);
    factory(2, tool, IVT_STOP, 0, 1000, 1500, 501, 3);

    start_feed(501, 3, FEED_TO_RELEASE, 10000);
    assert(TIM9->CCR2 && !transport[tool].direction);
    factory_input(tool, 1, 1);
    factory_advance(10, 1);
    assert(factory_feeder.state == FEED_REACHED && factory_feeder.a && factory_feeder.b && !TIM9->CCR2);
    factory(2, tool, IVT_GATED, 0, 1000, 1500, 501, 4);
    start_feed(501, 4, FEED_TO_EMPTY, 6000);
    assert(factory_feeder.state == FEED_RUNNING && TIM9->CCR2);

    motion(1, tool ? 0 : 8, 0, 0, 0, 96, 960, 10, 1000, 0, 0, 0, 601);
    assert(cm.active && cm.planned[3] == 96 && cm.rate == 960);
    factory_motion_tick();
    factory_input(tool, 0, 1);
    control_guard(mock_now);
    assert(factory_feeder.state == FEED_REACHED && !TIM9->CCR2 && cm.active);
    while (cm.active)
        factory_motion_tick();
    assert(cm.sent[3] == 96 && !cm.sent[2]);
    factory(2, tool, IVT_STOP, 0, 1000, 1500, 501, 5);
    motion(1, 0, 0, 0, 80, 0, 802, 10, 1000, 0, 0, 0, 602);
    while (cm.active)
        motion_tick();
    assert(cm.sent[2] == 80 && !cm.sent[3]);
}

static void
check_feed_failures(void)
{
    begin_factory(0, 0xffff0000);
    factory_input(0, 0, 0);
    start_feed(999, 1, FEED_TO_A, 200);
    assert(!TIM9->CCR2);
    start_feed(501, 1, FEED_TO_A, 200);
    factory_advance(190, 1);
    assert(TIM9->CCR2);
    factory_input(0, 1, 0);
    factory_advance(10, 1);
    assert(factory_feeder.state == FEED_EXPIRED && !TIM9->CCR2);
    factory(2, 0, IVT_STOP, 0, 1000, 1500, 501, 1);
    start_feed(501, 2, FEED_TO_EMPTY, 6000);
    assert(TIM9->CCR2);
    factory_advance(1500, 1);
    assert(!fs.active && fs.reason == 3 && factory_feeder.state == FEED_CANCELLED && !TIM9->CCR2);
    start_feed(501, 2, FEED_TO_EMPTY, 6000);
    assert(!TIM9->CCR2 && factory_feeder.state == FEED_CANCELLED);

    begin_factory(1, 0);
    factory_input(1, 0, 0);
    start_feed(501, 1, FEED_TO_A, 5000);
    assert(TIM9->CCR2);
    GPIOD->IDR |= BIT(11);
    factory_advance(10, 1);
    assert(!fs.active && !TIM9->CCR2 && factory_feeder.state == FEED_CANCELLED);
    begin_factory(0, 0);
    factory_input(0, 0, 0);
    start_feed(501, 1, FEED_TO_A, 5000);
    assert(TIM9->CCR2);
    factory(3, 0, IVT_STOP, 0, 1000, 1500, 501, 0);
    assert(!TIM9->CCR2 && !fs.active);
    factory_advance(10, 1);
    assert(factory_feeder.state == FEED_CANCELLED);
}

int
main(void)
{
    factory_regression_main();
    check_feed_phases(0);
    check_feed_phases(1);
    check_feed_failures();
    puts("PASS R31 cassette windows on T0/T1, sensor endings, replay, deadline, lease, abort, independent E/Z");
    return 0;
}
