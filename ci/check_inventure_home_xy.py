#!/usr/bin/env python3
"""Compare installed Home X/Y against the recorded pre-UI native adapter.

This exercises the actual macro/parser/OLED and native adapter with the
existing simulated interfaces. It never connects a printer or changes motion
implementation, configuration, pin polarity or motor-release behavior.
"""
import argparse
from contextlib import redirect_stdout
from hashlib import sha1,sha256
import importlib
import importlib.util
import inspect
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
from itertools import product

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'ci')]
REFERENCE=ROOT/'restored/scheduler_r21/inventure_native_z.py'
REFERENCE_BLOB='3fc954974a4a375e003b50c8624304301457f1de'


def attach_panel(rig,transport_r26=False):
    from test_orange_pi_oled_fast import Printer,Config,ready_stream
    package=type(rig.board).__module__.rsplit('.',1)[0]
    module=importlib.import_module(package+'.inventure_oled')
    catalog=importlib.import_module(package+'.inventure_oem_menu')
    fixture=Printer()
    if transport_r26:
        constants=fixture.mcu.get_constants()
        fixture.mcu.get_constants=lambda:dict(constants,INVENTURE_OLED_COEXISTENCE_ABI=1)
    panel=module.InventureOLED(Config(fixture))
    panel._build_config()
    panel.board,panel.printer,panel.reactor=rig.board,rig.board.printer,rig.board.reactor
    panel.connected,panel.stream=True,ready_stream()
    panel._script=rig.run
    panel.oem_display_mode='status'
    panel.oem_menu=catalog.OEMMenu(root_page='launcher',root_back_to_status=False,operational=True)
    rig.board.printer.objects['inventure_oled']=panel
    rig.gcode.register_command('INVENTURE_DISPLAY_CONTROL',None)
    rig.gcode.register_command('INVENTURE_DISPLAY_CONTROL',panel.cmd_display_control)
    return panel


def inspect_installed(klipper,config):
    from check_inventure_full_cycles import InstalledRig,require
    sys.path.insert(0,str(klipper/'klippy'))
    import klippy
    import reactor
    spec=importlib.util.spec_from_file_location('extras.inventure_recorded_xy_reference',REFERENCE)
    reference=importlib.util.module_from_spec(spec)
    reference_bytes=REFERENCE.read_bytes()
    require(sha1(b'blob '+str(len(reference_bytes)).encode()+b'\0'+reference_bytes).hexdigest()==REFERENCE_BLOB,
            'The saved reference module changed')
    sys.modules[spec.name]=reference
    spec.loader.exec_module(reference)
    loop=reactor.Reactor()
    printer=klippy.Printer(loop,None,dict(config_file=str(config),debuginput=True,
                           start_reason='startup',software_version='offline-home-xy-reference'))
    try:
        printer._read_config()
        gcode=printer.lookup_object('gcode')
        gcode.gcode_handlers=gcode.ready_gcode_handlers
        gcode.is_printer_ready=True
        settings=printer.lookup_object('configfile').get_status(0.)['settings']
        require(not any(name in settings for name in ('gcode_macro G28','homing_override','safe_z_home')),
                'A configured override changes the G28 route')
        handler=gcode.ready_gcode_handlers['G28']
        function=getattr(handler,'__func__',handler)
        handler_file=Path(inspect.getsourcefile(function)).resolve()
        require(handler_file==klipper/'klippy/extras/homing.py',
                'G28 handler is not the pinned core: '+str(handler_file))
        require(function.__qualname__=='PrinterHoming.cmd_G28','G28 handler changed')
        native=printer.lookup_object('inventure_native_z')
        recovered_class=type(native)
        rails=printer.lookup_object('toolhead').get_kinematics().rails
        rail_settings=[]
        for rail in rails:
            info=rail.get_homing_info()
            rail_settings.append(dict(homing_speed=rail.homing_speed,
                second_homing_speed=rail.second_homing_speed,homing_retract_speed=rail.homing_retract_speed,
                minimum=rail.get_range()[0],maximum=rail.get_range()[1],
                positive_dir=info.positive_dir,retract_dist=info.retract_dist,position_endstop=info.position_endstop))
        rows=[]
        cases=(('X',),('Y',),('X','Y'),('Y','X'),('X','Y','X'),('Y','X','Y'))
        for origin,hot in product(('fluidd','oled'),(False,True)):
            for sequence in cases:
                pair=[]
                for implementation,cls in (('recovered',recovered_class),('recorded',reference.InventureNativeZ)):
                    native.__class__=recovered_class
                    rig=InstalledRig(printer,klipper,hot=hot)
                    native.__class__=cls
                    for model_rail,values in zip(rig.model.rails,rail_settings):
                        for key in ('homing_speed','second_homing_speed','homing_retract_speed'):
                            setattr(model_rail,key,values[key])
                        model_rail.get_range=lambda values=values:(values['minimum'],values['maximum'])
                        model_rail.get_homing_info=lambda values=values:types.SimpleNamespace(
                            positive_dir=values['positive_dir'],retract_dist=values['retract_dist'],
                            position_endstop=values['position_endstop'])
                    if hot:
                        rig.model.axes='xyz'
                        rig.model.position[2]=native.park_z
                        rig.model.physical_z=rig.model.threshold+native.park_z
                        native.last_result=dict(reference_established=True,coordinate_saved=True)
                    targets={name:heater.get_temp(0.)[1] for name,heater in rig.model.heaters.items()}
                    panel=attach_panel(rig) if origin=='oled' else None
                    start=len(rig.model.calls)
                    completions=[]
                    for axis in sequence:
                        if origin=='fluidd':
                            rig.run('INVENTURE_HOME_'+axis)
                        else:
                            if rig.menu.view is not None:
                                require(rig.menu.view.kind=='info','Unexpected modal before the next Home')
                                rig.menu.view.selected,rig.menu.view.released=0,True
                                rig.run(rig.menu.panel_select_command(acknowledged=True))
                                require(rig.menu.view is None,'Back did not close the completed Home message')
                            panel.oem_menu.open_page('klipper_control')
                            panel.oem_display_mode='menu'
                            panel.oem_menu.selected=next(i for i,n in enumerate(panel.oem_menu.items())
                                                        if n.label=='Home '+axis)
                            panel._select(panel.reactor.monotonic())
                            if rig.menu.view is not None and rig.menu.view.kind=='confirm':
                                rig.confirm('oled')
                        require(rig.model.shared_enable,'Home '+axis+' released the shared enable')
                        require(rig.board._control_query()['mode']=='NATIVE','Cold Home changed motor owner')
                        require(rig.board.load_test_context is None and not rig.actions,
                                'Cold Home entered a factory/selector procedure')
                        require(any(line.removeprefix('// ')=='INVENTURE_HOME '+axis+' done' for line in rig.lines),
                                'The installed '+axis+' entry did not complete: '+
                                repr((origin,sequence,implementation,rig.lines[-4:],rig.model.calls[-4:])))
                        completions.append(dict(axis=axis,enable_held=True))
                    calls=rig.model.calls[start:]
                    require(not any(call[0]=='motor_off' for call in calls),'Home X/Y invoked motor_off')
                    require(not any(call[0]=='heat' for call in calls),'Cold Home started heating')
                    require(targets=={name:heater.get_temp(0.)[1] for name,heater in rig.model.heaters.items()},
                            'Home X/Y changed existing temperature targets')
                    xy=[call[1] for call in calls if call[0]=='G28' and call[1] in ('X','Y')]
                    require(xy==list(sequence),'A single-axis request homed another axis: '+repr(xy))
                    signature=dict(calls=calls,speeds=rig.model.speeds,axes=rig.model.axes,
                                   position=rig.model.get_position(),enable=rig.model.shared_enable,
                                   mode=rig.board._control_query()['mode'])
                    pair.append(signature)
                require(pair[0]==pair[1],'Recovered and recorded Home X/Y differ: '+repr(sequence))
                rows.append(dict(origin=origin,initial_state='NATIVE_REFERENCED' if hot else 'OFF_UNREFERENCED',
                                 sequence=list(sequence),reference_trace_equal=True,targets_unchanged=True,
                                 completions=completions,motor_off=False,heating=False,service=False))
        native.__class__=recovered_class
        return dict(status='PASS_INSTALLED_HOME_XY_REFERENCE',reference_blob=REFERENCE_BLOB,
                    case_count=len(rows),cases=rows,
                    g28_handler=function.__qualname__,g28_module='klippy/extras/homing.py',
                    g28_sha256=sha256(handler_file.read_bytes()).hexdigest(),
                    rail_settings=rail_settings,
                    overrides=[],serial_opened=False,firmware_flashed=False,hardware_tested=False)
    finally:
        dispatcher=printer.lookup_object('aio_executor',None)
        if dispatcher is not None:dispatcher._handle_disconnect()
        loop.finalize()


