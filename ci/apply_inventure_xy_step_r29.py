"""Keep the R28 source kit and extend the normal CoreXY step queue for r0323 rates."""
import shutil
from pathlib import Path

from apply_inventure_home_r24 import ROOT, replace
from apply_inventure_xy_step_r28 import RELEASE as PREVIOUS, overlay as parent_overlay
from apply_inventure_xy_step_r28 import patch_stepper as patch_r28

RELEASE = 'RC8-CONTROL-R7-HOME-R29-XY-OEM'
PATCHES = ROOT/'restored/xy_step_r29'


def patch_stepper(root):
    patch_r28(root)
    path = Path(root)/'src/stepper.c'
    text = path.read_text()
    first = '    uint32_t xy_first;\n'
    text = replace(text, first, first + '    volatile uint32_t *xy_oneshot;\n')
    empty = '        // There is no next move - the queue is empty\n'
    text = replace(text, empty, empty + '        stepper_xy_release_one(s);\n')
    start = text.index('    if (unlikely(s->xy_mode != XY_SOFTWARE))')
    end = text.index('    gpio_out_toggle_noirq(s->step_pin);', start)
    text = text[:start] + '''    if (unlikely(s->xy_mode != XY_SOFTWARE)) {
        if (s->xy_mode == XY_ONE_RUNNING)
            return stepper_xy_one_step(s);
        if (s->xy_mode == XY_ONE_DRAIN)
            return stepper_xy_one_finish(s);
        if (s->xy_mode == XY_RUNNING)
            return stepper_xy_finish(s);
        if (!s->stop_signal.func && inventure_xy_step_allowed()) {
            if (s->xy_mode == XY_PLANNED)
                return stepper_xy_start(s);
            if (!s->xy_oneshot)
                s->xy_oneshot = inventure_xy_one_arm(s->xy_axis);
            if (timer_is_before(timer_read_time(), s->xy_first)) {
                s->time.waketime = s->xy_first;
                return SF_RESCHEDULE;
            }
            s->xy_mode = XY_ONE_RUNNING;
            s->xy_count = s->count / 2;
            return stepper_xy_one_step(s);
        }
        stepper_xy_release_one(s);
        s->xy_mode = XY_SOFTWARE;
        if (timer_is_before(timer_read_time(), s->xy_first)) {
            s->time.waketime = s->xy_first;
            return SF_RESCHEDULE;
        }
    }
''' + text[end:]
    position = '    else if (s->flags & SF_SINGLE_SCHED)\n        position -= s->count;'
    text = replace(text, position, '    else if (s->xy_mode == XY_ONE_DRAIN)\n'
                   '        return (position & 0x80000000) ? -position : position;\n' + position)
    reset = '    s->xy_mode = XY_SOFTWARE;\n    s->next_step_time = s->time.waketime = 0;'
    text = replace(text, reset, '''    else if (s->xy_mode == XY_ONE_DRAIN)
        s->count = 0;
    stepper_xy_release_one(s);
''' + reset)
    path.write_text(text)


def overlay(destination):
    source = parent_overlay(destination)
    for path in source.iterdir():
        if path.suffix in ('.c', '.inc', '.h', '.py'):
            path.write_text(path.read_text().replace(PREVIOUS, RELEASE))
    for name in ('inventure_xy_step.inc', 'inventure_xy_step.h', 'inventure_stepper_timer.inc'):
        shutil.copy2(PATCHES/name, source)
    script = source/'build_thermal_test.py'
    text = replace(script.read_text(), 'from apply_inventure_xy_step_r28 import patch_stepper',
                   'from apply_inventure_xy_step_r29 import patch_stepper')
    script.write_text(text)
    return source
