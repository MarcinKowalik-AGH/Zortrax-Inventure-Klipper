// SPDX-License-Identifier: GPL-3.0-or-later
#line 42000 "inventure_factory_feeder.inc"
DECL_CONSTANT("INVENTURE_FACTORY_FEED_WINDOW_ABI", 1);

enum { FEED_TO_A = 1, FEED_TO_RELEASE, FEED_TO_EMPTY };
enum { FEED_RUNNING = 1, FEED_REACHED, FEED_EXPIRED, FEED_CANCELLED, FEED_REJECTED };

struct factory_feed_window {
    uint32_t owner, run, deadline;
    uint16_t timeout_ms;
    uint8_t condition, tool, state, owns_mode, a, b;
};

static struct factory_feed_window factory_feeder;
static struct task_wake factory_feed_wake;

static uint8_t
factory_feed_owns_mode(void)
{
    return factory_feeder.owns_mode && factory_feeder.owner == fs.token;
}

static uint8_t
factory_feed_reached(struct factory_feed_window *window)
{
    window->a = transport_a(window->tool);
    window->b = transport_b(window->tool);
    if (window->condition == FEED_TO_A)
        return window->a;
    return !window->a || (window->condition == FEED_TO_RELEASE && window->b);
}

static void
factory_feed_release(void)
{
    factory_feeder.owns_mode = 0;
    if (factory_feeder.state == FEED_RUNNING) {
        factory_feeder.state = FEED_CANCELLED;
        sched_wake_task(&factory_feed_wake);
    }
}

static void
factory_feed_tick(uint32_t now)
{
    struct factory_feed_window *window = &factory_feeder;
    if (!window->owns_mode)
        return;
    if (!fs.active || !factory_feed_owns_mode()) {
        // The factory stop/guard already turned the outputs off.
        factory_feed_release();
        return;
    }
    if (window->state != FEED_RUNNING)
        return;
    uint8_t reached = factory_feed_reached(window);
    uint8_t expired = iv9_due(now, window->deadline);
    if (!reached && !expired)
        return;
    transport_stop_channel(window->tool);
    window->state = reached && !(expired && window->condition == FEED_TO_A) ? FEED_REACHED : FEED_EXPIRED;
    sched_wake_task(&factory_feed_wake);
}

static void fs_set_mode(unsigned mode, unsigned monitor, uint32_t now);
static void fs_guard(uint32_t now);

static void
factory_feed_send(const struct factory_feed_window *window)
{
    sendf("inventure_factory_feed_state owner=%u run=%u state=%c a=%c b=%c",
          window->owner, window->run, window->state, window->a, window->b);
}

void
factory_feed_task(void)
{
    if (!sched_check_wake(&factory_feed_wake))
        return;
    irqstatus_t flags = irq_save();
    struct factory_feed_window report = factory_feeder;
    irq_restore(flags);
    factory_feed_send(&report);
}
DECL_TASK(factory_feed_task);

void
command_inventure_factory_feed_start(uint32_t *args)
{
    uint32_t owner = args[0], run = args[1], condition = args[2], timeout = args[3];
    struct factory_feed_window report = {.owner = owner, .run = run, .state = FEED_REJECTED};
    irqstatus_t flags = irq_save();
    uint32_t now = timer_read_time();
    fs_guard(now);
    factory_feed_tick(now);
    uint8_t same_owner = owner && owner == factory_feeder.owner;
    if (same_owner && run == factory_feeder.run && condition == factory_feeder.condition
        && timeout == factory_feeder.timeout_ms) {
        // A lost report can be requested again without restarting a motor.
        report = factory_feeder;
    } else if (fs.active && owner == fs.token && run && (!same_owner || run > factory_feeder.run)
               && condition >= FEED_TO_A && condition <= FEED_TO_EMPTY
               && timeout >= 50 && timeout <= 30000 && !factory_feed_owns_mode()
               && !cm.active && !fs.dual_nozzle_z
               && fs_nozzle_temperature_ok(fs.tool, now, 1)) {
        struct factory_feed_window next = {
            .owner = owner, .run = run, .deadline = now + MS(timeout), .timeout_ms = timeout,
            .condition = condition, .tool = fs.tool, .state = FEED_RUNNING, .owns_mode = 1,
        };
        uint8_t mode = condition == FEED_TO_A ? IVT_FORWARD : IVT_REVERSE;
        if (factory_feed_reached(&next)) {
            transport_stop_channel(next.tool);
            fs.mode = mode;
            fs.monitor = 0;
            next.state = FEED_REACHED;
        } else {
            fs_set_mode(mode, 0, now);
        }
        factory_feeder = report = next;
    }
    irq_restore(flags);
    factory_feed_send(&report);
}
DECL_COMMAND(command_inventure_factory_feed_start,
             "inventure_factory_feed_start owner=%u run=%u condition=%c timeout_ms=%hu");

void
command_inventure_factory_feed_keepalive(uint32_t *args)
{
    irqstatus_t flags = irq_save();
    uint32_t now = timer_read_time();
    if (fs.active && factory_feed_owns_mode() && args[0] == factory_feeder.owner
        && args[1] == factory_feeder.run && !iv9_due(now, fs.deadline))
        fs.deadline = now + MS(fs.lease_ms);
    irq_restore(flags);
}
DECL_COMMAND(command_inventure_factory_feed_keepalive,
             "inventure_factory_feed_keepalive owner=%u run=%u");