def check(destination):
    import inventure_bootstrap as bootstrap
    import inventure_oem_home_restore as restoration
    from check_inventure_oem_home import check as stage
    destination.mkdir(parents=True,exist_ok=False)
    stage(destination/'recovery')
    host=destination/'recovery/runtime'
    config=destination/'recovery/config/printer.cfg'
    before={p:p.read_bytes() for p in config.parent.rglob('*') if p.is_file()}
    with bootstrap.procedure_profile(restoration.PROFILE):
        identity_before=bootstrap.verify_host(host)
    run=subprocess.run([sys.executable,__file__,'--klipper',str(host),'--config',str(config)],
                       capture_output=True,text=True,timeout=90)
    (destination/'trace.log').write_text(run.stdout+run.stderr)
    if run.returncode:raise RuntimeError(run.stdout+'\n'+run.stderr)
    report=json.loads(run.stdout)
    if before!={p:p.read_bytes() for p in config.parent.rglob('*') if p.is_file()}:
        raise RuntimeError('Route verification changed configuration files')
    with bootstrap.procedure_profile(restoration.PROFILE):
        identity_after=bootstrap.verify_host(host)
        # Configuration loading may create Python caches; authenticated source
        # identities must remain identical and pass the full verifier again.
        if ({k:v for k,v in identity_after.items() if k!='generated_files'}
                !={k:v for k,v in identity_before.items() if k!='generated_files'}):
            raise RuntimeError('Route verification changed runtime files')
    report['config_unchanged']=report['runtime_unchanged']=True
    (destination/'HOME_XY_REFERENCE.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper',type=Path)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--destination',type=Path)
    args=parser.parse_args()
    if args.klipper and args.config:
        report=inspect_installed(args.klipper.absolute(),args.config.absolute())
    elif args.klipper or args.config:
        parser.error('--klipper and --config are required together')
    elif args.destination:
        with redirect_stdout(io.StringIO()):report=check(args.destination.absolute())
    else:
        with tempfile.TemporaryDirectory(prefix='inventure-home-xy-') as directory:
            with redirect_stdout(io.StringIO()):report=check(Path(directory)/'verified')
    print(json.dumps(report,indent=2))
