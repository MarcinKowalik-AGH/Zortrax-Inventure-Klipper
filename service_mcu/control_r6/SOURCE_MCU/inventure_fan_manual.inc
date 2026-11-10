// SPDX-License-Identifier: GPL-3.0-or-later
// Included by inventure_board.c. MCU owns all timeouts and fan outputs.
DECL_CONSTANT("INVENTURE_FAN_MANUAL_ABI", 1);
DECL_CONSTANT_STR("INVENTURE_FAN_MANUAL_RELEASE", "RC8-THERMAL-TEST-R2-20S");
#define FAN_ON_SETTLE MS(1000)
#define FAN_OFF_SETTLE MS(2000)
#define FAN_WINDOW MS(1000)
enum { FM_IDLE, FM_ON, FM_OFF, FM_PASS, FM_FAIL, FM_ABORT, FM_TIMEOUT };
enum { FM_STATUS, FM_START, FM_STOP, FM_CONFIRM_PASS, FM_CONFIRM_FAIL, FM_CANCEL };
enum { FM_OK, FM_INVALID, FM_BUSY, FM_INTERLOCK, FM_COOLING, FM_SEQUENCE, FM_WAIT, FM_TACH };
#define FM_NO_EDGES 65535u

static struct {
    uint32_t run, since, mark;
    uint16_t on_edges, off_edges;
    uint8_t channel, phase, settled, reason, passed, failed;
} fan_manual = { .channel = 255, .on_edges = FM_NO_EDGES, .off_edges = FM_NO_EDGES };

static uint8_t fan_manual_active(void)
{
    return fan_manual.phase == FM_ON || fan_manual.phase == FM_OFF;
}

static uint8_t fan_manual_allowed(uint32_t now)
{
    return sample_started && !fault && heartbeat_live
        && !iv9_due(now, heartbeat_deadline) ? FM_OK : FM_INTERLOCK;
}

// Runs under the IRQ lock: the ADC and pulse data share this clock snapshot.
static void fan_manual_service(uint32_t now)
{
    if (!fan_manual_active())
        return;

    uint8_t channel = fan_manual.channel;
    uint8_t blocked = fan_manual_allowed(now);
    if (blocked) {
        fan_manual.reason = blocked;
        fan_manual.phase = FM_ABORT;
        return;
    }
    if (channel >= 4) {
        // Latch coast-down readiness before the MCU clock can wrap.
        if (fan_manual.phase == FM_OFF && !fan_manual.settled
            && iv9_due(now, fan_manual.since + FAN_OFF_SETTLE))
            fan_manual.settled = 1;
        return;
    }

    uint16_t *edges = fan_manual.phase == FM_ON
        ? &fan_manual.on_edges : &fan_manual.off_edges;
    uint32_t settle = fan_manual.phase == FM_ON ? FAN_ON_SETTLE : FAN_OFF_SETTLE;
    if (fan_manual.phase == FM_ON && *edges != FM_NO_EDGES)
        return;
    if (!fan_manual.settled && iv9_due(now, fan_manual.since + settle)) {
        fan_manual.mark = pulses[channel];
        fan_manual.since = now;
        fan_manual.settled = 1;
    } else if (fan_manual.settled && iv9_due(now, fan_manual.since + FAN_WINDOW)) {
        uint32_t count = pulses[channel] - fan_manual.mark;
        *edges = count < FM_NO_EDGES ? count : FM_NO_EDGES - 1;
        fan_manual.mark = pulses[channel];
        fan_manual.since = now;
    }
}

static void fan_manual_abort(void)
{
    if (fan_manual_active()) {
        fan_manual.phase = FM_ABORT;
        fan_manual.reason = FM_INTERLOCK;
    }
}

void command_inventure_fan_manual(uint32_t *args)
{
    unsigned action = args[0], channel = args[1];
    cooling_update();
    irqstatus_t flags = irq_save();
    uint32_t now = timer_read_time();
    uint8_t reject = FM_OK;
    if (action == FM_CANCEL) {
        t0_heat_manual_cancel();
        if (fan_manual_active()) {
            fan_manual.phase = FM_ABORT;
            fan_manual.reason = FM_OK;
        }
    } else if (action > FM_CONFIRM_FAIL || (action && channel >= 6)) {
        reject = FM_INVALID;
    } else if (action == FM_START) {
        if (fan_manual_active() || t0_heat_busy() || motor_test_busy()) {
            reject = FM_BUSY;
        } else if (!(reject = fan_manual_allowed(now))) {
            fan_manual.run++;
            fan_manual.channel = channel;
            fan_manual.phase = FM_ON;
            fan_manual.since = now;
            fan_manual.on_edges = fan_manual.off_edges = FM_NO_EDGES;
            fan_manual.settled = fan_manual.reason = 0;
        }
    } else if (action && channel != fan_manual.channel) {
        reject = FM_SEQUENCE;
    } else if (action == FM_STOP) {
        if (fan_manual.phase != FM_ON)
            reject = FM_SEQUENCE;
        else {
            fan_manual.phase = FM_OFF;
            fan_manual.since = now;
            fan_manual.settled = 0;
        }
    } else if (action == FM_CONFIRM_PASS || action == FM_CONFIRM_FAIL) {
        if (fan_manual.phase != FM_OFF)
            reject = FM_SEQUENCE;
        else if (action == FM_CONFIRM_FAIL) {
            fan_manual.phase = FM_FAIL;
            fan_manual.failed |= BIT(channel);
            fan_manual.passed &= ~BIT(channel);
        }
        else if (channel < 4 && fan_manual.off_edges == FM_NO_EDGES)
            reject = FM_WAIT;
        else if (channel >= 4 && !fan_manual.settled)
            reject = FM_WAIT;
        else if (channel < 4 && (!fan_manual.on_edges
                                || fan_manual.on_edges == FM_NO_EDGES
                                || fan_manual.off_edges
                                || pulses[channel] != fan_manual.mark))
            reject = FM_TACH;
        else {
            fan_manual.phase = FM_PASS;
            fan_manual.passed |= BIT(channel);
            fan_manual.failed &= ~BIT(channel);
        }
    }
    irq_restore(flags);
    cooling_update();
    sendf("inventure_fan_manual_state run=%u channel=%c phase=%c reject=%c reason=%c required=%c applied=%c on_edges=%hu off_edges=%hu pcb_adc=%hu pcb_temp=%hi passed=%c failed=%c",
          fan_manual.run, fan_manual.channel, fan_manual.phase, reject, fan_manual.reason,
          0, cool_mask, fan_manual.on_edges, fan_manual.off_edges, cool_pcb_adc, cool_pcb_temp,
          fan_manual.passed, fan_manual.failed);
}
DECL_COMMAND_FLAGS(command_inventure_fan_manual, HF_IN_SHUTDOWN,
                   "inventure_fan_manual action=%c channel=%c");
