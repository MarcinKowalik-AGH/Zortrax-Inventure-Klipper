#!/usr/bin/env python3
"""Compare Inventure OEM cursor windows with identical pixels and compression.

This is an offline UART cost model using the real R7 MCU message dictionary.
It does not measure physical button-to-pixel latency or a printer's UART errors.
"""
import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import types

ROOT=Path(__file__).resolve().parents[1]


def benchmark():
    package=types.ModuleType('inventure_benchmark')
    package.__path__=[str(ROOT/'orange_pi'),str(ROOT/'host')]
    sys.modules[package.__name__]=package
    transport=importlib.import_module(package.__name__+'.inventure_oled_uifast_transport')
    renderer=importlib.import_module(package.__name__+'.inventure_oled_render')
    menus=importlib.import_module(package.__name__+'.inventure_oem_menu')
    spec=importlib.util.spec_from_file_location('inventure_benchmark_msgproto',ROOT/'tests/fixtures/pinned_msgproto.py')
    proto=importlib.util.module_from_spec(spec);spec.loader.exec_module(proto)
    dictionary=json.loads((ROOT/'tests/fixtures/inventure_oled_protocol.json').read_text())
    parser=proto.MessageParser();parser.process_identify(json.dumps(dictionary).encode(),decompress=False)
    burst=parser.lookup_command(next(f for f in dictionary['commands'] if f.startswith('inventure_oled_burst ')))
    ack=parser.lookup_command(next(iter(dictionary['responses'])))
    # Exact native generation/token widths, same for both plans.
    generation=(0x42a531dd,0x762ccad9);token_start=4096

    def score(ops):
        tx=rx=decoded=payload=0
        for i,(_,args,_) in enumerate(ops):
            token=token_start+i
            tx+=len(burst.encode([*generation,token,*args]))+proto.MESSAGE_MIN
            rx+=len(ack.encode([*generation,token,0]))+proto.MESSAGE_MIN
            decoded+=args[2];payload+=len(args[3])
        return dict(operations=len(ops),uart_bytes=tx+rx,decoded_bytes=decoded,payload_bytes=payload)

    rows=[]
    for language in ('en','pl'):
        menu=menus.OEMMenu(language,root_page='launcher',bench=True,operational=True)
        for page in menus.MENU:
            menu.page=page;menu.selected=0;menu.window=1
            frames=[]
            for selected in range(len(menu.items())):
                menu.selected=selected;menu.generation+=1
                frames.append(renderer.Frame().oem_menu(menu.view()).wire_bytes())
            for index,(first,last) in enumerate(zip(frames,frames[1:])):
                for direction,before,after in ((1,first,last),(-1,last,first)):
                    regions=transport.diff_regions(before,after)
                    ordinary=tuple(op for window in regions for op in transport.fast_frame_operations(after,*window))
                    planned=transport.optimized_frame_operations(before,after,regions)
                    baseline,result=score(ordinary),score(planned)
                    if result['uart_bytes']>baseline['uart_bytes'] or result['decoded_bytes']>baseline['decoded_bytes']:
                        raise AssertionError('Optimized plan regressed: '+page)
                    rows.append(dict(language=language,page=page,cursor=index,direction=direction,
                                     fixed_dirty_regions=baseline,encoded_window_plan=result))
    baseline=sum(r['fixed_dirty_regions']['uart_bytes'] for r in rows)
    result=sum(r['encoded_window_plan']['uart_bytes'] for r in rows)
    pages=[]
    for language,page in dict.fromkeys((r['language'],r['page']) for r in rows):
        subset=[r for r in rows if r['language']==language and r['page']==page]
        pages.append(dict(language=language,page=page,transitions=len(subset),
            fixed_dirty_regions_uart_bytes=sum(r['fixed_dirty_regions']['uart_bytes'] for r in subset),
            encoded_window_plan_uart_bytes=sum(r['encoded_window_plan']['uart_bytes'] for r in subset)))
    return dict(model='OFFLINE_UART_COST_ONLY',host_revision='RC8-CONTROL-R7-UIFAST',
        reference='same reference OLED pixels, same single-pass PackBits; vary only window planning',
        m300_source_commit='6c359c8b301bf8dd3c0e3e4ec69382c6be324cad',
        mcu_dictionary_source_sha256=dictionary['source_sha256'],
        physical_latency_measured=False,physical_UART_errors_measured=False,
        transitions=len(rows),improved_transitions=sum(r['encoded_window_plan']['uart_bytes']<r['fixed_dirty_regions']['uart_bytes'] for r in rows),
        fixed_dirty_regions_uart_bytes=baseline,encoded_window_plan_uart_bytes=result,
        uart_reduction_percent=round(100.*(baseline-result)/baseline,2),
        pages=pages)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=benchmark()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='pages'},indent=2))


if __name__=='__main__':main()
