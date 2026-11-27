"""Isolate M300-style interruptible display transport above unchanged R25."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
from apply_inventure_information_r25 import overlay as info_overlay
from apply_inventure_home_r24 import ROOT, replace

RELEASE='RC8-CONTROL-R7-HOME-R26-TRANSPORT-OLED'


def patch_uart(root):
    path=Path(root)/'src/generic/serial_irq.c'
    text=path.read_text()
    text=replace(text,'static volatile uint32_t transmit_dropped;',
        'static volatile uint32_t transmit_dropped,receive_overflows;')
    text=replace(text,'    if (receive_pos >= sizeof(receive_buf))\n'
        '        // Serial overflow - ignore it as crc error will force retransmit\n'
        '        return;\n',
        '    if (receive_pos >= sizeof(receive_buf)) {\n'
        '        receive_overflows++;return;\n    }\n')
    # Like upstream/M300: the console task is the sole frame producer. UART
    # TX IRQ consumes only the published prefix. Copying an uncommitted frame
    # does not require masking RX, step or endstop interrupts for its bytes.
    start=text.index('    irqstatus_t flag = irq_save();',text.index('void\nconsole_sendf('))
    stop=text.index('\n}',start)
    text=text[:start]+'''    uint16_t head=transmit_head;
    uint16_t used=(uint16_t)(head-transmit_tail);
    if(used>=TX_RING_SIZE || len>TX_RING_SIZE-used) {
        transmit_dropped++;return;
    }
    for(uint_fast8_t i=0;i<len;i++)
        transmit_buf[(head+i)&TX_RING_MASK]=frame[i];
    // Only publication is atomic; the consumer cannot see a partial frame.
    irqstatus_t flag=irq_save();
    transmit_head=head+len;
    used=(uint16_t)(transmit_head-transmit_tail);
    if(used>transmit_highwater)transmit_highwater=used;
    serial_enable_tx_irq();
    irq_restore(flag);'''+text[stop:]
    text+='\nuint32_t inventure_uart_rx_overflows(void){return receive_overflows;}\n'
    path.write_text(text)
    marker=Path(root)/'.inventure-v9-patch.json'
    if marker.exists():
        data=json.loads(marker.read_text())
        data['patched_sha256']['src/generic/serial_irq.c']=sha256(path.read_bytes()).hexdigest()
        marker.write_text(json.dumps(data,indent=2)+'\n')


def overlay(destination):
    source=info_overlay(destination)
    protected={path.name:path.read_text() for path in source.iterdir()
        if path.suffix in ('.c','.inc','.h') and path.name not in ('inventure_panel.c','serial.c')}
    for path in source.iterdir():
        if path.suffix in ('.c','.inc','.py','.h'):
            path.write_text(path.read_text().replace('RC8-CONTROL-R7-HOME-R25-INFO-OLED',RELEASE))
    panel=source/'inventure_panel.c';text=panel.read_text()
    begin=text.index('static void\noled_fast_set_alarm(')
    end=text.index('\nvoid\ninventure_oled_dma_irq(',begin)
    text=text[:begin]+text[end:]
    begin=text.index('void\ninventure_oled_fast_task(')
    end=text.index('DECL_TASK(inventure_oled_fast_task);',begin)+len('DECL_TASK(inventure_oled_fast_task);')
    text=text[:begin]+'#include "inventure_oled_dma_task.inc"'+text[end:]
    text=replace(text,'DECL_CONSTANT("INVENTURE_OLED_FAST_ABI", 1);',
        'DECL_CONSTANT("INVENTURE_OLED_FAST_ABI", 1);\n'
        'DECL_CONSTANT("INVENTURE_OLED_COEXISTENCE_ABI", 1);')
    panel.write_text(text)
    shutil.copy2(ROOT/'restored/transport_r26/inventure_oled_dma_task.inc',source)
    serial=source/'serial.c';text=serial.read_text()
    text=replace(text,'void\nUSARTx_IRQHandler(void)\n{',
        'static volatile uint32_t uart_overrun,uart_noise,uart_framing;\n'
        'void inventure_serial_errors(uint32_t *overrun,uint32_t *noise,uint32_t *framing) {\n'
        '    *overrun=uart_overrun;*noise=uart_noise;*framing=uart_framing;\n}\n'
        'void\nUSARTx_IRQHandler(void)\n{')
    text=replace(text,'    uint32_t sr = USARTx->SR;\n',
        '    uint32_t sr = USARTx->SR;\n'
        '    if(sr & USART_SR_ORE)uart_overrun++;\n'
        '    if(sr & USART_SR_NE)uart_noise++;\n'
        '    if(sr & USART_SR_FE)uart_framing++;\n')
    serial.write_text(text)
    script=source/'build_thermal_test.py';text=script.read_text()
    text=replace(text,'    base.apply_rc7(root, original, expected)\n',
        '    base.apply_rc7(root, original, expected)\n'
        f'    sys.path.insert(0, {str(ROOT/"ci")!r})\n'
        '    from apply_inventure_transport_r26 import patch_uart\n'
        '    patch_uart(root)\n')
    text=replace(text,"'inventure_panel.c', 'inventure_fans.inc', 'gpioperiph.c',",
        "'inventure_panel.c', 'inventure_oled_dma_task.inc', 'inventure_fans.inc', 'gpioperiph.c',")
    script.write_text(text)
    for name,original in protected.items():
        if (source/name).read_text()!=original.replace('RC8-CONTROL-R7-HOME-R25-INFO-OLED',RELEASE):
            raise ValueError('R26 changed a protected R25 MCU source: '+name)
    return source
