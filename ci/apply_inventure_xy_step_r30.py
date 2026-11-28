"""Build the R29 STEP paths with the 224-tick pulse period and ARM hot path."""
import shutil

from apply_inventure_home_r24 import ROOT, replace
from apply_inventure_xy_step_r29 import RELEASE as PREVIOUS, overlay as parent_overlay

RELEASE = 'RC8-CONTROL-R7-HOME-R30-XY-STEP-224'


def overlay(destination):
    source = parent_overlay(destination)
    for path in source.iterdir():
        if path.suffix in ('.c', '.inc', '.h', '.py'):
            path.write_text(path.read_text().replace(PREVIOUS, RELEASE))

    header = source/'inventure_xy_step.h'
    text = header.read_text()
    text = replace(text, 'INVENTURE_XY_MIN_LOW = 294,     // Same minimum low interval as the M300.',
                   'INVENTURE_XY_MIN_LOW = 90,      // 0.75 us physical low-time floor.')
    text = replace(text, 'INVENTURE_XY_MIN_PERIOD = 660, // r0323 TIM6: (10 + 1) / 2 MHz = 5.5 us.',
                   'INVENTURE_XY_MIN_PERIOD = 448, // 224 timer ticks: HIGH 147, LOW 77.')
    text = replace(text, 'INVENTURE_XY_SETUP = 240,',
                   'INVENTURE_XY_SETUP = 150,      // Start TIM3 with CNT in the PWM low phase.')
    header.write_text(text)

    board = source/'inventure_xy_step.inc'
    text = board.read_text()
    include = '_Static_assert(CONFIG_CLOCK_FREQ == 120000000, "Inventure STEP timer clock");'
    text = replace(text, include, include + '''
_Static_assert(INVENTURE_XY_SETUP <= INVENTURE_XY_MIN_PERIOD - INVENTURE_XY_FAST_HIGH,
               "TIM3 start must remain in the PWM low phase");''')
    text = replace(text, 'DECL_CONSTANT("INVENTURE_XY_STEP_TIMER_ABI", 2);',
                   'DECL_CONSTANT("INVENTURE_XY_STEP_TIMER_ABI", 3);')
    board.write_text(text + 'DECL_CONSTANT("INVENTURE_XY_STEP_LOW_FLOOR_TICKS", INVENTURE_XY_MIN_LOW);\n')

    stepper = source/'inventure_stepper_timer.inc'
    text = stepper.read_text()
    text = replace(text, 'static void\nstepper_xy_plan(struct stepper *s)',
                   '#include "board/inventure_xy_step_fast.inc"\n\n'
                   'static void\nstepper_xy_plan(struct stepper *s)')
    text = replace(text, '''stepper_xy_plan(struct stepper *s)
{
    if (!s->xy_axis || s->stop_signal.func) {''',
                   '''stepper_xy_plan(struct stepper *s)
{
    if (s->xy_mode == XY_ONE_RUNNING) {
        s->xy_count = s->count / 2;
        s->xy_first = s->time.waketime;
        s->time.waketime -= INVENTURE_XY_ONE_WAKE_ADVANCE;
        return;
    }
    if (!s->xy_axis || s->stop_signal.func) {''')
    text = replace(text, '''    // Klipper's first gap was used to position waketime. Look at the
    // shortest subsequent gap when an accelerating block crosses the threshold.
    uint32_t shortest = s->interval;
    if (s->add < 0 && pulses > 2)
        shortest += s->add * (int32_t)(pulses - 2);''',
                   '''    uint32_t shortest = stepper_xy_shortest_period(s->interval, s->add, pulses);''')
    text = replace(text, '''    if (!s->add && pulses >= 32 && s->interval >= INVENTURE_XY_MIN_PERIOD) {
        stepper_xy_release_one(s);
        s->xy_mode = XY_PLANNED;
    }''', '''    if (!s->add && !(s->interval & 1) && s->interval >= INVENTURE_XY_MIN_PERIOD) {
        stepper_xy_join_constant_moves(s);
        uint32_t now = timer_read_time();
        uint32_t eligible = timer_is_before(now, s->time.waketime) ? s->time.waketime : now;
        // Keep the host clock authoritative. If a block handoff consumed a
        // full period, let one-shots close the gap inside the LOW-time floor
        // instead of starting another constant train on a shifted clock.
        if (s->count >= 64 && (int32_t)(eligible - s->next_step_time) < (int32_t)s->interval) {
            stepper_xy_release_one(s);
            s->xy_mode = XY_PLANNED;
        }
    }''')
    text = replace(text, '''    uint32_t now = timer_read_time();
    if (*s->xy_oneshot & 1u || (int32_t)(now - s->next_step_time) > (int32_t)timer_from_us(1000))
        shutdown("Inventure XY one-shot missed its deadline");
    *s->xy_oneshot = INVENTURE_XY_ONESHOT_START;
    uint32_t earliest = timer_read_time() + INVENTURE_XY_ONESHOT_GAP;''',
                   '''    uint32_t fired = stepper_xy_one_emit(s->xy_oneshot, s->xy_first,
                                          s->next_step_time);
    uint32_t earliest = fired + INVENTURE_XY_ONESHOT_GAP;''')
    text = replace(text, '''    if (unlikely(!remaining)) {
        // Keep the queue busy''',
                   '''    if (unlikely(!remaining)) {
        if (stepper_xy_can_chain_one(s)) {
            inventure_xy_one_record(s->xy_axis, s->xy_count);
            s->time.waketime = earliest;
            return stepper_load_next(s);
        }
        // Keep the queue busy''')
    text = replace(text, '''    s->time.waketime = timer_is_before(s->next_step_time, earliest)
        ? earliest : s->next_step_time;''',
                   '''    s->xy_first = timer_is_before(s->next_step_time, earliest)
        ? earliest : s->next_step_time;
    s->time.waketime = s->xy_first - INVENTURE_XY_ONE_WAKE_ADVANCE;''')
    stepper.write_text(text)
    shutil.copy2(ROOT/'restored/xy_step_r30/inventure_xy_step_fast.inc', source)

    script = source/'build_thermal_test.py'
    text = replace(script.read_text(), "'inventure_stepper_timer.inc',",
                   "'inventure_stepper_timer.inc', 'inventure_xy_step_fast.inc',")
    script.write_text(text)
    return source
