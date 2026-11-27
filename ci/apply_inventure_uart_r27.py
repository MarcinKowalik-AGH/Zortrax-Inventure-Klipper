"""Allow the existing priority-0 UART ISR during the unchanged F207 sampler."""
from pathlib import Path
import shutil
from apply_inventure_transport_r26 import overlay as parent_overlay
from apply_inventure_home_r24 import ROOT,replace

RELEASE='RC8-CONTROL-R7-HOME-R27-UART-OLED'

def overlay(destination):
    source=parent_overlay(destination)
    for path in source.iterdir():
        if path.suffix in ('.c','.inc','.h','.py'):
            path.write_text(path.read_text().replace('RC8-CONTROL-R7-HOME-R26-TRANSPORT-OLED',RELEASE))
    board=source/'inventure_board.c';text=board.read_text()
    text=replace(text,'static uint_fast8_t sample_event(struct timer *t) {',
        'static uint_fast8_t noinline sample_event_body(struct timer *t) {')
    text=replace(text,'\nvoid inventure_board_init(void) {',
        '\n#include "inventure_sampler_uart.inc"\n\nvoid inventure_board_init(void) {')
    board.write_text(text)
    shutil.copy2(ROOT/'restored/transport_r27/inventure_sampler_uart.inc',source)
    serial=source/'serial.c';text=serial.read_text()
    text=replace(text,'void\nUSARTx_IRQHandler(void)\n{',
        'extern void inventure_sampler_uart_irq(uint32_t status);\nvoid\nUSARTx_IRQHandler(void)\n{')
    text=replace(text,'    uint32_t sr = USARTx->SR;\n',
        '    uint32_t sr = USARTx->SR;\n    inventure_sampler_uart_irq(sr);\n')
    serial.write_text(text)
    script=source/'build_thermal_test.py';text=script.read_text()
    text=replace(text,"'inventure_board.c', 'inventure_control.inc', 'inventure_z_home.inc',",
        "'inventure_board.c', 'inventure_control.inc', 'inventure_z_home.inc',\n                 'inventure_sampler_uart.inc',")
    script.write_text(text)
    return source
