"""Isolated Info ABI on top of unchanged R24 motion/ownership sources."""
from pathlib import Path
import shutil
from apply_inventure_home_r24 import ROOT, replace, overlay as home_overlay

RELEASE = 'RC8-CONTROL-R7-HOME-R25-INFO-OLED'


def overlay(destination):
    source = home_overlay(destination)
    for name in ('inventure_factory_info.h', 'inventure_information.inc'):
        shutil.copy2(ROOT/'restored/information_r25'/name, source/name)
    board = source/'inventure_board.c'
    text = board.read_text()
    for path in source.iterdir():
        if path.suffix in ('.c','.h','.inc','.py'):
            value=path.read_text()
            if 'RC8-CONTROL-R7-HOME-R24-OLED' in value:
                path.write_text(value.replace('RC8-CONTROL-R7-HOME-R24-OLED',RELEASE))
    text = board.read_text()
    text += '\n#include "inventure_information.inc"\n'
    board.write_text(text)
    script = source/'build_thermal_test.py'
    text = script.read_text()
    # Include the new files in the existing source provenance and copy loop.
    text = replace(text, "'inventure_board.c', 'inventure_control.inc', 'inventure_z_home.inc',",
        "'inventure_board.c', 'inventure_factory_info.h', 'inventure_information.inc', 'inventure_control.inc', 'inventure_z_home.inc',")
    # The parent builder still recognizes its exact insertion anchor.
    text = text.replace("'inventure_board.c', 'inventure_factory_info.h', 'inventure_information.inc', 'inventure_control.inc', 'inventure_z_home.inc',",
        "'inventure_board.c', 'inventure_control.inc', 'inventure_z_home.inc',\n"
        "                 'inventure_factory_info.h', 'inventure_information.inc',")
    anchor="    provenance['p4_mode']='InventureBoot 1.7 journal; forced baud disabled'\n"
    text=replace(text,anchor,anchor+
        f'    sys.path.insert(0, {str(ROOT/"ci")!r})\n'
        '    from apply_inventure_information_r25 import patch_scheduler\n'
        '    patch_scheduler(root)\n'
        "    provenance['patched_sha256']['src/sched.c']=base.sha((root/'src/sched.c').read_bytes())\n")
    text=replace(text, '    base.validate(root, out)\n',
        '    base.validate(root, out)\n'
        '    import shutil\n'
        "    shutil.copy2(root/'out/klipper.elf', out/'klipper.elf')\n")
    script.write_text(text)
    return source


def patch_scheduler(root):
    path = Path(root)/'src/sched.c'
    text = path.read_text()
    if 'sched_inventure_flash_ready' in text:
        raise ValueError('Info scheduler additions already present')
    text += '''
// R25: reached only after independent host and MCU all-OFF/empty-queue checks.
// Preserve every existing callback and its relative deadline during Flash bus
// stalls. Native Home and normal scheduler dispatch never enter these helpers.
uint8_t sched_inventure_flash_ready(uint32_t now) {
    return !sched_is_shutdown() && (int32_t)(SchedStatus.timer_list->waketime-now)
           > (int32_t)timer_from_us(12);
}
void sched_inventure_flash_rebase(uint32_t started) {
    uint32_t elapsed=timer_read_time()-started;
    struct timer *t=SchedStatus.timer_list;
    // Caller holds IRQs off, so list links and callbacks cannot change here.
    for (;;) {
        t->waketime+=elapsed;
        if(t==&sentinel_timer)break;
        t=t->next;
    }
    timer_kick();
}
'''
    path.write_text(text)
