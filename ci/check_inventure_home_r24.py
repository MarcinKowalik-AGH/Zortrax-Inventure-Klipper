#!/usr/bin/env python3
"""Exercise installed R24 macros/OLED, original factory interpreter and Z engine.

Only physical interfaces are simulated. No UART, flash or running host is used.
"""
import argparse
from contextlib import redirect_stdout
import importlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'ci'),str(ROOT/'tests')]

def inspect(klipper,config):
    sys.path.insert(0,str(klipper/'klippy'))
    import klippy,reactor
    from check_inventure_full_cycles import InstalledRig,require
    from check_inventure_home_xy import attach_panel
    from test_orange_pi_native_z import HomingMove as ContactMove,Z_STEPS_PER_MM
    from test_orange_pi_load_test import Wire
    loop=reactor.Reactor()
    printer=klippy.Printer(loop,None,dict(config_file=str(config),debuginput=True,
                           start_reason='startup',software_version='offline-r24-operator-home'))
    rows=[];refusals=[]
    try:
        printer._read_config()
        printer.lookup_object('inventure_native_z')._install_g28()
        gcode=printer.lookup_object('gcode')
        gcode.gcode_handlers=gcode.ready_gcode_handlers;gcode.is_printer_ready=True
        package=type(printer.lookup_object('inventure_native_z')).__module__.rsplit('.',1)[0]
        maximum_z_speed=printer.lookup_object('toolhead').get_kinematics().max_z_velocity
        motion=importlib.import_module(package+'.homing')
        sequences=(('X','Y','Z'),('Y','X','Z'),('X','Z'),('Y','Z'),('Z',),
                   ('ALL',),('ALL','Z'),('X','Y','Z','Z'),('X','Y','DRIVE_RELEASE','Z'))
        cases=[(sequence,None) for sequence in sequences]+[
            (('X',),'seek_x'),(('X',),'release_x'),(('Y',),'release_y'),
            (('X',),'early_release_x'),(('Z',),'held_receipt'),
            (('X',),'backoff_x'),(('Y',),'backoff_y')]
        for origin in ('fluidd','oled','gcode'):
            for hot in (False,True):
                for sequence,fault in cases:
                    rig=InstalledRig(printer,klipper,hot=hot)
                    native,model,board=rig.native,rig.model,rig.board
                    model.max_z_velocity=maximum_z_speed
                    native.xy_references={};native.xy_clearance_position=None
                    native.requested_home_axes=None
                    model.axes='';model.position[2]=0.;model.physical_z=80.
                    pins={axis:types.SimpleNamespace(active=False) for axis in 'XY'}
                    for i,axis in enumerate('XY'):
                        pin=pins[axis]
                        pin.query_endstop=lambda _,pin=pin:pin.active
                        rail=native.kin.rails[i]
                        rail.homing_retract_dist=1.
                        rail.get_endstops=lambda pin=pin:[(pin,'limit')]
                    # Deliver actual invalidation events; the older fixture
                    # intentionally ignored them for its separate policy checks.
                    def event(name,*args):
                        if name=='inventure:native_motion_lost':native._native_motion_lost()
                        return []
                    native.printer.send_event=event
                    def held_entry(args):
                        owner,=args
                        require(model.shared_enable,'Held entry lost PE10 before the MCU call')
                        require(board.control_reply['mode']==1,'Held entry was not NATIVE')
                        model.calls.append(('held_service',owner))
                        board.control_reply.update(mode=2,armed=0)
                        # A periodic query can observe SERVICE before the
                        # held-entry reply reaches the homing coroutine.
                        board._control_query()
                        require(set(native.xy_references)=={'X','Y'},'Status polling discarded held XY')
                        return dict(owner=owner+(fault=='held_receipt'),mode=2,enabled=1,reject=0)
                    native.held_service=Wire(held_entry)
                    manual_move=model.manual_move
                    def checked_move(position,speed):
                        for index,axis in enumerate('XY'):
                            if (fault=='backoff_'+axis.lower() and position[index] is not None
                                    and abs(position[index]-model.position[index]-5.)<1e-9):
                                raise ValueError('Selected axis post-home backoff failed')
                        return manual_move(position,speed)
                    model.manual_move=checked_move
                    class Approach:
                        def __init__(self,p):self.axes=[]
                        def set_axes(self,axes):self.axes=axes
                        def home_rails(self,rails,force,target):
                            i,=self.axes;axis='XYZ'[i];rail,=rails
                            require(force[i]-target[i]==160.,'OEM approach budget changed')
                            require(rail.homing_retract_dist==0.,'A second contact replaced release')
                            native._home_rails_begin(self,rails)
                            model.calls.append(('xy_seek',axis,rail.homing_speed))
                            if fault=='seek_'+axis.lower():raise ValueError('No selected endstop trigger')
                            model.position[i]=target[i];model.set_position(model.position,axis.lower())
                            pins[axis].active=True;model.shared_enable=True
                    class PhysicalMove(ContactMove):
                        def homing_move(self,position,speed,probe_pos,triggered,check_triggered):
                            if self.pin in pins.values():
                                axis=next(a for a,p in pins.items() if p is self.pin)
                                i='XY'.index(axis)
                                require(not triggered and check_triggered and speed==1.,'Invalid OEM reverse release')
                                require(abs(position[i]-model.position[i]-5000./640.)<1e-9,'Release bound changed')
                                value=(80 if axis=='X' else 120)/640.
                                if fault=='release_'+axis.lower():raise ValueError('Selected endstop did not release')
                                if fault=='early_release_'+axis.lower():value=0.
                                model.calls.append(('xy_release',axis,round(value*640)))
                                measured=model.get_position();measured[i]=value
                                model.position[i]=value+1./640.;self.pin.active=False
                                return measured
                            if self.pin is model.bottom:
                                if triggered:
                                    require(not check_triggered,'Pre-XY clearance became a full bottom home')
                                    delta=position[2]-model.position[2]
                                    require(0<delta<=10.,'Initial clearance exceeds the accepted bound')
                                    model.calls.append(('initial_clearance',delta))
                                    delta=min(delta,native.bottom_position-model.physical_z)
                                    model.position[2]+=delta;model.physical_z+=delta;model.shared_enable=True
                                    return model.get_position()
                                model.calls.append(('bottom_release',))
                                release=native.bottom_position-1./Z_STEPS_PER_MM
                                model.z+=release-model.physical_z;model.physical_z=release
                                return model.get_position()
                            return super().homing_move(position,speed,probe_pos,triggered,check_triggered)
                        def check_no_movement(self):return None
                    panel=attach_panel(rig) if origin=='oled' else None
                    with patch.object(motion,'Homing',Approach),patch.object(motion,'HomingMove',PhysicalMove):
                        for name in sequence:
                            if name=='DRIVE_RELEASE':
                                native._xy_enable_changed(model.get_last_move_time(),False)
                                model.shared_enable=False
                                require(not native.xy_references and not model.axes,'Drive release left a reusable reference')
                                continue
                            if rig.menu.view is not None:
                                require(rig.menu.view.kind=='info','Unexpected modal between completed Homes')
                                rig.menu.view.selected,rig.menu.view.released=0,True
                                rig.run(rig.menu.panel_select_command(acknowledged=True))
                            start=len(model.calls);action_start=len(rig.actions)
                            rig.lines.clear()
                            before=set(native.xy_references)
                            before_position=model.get_position()
                            targets={n:h.get_temp(0.)[1] for n,h in model.heaters.items()}
                            try:
                                if origin=='fluidd':rig.run('INVENTURE_HOME_'+name)
                                elif origin=='gcode':rig.run('G28'+('' if name=='ALL' else ' '+' '.join(name)))
                                else:
                                    panel.oem_menu.open_page('klipper_control');panel.oem_display_mode='menu'
                                    label='Home XYZ' if name=='ALL' else 'Home '+name
                                    panel.oem_menu.selected=next(i for i,n in enumerate(panel.oem_menu.items()) if n.label==label)
                                    panel._select(panel.reactor.monotonic())
                                if rig.menu.view is not None and rig.menu.view.kind=='confirm':rig.confirm(origin)
                            except Exception as exc:
                                if fault is None:raise
                                require(not native.xy_references,'Rejected Home retained an XY reference')
                                require(not native.last_result or not native.last_result.get('reference_established'),
                                        'Rejected Home certified Z')
                                require(board.load_test_context is None,'Rejected Home retained a factory owner')
                                require(not rig.actions,'Rejected XY/entry started T0 motion')
                                refusals.append(dict(origin=origin,fault=fault,reason=str(exc),reference_invalid=True))
                                continue
                            if fault is not None:raise AssertionError('Expected refusal: '+fault)
                            calls=model.calls[start:]
                            seeks=[c[1] for c in calls if c[0]=='xy_seek']
                            expected=(['Y','X'] if name=='ALL' else [a for a in 'YX' if a not in before]
                                      if name=='Z' else [a for a in 'YX' if a in name])
                            require(seeks==expected,'Wrong requested/retained XY: '+repr((sequence,name,seeks,expected)))
                            require([c[1] for c in calls if c[0]=='xy_release']==seeks,'Missing release after approach')
                            backoffs=[]
                            for offset,call in enumerate(calls):
                                if call[0]!='xy_release':continue
                                axis=call[1];index='XY'.index(axis)
                                following=calls[offset+1:]
                                moves=[(i,c) for i,c in enumerate(following) if c[0]=='move']
                                require(bool(moves),'Missing 5 mm move after '+axis+' release')
                                move_index,backoff=moves[0]
                                require(not any(c[0] in ('xy_seek','held_service') for c in following[:move_index]),
                                        'Next procedure started before the '+axis+' backoff')
                                target=[None,None,None,None]
                                target[index]=call[2]/640.+1./640.+5.
                                require(backoff[1]==tuple(target) and backoff[2]==1.,
                                        'Backoff moved another axis, changed speed or was not 5 mm: '+repr(backoff))
                                ref=native.xy_references[axis]
                                require(ref['release_steps']==call[2] and ref['post_home_backoff_mm']==5.,
                                        'Backoff changed the measured datum')
                                require(abs(ref['parked_native_mm']-target[index])<1e-9,'Wrong retained park')
                                if name in ('X','Y','XY'):
                                    require(model.position[index]==target[index],'Standalone XY did not stay parked')
                                backoffs.append(axis)
                            require(not any(c[0]=='motor_off' for c in calls),'A successful Home released motors')
                            if name in ('X','Y'):
                                other=1 if name=='X' else 0
                                require(model.position[other]==before_position[other],
                                        'Single-axis Home displaced the other axis')
                            require(model.shared_enable,'Successful Home did not hold PE10')
                            require(board._control_query()['mode']=='NATIVE','Home did not return NATIVE')
                            require(targets=={n:h.get_temp(0.)[1] for n,h in model.heaters.items()},'Targets changed')
                            actions=rig.actions[action_start:]
                            require(not any(a['kind'] in ('home','home_release','z_contact_move') for a in actions),
                                    'Retained T0 preparation repeated SERVICE homing/clearance')
                            if name in ('Z','ALL'):
                                require(native.last_result and native.last_result.get('coordinate_saved'),'Z not certified')
                                require(model.position[2]==native.park_z,'Z did not park at its existing setting')
                                require(model.physical_tool==0,'T0 was not physically selected')
                                require(len([c for c in calls if c[0]=='held_service'])==1,'Missing held entry')
                                require([c[1] for c in calls if c[0]=='G28']==['Z'],'Full PE12 reference repeated')
                                require(board.load_test_context is None,'Factory owner survived completion')
                            require(set(native.xy_references).issuperset(before|set(seeks)),'Held XY reference was discarded')
                            rows.append(dict(origin=origin,initial='NATIVE_HOT' if hot else 'OFF',sequence=list(sequence),
                                request=name,xy_seek=seeks,xy_release=seeks,xy_backoff=backoffs,
                                xy_backoff_mm=5.,enable_held=True,targets_unchanged=True,
                                z_certified=name in ('Z','ALL'),motion_interfaces='simulated'))
        return dict(status='PASS_INSTALLED_R24_HOME_CYCLES',case_count=len(rows),cases=rows,
                    refusal_count=len(refusals),refusals=refusals,
                    serial_opened=False,firmware_flashed=False,hardware_tested=False)
    finally:
        executor=printer.lookup_object('aio_executor',None)
        if executor is not None:executor._handle_disconnect()
        loop.finalize()

