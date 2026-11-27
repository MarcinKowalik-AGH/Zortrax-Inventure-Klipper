"""Apply the F207 hardware STEP port above the unchanged R27 source kit."""
import shutil
from pathlib import Path

from apply_inventure_home_r24 import ROOT, replace
from apply_inventure_uart_r27 import RELEASE as PREVIOUS, overlay as parent_overlay

RELEASE = 'RC8-CONTROL-R7-HOME-R28-XY-TIMERS'
PATCHES = ROOT/'restored/xy_step_r28'


def patch_stepper(root):
    path = Path(root)/'src/stepper.c'
    text = path.read_text()
    include = '#include "trsync.h" // trsync_add_signal\n'
    text = replace(text, include, include + '#include "board/inventure_xy_step.h"\n')
    field = '    uint8_t flags : 8;\n'
    text = replace(text, field, field + '    uint8_t xy_axis, xy_mode;\n'
                   '    uint16_t xy_count;\n    uint32_t xy_first;\n')
    start = '// Setup a stepper for the next move in its queue\n'
    text = replace(text, start, 'static uint_fast8_t stepper_load_next(struct stepper *s);\n'
                   '#include "board/inventure_stepper_timer.inc"\n\n' + start)
    end_direction = '                s->time.waketime = min_next_time;\n            return SF_RESCHEDULE;'
    text = replace(text, end_direction, end_direction.replace(
        '            return', '            stepper_xy_plan(s);\n            return'))
    end_load = '    if (need_dir_change)\n        gpio_out_toggle_noirq(s->dir_pin);\n    return SF_RESCHEDULE;'
    text = replace(text, end_load, end_load.replace(
        '    return', '    stepper_xy_plan(s);\n    return'))
    event = 'stepper_event_full(struct timer *t)\n{\n    struct stepper *s = container_of(t, struct stepper, time);\n'
    text = replace(text, event, event + '''    if (unlikely(s->xy_mode != XY_SOFTWARE)) {
        if (s->xy_mode == XY_RUNNING)
            return stepper_xy_finish(s);
        s->xy_mode = XY_SOFTWARE;
        if (!s->stop_signal.func && inventure_xy_step_allowed())
            return stepper_xy_start(s);
        // A homing trigger can be installed after this block was queued.
        // Wait for its original STEP clock before using the software path.
        if (timer_is_before(timer_read_time(), s->xy_first)) {
            s->time.waketime = s->xy_first;
            return SF_RESCHEDULE;
        }
    }
''')
    pulse = '    s->step_pulse_ticks = args[4];\n'
    text = replace(text, pulse, pulse + '''    if (!invert_step && s->step_pulse_ticks == INVENTURE_XY_PULSE)
        s->xy_axis = inventure_xy_step_axis(args[1], args[2]);
''')
    comment = '    // If stepper is mid-move, subtract out steps not yet taken\n'
    text = replace(text, comment + '    if (s->flags & SF_SINGLE_SCHED)\n',
                   comment + '''    if (s->xy_mode == XY_RUNNING)
        position -= stepper_xy_remaining(s, inventure_xy_step_remaining(s->xy_axis));
    else if (s->flags & SF_SINGLE_SCHED)
''')
    stop = '    sched_del_timer(&s->time);\n    s->next_step_time = s->time.waketime = 0;'
    text = replace(text, stop, '''    sched_del_timer(&s->time);
    if (s->xy_mode == XY_RUNNING)
        s->count = 2u * stepper_xy_remaining(s, inventure_xy_step_stop(s->xy_axis));
    s->xy_mode = XY_SOFTWARE;
    s->next_step_time = s->time.waketime = 0;''')
    path.write_text(text)


def overlay(destination):
    source = parent_overlay(destination)
    for path in source.iterdir():
        if path.suffix in ('.c', '.inc', '.h', '.py'):
            path.write_text(path.read_text().replace(PREVIOUS, RELEASE))
    for name in ('inventure_xy_step.inc', 'inventure_xy_step.h',
                 'inventure_stepper_timer.inc'):
        shutil.copy2(PATCHES/name, source)

    board = source/'inventure_board.c'
    text = board.read_text()
    prototype = 'static void control_stop_all(void);'
    text = replace(text, prototype, prototype + '\nstatic uint8_t inventure_xy_step_quiesce(void);')
    gpio = '        int fi=fan_index(r,bit);\n'
    text = replace(text, gpio, '''        if (r == GPIOE && bit == BIT(10) && !value && inventure_xy_step_quiesce())
            try_shutdown("Inventure XY motor enable lost during a hardware block");
''' + gpio)
    safe = 'static void force_safe(void) {\n'
    text = replace(text, safe, safe + '    inventure_xy_step_quiesce();\n')
    sampler = '#include "inventure_sampler_uart.inc"\n'
    text = replace(text, sampler, sampler + '#include "inventure_xy_step.inc"\n')
    init = '    initialized=1;\n'
    text = replace(text, init, '    inventure_xy_step_init();\n' + init)
    board.write_text(text)

    control = source/'inventure_control.inc'
    text = control.read_text()
    stop = 'static void control_stop_all(void) {\n'
    text = replace(text, stop, stop + '    uint8_t xy_stopped = inventure_xy_step_quiesce();\n')
    stop_end = '    cool_mask=control_cooling_mask();cool_applied=255;cooling_apply();\n}\nstatic void control_shutdown'
    text = replace(text, stop_end, stop_end.replace(
        '\n}\n', '\n    if (xy_stopped) try_shutdown("Inventure XY hardware motion stopped by owner");\n}\n'))
    control.write_text(text)

    script = source/'build_thermal_test.py'
    text = script.read_text()
    text = replace(text, '    patch_uart(root)\n', '    patch_uart(root)\n'
                   '    from apply_inventure_xy_step_r28 import patch_stepper as patch_xy_stepper\n'
                   '    patch_xy_stepper(root)\n')
    text = replace(text, "'inventure_sampler_uart.inc',",
                   "'inventure_sampler_uart.inc', 'inventure_xy_step.inc',\n"
                   "                 'inventure_xy_step.h', 'inventure_stepper_timer.inc',")
    # The parent builder inserts its R24 hook immediately after apply_rc7.
    # Provenance computed later therefore contains both changes to stepper.c.
    script.write_text(text)
    return source
