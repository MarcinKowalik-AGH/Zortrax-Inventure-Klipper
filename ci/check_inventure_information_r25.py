#!/usr/bin/env python3
"""Run literal Info macros and physical OLED clicks through installed Klipper."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import types

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'ci'),str(ROOT/'tests')]


def inspect_startup(owner, printer, package, require):
    """Run the real Info synchronizer against the installed heater registry."""
    heaters=printer.lookup_object('heaters')
    names=heaters.get_all_heaters()
    require(all('heater_generic '+n in names for n in ('inventure_t1','chamber_l','chamber_r')),
            'Startup fixture does not cover Inventure generic heaters')
    original_printer,original_board,original_mcu=owner.printer,owner.board,owner.mcu
    original_reactor,original_executor=owner.reactor,owner.executor
    # Use an isolated real reactor/executor. The parser's reactor still has
    # Klippy's physical _connect callback queued and must never be started.
    import reactor
    from extras.aio_executor import Executor
    startup_loop=reactor.Reactor()
    startup_executor=Executor(startup_loop,'information-startup-test')
    owner.reactor,owner.executor=startup_loop,startup_executor
    idle=types.SimpleNamespace(check_busy=lambda now:(0.,0.,True))
    owner.printer=types.SimpleNamespace(is_shutdown=lambda:False,
        lookup_object=lambda name,default=None:heaters if name=='heaters' else idle if name=='toolhead' else default)
    owner.board=types.SimpleNamespace(load_test_context=None,zortrax=None,
        _control_query=lambda:dict(mode='OFF',armed=0,latched=0,fault=0))
    owner.mcu=types.SimpleNamespace(estimated_print_time=lambda now:0.,print_time_to_clock=lambda now:0)
    blocked=[]
    try:
        owner.verified_identity=True;owner.ready=True
        # Independently reproduce the field exception using the exact RC1/RC2
        # method and the real Klipper registry, before testing its correction.
        old=types.ModuleType(package+'.information_rc1_oracle')
        old.__package__=package
        exec(compile((ROOT/'restored/information_r25/rc1/inventure_information.py').read_bytes(),
                     'rc1-information-oracle','exec'),old.__dict__)
        try:
            old.InventureInformation.blocker(owner,0.)
        except Exception as exc:
            require(str(exc)=="Unknown heater 'heater_generic inventure_t1'",'Unexpected old startup failure: '+str(exc))
        else:
            raise AssertionError('The original field exception was not reproduced')
        require(owner.blocker(0.) is None,'Cold, idle generic-heater configuration was refused')
        for section in names:
            heater=heaters.lookup_heater(section.split()[-1])
            for attribute,value in (('target_temp',100.),('last_pwm_value',.1)):
                previous=getattr(heater,attribute)
                try:
                    setattr(heater,attribute,value)
                    require(owner.blocker(0.)==section,'An active heater did not defer factory writes: '+section)
                    blocked.append((section,attribute))
                finally:
                    setattr(heater,attribute,previous)
        require(len(blocked)==8,'Not all four native/generic heater guards were checked')
        owner.verified_identity=False
        def query(kind='state'):
            if kind=='identity':return dict(valid=1,serial=0x1234abcd,hardware=9,
                uid0=0x11223344,uid1=0x55667788,uid2=0x99aabbcc)
            return dict(serial=0x1234abcd,state=1,source=0,print_minutes=321,settings_crc=42)
        owner.transport=types.SimpleNamespace(query=query)
        def synchronize(now):
            try:
                owner.synchronize(now)
            finally:
                startup_loop.end()
        startup_loop.register_callback(synchronize)
        startup_loop.run()
        require(owner.verified_identity and owner.sync.status=='CURRENT','Info startup did not complete')
        require(owner.get_status(0.)['total_printing_time']=='5 h 21 min','Startup lost factory printing time')
        return dict(status='PASS_GENERIC_HEATER_STARTUP',original_exception_reproduced=True,
            heater_sections=names,active_target_and_pwm_refusals=len(blocked),counter_sync=owner.sync.status)
    finally:
        startup_executor.join()
        startup_loop.finalize()
        owner.reactor,owner.executor=original_reactor,original_executor
        owner.printer,owner.board,owner.mcu=original_printer,original_board,original_mcu


def cached_information_fixture(board, package, path):
    """Only simulated physical readings; use the actual installed data owner."""
    module=importlib.import_module(package+'.inventure_information')
    owner=module.InventureInformation.__new__(module.InventureInformation)
    owner.store=module.InfoStore(path)
    owner.store.bind_identity('112233445566778899AABBCC','Z1234ABCD',9)
    owner.store.update(lambda data:data['counters'].update(print_seconds=125.,completed_jobs=2))
    owner.clock=module.PrintClock();owner.saved=(0.,0)
    owner.sync=module.CounterSync();owner.sync.minutes=321;owner.sync.committed_seconds=120
    owner.sync.status='CURRENT';owner.verified_identity=True;owner.board=board
    owner.error=owner.wait_reason=None
    board.printer.objects['inventure_information']=owner
    return owner


def inspect(klipper, config, destination):
    sys.path.insert(0,str(klipper/'klippy'))
    import klippy,reactor
    from check_inventure_full_cycles import InstalledRig,require
    from check_inventure_home_xy import attach_panel
    from test_orange_pi_oled_fast import Panel
    from render_inventure_leveling_preview import png
    loop=reactor.Reactor()
    printer=klippy.Printer(loop,None,dict(config_file=str(config),debuginput=True,
        start_reason='startup',software_version='offline-inventure-info-r25'))
    try:
        printer._read_config()
        gcode=printer.lookup_object('gcode');gcode.gcode_handlers=gcode.ready_gcode_handlers;gcode.is_printer_ready=True
        rig=InstalledRig(printer,klipper)
        package=type(rig.board).__module__.rsplit('.',1)[0]
        panel=attach_panel(rig)
        owner=printer.lookup_object('inventure_information')
        require(Path(sys.modules[type(owner).__module__].__file__).resolve()==klipper/'klippy/extras/inventure_information.py',
                'Info did not load the installed module')
        startup=inspect_startup(owner,printer,package,require)
        rig.board.printer.objects['inventure_information']=owner
        owner.board=rig.board
        rig.board.mcu_release='RC8-CONTROL-R7-HOME-R25-INFO-OLED'
        owner.store.bind_identity('112233445566778899AABBCC','Z1234ABCD',9)
        owner.store.update(lambda data:data['counters'].update(print_seconds=125.,completed_jobs=2))
        owner.verified_identity=True;owner.sync.minutes=321;owner.sync.committed_seconds=120
        owner.sync.status='CURRENT'
        owner.transport=types.SimpleNamespace(query=lambda *args:(_ for _ in ()).throw(AssertionError('Info paint queried MCU')))
        graphics=importlib.import_module(package+'.inventure_oled_graphics')
        rows=[];frames=[]
        for action,macro,label in (('printer_info','INVENTURE_PRINTER_INFORMATION','Printer information'),
                                   ('version','INVENTURE_FIRMWARE_VERSION','Firmware version'),
                                   ('help','INVENTURE_HELP','Help')):
            pair=[]
            for origin in ('fluidd','oled'):
                rig.menu.view=None
                if origin=='fluidd':rig.run(macro)
                else:
                    panel.oem_menu.open_page('information');panel.oem_display_mode='menu'
                    panel.oem_menu.selected=next(i for i,item in enumerate(panel.oem_menu.items()) if item.label==label)
                    panel._select(panel.reactor.monotonic())
                view=rig.menu.view_status()
                require(view['labels'][0]=='Back','Info Back is not first')
                require(len(view['labels'])==(6 if action=='help' else 5),'Info has not retained the M300 layout')
                pair.append(dict(title=view['title'],labels=view['labels'],icon=view['icon']))
                frames.append(graphics.menu_wire(view))
                rig.menu.panel_back()
                require(panel.oem_menu.page=='information','Info Back lost its parent page')
                require(panel.oem_menu.items()[panel.oem_menu.selected].label==label,'Info Back lost its selected row')
            require(pair[0]==pair[1],'OLED/Fluidd Info rows differ')
            rows.append(dict(action=action,macro=macro,oled_fluidd_same=True,**pair[0]))
        # Help/Errors and POWER Back share the same parent, like M300.
        rig.menu.open('help');rig.menu.open('information_errors');rig.menu.panel_back()
        require(rig.menu.view.title=='Help' and rig.menu.view.selected==1,'Errors Back did not restore Help/Errors')
        for index in range(14):
            rig.menu.open('information_error_%d'%index);rig.menu.panel_back()
            require(rig.menu.view.title=='Errors' and rig.menu.view.selected==index+1,'Error detail Back did not restore the catalog cursor')
        expected=['Back','Model: Zortrax Inventure','Hardware version: 9',
                  'Serial number: Z1234ABCD','Total printing time: 5 h 21 min']
        require(rows[0]['labels']==expected,'Printer Info differs from the M300 field contract')
        rig.run('INVENTURE_PRINTER_INFORMATION')
        token,selected,deadline=rig.menu.view.token,rig.menu.view.selected,rig.menu.view.deadline
        owner.store.update(lambda data:data['counters'].update(print_seconds=185.))
        require(rig.menu.view_status()['labels'][-1]=='Total printing time: 5 h 22 min','Open Info did not refresh cached time')
        rig.menu._flush_information_prompt()
        require((rig.menu.view.token,rig.menu.view.selected,rig.menu.view.deadline)==(token,selected,deadline),
                'Read-only refresh changed a navigation token or selection')
        require(any('5 h 22 min' in line for line in rig.lines),'Fluidd prompt did not refresh with OLED')
        rig.run('INVENTURE_INFO')
        require(any('INVENTURE_INFO ' in line and 'Z1234ABCD' in line for line in rig.lines),'Info macro did not report the same identity')
        require(not rig.board.device.packets,'Read-only Info accessed an actuator interface')
        destination.mkdir(parents=True,exist_ok=True)
        image=b''.join(b''.join(frames[i][y*128:(y+1)*128] for i in (0,2,4)) for y in range(64))
        (destination/'R25_INFORMATION_OLED.png').write_bytes(png(image,scale=2,width=768,height=64))
        result=dict(status='PASS_INSTALLED_R25_INFORMATION',rows=rows,information=owner.get_status(0.),
            startup=startup,
            serial_values_are_simulated=True,read_only_render=True,oled_fluidd_same=True,
            live_cached_refresh=True,
            serial_opened=False,firmware_flashed=False,hardware_tested=False)
        (destination/'R25_INFORMATION.json').write_text(json.dumps(result,indent=2)+'\n')
        return result
    finally:
        executor=printer.lookup_object('aio_executor',None)
        if executor is not None:executor._handle_disconnect()
        loop.finalize()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(inspect(args.klipper.resolve(),args.config.resolve(),args.destination.resolve()),indent=2))