def check(destination):
    import inventure_bootstrap as bootstrap
    from inventure_bootstrap_config import stage_config,_set_scalar
    destination.mkdir(parents=True,exist_ok=False)
    source=destination/'source'
    shutil.copytree(ROOT/'tests/fixtures/field_config_20261125',source)
    gcodes=destination/'gcodes';gcodes.mkdir()
    with bootstrap.procedure_profile('operator-home-r24'):
        host=bootstrap.prepare_host(destination/'runtime')
        import importlib.util
        spec=importlib.util.spec_from_file_location('r24_installer',ROOT/'install-new-host.py')
        installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        config_module=installer.updater.klipper_config_module(host)
        _set_scalar(config_module,source/'printer.cfg','virtual_sdcard','path',str(gcodes))
        config=Path(stage_config(host,destination/'config',source/'printer.cfg',serial='/dev/ttyS5',
                                 baud=750000,gcodes_path=gcodes)['config_path'])
        installer.require_identical_configuration(host,source/'printer.cfg',config)
        before={p:p.read_bytes() for p in config.parent.rglob('*') if p.is_file()}
        result=subprocess.run([sys.executable,__file__,'--klipper',str(host),'--config',str(config)],
                              text=True,capture_output=True,timeout=120)
        (destination/'cycles.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(result.stdout+'\n'+result.stderr)
        report=json.loads(result.stdout)
        if before!={p:p.read_bytes() for p in config.parent.rglob('*') if p.is_file()}:
            raise RuntimeError('Cycle test changed printer configuration')
        bootstrap.verify_host(host)
    report['configuration_unchanged']=True
    (destination/'R24_HOME_CYCLES.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--klipper',type=Path)
    parser.add_argument('--config',type=Path)
    args=parser.parse_args()
    if args.klipper and args.config:report=inspect(args.klipper.absolute(),args.config.absolute())
    elif args.destination:
        with redirect_stdout(io.StringIO()):report=check(args.destination.absolute())
    else:
        with tempfile.TemporaryDirectory(prefix='inventure-r24-home-') as temp:
            with redirect_stdout(io.StringIO()):report=check(Path(temp)/'verified')
    print(json.dumps(report,indent=2))
