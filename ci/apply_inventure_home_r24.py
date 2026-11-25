"""Build an isolated R24 overlay; never modify frozen R23 production inputs."""
from hashlib import sha256
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
RELEASE='RC8-CONTROL-R7-HOME-R24-OLED'
STEPPER_IDLE='''
// R24: inspect every native step queue with interrupts already excluded.
uint8_t inventure_native_stepper_idle(void) {
    uint8_t oid;struct stepper *s;
    foreach_oid(oid,s,command_config_stepper) {
        if(s->count || !move_queue_empty(&s->mq))return 0;
    }
    return 1;
}
'''

def replace(text,before,after):
    if text.count(before)!=1:raise ValueError('Unexpected frozen R23 anchor: '+before[:80])
    return text.replace(before,after,1)

def patch_stepper(root):
    path=Path(root)/'src/stepper.c'
    text=path.read_text()
    text=replace(text,'command_queue_step(uint32_t *args)\n{\n',
        'command_queue_step(uint32_t *args)\n{\n'
        '    extern uint8_t inventure_native_queue_allowed(void);\n'
        '    if (!inventure_native_queue_allowed())\n'
        '        shutdown("Inventure native queue has no motion ownership");\n')
    path.write_text(text+STEPPER_IDLE)

def overlay(destination):
    destination=Path(destination)
    shutil.copytree(ROOT/'service_mcu/control_r6/SOURCE_MCU',destination)
    for path in destination.iterdir():
        if path.suffix in ('.c','.inc','.py'):
            path.write_text(path.read_text().replace('RC8-CONTROL-R7-HOMING-R23-OLED',RELEASE))
    control=destination/'inventure_control.inc'
    text=control.read_text()
    text=replace(text,'static uint8_t control_guard_mask=127,control_fan_phase,control_heat_seen;',
        'static uint32_t held_service_owner,held_service_deadline;\n'
        'static uint8_t held_service_pending,held_service_live;\n'
        'static uint8_t control_guard_mask=127,control_fan_phase,control_heat_seen;')
    text=replace(text,'static void control_stop_all(void) {\n',
        'static void control_stop_all(void) {\n    uint8_t was_held=held_service_live;\n'
        '    held_service_owner=0;held_service_pending=held_service_live=0;\n')
    text=replace(text,'    if(was_native) {control_latched=1;control_mode=CT_OFF;}\n',
        '    if(was_native || was_held) {control_latched=1;control_mode=CT_OFF;}\n')
    text=replace(text,'static void control_shutdown(void) {\n',
        'static void control_shutdown(void) {\n    held_service_owner=0;held_service_pending=held_service_live=0;\n')
    text=replace(text,'static void control_guard(uint32_t now) {\n',
        'static void control_guard(uint32_t now) {\n'
        '    if(held_service_pending && (iv9_due(now,held_service_deadline)\n'
        '            || !(GPIOE->ODR&BIT(10))))control_stop_all();\n')
    anchor='''        if(!reject && (fs.active || sl.active || cm.active || motor_test.running || xy_seek_busy()
'''
    text=replace(text,anchor,
        '        if(!reject && held_service_pending\n'
        '                && (token!=held_service_owner || action!=1 || tool || mode || monitor))reject=4;\n'+anchor)
    text=replace(text,'            fs.token=token;fs.sequence=0;fs.deadline=now+MS(lease);fs.lease_ms=lease;\n',
        '            if(held_service_pending)held_service_pending=0;\n'
        '            fs.token=token;fs.sequence=0;fs.deadline=now+MS(lease);fs.lease_ms=lease;\n')
    text=replace(text,'            control_mode=CT_NATIVE; // Keep PE10 asserted through the ownership transfer.\n',
        '            held_service_live=held_service_pending=0;\n'
        '            control_mode=CT_NATIVE; // Keep PE10 asserted through the ownership transfer.\n')
    text+='\n#include "inventure_held_service.inc"\n'
    control.write_text(text)
    shutil.copy2(ROOT/'restored/operator_home_r24/inventure_held_service.inc',destination)
    board=destination/'inventure_board.c';text=board.read_text()
    text=replace(text,'static uint8_t control_aux_allowed(void);',
        'static uint8_t held_service_owns_pins(void);\nstatic uint8_t control_aux_allowed(void);')
    text=replace(text,'uint32_t inventure_filter_gpio(void *r, uint32_t bit, uint32_t value) {\n',
        'uint32_t inventure_filter_gpio(void *r, uint32_t bit, uint32_t value) {\n'
        '    if(held_service_owns_pins()\n'
        '            && ((r==GPIOA && (bit==BIT(6)||bit==BIT(7)))\n'
        '                || (r==GPIOB && (bit==BIT(1)||bit==BIT(2)||bit==BIT(8)||bit==BIT(9)))\n'
        '                || (r==GPIOE && (bit==BIT(7)||bit==BIT(8)||bit==BIT(10))))) {\n'
        '        if(r==GPIOE && bit==BIT(10) && !value) {\n'
        '            control_stop_all();return 0;\n'
        '        }\n'
        '        shutdown("Inventure native GPIO during held SERVICE");\n'
        '        return !!(((GPIO_TypeDef*)r)->ODR&bit);\n'
        '    }\n')
    # Forward declaration is needed by the early GPIO filter.
    text=replace(text,'static void control_shutdown(void);',
        'static void control_stop_all(void);\nstatic void control_shutdown(void);')
    board.write_text(text)
    return destination
