"""Add bounded cassette feed phases to the isolated R30 source overlay."""
import shutil

from apply_inventure_home_r24 import ROOT, replace
from apply_inventure_xy_step_r30 import RELEASE as PREVIOUS, overlay as parent_overlay

RELEASE = 'RC8-CONTROL-R7-HOME-R31-CASSETTE-WINDOWS'


def patch_feed_windows(source):
    control = source/'inventure_control.inc'
    text = control.read_text()
    text = replace(text, 'static void fs_age_update(uint32_t now);',
                   '''static void fs_age_update(uint32_t now);
static void factory_feed_tick(uint32_t now);''')
    text = replace(text, '    fs_guard(now);\n    service_native_hot_history(now);',
                   '    fs_guard(now);\n    factory_feed_tick(now);\n    service_native_hot_history(now);')
    text = replace(text, 'static void fs_set_mode(unsigned mode,unsigned monitor,uint32_t now) {',
                   '''#include "inventure_factory_feeder.inc"
static void fs_set_mode(unsigned mode,unsigned monitor,uint32_t now) {
    if (factory_feed_owns_mode()) {
        if (mode == fs.mode)
            return;
        factory_feed_release();
    }''')
    control.write_text(text)
    shutil.copy2(ROOT/'restored/cassette_windows_r31/inventure_factory_feeder.inc', source)


def overlay(destination):
    source = parent_overlay(destination)
    for path in source.iterdir():
        if path.suffix in ('.c', '.inc', '.h', '.py'):
            path.write_text(path.read_text().replace(PREVIOUS, RELEASE))
    patch_feed_windows(source)
    script = source/'build_thermal_test.py'
    text = replace(script.read_text(), "'inventure_xy_step_fast.inc',",
                   "'inventure_xy_step_fast.inc', 'inventure_factory_feeder.inc',")
    script.write_text(text)
    return source
